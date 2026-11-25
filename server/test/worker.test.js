// Tests the worker logic in plain Node (no Cloudflare runtime): mocks D1, cache, fetch.
import test from "node:test";
import assert from "node:assert/strict";
import worker, { sign, verify } from "../src/index.js";

function mockDB() {
  const users = [], data = new Map(), avatars = new Map();
  return {
    users,
    prepare(sql) {
      let args = [];
      return {
        bind(...a) { args = a; return this; },
        async run() {
          if (sql.startsWith("CREATE TABLE")) return {};
          if (sql.includes("INTO config (key, value) VALUES ('riot_key")) { (globalThis.__conf ||= {})[sql.match(/'(riot_key(?:_at)?)'/)[1]] = args[0]; return {}; }
          if (sql.startsWith("DELETE FROM config")) { globalThis.__conf = {}; return {}; }
          if (sql.includes("INTO config")) { globalThis.__cfg = globalThis.__cfg || args[0]; return {}; }
          if (sql.startsWith("UPDATE users SET is_admin")) { users.find((u) => u.id === args[0]).is_admin = 1; return {}; }
          if (sql.startsWith("ALTER TABLE users")) return {};
          if (sql.startsWith("INSERT INTO users")) {
            const [provider, pid, name, c, l] = args;
            const ex = users.find((u) => u.provider === provider && u.provider_id === pid);
            if (ex) { ex.name = name; ex.last_login = l; } else users.push({ id: users.length + 1, provider, provider_id: pid, name, riot_id: null, created_at: c, last_login: l });
          } else if (sql.startsWith("UPDATE users SET riot_id")) {
            users.find((u) => u.id === args[1]).riot_id = args[0];
          } else if (sql.startsWith("INSERT INTO user_data (user_id, riot_key)")) {
            data.set(args[0], { ...(data.get(args[0]) || {}), riot_key: args[1] });
          } else if (sql.startsWith("INSERT INTO user_data")) {
            const [, col, atc] = sql.match(/user_id, (\w+), (\w+)\)/);
            data.set(args[0], { ...(data.get(args[0]) || {}), [col]: args[1], [atc]: args[2] });
          } else if (sql.startsWith("INSERT INTO avatars")) {
            avatars.set(args[0], { id: args[0], user_id: args[1], mime: args[2], data: args[3] });
          } else if (sql.startsWith("DELETE FROM avatars")) {
            for (const [k, v] of avatars) if (v.user_id === args[0]) avatars.delete(k);
          } else if (sql.startsWith("DELETE FROM user_data")) {
            data.delete(args[0]);
          } else if (sql.startsWith("DELETE FROM users")) {
            users.splice(users.findIndex((u) => u.id === args[0]), 1);
          }
          return {};
        },
        async all() {
          if (sql.includes("FROM users u JOIN user_data")) {
            return { results: users.filter((u) => u.riot_id && args.includes(u.riot_id.toLowerCase()) && data.has(u.id))
              .map((u) => ({ rid: u.riot_id, p: data.get(u.id).profile, s: data.get(u.id).status, sa: data.get(u.id).status_at })) };
          }
          return { results: [] };
        },
        async first() {
          if (sql.includes("FROM config WHERE key = 'riot_key")) { const k = sql.match(/'(riot_key(?:_at)?)'/)[1]; const v = (globalThis.__conf || {})[k]; return v ? { value: v } : null; }
          if (sql.includes("WHERE is_admin = 1")) return users.find((u) => u.is_admin) || null;
          if (sql.includes("FROM config")) return this.cfg ?? (globalThis.__cfg ? { value: globalThis.__cfg } : null);
          if (sql.includes("FROM avatars")) return avatars.get(args[0]) || null;
          if (sql.includes("FROM user_data")) {
            const d = data.get(args[0]); const col = sql.match(/SELECT (\w+) AS v/)[1];
            const atc = (sql.match(/, (\w+) AS at/) || [])[1] || "updated_at";
            return d ? { v: d[col] ?? null, at: d[atc] ?? null } : null;
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
  r = await worker.fetch(new Request("https://jc.dev/riot/euw1/lol/challenges/v1/player-data/x", { headers: { authorization: "Bearer " + token } }), env, ctx);
  assert.equal(r.status, 403); // not on the allowlist
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

test("profiles, status and public view", async () => {
  const { sign } = await import("../src/index.js");
  const cfgRow = await env.DB.prepare("SELECT value FROM config WHERE key = 'jwt_secret'").first();
  env.DB.users.push({ id: 77, provider: "google", provider_id: "p77", name: "P", riot_id: "Luca#EUW", created_at: 1, last_login: 1 });
  env.DB.users.push({ id: 78, provider: "google", provider_id: "p78", name: "Q", riot_id: "Mia#GER", created_at: 1, last_login: 1 });
  const H = async (uid) => ({ authorization: "Bearer " + await sign({ uid, exp: Date.now() / 1000 + 600 }, cfgRow.value) });
  let r = await worker.fetch(new Request("https://jc.dev/api/sync/profile", { method: "PUT", headers: await H(77),
    body: JSON.stringify({ profile: { name: "Luca <b>", bio: "Jungle main", avatar: { type: "champ", id: "Graves" }, favs: ["Graves", "x y", "Gwen"], banner: "Gwen" } }) }), env, ctx);
  const pr = (await r.json()).profile;
  assert.equal(pr.name, "Luca b");
  assert.deepEqual(pr.favs, ["Graves", "Gwen"]);
  await worker.fetch(new Request("https://jc.dev/api/sync/status", { method: "PUT", headers: await H(77), body: JSON.stringify({ state: "ingame", champ: "Graves" }) }), env, ctx);
  await worker.fetch(new Request("https://jc.dev/api/sync/profile", { method: "PUT", headers: await H(78), body: JSON.stringify({ profile: { name: "Hidden", visible: false } }) }), env, ctx);
  r = await worker.fetch(new Request("https://jc.dev/api/public/profiles?ids=" + encodeURIComponent("luca#euw,Mia#GER"), { headers: await H(78) }), env, ctx);
  const j = await r.json();
  assert.equal(j.profiles["luca#euw"].status.state, "ingame");
  assert.equal(j.profiles["luca#euw"].avatar.id, "Graves");
  assert.equal(j.profiles["mia#ger"], undefined); // chose not to be visible
  r = await worker.fetch(new Request("https://jc.dev/api/public/profiles?ids=x"), env, ctx);
  assert.equal(r.status, 401);
});

test("avatar upload and serving", async () => {
  const { sign } = await import("../src/index.js");
  const cfgRow = await env.DB.prepare("SELECT value FROM config WHERE key = 'jwt_secret'").first();
  const H = { authorization: "Bearer " + await sign({ uid: 77, exp: Date.now() / 1000 + 600 }, cfgRow.value) };
  const jpeg = btoa(String.fromCharCode(0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4));
  let r = await worker.fetch(new Request("https://jc.dev/api/sync/avatar", { method: "PUT", headers: H, body: JSON.stringify({ data: "data:image/jpeg;base64," + jpeg }) }), env, ctx);
  const j = await r.json();
  assert.equal(j.avatar.type, "upload");
  r = await worker.fetch(new Request("https://jc.dev" + j.url), env, ctx);
  assert.equal(r.headers.get("content-type"), "image/jpeg");
  assert.equal((await r.arrayBuffer()).byteLength, 8);
  r = await worker.fetch(new Request("https://jc.dev/api/sync/avatar", { method: "PUT", headers: H, body: JSON.stringify({ data: "data:text/html;base64," + btoa("<script>") }) }), env, ctx);
  assert.equal(r.status, 400);
  r = await worker.fetch(new Request("https://jc.dev/api/sync/profile", { method: "PUT", headers: H, body: JSON.stringify({ profile: { avatar: j.avatar, role: "JUNGLE" } }) }), env, ctx);
  const pr = (await r.json()).profile;
  assert.equal(pr.avatar.id, j.avatar.id);
  assert.equal(pr.role, "JUNGLE");
});

test("own riot key per user: stored, used by the proxy, expired key -> 502 not 401", async () => {
  env.DB.users.push({ id: 77, provider: "google", provider_id: "k", name: "K", riot_id: null, created_at: 1, last_login: 1 });
  const cfgRow = await env.DB.prepare("SELECT value FROM config WHERE key = 'jwt_secret'").first();
  const H = { authorization: "Bearer " + await sign({ uid: 77, exp: Date.now() / 1000 + 600 }, cfgRow.value) };
  let r = await worker.fetch(new Request("https://jc.dev/api/sync/riotkey", { method: "PUT", headers: H, body: JSON.stringify({ key: "nope" }) }), env, ctx);
  assert.equal(r.status, 400);
  const key = "RGAPI-12345678-abcd-abcd-abcd-1234567890ab";
  r = await worker.fetch(new Request("https://jc.dev/api/sync/riotkey", { method: "PUT", headers: H, body: JSON.stringify({ key }) }), env, ctx);
  const j = await r.json();
  assert.equal(j.set, true); assert.equal(j.tail, "90ab"); assert.ok(!JSON.stringify(j).includes(key));
  const used = [];
  globalThis.fetch = async (u, opt) => { used.push(opt.headers["X-Riot-Token"]); return new Response("{}", { status: 401 }); };
  r = await worker.fetch(new Request("https://jc.dev/riot/euw1/lol/league/v4/entries/by-puuid/xyz", { headers: H }), env, ctx);
  assert.equal(used[0], key);                 // own key first …
  assert.notEqual(used[1], key);              // … expired -> falls back to the server key
  assert.equal(r.status, 502);                // both rejected -> 502 (not 401: the login is fine)
  // own key expired but the server key works -> the user just gets the data
  globalThis.fetch = async (u, opt) => new Response("{}", { status: opt.headers["X-Riot-Token"] === key ? 401 : 200 });
  r = await worker.fetch(new Request("https://jc.dev/riot/euw1/lol/league/v4/entries/by-puuid/abc", { headers: H }), env, ctx);
  assert.equal(r.status, 200);
  r = await worker.fetch(new Request("https://jc.dev/api/sync/riotkey", { method: "DELETE", headers: H }), env, ctx);
  assert.equal((await r.json()).set, false);
});

test("admin: one-time claim, global riot key used for everyone", async () => {
  env.DB.users.push({ id: 201, provider: "google", provider_id: "adm", name: "A", riot_id: null, created_at: 1, last_login: 1 });
  env.DB.users.push({ id: 202, provider: "google", provider_id: "oth", name: "B", riot_id: null, created_at: 1, last_login: 1 });
  const cfgRow = await env.DB.prepare("SELECT value FROM config WHERE key = 'jwt_secret'").first();
  const HA = { authorization: "Bearer " + await sign({ uid: 201, exp: Date.now() / 1000 + 600 }, cfgRow.value) };
  const HB = { authorization: "Bearer " + await sign({ uid: 202, exp: Date.now() / 1000 + 600 }, cfgRow.value) };
  let r = await worker.fetch(new Request("https://jc.dev/api/admin/riotkey", { headers: HA }), env, ctx);
  assert.equal(r.status, 403);
  r = await worker.fetch(new Request("https://jc.dev/api/admin/claim", { method: "POST", headers: HA }), env, ctx);
  assert.equal(r.status, 200);
  r = await worker.fetch(new Request("https://jc.dev/api/admin/claim", { method: "POST", headers: HB }), env, ctx);
  assert.equal(r.status, 403);
  const key = "RGAPI-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
  globalThis.fetch = async () => new Response("{}", { status: 200 });
  r = await worker.fetch(new Request("https://jc.dev/api/admin/riotkey", { method: "PUT", headers: HA, body: JSON.stringify({ key }) }), env, ctx);
  const j = await r.json();
  assert.equal(j.set, true); assert.equal(j.tail, "eeee");
  r = await worker.fetch(new Request("https://jc.dev/api/admin/riotkey", { method: "PUT", headers: HB, body: JSON.stringify({ key }) }), env, ctx);
  assert.equal(r.status, 403);
  let used = null;
  globalThis.fetch = async (u, opt) => { used = opt.headers["X-Riot-Token"]; return new Response("{}", { status: 200 }); };
  r = await worker.fetch(new Request("https://jc.dev/riot/euw1/lol/league/v4/entries/by-puuid/other", { headers: HB }), env, ctx);
  assert.equal(used, key);
  r = await worker.fetch(new Request("https://jc.dev/api/me", { headers: HA }), env, ctx);
  assert.equal((await r.json()).is_admin, true);
});
