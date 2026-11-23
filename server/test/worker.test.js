// Tests the worker logic in plain Node (no Cloudflare runtime): mocks D1, cache, fetch.
import test from "node:test";
import assert from "node:assert/strict";
import worker, { sign, verify } from "../src/index.js";

function mockDB() {
  const users = [], data = new Map();
  return {
    users,
    prepare(sql) {
      let args = [];
      return {
        bind(...a) { args = a; return this; },
        async run() {
          if (sql.startsWith("CREATE TABLE")) return {};
          if (sql.includes("INTO config")) { globalThis.__cfg = globalThis.__cfg || args[0]; return {}; }
          if (sql.startsWith("INSERT INTO users")) {
            const [provider, pid, name, c, l] = args;
            const ex = users.find((u) => u.provider === provider && u.provider_id === pid);
            if (ex) { ex.name = name; ex.last_login = l; } else users.push({ id: users.length + 1, provider, provider_id: pid, name, riot_id: null, created_at: c, last_login: l });
          } else if (sql.startsWith("UPDATE users SET riot_id")) {
            users.find((u) => u.id === args[1]).riot_id = args[0];
          } else if (sql.startsWith("INSERT INTO user_data")) {
            const col = sql.match(/user_id, (\w+),/)[1];
            data.set(args[0], { ...(data.get(args[0]) || {}), [col]: args[1], updated_at: args[2] });
          } else if (sql.startsWith("DELETE FROM user_data")) {
            data.delete(args[0]);
          } else if (sql.startsWith("DELETE FROM users")) {
            users.splice(users.findIndex((u) => u.id === args[0]), 1);
          }
          return {};
        },
        async first() {
          if (sql.includes("FROM config")) return this.cfg ?? (globalThis.__cfg ? { value: globalThis.__cfg } : null);
          if (sql.includes("FROM user_data")) {
            const d = data.get(args[0]); const col = sql.match(/SELECT (\w+) AS v/)[1];
            return d ? { v: d[col] ?? null, updated_at: d.updated_at } : null;
          }
          if (sql.includes("WHERE id = ?")) return users.find((u) => u.id === args[0]) || null;
          return users.find((u) => u.provider === args[0] && u.provider_id === args[1]) || null;
        },
      };
    },
  };
}

const env = { RIOT_API_KEY: "RGAPI-test", DISCORD_CLIENT_ID: "cid", DISCORD_CLIENT_SECRET: "cs",
  GOOGLE_CLIENT_ID: "gid", GOOGLE_CLIENT_SECRET: "gs", DB: mockDB() };
const ctx = { waitUntil() {} };
globalThis.caches = { default: { store: new Map(), async match(r) { return this.store.get(r.url)?.clone(); }, async put(r, res) { this.store.set(r.url, res); } } };

test("jwt sign/verify and tamper detection", async () => {
  const t = await sign({ uid: 1, exp: Date.now() / 1000 + 60 }, "s");
  assert.equal((await verify(t, "s")).uid, 1);
  assert.equal(await verify(t + "x", "s"), null);
  assert.equal(await verify(await sign({ uid: 1, exp: 1 }, "s"), "s"), null); // expired
});

test("login flow: discord -> account -> token back to app", async () => {
  let r = await worker.fetch(new Request("https://jc.dev/auth/discord?redirect=" + encodeURIComponent("http://localhost:8765/auth/callback")), env, ctx);
  assert.equal(r.status, 302);
  const loc = new URL(r.headers.get("location"));
  assert.equal(loc.hostname, "discord.com");
  assert.equal(loc.searchParams.get("scope"), "identify");
  const state = loc.searchParams.get("state");
  // evil redirect is rejected
  r = await worker.fetch(new Request("https://jc.dev/auth/discord?redirect=https://evil.example/cb"), env, ctx);
  assert.equal(r.status, 400);
  globalThis.fetch = async (u, opt) => {
    if (String(u).includes("oauth2/token")) return new Response(JSON.stringify({ access_token: "at" }));
    if (String(u).includes("users/@me")) return new Response(JSON.stringify({ id: "42", username: "gercool", global_name: "Gercool" }));
    throw new Error("unexpected " + u);
  };
  r = await worker.fetch(new Request(`https://jc.dev/auth/discord/callback?code=abc&state=${encodeURIComponent(state)}`), env, ctx);
  assert.equal(r.status, 302);
  const back = new URL(r.headers.get("location"));
  assert.equal(back.origin + back.pathname, "http://localhost:8765/auth/callback");
  const token = back.searchParams.get("token");
  assert.equal(env.DB.users[0].name, "Gercool");
  // /api/me
  r = await worker.fetch(new Request("https://jc.dev/api/me", { headers: { authorization: "Bearer " + token } }), env, ctx);
  assert.equal((await r.json()).provider, "discord");
  r = await worker.fetch(new Request("https://jc.dev/api/me", { method: "PUT", headers: { authorization: "Bearer " + token }, body: JSON.stringify({ riot_id: "Gercool#EUW" }) }), env, ctx);
  assert.equal(env.DB.users[0].riot_id, "Gercool#EUW");
  // riot proxy: allowed + cached, not allowed, unauthenticated
  let calls = 0;
  globalThis.fetch = async (u, opt) => { calls++; assert.equal(opt.headers["X-Riot-Token"], "RGAPI-test"); return new Response(JSON.stringify({ ok: 1 }), { status: 200 }); };
  const mreq = () => new Request("https://jc.dev/riot/europe/lol/match/v5/matches/EUW1_123", { headers: { authorization: "Bearer " + token } });
  assert.equal((await worker.fetch(mreq(), env, ctx)).status, 200);
  assert.equal((await worker.fetch(mreq(), env, ctx)).status, 200);
  assert.equal(calls, 1);
  r = await worker.fetch(new Request("https://jc.dev/riot/europe/lol/spectator/v5/active-games/by-summoner/x", { headers: { authorization: "Bearer " + token } }), env, ctx);
  assert.equal(r.status, 403);
  r = await worker.fetch(new Request("https://jc.dev/riot/evil.com/x"), env, ctx);
  assert.equal(r.status, 401);
  // delete account
  r = await worker.fetch(new Request("https://jc.dev/api/me", { method: "DELETE", headers: { authorization: "Bearer " + token } }), env, ctx);
  assert.equal((await r.json()).deleted, true);
  assert.equal(env.DB.users.length, 0);
  r = await worker.fetch(new Request("https://jc.dev/api/me", { headers: { authorization: "Bearer " + token } }), env, ctx);
  assert.equal(r.status, 401);
});

test("mobile redirects and sync", async () => {
  let r = await worker.fetch(new Request("https://jc.dev/auth/google?redirect=" + encodeURIComponent("junglecoach://auth")), env, ctx);
  assert.equal(r.status, 302);
  r = await worker.fetch(new Request("https://jc.dev/auth/google?redirect=" + encodeURIComponent("https://jc.dev/m/")), env, ctx);
  assert.equal(r.status, 302);
  r = await worker.fetch(new Request("https://jc.dev/auth/google?redirect=" + encodeURIComponent("https://other.dev/m/")), env, ctx);
  assert.equal(r.status, 400);
  env.DB.users.push({ id: 99, provider: "google", provider_id: "s", name: "S", riot_id: null, created_at: 1, last_login: 1 });
  const { sign } = await import("../src/index.js");
  const cfgRow = await env.DB.prepare("SELECT value FROM config WHERE key = 'jwt_secret'").first();
  const tok = await sign({ uid: 99, exp: Date.now() / 1000 + 600 }, cfgRow.value);
  const H = { authorization: "Bearer " + tok };
  r = await worker.fetch(new Request("https://jc.dev/api/sync/friends", { method: "PUT", headers: H,
    body: JSON.stringify({ friends: ["Luca#EUW", "bad", "Luca#EUW", "Mia#GER"] }) }), env, ctx);
  assert.deepEqual((await r.json()).friends, ["Luca#EUW", "Mia#GER"]);
  r = await worker.fetch(new Request("https://jc.dev/api/sync/friends", { headers: H }), env, ctx);
  assert.deepEqual((await r.json()).friends, ["Luca#EUW", "Mia#GER"]);
  r = await worker.fetch(new Request("https://jc.dev/api/sync/coach", { method: "PUT", headers: H, body: JSON.stringify({ plan: { score: 61 } }) }), env, ctx);
  r = await worker.fetch(new Request("https://jc.dev/api/sync/coach", { headers: H }), env, ctx);
  assert.equal((await r.json()).coach.score, 61);
  r = await worker.fetch(new Request("https://jc.dev/api/sync/coach", { method: "PUT", headers: H, body: "x".repeat(90000) }), env, ctx);
  assert.equal(r.status, 413);
  r = await worker.fetch(new Request("https://jc.dev/api/sync/friends"), env, ctx);
  assert.equal(r.status, 401);
});
