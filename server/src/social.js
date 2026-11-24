// Moosi accounts (several LoL accounts per Moosi account) and the internal friend system
// (request -> accept). Accepted friends are also added to each other's Riot friend list,
// so stats, live status and push work for them without any extra client logic.
const RIOT_ID = /^[^#]{1,24}#[^#]{1,8}$/;
const PLATS = ["euw1", "eun1", "tr1", "ru", "me1", "na1", "br1", "la1", "la2", "kr", "jp1", "oc1", "sg2", "tw2", "vn2"];
const MAX_ACCOUNTS = 5, MAX_FRIENDS = 50;
const now = () => Math.floor(Date.now() / 1000);
const json = (d, s = 200) => new Response(JSON.stringify(d), { status: s, headers: { "content-type": "application/json", "cache-control": "no-store" } });
const parse = (v, d) => { try { return v ? JSON.parse(v) : d; } catch { return d; } };

export async function ensureSocial(env) {
  await env.DB.prepare(`CREATE TABLE IF NOT EXISTS friendships (a INTEGER NOT NULL, b INTEGER NOT NULL, state TEXT NOT NULL,
    at INTEGER NOT NULL, PRIMARY KEY (a, b))`).run();
  await env.DB.prepare("CREATE INDEX IF NOT EXISTS friendships_b ON friendships (b)").run();
  try { await env.DB.prepare("ALTER TABLE user_data ADD COLUMN accounts TEXT").run(); } catch { /* exists */ }
}

export function cleanAccounts(list, main) {
  const seen = new Set(), out = [];
  for (const a of Array.isArray(list) ? list : []) {
    const rid = String((a && a.rid) || "").replace(/[\u0000-\u001f<>]/g, "").trim();
    if (!RIOT_ID.test(rid) || seen.has(rid.toLowerCase())) continue;
    seen.add(rid.toLowerCase());
    out.push({ rid, plat: PLATS.includes(a.plat) ? a.plat : null });
    if (out.length >= MAX_ACCOUNTS) break;
  }
  const m = out.find((a) => a.rid.toLowerCase() === String(main || "").toLowerCase());
  return { accounts: out, main: m ? m.rid : out[0] ? out[0].rid : null };
}

async function accountsOf(env, uid) {
  const r = await env.DB.prepare("SELECT u.riot_id AS rid, d.accounts AS a FROM users u LEFT JOIN user_data d ON d.user_id = u.id WHERE u.id = ?").bind(uid).first();
  if (!r) return { accounts: [], main: null };
  let acc = parse(r.a, []);
  if (r.rid && !acc.some((x) => x.rid.toLowerCase() === r.rid.toLowerCase())) acc = [{ rid: r.rid, plat: null }, ...acc];
  return cleanAccounts(acc, r.rid);
}

export async function accountsApi(req, env, user) {
  await ensureSocial(env);
  if (req.method === "GET") return json(await accountsOf(env, user.id));
  if (req.method !== "PUT") return json({ error: "method not allowed" }, 405);
  const body = await req.json().catch(() => ({}));
  const v = cleanAccounts(body.accounts, body.main);
  await env.DB.prepare(`INSERT INTO user_data (user_id, accounts) VALUES (?, ?)
      ON CONFLICT(user_id) DO UPDATE SET accounts = excluded.accounts`).bind(user.id, JSON.stringify(v.accounts)).run();
  await env.DB.prepare("UPDATE users SET riot_id = ? WHERE id = ?").bind(v.main, user.id).run();
  return json(v);
}

// what other Moosi users see of someone in search / friend list
async function cards(env, ids) {
  if (!ids.length) return {};
  const rows = (await env.DB.prepare(`SELECT u.id AS id, u.name AS n, u.riot_id AS rid, d.profile AS p FROM users u
      LEFT JOIN user_data d ON d.user_id = u.id WHERE u.id IN (${ids.map(() => "?").join(",")})`).bind(...ids).all()).results || [];
  const out = {};
  for (const r of rows) {
    const p = parse(r.p, {});
    out[r.id] = { uid: r.id, name: String(p.name || (r.rid || "").split("#")[0] || r.n || "Moosi-Spieler").slice(0, 24),
      riot_id: r.rid || null, avatar: p.avatar || null, role: p.role || null, visible: p.visible !== false };
  }
  return out;
}

async function rel(env, me, other) {
  const r = await env.DB.prepare("SELECT a, b, state FROM friendships WHERE (a = ? AND b = ?) OR (a = ? AND b = ?)").bind(me, other, other, me).first();
  if (!r) return null;
  return r.state === "ok" ? "friend" : r.a === me ? "out" : "in";
}

async function editFriendList(env, uid, add, remove) {
  const r = await env.DB.prepare("SELECT friends FROM user_data WHERE user_id = ?").bind(uid).first();
  let list = parse(r && r.friends, []);
  const rm = new Set((remove || []).map((x) => x.toLowerCase()));
  list = list.filter((x) => !rm.has(x.toLowerCase()));
  for (const x of add || []) if (x && !list.some((y) => y.toLowerCase() === x.toLowerCase())) list.push(x);
  await env.DB.prepare(`INSERT INTO user_data (user_id, friends, friends_at) VALUES (?, ?, ?)
      ON CONFLICT(user_id) DO UPDATE SET friends = excluded.friends, friends_at = excluded.friends_at`)
    .bind(uid, JSON.stringify(list.slice(0, MAX_FRIENDS)), now()).run();
}

async function befriend(env, a, b) {   // both sides get each other's main Riot ID in their friend list
  await env.DB.prepare("UPDATE friendships SET state = 'ok', at = ? WHERE (a = ? AND b = ?) OR (a = ? AND b = ?)").bind(now(), a, b, b, a).run();
  const [A, B] = [await accountsOf(env, a), await accountsOf(env, b)];
  if (B.main) await editFriendList(env, a, [B.main], []);
  if (A.main) await editFriendList(env, b, [A.main], []);
}

export async function socialApi(req, env, user, p, url) {
  await ensureSocial(env);
  const me = user.id;
  if (p === "/api/social/search" && req.method === "GET") {
    const q = String(url.searchParams.get("q") || "").trim().toLowerCase().slice(0, 30);
    if (q.length < 3) return json({ results: [] });
    const like = "%" + q.replace(/[%_]/g, "") + "%";
    const rows = (await env.DB.prepare(`SELECT u.id AS id FROM users u LEFT JOIN user_data d ON d.user_id = u.id
        WHERE u.id != ? AND COALESCE(json_extract(d.profile, '$.visible'), 1) != 0 AND (
          lower(COALESCE(json_extract(d.profile, '$.name'), '')) LIKE ? OR lower(COALESCE(u.riot_id, '')) LIKE ?
          OR EXISTS (SELECT 1 FROM json_each(COALESCE(d.accounts, '[]')) j WHERE lower(json_extract(j.value, '$.rid')) LIKE ?))
        LIMIT 10`).bind(me, like, like, like).all()).results || [];
    const c = await cards(env, rows.map((r) => r.id));
    const results = [];
    for (const r of rows) if (c[r.id]) results.push({ ...c[r.id], rel: await rel(env, me, r.id) });
    return json({ results });
  }
  if (p === "/api/social/list" && req.method === "GET") {
    const rows = (await env.DB.prepare("SELECT a, b, state, at FROM friendships WHERE a = ? OR b = ? ORDER BY at DESC").bind(me, me).all()).results || [];
    const c = await cards(env, rows.map((r) => (r.a === me ? r.b : r.a)));
    const out = { friends: [], incoming: [], outgoing: [] };
    for (const r of rows) {
      const o = c[r.a === me ? r.b : r.a]; if (!o) continue;
      (r.state === "ok" ? out.friends : r.a === me ? out.outgoing : out.incoming).push({ ...o, at: r.at });
    }
    return json(out);
  }
  if (req.method !== "POST") return json({ error: "not found" }, 404);
  const body = await req.json().catch(() => ({}));
  const other = Number(body.uid);
  if (!Number.isInteger(other) || other === me) return json({ error: "ungültig" }, 400);
  if (!(await env.DB.prepare("SELECT id FROM users WHERE id = ?").bind(other).first())) return json({ error: "Spieler nicht gefunden" }, 404);
  const r = await rel(env, me, other);
  if (p === "/api/social/request") {
    if (r === "friend" || r === "out") return json({ ok: true, rel: r });
    if (r === "in") { await befriend(env, me, other); return json({ ok: true, rel: "friend" }); }   // they asked first -> accept
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM friendships WHERE (a = ? OR b = ?)").bind(me, me).first();
    if ((n && n.n) >= MAX_FRIENDS * 2) return json({ error: "Zu viele Freunde und Anfragen" }, 429);
    await env.DB.prepare("INSERT OR IGNORE INTO friendships (a, b, state, at) VALUES (?, ?, 'pending', ?)").bind(me, other, now()).run();
    return json({ ok: true, rel: "out" });
  }
  if (p === "/api/social/respond") {
    if (r !== "in") return json({ error: "keine offene Anfrage" }, 400);
    if (body.accept) { await befriend(env, me, other); return json({ ok: true, rel: "friend" }); }
    await env.DB.prepare("DELETE FROM friendships WHERE a = ? AND b = ?").bind(other, me).run();
    return json({ ok: true, rel: null });
  }
  if (p === "/api/social/remove") {   // unfriend, or withdraw an own request
    await env.DB.prepare("DELETE FROM friendships WHERE (a = ? AND b = ?) OR (a = ? AND b = ?)").bind(me, other, other, me).run();
    if (r === "friend") {
      const [A, B] = [await accountsOf(env, me), await accountsOf(env, other)];
      await editFriendList(env, me, [], B.accounts.map((x) => x.rid));
      await editFriendList(env, other, [], A.accounts.map((x) => x.rid));
    }
    return json({ ok: true, rel: null });
  }
  return json({ error: "not found" }, 404);
}

// delete everything social when an account is deleted
export async function socialDelete(env, uid) {
  await ensureSocial(env);
  await env.DB.prepare("DELETE FROM friendships WHERE a = ? OR b = ?").bind(uid, uid).run();
}

// old clients (and the PC app) set a single riot_id – keep every account ever set in the list
export async function rememberAccount(env, uid, rid) {
  await ensureSocial(env);
  const cur = await accountsOf(env, uid);
  const v = cleanAccounts([...cur.accounts, { rid, plat: null }], rid);
  await env.DB.prepare(`INSERT INTO user_data (user_id, accounts) VALUES (?, ?)
      ON CONFLICT(user_id) DO UPDATE SET accounts = excluded.accounts`).bind(uid, JSON.stringify(v.accounts)).run();
}
