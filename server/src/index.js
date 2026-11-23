/**
 * Jungle Coach backend (Cloudflare Worker)
 *
 *  /auth/{discord|google}            start login (from the desktop app, via the system browser)
 *  /auth/{discord|google}/callback   OAuth callback -> account -> session token -> back to the app
 *  /api/me            GET account | PUT {riot_id} | DELETE account (GDPR Art. 17)
 *  /riot/{host}/...   authenticated proxy to the Riot API (key stays on the server)
 *  everything else    static website (public/)
 *
 * Data minimisation: we store only provider, provider user id, display name, optional Riot ID
 * and timestamps. No e-mail, no password.
 */

const APP_REDIRECTS = [/^http:\/\/(localhost|127\.0\.0\.1):87\d\d\/auth\/callback$/];
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
  if (!APP_REDIRECTS.some((r) => r.test(redirect))) return json({ error: "invalid redirect" }, 400);
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
  if (!tok.access_token) return page("Anmeldung fehlgeschlagen", "Der Anbieter hat die Anmeldung abgelehnt.");
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

// ------------------------------------------------------------------ router
export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      let m;
      if ((m = p.match(/^\/auth\/(discord|google)$/))) return await startLogin(m[1], url, env);
      if ((m = p.match(/^\/auth\/(discord|google)\/callback$/))) return await finishLogin(m[1], url, env);
      if (p === "/api/me" || p.startsWith("/riot/")) {
        const user = await authUser(req, env);
        if (!user) return json({ error: "Nicht angemeldet oder Sitzung abgelaufen." }, 401);
        if (p.startsWith("/riot/")) return await riotProxy(req, url, env, ctx, user);
        if (req.method === "GET") return json(user);
        if (req.method === "PUT") {
          const body = await req.json().catch(() => ({}));
          const rid = String(body.riot_id || "").slice(0, 40);
          await env.DB.prepare("UPDATE users SET riot_id = ? WHERE id = ?").bind(rid || null, user.id).run();
          return json({ ok: true });
        }
        if (req.method === "DELETE") {
          await env.DB.prepare("DELETE FROM users WHERE id = ?").bind(user.id).run();
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
