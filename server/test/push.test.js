// Web Push: subscribe, VAPID-signed empty push, inbox for the service worker, friend-in-game + daily task (cron).
import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { pushApi, pushInbox, scheduled, sendPush } from "../src/push.js";

function d1() {   // tiny D1 shim on top of node:sqlite
  const db = new DatabaseSync(":memory:");
  return {
    prepare(sql) {
      let args = [];
      const st = () => db.prepare(sql);
      return {
        bind(...a) { args = a; return this; },
        async run() { const r = st().run(...args); return { meta: { changes: r.changes } }; },
        async all() { return { results: st().all(...args) }; },
        async first() { return st().get(...args) || null; },
      };
    },
    exec: (s) => db.exec(s),
  };
}

test("push: subscribe, test push, inbox, cron", async () => {
  const env = { DB: d1() };
  env.DB.exec(`CREATE TABLE users (id INTEGER PRIMARY KEY, riot_id TEXT); CREATE TABLE user_data (user_id INTEGER PRIMARY KEY, friends TEXT, coach TEXT, profile TEXT, status TEXT, status_at INTEGER);
    INSERT INTO users VALUES (1, 'Me#EUW'), (2, 'Luca#EUW');`);
  const user = { id: 1 };
  const ep = "https://fcm.googleapis.com/fcm/send/abc123";
  let r = await pushApi(new Request("https://x/api/push/key"), env, user, "/api/push/key");
  assert.equal(Buffer.from((await r.json()).key, "base64url").length, 65);
  r = await pushApi(new Request("https://x/api/push/subscribe", { method: "POST", body: JSON.stringify({ subscription: { endpoint: "https://evil.example/x" } }) }), env, user, "/api/push/subscribe");
  assert.equal(r.status, 400);
  r = await pushApi(new Request("https://x/api/push/subscribe", { method: "POST", body: JSON.stringify({ subscription: { endpoint: ep }, prefs: { task: false } }) }), env, user, "/api/push/subscribe");
  assert.equal((await r.json()).prefs.task, false);

  const calls = [];
  globalThis.fetch = async (u, opt) => { calls.push({ u: String(u), opt }); return new Response("", { status: 201 }); };
  r = await pushApi(new Request("https://x/api/push/test", { method: "POST" }), env, user, "/api/push/test");
  assert.equal((await r.json()).sent, 1);
  assert.equal(calls[0].u, ep);
  assert.match(calls[0].opt.headers.Authorization, /^vapid t=[\w-]+\.[\w-]+\.[\w-]+, k=[\w-]+$/);
  r = await pushInbox(new URL("https://x/api/push/inbox?ep=" + encodeURIComponent(ep)), env);
  assert.match((await r.json()).body, /funktionieren/);

  // cron: friend Luca uses Moosi and is in game -> exactly one push, task push is switched off
  env.DB.exec(`INSERT INTO user_data (user_id, friends) VALUES (1, '["Luca#EUW"]');
    INSERT INTO user_data (user_id, status, status_at, profile) VALUES (2, '{"state":"ingame","champ":"Ahri","since":111}', ${Math.floor(Date.now() / 1000)}, '{}');`);
  calls.length = 0;
  await scheduled(env, null);
  await scheduled(env, null);
  assert.equal(calls.length, 1);
  r = await pushInbox(new URL("https://x/api/push/inbox?ep=" + encodeURIComponent(ep)), env);
  const m = await r.json();
  assert.equal(m.title, "Luca spielt gerade");
  assert.match(m.body, /Ahri/);

  // expired subscription is removed
  globalThis.fetch = async () => new Response("", { status: 410 });
  await sendPush(env, 1, { title: "x", body: "y" });
  r = await pushApi(new Request("https://x/api/push/test", { method: "POST" }), env, user, "/api/push/test");
  assert.equal((await r.json()).sent, 0);
});
