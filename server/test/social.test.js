// Several LoL accounts per Moosi account + friend requests (request -> accept -> both friend lists).
import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { accountsApi, socialApi, rememberAccount } from "../src/social.js";

function d1() {
  const db = new DatabaseSync(":memory:");
  return {
    prepare(sql) {
      let args = [];
      return {
        bind(...a) { args = a; return this; },
        async run() { const r = db.prepare(sql).run(...args); return { meta: { changes: r.changes } }; },
        async all() { return { results: db.prepare(sql).all(...args) }; },
        async first() { return db.prepare(sql).get(...args) || null; },
      };
    },
    exec: (s) => db.exec(s),
  };
}
const req = (path, method = "GET", body) => new Request("https://x" + path, { method, body: body ? JSON.stringify(body) : undefined });
const call = async (u, path, method, body, env) => (await socialApi(req(path, method, body), env, { id: u }, new URL("https://x" + path).pathname, new URL("https://x" + path))).json();

test("accounts: several Riot IDs, main switches users.riot_id", async () => {
  const env = { DB: d1() };
  env.DB.exec(`CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, riot_id TEXT);
    CREATE TABLE user_data (user_id INTEGER PRIMARY KEY, friends TEXT, friends_at INTEGER, profile TEXT);
    INSERT INTO users VALUES (1, 'J', 'Main#EUW');`);
  let r = await (await accountsApi(req("/api/sync/accounts"), env, { id: 1 })).json();
  assert.deepEqual(r.accounts.map((a) => a.rid), ["Main#EUW"]);
  r = await (await accountsApi(req("/api/sync/accounts", "PUT", { accounts: [{ rid: "Main#EUW", plat: "euw1" }, { rid: "Smurf#EUW", plat: "euw1" }, { rid: "bad" }], main: "Smurf#EUW" }), env, { id: 1 })).json();
  assert.equal(r.main, "Smurf#EUW");
  assert.equal(r.accounts.length, 2);
  assert.equal((await env.DB.prepare("SELECT riot_id FROM users WHERE id = 1").first()).riot_id, "Smurf#EUW");
  await rememberAccount(env, 1, "Third#EUW");
  r = await (await accountsApi(req("/api/sync/accounts"), env, { id: 1 })).json();
  assert.equal(r.accounts.length, 3);
});

test("friends: search, request, accept, remove", async () => {
  const env = { DB: d1() };
  env.DB.exec(`CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, riot_id TEXT);
    CREATE TABLE user_data (user_id INTEGER PRIMARY KEY, friends TEXT, friends_at INTEGER, profile TEXT);
    INSERT INTO users VALUES (1, 'Jannis', 'Jannis#EUW'), (2, 'Luca', 'Luca#EUW'), (3, 'Hidden', 'Ghost#EUW');
    INSERT INTO user_data (user_id, profile) VALUES (2, '{"name":"Lucaaa","visible":true}'), (3, '{"name":"Ghosty","visible":false}');`);
  let s = await call(1, "/api/social/search?q=luc", "GET", null, env);
  assert.equal(s.results.length, 1);
  assert.equal(s.results[0].name, "Lucaaa");
  assert.equal((await call(1, "/api/social/search?q=ghost", "GET", null, env)).results.length, 0);   // hidden profile
  assert.equal((await call(1, "/api/social/request", "POST", { uid: 2 }, env)).rel, "out");
  let l2 = await call(2, "/api/social/list", "GET", null, env);
  assert.equal(l2.incoming.length, 1);
  assert.equal(l2.incoming[0].riot_id, "Jannis#EUW");
  assert.equal((await call(2, "/api/social/respond", "POST", { uid: 1, accept: true }, env)).rel, "friend");
  const fl = async (u) => JSON.parse((await env.DB.prepare("SELECT friends FROM user_data WHERE user_id = ?").bind(u).first()).friends);
  assert.deepEqual(await fl(1), ["Luca#EUW"]);
  assert.deepEqual(await fl(2), ["Jannis#EUW"]);
  assert.equal((await call(1, "/api/social/list", "GET", null, env)).friends.length, 1);
  await call(1, "/api/social/remove", "POST", { uid: 2 }, env);
  assert.deepEqual(await fl(1), []);
  assert.deepEqual(await fl(2), []);
  // crossing requests become a friendship right away
  await call(1, "/api/social/request", "POST", { uid: 2 }, env);
  assert.equal((await call(2, "/api/social/request", "POST", { uid: 1 }, env)).rel, "friend");
  assert.equal((await call(1, "/api/social/request", "POST", { uid: 1 }, env)).error, "ungültig");
});
