/**
 * Jungle Coach backend (Cloudflare Worker)
 *
 *  /auth/{discord|google}            start login (from the desktop app, via the system browser)
 *  /auth/{discord|google}/callback   OAuth callback -> account -> session token -> back to the app
 *  /api/me            GET account | PUT {riot_id} | DELETE account (GDPR Art. 17)
 *  /api/sync/friends  GET | PUT {friends:[Riot IDs]}   – same friend list on PC and phone
 *  /api/sync/coach    GET | PUT {plan}                  – last coach summary from the PC app, for the phone
 *  /api/sync/profile  GET | PUT {profile}               – own display name, avatar, banner, bio, favourite champions
 *  /api/sync/avatar   PUT {data: "data:image/jpeg;base64,..."} – own profile picture (256 px, max 80 KB) | DELETE
 *  /a/<id>            GET – profile picture (public URL with a random id)
 *  /api/sync/status   PUT {state, champ}                – "playing right now" from the PC app (shown to friends)
 *  /api/public/profiles?ids=A#1,B#2  GET               – friends' public profiles + live status (only if shared)
 *  /m/                mobile web app (also loaded by the iOS app)
 *  /riot/{host}/...   authenticated proxy to the Riot API (key stays on the server)
 *  everything else    static website (public/)
 *
 * Data minimisation: we store only provider, provider user id, display name, optional Riot ID
 * and timestamps. No e-mail, no password.
 */

const APP_REDIRECTS = [/^http:\/\/(localhost|127\.0\.0\.1):87\d\d\/auth\/callback$/, /^junglecoach:\/\/auth$/];
const allowedRedirect = (r, origin) => APP_REDIRECTS.some((re) => re.test(r)) || r === `${origin}/m/`;
const MAX_SYNC = 80000; // bytes per stored JSON blob
const SESSION_DAYS = 30;

const RIOT_HOSTS = new Set(["europe", "americas", "asia", "sea", "euw1", "eun1", "tr1", "ru", "na1", "br1",
  "la1", "la2", "oc1", "kr", "jp1", "sg2", "tw2", "vn2", "me1"]);
// [path regex, cache seconds]
const RIOT_ROUTES = [
  [/^\/riot\/account\/v1\/accounts\/by-riot-id\/[^/]+\/[^/]+$/, 3600],
  [/^\/lol\/summoner\/v4\/summoners\/by-puuid\/[\w-]+$/, 300],
  [/^\/lol\/league\/v4\/entries\/by-puuid\/[\w-]+$/, 120],
  [/^\/lol\/champion-mastery\/v4\/champion-masteries\/by-puuid\/[\w-]+\/top$/, 3600],
  [/^\/lol\/match\/v5\/matches\/by-puuid\/[\w-]+\/ids$/, 60],
  [/^\/lol\/match\/v5\/matches\/[A-Z0-9]+_\d+$/, 7 * 86400],
  [/^\/lol\/match\/v5\/matches\/[A-Z0-9]+_\d+\/timeline$/, 7 * 86400],
  [/^\/lol\/status\/v4\/platform-data$/, 60],
  [/^\/lol\/spectator\/v5\/active-games\/by-summoner\/[\w-]+$/, 30], // "is playing right now" (official)
];

const PROVIDERS = {
  discord: {
    authorize: "https://discord.com/oauth2/authorize",
    token: "https://discord.com/api/oauth2/token",
    profile: "https://discord.com/api/users/@me",
    scope: "identify",
    id: (env) => env.DISCORD_CLIENT_ID, secret: (env) => env.DISCORD_CLIENT_SECRET,
    user: (p) => ({ id: String(p.id), name: p.global_name || p.username || "Discord-Nutzer" }),
  },
  google: {
    authorize: "https://accounts.google.com/o/oauth2/v2/auth",
    token: "https://oauth2.googleapis.com/token",
    profile: "https://openidconnect.googleapis.com/v1/userinfo",
    scope: "openid profile",
    id: (env) => env.GOOGLE_CLIENT_ID, secret: (env) => env.GOOGLE_CLIENT_SECRET,
    user: (p) => ({ id: String(p.sub), name: p.given_name || p.name || "Google-Nutzer" }),
  },
};

// ------------------------------------------------------------------ helpers
const enc = new TextEncoder();
const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const b64urlJson = (obj) => b64url(enc.encode(JSON.stringify(obj)));
const fromB64url = (s) => JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0))));

async function hmac(secret, data) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return b64url(await crypto.subtle.sign("HMAC", key, enc.encode(data)));
}

export async function sign(payload, secret) {
  const body = `${b64urlJson({ alg: "HS256", typ: "JWT" })}.${b64urlJson(payload)}`;
  return `${body}.${await hmac(secret, body)}`;
}

export async function verify(token, secret) {
  const parts = (token || "").split(".");
  if (parts.length !== 3) return null;
  const expected = await hmac(secret, `${parts[0]}.${parts[1]}`);
  if (expected.length !== parts[2].length) return null;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ parts[2].charCodeAt(i);
  if (diff) return null;
  const payload = fromB64url(parts[1]);
  return payload.exp && payload.exp > Date.now() / 1000 ? payload : null;
}

const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", ...headers } });

const page = (title, text) => new Response(`<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>${title}</title><link rel="stylesheet" href="/style.css"><main class="narrow"><h1>${title}</h1><p>${text}</p></main></html>`,
  { headers: { "content-type": "text/html; charset=utf-8" } });

// Signing secret: JWT_SECRET if set, otherwise generated once and kept in D1 (no manual setup needed)
let cachedSecret = null;
async function secret(env) {
  if (env.JWT_SECRET) return env.JWT_SECRET;
  if (cachedSecret) return cachedSecret;
  await env.DB.prepare("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)").run();
  let row = await env.DB.prepare("SELECT value FROM config WHERE key = 'jwt_secret'").first();
  if (!row) {
    const bytes = crypto.getRandomValues(new Uint8Array(48));
    await env.DB.prepare("INSERT OR IGNORE INTO config (key, value) VALUES ('jwt_secret', ?)").bind(b64url(bytes)).run();
    row = await env.DB.prepare("SELECT value FROM config WHERE key = 'jwt_secret'").first();
  }
  cachedSecret = row.value;
  return cachedSecret;
}

async function authUser(req, env) {
  const h = req.headers.get("authorization") || "";
  const p = await verify(h.replace(/^Bearer\s+/i, ""), await secret(env));
  if (!p) return null;
  return env.DB.prepare("SELECT id, provider, name, riot_id, created_at FROM users WHERE id = ?").bind(p.uid).first();
}

// ------------------------------------------------------------------ auth
async function startLogin(provider, url, env) {
  const P = PROVIDERS[provider];
  const redirect = url.searchParams.get("redirect") || "";
  if (!allowedRedirect(redirect, url.origin)) return json({ error: "invalid redirect" }, 400);
  const state = await sign({ r: redirect, n: crypto.randomUUID(), exp: Math.floor(Date.now() / 1000) + 600 }, await secret(env));
  const q = new URLSearchParams({
    client_id: P.id(env), redirect_uri: `${url.origin}/auth/${provider}/callback`, response_type: "code",
    scope: P.scope, state, prompt: provider === "google" ? "select_account" : "consent",
  });
  return Response.redirect(`${P.authorize}?${q}`, 302);
}

async function finishLogin(provider, url, env) {
  const P = PROVIDERS[provider];
  const state = await verify(url.searchParams.get("state"), await secret(env));
  const code = url.searchParams.get("code");
  if (!state || !code) return page("Anmeldung fehlgeschlagen", "Der Anmeldelink ist abgelaufen. Bitte starte die Anmeldung in der App erneut.");
  const tok = await fetch(P.token, {
    method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: P.id(env), client_secret: P.secret(env), grant_type: "authorization_code",
      code, redirect_uri: `${url.origin}/auth/${provider}/callback` }),
  }).then((r) => r.json());
  if (!tok.access_token) return page("Anmeldung fehlgeschlagen",
    `Der Anbieter hat die Anmeldung abgelehnt (${String(tok.error || "unbekannt").replace(/[^\w.-]/g, "")}).`);
  const prof = await fetch(P.profile, { headers: { authorization: `Bearer ${tok.access_token}` } }).then((r) => r.json());
  const u = P.user(prof);
  const now = Math.floor(Date.now() / 1000);
  await env.DB.prepare(`INSERT INTO users (provider, provider_id, name, created_at, last_login) VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(provider, provider_id) DO UPDATE SET name = excluded.name, last_login = excluded.last_login`)
    .bind(provider, u.id, u.name, now, now).run();
  const row = await env.DB.prepare("SELECT id FROM users WHERE provider = ? AND provider_id = ?").bind(provider, u.id).first();
  const session = await sign({ uid: row.id, exp: now + SESSION_DAYS * 86400 }, await secret(env));
  return Response.redirect(`${state.r}?token=${encodeURIComponent(session)}`, 302);
}

// ------------------------------------------------------------------ riot proxy
async function riotProxy(req, url, env, ctx, user) {
  const [, , host, ...rest] = url.pathname.split("/");
  const path = "/" + rest.join("/");
  if (!RIOT_HOSTS.has(host)) return json({ status: { message: "host not allowed", status_code: 400 } }, 400);
  const route = RIOT_ROUTES.find(([re]) => re.test(path));
  if (!route) return json({ status: { message: "endpoint not allowed", status_code: 403 } }, 403);
  const target = `https://${host}.api.riotgames.com${path}${url.search}`;
  const cache = caches.default;
  const cacheKey = new Request(target, { method: "GET" });
  const hit = await cache.match(cacheKey);
  if (hit) return hit;
  const r = await fetch(target, { headers: { "X-Riot-Token": env.RIOT_API_KEY, "User-Agent": "JungleCoach/1.0", Accept: "application/json" } });
  const body = await r.text();
  const res = new Response(body, { status: r.status, headers: {
    "content-type": "application/json; charset=utf-8", "cache-control": `public, max-age=${route[1]}`,
    ...(r.headers.get("Retry-After") ? { "Retry-After": r.headers.get("Retry-After") } : {}) } });
  if (r.ok) ctx.waitUntil(cache.put(cacheKey, res.clone()));
  return res;
}

// ------------------------------------------------------------------ sync (friends, coach summary, profile, status)
async function ensureData(env) {
  await env.DB.prepare(`CREATE TABLE IF NOT EXISTS user_data (user_id INTEGER PRIMARY KEY, friends TEXT, coach TEXT,
    updated_at INTEGER, friends_at INTEGER, profile TEXT, status TEXT, status_at INTEGER)`).run();
  for (const col of ["friends_at INTEGER", "profile TEXT", "status TEXT", "status_at INTEGER"]) {
    try { await env.DB.prepare(`ALTER TABLE user_data ADD COLUMN ${col}`).run(); } catch { /* exists */ }
  }
}
const RIOT_ID = /^[^#]{1,24}#[^#]{1,8}$/;
const CHAMP_ID = /^[A-Za-z]{1,20}$/;
const clean = (v, n) => String(v ?? "").replace(/[\u0000-\u001f<>]/g, "").trim().slice(0, n);
function cleanProfile(p) {
  p = p && typeof p === "object" ? p : {};
  const av = p.avatar && typeof p.avatar === "object" ? p.avatar : {};
  return {
    name: clean(p.name, 24),
    bio: clean(p.bio, 140),
    avatar: av.type === "champ" && CHAMP_ID.test(av.id || "") ? { type: "champ", id: av.id }
      : av.type === "upload" && /^[a-f0-9]{24}$/.test(av.id || "") ? { type: "upload", id: av.id }
      : av.type === "moosi" && /^(teal|green|orange|pink|violet|gold)$/.test(av.id || "") ? { type: "moosi", id: av.id } : null,
    banner: CHAMP_ID.test(p.banner || "") ? p.banner : null,
    favs: (Array.isArray(p.favs) ? p.favs : []).filter((c) => CHAMP_ID.test(String(c))).slice(0, 3),
    role: ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"].includes(p.role) ? p.role : null,
    visible: p.visible !== false,
    show_status: p.show_status !== false,
  };
}
const COL_AT = { friends: "friends_at", coach: "updated_at", profile: "updated_at" };
async function sync(req, env, user, kind) {
  await ensureData(env);
  if (req.method === "GET") {
    const row = await env.DB.prepare(`SELECT ${kind} AS v, ${COL_AT[kind] || "updated_at"} AS at FROM user_data WHERE user_id = ?`).bind(user.id).first();
    const v = row && row.v ? JSON.parse(row.v) : (kind === "friends" ? [] : kind === "profile" ? cleanProfile({}) : null);
    return json({ [kind]: v, updated_at: row ? row.at : null });
  }
  if (req.method !== "PUT") return json({ error: "method not allowed" }, 405);
  const raw = await req.text();
  if (raw.length > MAX_SYNC) return json({ error: "zu groß" }, 413);
  let body;
  try { body = JSON.parse(raw || "{}"); } catch { return json({ error: "ungültiges JSON" }, 400); }
  const now = Math.floor(Date.now() / 1000);
  if (kind === "status") {
    const st = ["online", "champselect", "ingame", "offline"].includes(body.state) ? body.state : "offline";
    const v = { state: st, champ: CHAMP_ID.test(body.champ || "") ? body.champ : null, since: Number(body.since) || now };
    await env.DB.prepare(`INSERT INTO user_data (user_id, status, status_at) VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET status = excluded.status, status_at = excluded.status_at`)
      .bind(user.id, JSON.stringify(v), now).run();
    return json({ ok: true });
  }
  let v;
  if (kind === "friends") {
    v = [...new Set((Array.isArray(body.friends) ? body.friends : []).map((f) => String(f).trim()).filter((f) => RIOT_ID.test(f)))].slice(0, 50);
  } else if (kind === "profile") {
    v = cleanProfile(body.profile);
  } else {
    v = body.plan && typeof body.plan === "object" ? body.plan : null;
  }
  const col = COL_AT[kind] || "updated_at";
  await env.DB.prepare(`INSERT INTO user_data (user_id, ${kind}, ${col}) VALUES (?, ?, ?)
      ON CONFLICT(user_id) DO UPDATE SET ${kind} = excluded.${kind}, ${col} = excluded.${col}`)
    .bind(user.id, JSON.stringify(v), now).run();
  return json({ ok: true, [kind]: kind === "coach" ? undefined : v, updated_at: now });
}

// Profile pictures: stored in D1 (resized on the device to 256 px), served under a random id.
const MAX_AVATAR = 80000;
async function ensureAvatars(env) {
  await env.DB.prepare(`CREATE TABLE IF NOT EXISTS avatars (id TEXT PRIMARY KEY, user_id INTEGER UNIQUE, mime TEXT, data TEXT, created_at INTEGER)`).run();
}
function sniff(bytes) {
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "image/jpeg";
  if (bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) return "image/png";
  if (bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46 && bytes[8] === 0x57 && bytes[9] === 0x45) return "image/webp";
  return null;
}
async function avatarPut(req, env, user) {
  await ensureAvatars(env);
  if (req.method === "DELETE") {
    await env.DB.prepare("DELETE FROM avatars WHERE user_id = ?").bind(user.id).run();
    return json({ ok: true });
  }
  if (req.method !== "PUT") return json({ error: "method not allowed" }, 405);
  const raw = await req.text();
  if (raw.length > MAX_AVATAR * 1.4 + 200) return json({ error: "Bild zu groß" }, 413);
  let b64;
  try { b64 = (JSON.parse(raw).data || "").split(",").pop(); } catch { return json({ error: "ungültig" }, 400); }
  let bytes;
  try { bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)); } catch { return json({ error: "ungültig" }, 400); }
  const mime = sniff(bytes);
  if (!mime || bytes.length > MAX_AVATAR) return json({ error: "Nur JPEG/PNG/WebP bis 80 KB" }, 400);
  const id = [...crypto.getRandomValues(new Uint8Array(12))].map((x) => x.toString(16).padStart(2, "0")).join("");
  await env.DB.prepare("DELETE FROM avatars WHERE user_id = ?").bind(user.id).run();
  await env.DB.prepare("INSERT INTO avatars (id, user_id, mime, data, created_at) VALUES (?, ?, ?, ?, ?)")
    .bind(id, user.id, mime, b64, Math.floor(Date.now() / 1000)).run();
  return json({ ok: true, avatar: { type: "upload", id }, url: `/a/${id}` });
}
async function avatarGet(id, env) {
  if (!/^[a-f0-9]{24}$/.test(id)) return new Response("not found", { status: 404 });
  await ensureAvatars(env);
  const row = await env.DB.prepare("SELECT mime, data FROM avatars WHERE id = ?").bind(id).first();
  if (!row) return new Response("not found", { status: 404 });
  const bytes = Uint8Array.from(atob(row.data), (c) => c.charCodeAt(0));
  return new Response(bytes, { headers: { "content-type": row.mime, "cache-control": "public, max-age=86400",
    "x-content-type-options": "nosniff", "access-control-allow-origin": "*" } });
}

// Public profiles of other Jungle Coach users (by Riot ID) – only what they chose to share.
async function publicProfiles(url, env) {
  await ensureData(env);
  const ids = (url.searchParams.get("ids") || "").split(",").map((x) => x.trim().toLowerCase()).filter((x) => RIOT_ID.test(x)).slice(0, 50);
  if (!ids.length) return json({ profiles: {} });
  const rows = await env.DB.prepare(`SELECT u.riot_id AS rid, d.profile AS p, d.status AS s, d.status_at AS sa
      FROM users u JOIN user_data d ON d.user_id = u.id WHERE lower(u.riot_id) IN (${ids.map(() => "?").join(",")})`).bind(...ids).all();
  const now = Math.floor(Date.now() / 1000), out = {};
  for (const r of rows.results || []) {
    const p = cleanProfile(r.p ? JSON.parse(r.p) : {});
    if (!p.visible) continue;
    const st = p.show_status && r.s && now - (r.sa || 0) <= 180 ? JSON.parse(r.s) : null;
    out[r.rid.toLowerCase()] = { name: p.name, bio: p.bio, avatar: p.avatar, banner: p.banner, favs: p.favs, role: p.role, status: st };
  }
  return json({ profiles: out });
}

// ------------------------------------------------------------------ router
export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      let m;
      if (p === "/api/health") return json({ ok: true,
        riot: !!env.RIOT_API_KEY, discord: !!(env.DISCORD_CLIENT_ID && env.DISCORD_CLIENT_SECRET),
        google: !!(env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET), db: !!env.DB });
      if ((m = p.match(/^\/auth\/(discord|google)$/))) return await startLogin(m[1], url, env);
      if ((m = p.match(/^\/auth\/(discord|google)\/callback$/))) return await finishLogin(m[1], url, env);
      if (p === "/m") return Response.redirect(`${url.origin}/m/`, 301);
      if (p.startsWith("/a/") && req.method === "GET") return await avatarGet(p.slice(3), env);
      if (p === "/api/me" || p.startsWith("/riot/") || p.startsWith("/api/sync/") || p.startsWith("/api/public/")) {
        const user = await authUser(req, env);
        if (!user) return json({ error: "Nicht angemeldet oder Sitzung abgelaufen." }, 401);
        if (p.startsWith("/riot/")) return await riotProxy(req, url, env, ctx, user);
        if (p === "/api/sync/friends") return await sync(req, env, user, "friends");
        if (p === "/api/sync/coach") return await sync(req, env, user, "coach");
        if (p === "/api/sync/profile") return await sync(req, env, user, "profile");
        if (p === "/api/sync/status") return await sync(req, env, user, "status");
        if (p === "/api/sync/avatar") return await avatarPut(req, env, user);
        if (p === "/api/public/profiles" && req.method === "GET") return await publicProfiles(url, env);
        if (p.startsWith("/api/sync/")) return json({ error: "not found" }, 404);
        if (req.method === "GET") return json(user);
        if (req.method === "PUT") {
          const body = await req.json().catch(() => ({}));
          const rid = String(body.riot_id || "").slice(0, 40);
          await env.DB.prepare("UPDATE users SET riot_id = ? WHERE id = ?").bind(rid || null, user.id).run();
          return json({ ok: true });
        }
        if (req.method === "DELETE") {
          await env.DB.prepare("DELETE FROM users WHERE id = ?").bind(user.id).run();
          await ensureData(env);
          await env.DB.prepare("DELETE FROM user_data WHERE user_id = ?").bind(user.id).run();
          await ensureAvatars(env);
          await env.DB.prepare("DELETE FROM avatars WHERE user_id = ?").bind(user.id).run();
          return json({ ok: true, deleted: true });
        }
        return json({ error: "method not allowed" }, 405);
      }
      if (p === "/api/health") return json({ ok: true });
      return env.ASSETS ? env.ASSETS.fetch(req) : new Response("Not found", { status: 404 });
    } catch (e) {
      return json({ error: "Serverfehler" }, 500);
    }
  },
};
