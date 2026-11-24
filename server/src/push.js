// Web Push for the Moosi web app (Android, desktop browsers, iPhone home-screen app from iOS 16.4).
// Pushes carry no payload (no encryption needed): the service worker fetches the text from /api/push/inbox.
// VAPID keys are generated once and kept in D1 (config table), like the JWT secret.

const enc = new TextEncoder();
const b64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8" } });

export async function ensurePush(env) {
  await env.DB.prepare(`CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
    prefs TEXT, msg TEXT, created_at INTEGER)`).run();
  await env.DB.prepare(`CREATE TABLE IF NOT EXISTS push_sent (user_id INTEGER, k TEXT, at INTEGER, PRIMARY KEY (user_id, k))`).run();
  await env.DB.prepare(`CREATE TABLE IF NOT EXISTS puuids (rid TEXT PRIMARY KEY, puuid TEXT, at INTEGER)`).run();
}

let vapidCache = null;
async function vapid(env) {
  if (vapidCache) return vapidCache;
  await env.DB.prepare("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)").run();
  let row = await env.DB.prepare("SELECT value FROM config WHERE key = 'vapid'").first();
  if (!row) {
    const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
    const priv = await crypto.subtle.exportKey("jwk", kp.privateKey);
    const pub = b64u(await crypto.subtle.exportKey("raw", kp.publicKey));
    await env.DB.prepare("INSERT OR IGNORE INTO config (key, value) VALUES ('vapid', ?)").bind(JSON.stringify({ priv, pub })).run();
    row = await env.DB.prepare("SELECT value FROM config WHERE key = 'vapid'").first();
  }
  const v = JSON.parse(row.value);
  v.key = await crypto.subtle.importKey("jwk", v.priv, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  vapidCache = v;
  return v;
}

async function vapidHeader(env, endpoint) {
  const v = await vapid(env);
  const head = b64u(enc.encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const body = b64u(enc.encode(JSON.stringify({ aud: new URL(endpoint).origin, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: "https://moosi.app" })));
  const sig = b64u(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, v.key, enc.encode(`${head}.${body}`)));
  return `vapid t=${head}.${body}.${sig}, k=${v.pub}`;
}

// store the message for the service worker, then wake it up with an empty push
export async function sendPush(env, userId, msg, pref) {
  await ensurePush(env);
  const subs = (await env.DB.prepare("SELECT endpoint, prefs FROM push_subs WHERE user_id = ?").bind(userId).all()).results || [];
  let sent = 0;
  for (const s of subs) {
    const prefs = s.prefs ? JSON.parse(s.prefs) : {};
    if (pref && prefs[pref] === false) continue;
    await env.DB.prepare("UPDATE push_subs SET msg = ? WHERE endpoint = ?").bind(JSON.stringify({ ...msg, at: Date.now() }), s.endpoint).run();
    try {
      const r = await fetch(s.endpoint, { method: "POST", headers: { Authorization: await vapidHeader(env, s.endpoint), TTL: "3600", Urgency: "normal", "Content-Length": "0" } });
      if (r.status === 404 || r.status === 410) await env.DB.prepare("DELETE FROM push_subs WHERE endpoint = ?").bind(s.endpoint).run();
      else if (r.ok) sent++;
    } catch { /* offline browser – try next time */ }
  }
  return sent;
}

const ALLOWED_PUSH = /^https:\/\/([a-z0-9-]+\.)*(googleapis\.com|mozilla\.com|mozaws\.net|windows\.com|notify\.windows\.com|push\.apple\.com)\//;

export async function pushApi(req, env, user, p, url) {
  await ensurePush(env);
  if (p === "/api/push/key") return json({ key: (await vapid(env)).pub });
  if (p === "/api/push/subscribe" && req.method === "POST") {
    const b = await req.json().catch(() => ({}));
    const ep = String((b.subscription || {}).endpoint || "");
    if (!ALLOWED_PUSH.test(ep) || ep.length > 800) return json({ error: "ungültige Push-Adresse" }, 400);
    const prefs = { friends: b.prefs?.friends !== false, task: b.prefs?.task !== false };
    await env.DB.prepare(`INSERT INTO push_subs (endpoint, user_id, prefs, created_at) VALUES (?, ?, ?, ?)
      ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, prefs = excluded.prefs`)
      .bind(ep, user.id, JSON.stringify(prefs), Math.floor(Date.now() / 1000)).run();
    return json({ ok: true, prefs });
  }
  if (p === "/api/push/subscribe" && req.method === "DELETE") {
    const b = await req.json().catch(() => ({}));
    await env.DB.prepare("DELETE FROM push_subs WHERE endpoint = ? AND user_id = ?").bind(String(b.endpoint || ""), user.id).run();
    return json({ ok: true });
  }
  if (p === "/api/push/test" && req.method === "POST") {
    const n = await sendPush(env, user.id, { title: "Moosi", body: "Benachrichtigungen funktionieren 👍", url: "/m/" });
    return json({ ok: n > 0, sent: n });
  }
  return json({ error: "not found" }, 404);
}

// the service worker asks what to show (no login inside the service worker – the endpoint is the secret)
export async function pushInbox(url, env) {
  await ensurePush(env);
  const row = await env.DB.prepare("SELECT msg FROM push_subs WHERE endpoint = ?").bind(url.searchParams.get("ep") || "").first();
  return json(row && row.msg ? JSON.parse(row.msg) : { title: "Moosi", body: "Neues in Moosi", url: "/m/" });
}

// ------------------------------------------------------------------ scheduled: every 5 minutes
async function once(env, userId, k) {   // true only the first time for this user + key
  const r = await env.DB.prepare("INSERT OR IGNORE INTO push_sent (user_id, k, at) VALUES (?, ?, ?)").bind(userId, k, Math.floor(Date.now() / 1000)).run();
  return !(r && r.meta && r.meta.changes !== undefined) || Number(r.meta.changes) > 0;
}

export async function scheduled(env, riotKey) {
  await ensurePush(env);
  const users = (await env.DB.prepare(`SELECT DISTINCT s.user_id AS id, d.friends AS f, d.coach AS c FROM push_subs s
    LEFT JOIN user_data d ON d.user_id = s.user_id`).all()).results || [];
  if (!users.length) return;
  const now = new Date();
  const berlin = new Intl.DateTimeFormat("de-DE", { timeZone: "Europe/Berlin", hour: "numeric", minute: "numeric", hour12: false }).formatToParts(now);
  const hh = +berlin.find((x) => x.type === "hour").value, mm = +berlin.find((x) => x.type === "minute").value;
  const day = now.toISOString().slice(0, 10);

  // 1) daily task at 17:00 (Berlin)
  if (hh === 17 && mm < 5) {
    for (const u of users) {
      const task = u.c ? (JSON.parse(u.c) || {}).session_task : null;
      if (task && await once(env, u.id, "task:" + day))
        await sendPush(env, u.id, { title: "Moosis Aufgabe für heute", body: `${task.title}: ${task.drill}`.slice(0, 180), url: "/m/" }, "task");
    }
  }

  // 2) friend is playing right now: Moosi users (status from their PC app) + Riot spectator for the rest
  const allFriends = [...new Set(users.flatMap((u) => (u.f ? JSON.parse(u.f) : []).map((x) => x.toLowerCase())))].slice(0, 60);
  if (!allFriends.length) return;
  const live = {};
  const rows = (await env.DB.prepare(`SELECT lower(u.riot_id) AS rid, d.status AS s, d.status_at AS sa, d.profile AS p FROM users u JOIN user_data d ON d.user_id = u.id
      WHERE lower(u.riot_id) IN (${allFriends.map(() => "?").join(",")})`).bind(...allFriends).all()).results || [];
  for (const r of rows) {
    const prof = r.p ? JSON.parse(r.p) : {};
    if (prof.show_status === false || !r.s || Date.now() / 1000 - (r.sa || 0) > 300) continue;
    const st = JSON.parse(r.s);
    if (st.state === "ingame") live[r.rid] = { champ: st.champ, key: "m" + st.since };
  }
  let budget = 30;   // Riot requests per run (dev keys allow 100 per 2 minutes)
  for (const rid of allFriends) {
    if (live[rid] || budget <= 0 || !riotKey) continue;
    let row = await env.DB.prepare("SELECT puuid FROM puuids WHERE rid = ?").bind(rid).first();
    if (!row) {
      const [n, t] = rid.split("#"); budget--;
      const a = await fetch(`https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/${encodeURIComponent(n)}/${encodeURIComponent(t)}`, { headers: { "X-Riot-Token": riotKey } });
      if (!a.ok) continue;
      row = { puuid: (await a.json()).puuid };
      await env.DB.prepare("INSERT OR REPLACE INTO puuids (rid, puuid, at) VALUES (?, ?, ?)").bind(rid, row.puuid, Math.floor(Date.now() / 1000)).run();
    }
    budget--;
    const g = await fetch(`https://euw1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/${row.puuid}`, { headers: { "X-Riot-Token": riotKey } });
    if (g.ok) {
      const game = await g.json();
      const me = (game.participants || []).find((x) => x.puuid === row.puuid);
      live[rid] = { champId: me && me.championId, key: "g" + game.gameId, queue: game.gameQueueConfigId };
    }
  }
  for (const u of users) {
    for (const f of (u.f ? JSON.parse(u.f) : [])) {
      const L = live[f.toLowerCase()];
      if (!L || !(await once(env, u.id, "play:" + f.toLowerCase() + ":" + L.key))) continue;
      await sendPush(env, u.id, { title: `${f.split("#")[0]} spielt gerade`, body: L.champ ? `mit ${L.champ} – schau rein, wenn ihr duo spielen wollt.` : "Gerade ins Spiel gestartet.", url: "/m/" }, "friends");
    }
  }
  await env.DB.prepare("DELETE FROM push_sent WHERE at < ?").bind(Math.floor(Date.now() / 1000) - 3 * 86400).run();
}
