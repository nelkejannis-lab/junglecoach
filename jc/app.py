"""Local dashboard server: http://localhost:8765 (second monitor / phone in same Wi-Fi)."""
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib.error
import urllib.parse
import urllib.request

from . import champselect, deep, friends, live, meta, meta_cloud, profile, ranked, recorder, review
from .ddragon import GameData
from .lcu import LCU

from .paths import DATA_DIR as ROOT, STATIC

STATE = {"phase": "Startet…", "champ_select": None, "live": None, "review": None, "error": None}
REC = {"obj": None}
META = {"stats": None, "crawler": None}
BUILD_CACHE = {}
GPM_BENCH = {}
OVERLAY = {"move": False, "pick": None}  # move mode requested from the app (settings button)
REVIEW_CACHE = os.path.join(ROOT, "review_cache.json")
EDITABLE = ["language", "role_auto", "main_role", "riot_id", "platform", "riot_api_key", "champion_pool", "auto_import_runes", "review_games", "profile_games",
            "meta_bracket", "meta_max_matches"]


def riot_cred(cfg):
    """Own developer key if set, otherwise the logged-in session via the Jungle Coach server."""
    key = (cfg.get("riot_api_key") or "").strip()
    logged_in = bool(cfg.get("auth_token") and cfg.get("server_url"))
    # a key the user typed in himself wins; otherwise everything goes through the Moosi server (admin keeps its key current)
    if key.startswith("RGAPI-") and "xxxx" not in key:
        return key
    if logged_in:
        return f"jc:{cfg['auth_token']}@{cfg['server_url']}"
    return None


NO_CRED = "Bitte zuerst in den Einstellungen anmelden (Discord oder Google)."


def save_config(cfg):
    with open(os.path.join(ROOT, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def current_bracket(cfg):
    b = cfg.get("meta_bracket", "auto")
    if b == "auto":
        h = ranked.load_history(ranked.active_account(cfg.get("riot_id")))
        return meta.bracket_of(h[-1]["tier"]) if h else None
    return None if b in ("all", "", None) else b


COACH = {}


HISTORY_DIR = os.path.join(ROOT, "cache", "history")


def _hist_path(account):
    safe = "".join(c if c.isalnum() else "_" for c in (account or "me").lower())[:60]
    return os.path.join(HISTORY_DIR, safe + ".json")


def load_history(account):
    try:
        with open(_hist_path(account), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_history(account, res):
    """Keeps a light record of every analysed game (stats only, no timelines) for the 30-day progress –
    the review itself only holds the most recent games."""
    hist = load_history(account)
    for g in (res.get("games") or []) + (res.get("other_games") or []):
        if g.get("match_id") and g.get("stats"):
            hist[g["match_id"]] = {"game_start": g.get("game_start"), "champion": g.get("champion"),
                                   "role": g.get("role") or "JUNGLE", "win": g.get("win"), "stats": g["stats"]}
    meta_ = hist.pop("__meta", None)
    if len(hist) > 1000:  # the database keeps growing up to 1000 games (~1 year for most players)
        hist = dict(sorted(hist.items(), key=lambda kv: -(kv[1].get("game_start") or 0))[:1000])
    if meta_:
        hist["__meta"] = meta_
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(_hist_path(account), "w", encoding="utf-8") as f:
        json.dump(hist, f)
    return hist


ROLE_NAMES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


def detect_role(cfg, hist):
    """Role played most in the last 20 games (>= 50 %) becomes the main role – unless switched off."""
    recent = sorted((v for k, v in hist.items() if not k.startswith("__") and v.get("role") in ROLE_NAMES),
                    key=lambda v: -(v.get("game_start") or 0))[:20]
    if len(recent) < 5:
        return None
    counts = {}
    for v in recent:
        counts[v["role"]] = counts.get(v["role"], 0) + 1
    role, n = max(counts.items(), key=lambda kv: kv[1])
    det = {"role": role, "games": len(recent), "count": n}
    cfg["role_detected"] = det
    if cfg.get("role_auto", True) and n / len(recent) >= 0.5 and cfg.get("main_role") != role:
        cfg["main_role"] = role
        COACH.pop("plan", None)
        threading.Thread(target=push_role, args=(cfg,), daemon=True).start()
    save_config(cfg)
    return det


def backfill_history(cfg, rid, per_round=20, cap=1000):
    """Grows your game database step by step with older games (stats only, 1 request per game),
    so the coach, progress and role detection get more data over time."""
    from .meta import metrics_of
    hist = load_history(rid)
    meta_ = hist.get("__meta") or {"start": 0, "done": False}
    if meta_.get("done") or len([k for k in hist if not k.startswith("__")]) >= cap:
        return 0
    api = review.RiotAPI(riot_cred(cfg), cfg.get("platform", "euw1"))
    name, tag = rid.split("#", 1)
    acct = api.get(f"/riot/account/v1/accounts/by-riot-id/{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}")
    ids = api.get(f"/lol/match/v5/matches/by-puuid/{acct['puuid']}/ids", {"type": "ranked", "start": meta_["start"], "count": per_round})
    added = 0
    for mid in ids:
        if mid in hist:
            continue
        m = api.get(f"/lol/match/v5/matches/{mid}")
        info = m.get("info", {})
        if info.get("gameDuration", 0) < 15 * 60:
            continue
        me = next((p for p in info.get("participants", []) if p.get("puuid") == acct["puuid"]), None)
        if not me:
            continue
        hist[mid] = {"game_start": info.get("gameStartTimestamp", info.get("gameCreation", 0)) / 1000,
                     "champion": me["championName"], "role": me.get("teamPosition") or "", "win": me["win"],
                     "stats": metrics_of(me, info.get("gameDuration", 0))}
        added += 1
    meta_["start"] += len(ids)
    meta_["done"] = len(ids) < per_round
    hist["__meta"] = meta_
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(_hist_path(rid), "w", encoding="utf-8") as f:
        json.dump(hist, f)
    return added


def history_info(rid):
    hist = load_history(rid)
    games = [v for k, v in hist.items() if not k.startswith("__")]
    return {"games": len(games), "oldest": min((g.get("game_start") or 0 for g in games), default=None),
            "complete": (hist.get("__meta") or {}).get("done", False)}


def run_review(cfg):
    """Loads + analyses the recent ranked games of the active account (or the configured Riot ID)."""
    rid = ranked.ACTIVE.get("account") or cfg.get("riot_id", "")
    with LOCK:
        STATE["review_status"] = {"running": True, "done": 0, "total": 0, "account": rid}

    def progress(done, total):
        with LOCK:
            STATE["review_status"] = {"running": True, "done": done, "total": total, "account": rid}
    try:
        with LOCK:
            prev = STATE.get("review") or {}
        if (prev.get("account") or "").lower() != (rid or "").lower():
            prev = None
        res = review.run(riot_cred(cfg), cfg.get("platform", "euw1"), rid, max(30, int(cfg.get("review_games", 30))),
                         progress=progress, prev=prev)
    except Exception as e:
        with LOCK:
            STATE["review_status"] = {"running": False, "error": str(e), "account": rid}
        raise
    res["account"] = rid
    res["analysed_at"] = time.time()
    try:
        detect_role(cfg, save_history(rid, res))
    except OSError:
        pass
    attach_clips(res["games"], recorder.list_sessions())
    with open(REVIEW_CACHE, "w", encoding="utf-8") as f:
        json.dump(res, f)
    with LOCK:
        STATE["review"] = res
        STATE["review_status"] = {"running": False, "account": rid, "at": res["analysed_at"]}
    COACH.clear()
    return res


def auto_review_loop(cfg):
    """Coach analyses by itself: at start (if older than 6 h or another account) and every 6 h."""
    time.sleep(25)  # let the client connect first, so the active account is known
    while True:
        try:
            with LOCK:
                rev = STATE.get("review") or {}
                running = (STATE.get("review_status") or {}).get("running")
            rid = ranked.ACTIVE.get("account") or cfg.get("riot_id", "")
            stale = time.time() - rev.get("analysed_at", 0) > 30 * 60  # cheap: only new games are loaded
            other = (rev.get("account") or "").lower() != (rid or "").lower()
            if riot_cred(cfg) and "#" in (rid or "") and not running and (stale or other or not rev.get("games")):
                run_review(cfg)
            if riot_cred(cfg) and "#" in (rid or ""):
                if backfill_history(cfg, rid):
                    detect_role(cfg, load_history(rid))
        except Exception:  # noqa: BLE001 - retried next round
            pass
        time.sleep(600)


def cloud(cfg, method, path, body=None):
    """Small JSON call to our server (only when logged in); used to sync friends + coach summary
    with the mobile app. Never raises – sync is best effort."""
    if not (cfg.get("auth_token") and cfg.get("server_url")):
        return None
    try:
        req = urllib.request.Request(cfg["server_url"].rstrip("/") + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + cfg["auth_token"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001
        return None


def cloud_bg(cfg, method, path, body=None):
    threading.Thread(target=cloud, args=(cfg, method, path, body), daemon=True).start()


def mobile_summary(plan):
    keep = ("score", "player_type", "session_task", "focus", "confidence", "games", "account", "scope", "strengths")
    out = {k: plan.get(k) for k in keep if plan.get(k) is not None}
    if plan.get("consistency"):
        out["consistency"] = {k: plan["consistency"].get(k) for k in ("score", "verdict")}
    if (plan.get("tilt") or {}).get("rule"):
        out["tilt"] = {"rule": plan["tilt"]["rule"]}
    prog = []
    for p in plan.get("progress") or []:  # light version for the phone: last 20 values
        if p.get("enough"):
            prog.append({"title": p["title"], "better": p.get("better"), "hit": p.get("hit"), "games": p.get("games"),
                         "target": p.get("target"), "target_txt": p.get("target_txt"),
                         "v": [round(x["v"], 2) for x in p["points"][-20:]], "w": [x["win"] for x in p["points"][-20:]]})
    if prog:
        out["progress"] = prog
    try:  # today's LP change (exact, from the PC's ranked tracker) for the session recap on the phone
        sm = ranked.summary(ranked.load_history(ranked.active_account(plan.get("account"))))
        if sm:
            out["today_lp"], out["lp_day"] = sm.get("today"), time.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        pass
    out["at"] = time.time()
    return out


def sync_friends(cfg):
    """Friend list PC <-> phone: the side changed last wins (so a friend removed on the phone stays removed)."""
    r = cloud(cfg, "GET", "/api/sync/friends")
    if r is None:
        return
    srv_t = r.get("updated_at") or 0
    if srv_t and srv_t > cfg.get("friends_updated", 0):
        if [f.lower() for f in r.get("friends") or []] != [f.lower() for f in cfg.get("friends", [])]:
            cfg["friends"] = list(r.get("friends") or [])[:50]
        cfg["friends_updated"] = srv_t
        save_config(cfg)
    elif cfg.get("friends_updated", 0) > srv_t or not srv_t:
        res = cloud(cfg, "PUT", "/api/sync/friends", {"friends": cfg.get("friends", [])})
        if res is not None:
            cfg["friends_updated"] = int(time.time())
            save_config(cfg)


def sync_loop(cfg):
    while True:
        time.sleep(300)
        try:
            sync_friends(cfg)
        except Exception:  # noqa: BLE001
            pass


def push_role(cfg):
    """Keeps the main role on the public profile up to date."""
    r = cloud(cfg, "GET", "/api/sync/profile")
    if r is not None and cfg.get("main_role") and (r.get("profile") or {}).get("role") != cfg["main_role"]:
        cloud(cfg, "PUT", "/api/sync/profile", {"profile": {**(r.get("profile") or {}), "role": cfg["main_role"]}})


def sync_on_start(cfg):
    """Merges the friend list with the server (phone additions) and publishes the Riot ID."""
    if not cfg.get("auth_token"):
        return
    sync_friends(cfg)
    push_role(cfg)
    me = cloud(cfg, "GET", "/api/me")
    if me is not None and not me.get("riot_id") and "#" in (cfg.get("riot_id") or ""):
        cloud(cfg, "PUT", "/api/me", {"riot_id": cfg["riot_id"]})


def coach_plan(gd, cfg, rev, cache=None):
    from . import coach
    cache = COACH if cache is None else cache
    games = (rev.get("games") or []) + (rev.get("other_games") or [])
    games.sort(key=lambda g: -(g.get("game_start") or 0))
    if not games:
        return {"ready": False, "reason": "Noch keine Games analysiert. Die Analyse läuft automatisch nach jedem "
                                          "Game – oder jetzt im Analyse-Tab starten."}
    cached = cache.get("plan")
    if cached and cached.get("_n") == len(games) and cached.get("_t") == games[0].get("game_start"):
        return cached
    ms = meta_stats(gd)
    if not ms:
        return {"ready": False, "reason": "Die Vergleichsdatenbank wird noch geladen …"}
    ms = meta.MetaStats(gd, ms.db)
    bracket = current_bracket(cfg)
    counts = {}
    for g in games:
        key = (g["champion"], g.get("role") or "JUNGLE")
        counts[key] = counts.get(key, 0) + 1
    by = {k: ms.benchmarks(k[0], k[1], bracket) for k in counts}
    by = {k: b for k, b in by.items() if b}
    main = max(counts, key=counts.get)
    plan = coach.analyze([dict(g) for g in games], by.get(main) or ms.benchmarks(main[0], main[1], None),
                         bench_by_champ=by)
    plan["roles"] = sorted({k[1] for k in counts})
    try:
        hist = [v for k, v in load_history(rev.get("account")).items() if not k.startswith("__")] or games
        plan["progress"] = coach.progress(hist, plan.get("focus"))
    except Exception:  # noqa: BLE001 - progress is a bonus, never break the plan
        plan["progress"] = []
    plan["account"] = rev.get("account")
    plan["_n"], plan["_t"] = len(games), games[0].get("game_start")
    if cache is COACH and plan.get("ready"):
        cloud_bg(cfg, "PUT", "/api/sync/coach", {"plan": mobile_summary(plan)})
    cache["plan"] = plan
    return plan


SHARDS = {5008: "Adaptive Kraft", 5005: "Angriffstempo", 5007: "Fähigkeitstempo", 5001: "Leben (skalierend)",
          5011: "Leben", 5002: "Rüstung", 5003: "Magieresistenz", 5010: "Lauftempo", 5013: "Zähigkeit & Verlangsamungsresistenz"}


def rune_tree(gd, page):
    """op.gg-style rune page: every row of both trees, chosen runes marked, plus stat shards."""
    if not page:
        return None
    chosen = set(page["perks"])
    trees = {t["id"]: t for t in gd.runes}

    def tree(tid, rows):
        t = trees.get(tid)
        if not t:
            return None
        return {"id": tid, "name": gd.rune_disp.get(tid, t["name"]), "icon": t.get("icon"),
                "rows": [[{"id": r["id"], "name": gd.rune_disp.get(r["id"], r["name"]), "icon": r.get("icon"),
                           "on": r["id"] in chosen} for r in slot["runes"]] for slot in t["slots"][rows]]}
    return {"primary": tree(page["primary"], slice(0, 4)), "secondary": tree(page["sub"], slice(1, 4)),
            "shards": [SHARDS.get(x, str(x)) for x in page["perks"][6:9]],
            "games": page.get("games"), "winrate": page.get("winrate")}


HIST_CACHE = {}
MCACHE = {}  # meta pages computed in the background -> the Meta tab opens instantly


def _mkey(*k):
    return (META.get("ver", 0), current_bracket_safe()) + k


def current_bracket_safe():
    try:
        return CFG_REF[0] and current_bracket(CFG_REF[0])
    except Exception:  # noqa: BLE001
        return None


CFG_REF = [None]


def cached_tierlist(gd, cfg, role, min_games=20):
    key = _mkey("tier", role, min_games)
    if key not in MCACHE:
        ms = meta_stats(gd)
        if not ms:
            return []
        lst = meta.MetaStats(gd, ms.db).tierlist(min_games=min_games, bracket=current_bracket(cfg), role=role)
        for t in lst:
            c = gd.champ(t["champion"])
            t["name"] = gd.champ_name(c) if c else t["champion"]
        MCACHE[key] = lst
    return MCACHE[key]


def cached_champion(gd, cfg, c, role=None):
    key = _mkey("champ", c["id"], role)
    if key not in MCACHE:
        MCACHE[key] = champion_page(gd, cfg, c, role)
    return MCACHE[key]


def warm_meta(gd, cfg):
    """Pre-computes tier lists (all roles) and the pages of your champions + the top picks of your role."""
    try:
        for role in ("JUNGLE", "TOP", "MIDDLE", "BOTTOM", "UTILITY"):
            cached_tierlist(gd, cfg, role)
        main = cfg.get("main_role") or "JUNGLE"
        refs = list(cfg.get("champion_pool", [])) + [t["champion"] for t in cached_tierlist(gd, cfg, main)[:12]]
        for ref in refs:
            c = gd.champ(ref)
            if c:
                cached_champion(gd, cfg, c, main)
                time.sleep(0.05)  # stay light in the background
    except Exception:  # noqa: BLE001 - only a cache
        pass


def champion_page(gd, cfg, c, role=None):
    """Everything for one champion like on op.gg / Mobalytics – from our own Riot data + the simulation."""
    from . import sim
    ms0 = meta_stats(gd)
    out = {"id": c["id"], "name": gd.champ_name(c), "title": c.get("title", ""), "version": gd.version,
           "tags": c.get("tags", []), "role": role}
    if not ms0:
        out["error"] = "Meta-Datenbank wird noch geladen …"
        return out
    ms = meta.MetaStats(gd, ms0.db)
    role = role or ms.main_role(c["id"]) or "JUNGLE"
    out["role"] = role
    roles = ms.db.execute("SELECT role, COUNT(*) FROM games WHERE champ=? AND role!='' GROUP BY role ORDER BY 2 DESC",
                          (c["id"],)).fetchall()
    out["roles"] = [{"role": r, "games": n} for r, n in roles]
    bracket = current_bracket(cfg)
    ms.role = role
    st = ms.champion(c["id"], bracket=bracket)
    tl = ms.tierlist(min_games=10, bracket=bracket, role=role)
    me = next((t for t in tl if t["champion"] == c["id"]), None)
    out["tier"] = me
    out["role_count"] = len(tl)
    out["stats"] = st

    def item(i):
        return {"id": i, "name": gd.item_name(i)}
    if st.get("enough"):
        out["runes"] = rune_tree(gd, st.get("rune_page"))
        out["keystones"] = [dict(k, icon=gd.rune_icon.get(k["id"])) for k in st.get("keystones", [])]
        out["core"] = st["meta_core"]
        out["boots"] = st.get("meta_boots")
        ms.role = role
        bp = ms.build_path(c["id"], bracket=bracket)
        ms.role = None
        if bp.get("enough"):
            out["core"] = [dict(x, pickrate=x["share"]) for x in bp["core"]]
            out["build_alts"] = bp["alternatives"]
            out["build_exact"] = bool(bp.get("exact"))
            out["late"] = bp["late"]
            if bp.get("boots"):
                out["boots"] = dict(bp["boots"], pickrate=bp["boots"]["share"])
        out["gems"] = st.get("hidden_items", []) + [dict(g, icon=gd.rune_icon.get(g["id"])) for g in st.get("hidden_keystones", [])]
        for m in st.get("worst_matchups", []) + st.get("best_matchups", []):
            e = gd.champ(m.get("enemy"))
            if e:
                m["id"] = e["id"]
                m["name"] = gd.champ_name(e)
    ms.role = None
    b = ms.benchmarks(c["id"], role, bracket)
    if b:
        good = []
        for k in ("jcs10" if role == "JUNGLE" else "cs10", "kp", "vpm", "deaths", "dpm", "gpm"):
            if k in b["dist"]:
                label, higher, unit = meta.METRICS[k]
                good.append({"metric": k, "label": label, "avg": meta.quantile(b["dist"][k], 0.5),
                             "top": meta.quantile(b["dist"][k], 0.75 if higher else 0.25), "unit": unit})
        out["benchmarks"] = {"scope": b["scope"], "games": b["games"], "values": good}
    return out


def meta_stats(gd):
    if META["stats"] is None and os.path.exists(meta.DB_PATH):
        META["stats"] = meta.MetaStats(gd)
    return META["stats"]
LOCK = threading.Lock()


def load_config():
    from .paths import ensure_config
    with open(ensure_config(), encoding="utf-8") as f:
        return json.load(f)


STATUS = {"sent": None, "at": 0}


def report_status(cfg, phase, champ=None):
    """Tells the server whether you are playing (friends see it), when it changes or every 90 s."""
    if not cfg.get("auth_token"):
        return
    state = ("ingame" if phase in ("InProgress", "GameStart", "Reconnect") else "champselect" if phase == "ChampSelect"
             else "offline" if phase in (None, "Offline") else "online")
    key = (state, champ)
    if key == STATUS["sent"] and time.time() - STATUS["at"] < 90:
        return
    if key != STATUS["sent"]:
        STATUS["since"] = int(time.time())
    STATUS["sent"], STATUS["at"] = key, time.time()
    cloud_bg(cfg, "PUT", "/api/sync/status", {"state": state, "champ": champ, "since": STATUS.get("since")})


PUBLIC = {"data": {}, "at": 0}


def public_profiles(cfg):
    """Friends' Jungle Coach profiles (name, picture, text, live status) – refreshed every 60 s in the background."""
    ids = cfg.get("friends", [])
    if ids and cfg.get("auth_token") and time.time() - PUBLIC["at"] > 60:
        PUBLIC["at"] = time.time()

        def job():
            r = cloud(cfg, "GET", "/api/public/profiles?ids=" + urllib.parse.quote(",".join(ids)))
            if r is not None:
                PUBLIC["data"] = r.get("profiles") or {}
        threading.Thread(target=job, daemon=True).start()
    return PUBLIC["data"]


def poll_loop(gd, cfg, lcu):
    last_phase = None
    last_runes_key = None
    last_import = None
    last_rank = 0
    while True:
        try:
            phase = lcu.phase()
            if last_phase == "InProgress" and phase != "InProgress" and riot_cred(cfg):
                def _after_game():  # Riot needs a moment until the match is available
                    for _ in range(6):
                        time.sleep(90)
                        try:
                            run_review(cfg)
                            return
                        except Exception:  # noqa: BLE001
                            continue
                threading.Thread(target=_after_game, daemon=True).start()
            last_phase = phase
            try:
                champ = None
                if phase in ("InProgress", "ChampSelect"):
                    champ = (STATE.get("live") or {}).get("_me_id") or (STATE.get("champ_select") or {}).get("my_champion_id")
                report_status(cfg, phase, champ)
            except Exception:  # noqa: BLE001
                pass
            cs = live_state = None
            if phase == "ChampSelect":
                acc_ = ranked.ACTIVE.get("account") or cfg.get("riot_id", "")
                if HIST_CACHE.get("acc") != acc_ or time.time() - HIST_CACHE.get("at", 0) > 600:
                    HIST_CACHE.update(acc=acc_, at=time.time(),
                                      games=[v for k, v in load_history(acc_).items() if not k.startswith("__")])
                cs = champselect.analyze(gd, lcu.champ_select(), cfg, meta_stats(gd), current_bracket(cfg),
                                         HIST_CACHE.get("games"))
                # auto-import runes: again whenever champion or enemy jungler (matchup) changes
                if cs and cs.get("runes") and cfg.get("auto_import_runes", True):
                    key = (cs["my_champion"], tuple(sorted(cs.get("enemies", []))))  # re-push on every new enemy
                    if key != last_runes_key:
                        r = cs["runes"]
                        res = lcu.set_rune_page(f"JC {key[0]}", r["primaryStyleId"], r["subStyleId"],
                                                r["selectedPerkIds"])
                        cs["runes_imported"] = not (res or {}).get("error")
                        n = cs.get("enemies_locked", 0)
                        cs["runes_import_msg"] = (res or {}).get("error") or (
                            "Final gesetzt – gegen alle 5 Gegner" if n >= 5 else f"Gesetzt ({n}/5 Gegner bekannt, wird aktualisiert)")
                        last_import = cs["runes_import_msg"]
                        last_runes_key = key
                    else:
                        cs["runes_import_msg"] = last_import
            else:
                last_runes_key = None
            if phase not in ("InProgress", "ChampSelect") and time.time() - last_rank > 60:
                last_rank = time.time()
                ranked.snapshot(lcu)
            data = live.fetch()
            if data:
                presets = dict(cfg.get("champion_presets", {}))
                with LOCK:
                    last_cs = STATE.get("champ_select") or {}
                if last_cs.get("build") and last_cs.get("my_champion"):
                    cid = gd.champ(last_cs["my_champion"]) or gd.champ_by_name.get(last_cs["my_champion"].lower())
                    if cid and cid["id"] not in presets:  # keep data-driven core from champ select in game
                        presets[cid["id"]] = {"core": [gd.items[c["id"]]["name"] for c in last_cs["build"]["core"]
                                                       if c["id"] in gd.items]}
                def preset_for(cid, _p=presets):
                    if cid in _p:
                        return _p[cid]
                    ms = meta_stats(gd)
                    if not ms:
                        return None
                    if cid not in BUILD_CACHE:
                        try:
                            bp = ms.build_path(cid, bracket=current_bracket(cfg))
                            BUILD_CACHE[cid] = {"core": [gd.items[x["id"]]["name"] for x in bp.get("core", [])
                                                         if x["id"] in gd.items]} if bp.get("enough") else None
                        except Exception:  # noqa: BLE001
                            BUILD_CACHE[cid] = None
                    return BUILD_CACHE[cid]
                live_state = live.analyze(gd, data, cfg.get("timers"), preset_for)
                if live_state:
                    try:
                        from . import hud
                        plan = COACH.get("plan") or {}
                        f = (plan.get("focus") or [None])[0]
                        focus = {"title": f["title"], "target": f["target"]} if f else None
                        step = ((last_cs.get("gameplan") or {}).get("steps") or [None])[0]
                        role = cfg.get("main_role") or "JUNGLE"
                        key = (live_state.get("_me_id"), role)
                        if key not in GPM_BENCH:
                            GPM_BENCH[key] = None
                            try:
                                ms = meta_stats(gd)
                                b = ms.benchmarks(key[0], role, current_bracket(cfg)) if ms and key[0] else None
                                g = (b or {}).get("dist", {}).get("gpm")
                                if g:
                                    GPM_BENCH[key] = {"p25": meta.quantile(g, 0.25), "p50": meta.quantile(g, 0.5)}
                            except Exception:  # noqa: BLE001
                                pass
                        live_state["hud"] = hud.build(live_state, gd, live_state.get("_gold"), live_state.get("_owned") or [],
                                                      focus, step, GPM_BENCH.get(key), role, OVERLAY.get("pick"),
                                                      set((cfg.get("overlay") or {}).get("hide") or []))
                    except Exception as e:  # noqa: BLE001
                        live_state["hud"] = {"error": str(e)}
            # ---- automatic recording + mistake clips
            rec = REC["obj"] = REC["obj"] or recorder.Recorder(cfg)
            if live_state and not rec.active and rec.cfg["enabled"]:
                rec.start(live_state["_t"])
            if live_state and rec.active:
                rec.note(live_state["mistakes"], {"champion": live_state["me"]})
            if not live_state and rec.active:
                threading.Thread(target=rec.stop, daemon=True).start()  # cutting clips takes a moment
            if not live_state:
                OVERLAY["pick"] = None  # next game starts with the build path again
            with LOCK:
                STATE["phase"] = "InGame" if live_state else (phase or "Client nicht gefunden")
                if cs:
                    STATE["champ_select"] = cs
                elif phase not in ("ChampSelect", "GameStart", "InProgress") and not live_state:
                    STATE["champ_select"] = None  # keep last champ select visible during the game
                STATE["live"] = live_state
                STATE["recording"] = rec.active
                STATE["error"] = None
        except Exception as e:  # keep running, show error in UI
            with LOCK:
                STATE["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        time.sleep(2)


def attach_clips(games, sessions):
    """Recording sessions start a bit after Riot's gameStartTimestamp (loading screen)."""
    for g in games:
        best = min(sessions, key=lambda s: abs(s["start_wall"] - g.get("game_start", 0)), default=None)
        if best and abs(best["start_wall"] - g.get("game_start", 0)) < 600:
            g["session_id"], g["clips"] = best["id"], best["clips"]


def make_handler(gd, cfg, lcu):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, fp, ctype):
            """Streams a file with HTTP range support – the video player can jump anywhere instantly
            instead of downloading the whole recording first."""
            size = os.path.getsize(fp)
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].split(",")[0].partition("-")
                try:
                    if a:
                        start = int(a)
                        end = int(b) if b else end
                    elif b:
                        start = max(0, size - int(b))
                except ValueError:
                    start, end = 0, size - 1
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            try:
                with open(fp, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (ConnectionResetError, BrokenPipeError):
                pass  # the player cancelled this request (normal while seeking)

        def do_GET(self):
            if self.path == "/i18n_en.js":
                with open(os.path.join(STATIC, "i18n_en.js"), "rb") as f:
                    return self._send(200, f.read(), "application/javascript")
            if self.path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html")
            if self.path in tuple(f"/moosi/{n}.webp" for n in ("body", "head", "logo", "proud", "worried", "sleepy")):
                with open(os.path.join(STATIC, "moosi", self.path.rsplit("/", 1)[1]), "rb") as f:
                    return self._send(200, f.read(), "image/webp")
            if self.path == "/api/state":
                with LOCK:
                    from . import i18n
                    light = {k: v for k, v in STATE.items() if k != "review"}  # big analysis is loaded separately
                    return self._send(200, dict(light, version=gd.version, lang=i18n.LANG["code"],
                                                ov_hide=(cfg.get("overlay") or {}).get("hide", [])))
            if self.path == "/api/review-data":
                with LOCK:
                    return self._send(200, STATE.get("review") or {})
            if self.path.startswith("/icon/item/"):  # item icon for the overlay (cached once per patch)
                iid = self.path.rsplit("/", 1)[-1].split("?")[0]
                if not iid.isdigit():
                    return self._send(404, {"error": "bad id"})
                d = os.path.join(ROOT, "cache", "icons")
                fp = os.path.join(d, f"{gd.version}_{iid}.png")
                if not os.path.exists(fp):
                    try:
                        os.makedirs(d, exist_ok=True)
                        with urllib.request.urlopen(f"https://ddragon.leagueoflegends.com/cdn/{gd.version}/img/item/{iid}.png",
                                                    timeout=5) as r:
                            data = r.read()
                        with open(fp, "wb") as f:
                            f.write(data)
                    except Exception:  # noqa: BLE001
                        return self._send(404, {"error": "icon not available"})
                return self._send_file(fp, "image/png")
            if self.path == "/api/overlay":  # tiny payload, polled every second by the in-game overlay
                from . import i18n
                with LOCK:
                    lv = STATE.get("live")
                    small = {"hud": lv.get("hud"), "game_time": lv.get("game_time"), "cs_per_min": lv.get("cs_per_min")} if lv else None
                oc = cfg.get("overlay", {})
                return self._send(200, {"live": small, "lang": i18n.LANG["code"],
                                        "ov": {"mode": oc.get("mode", "always"), "opacity": oc.get("bg_opacity", 0.72),
                                               "size": oc.get("size", 1.0), "move": OVERLAY["move"],
                                               "pick": OVERLAY["pick"]}})
            if self.path.startswith("/api/profile"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                rid = (q.get("riot_id") or [cfg.get("riot_id", "")])[0].strip()
                if not riot_cred(cfg):
                    return self._send(400, {"error": NO_CRED})
                if "#" not in rid:
                    return self._send(400, {"error": "Riot ID im Format Name#TAG eingeben."})
                queue = (q.get("queue") or ["all"])[0]
                if q.get("all"):
                    profile.load_all(riot_cred(cfg), cfg.get("platform", "euw1"), rid, gd, queue)
                try:
                    prof = profile.fetch(riot_cred(cfg), cfg.get("platform", "euw1"), rid, gd,
                                         int(cfg.get("profile_games", 20)), queue, more=bool(q.get("more")))
                except review.RiotNotFound:
                    return self._send(404, {"error": f"Spieler „{rid}“ nicht gefunden. Riot ID genau wie im Client "
                                                     f"(Name#TAG) und Region {cfg.get('platform', 'euw1')} prüfen."})
                except Exception as e:
                    return self._send(500, {"error": str(e)})
                solo = prof["ranks"].get("solo")
                if solo:  # feed this account's LP tracker without client (each account has its own history)
                    class _FromApi:
                        def get(self, _p):
                            return {"queueMap": {"RANKED_SOLO_5x5": {"tier": solo["tier"], "division": solo["division"],
                                    "leaguePoints": solo["lp"], "wins": solo["wins"], "losses": solo["losses"]}}}
                    ranked.snapshot(_FromApi(), account=rid)
                res = {k: v for k, v in prof.items() if not k.startswith("_")}
                res["load_all"] = profile.LOAD_ALL.get((rid.lower(), queue if queue in profile.FILTERS else "all"))
                return self._send(200, res)
            if self.path.startswith("/api/login"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                prov = (q.get("provider") or ["discord"])[0]
                if prov not in ("discord", "google") or not cfg.get("server_url"):
                    return self._send(400, {"error": "Anmeldung ist noch nicht eingerichtet."})
                port = cfg.get("port", 8765)
                cb = urllib.parse.quote(f"http://localhost:{port}/auth/callback", safe="")
                import webbrowser
                webbrowser.open(f"{cfg['server_url'].rstrip('/')}/auth/{prov}?redirect={cb}")
                return self._send(200, {"ok": True})
            if self.path.startswith("/auth/callback"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                tok = (q.get("token") or [""])[0]
                if tok.count(".") == 2:
                    cfg["auth_token"] = tok
                    save_config(cfg)
                    profile._mem.clear()
                    threading.Thread(target=sync_on_start, args=(cfg,), daemon=True).start()
                    body = "<h1>Angemeldet ✓</h1><p>Du kannst dieses Fenster schließen und zu Moosi zurückkehren.</p>"
                else:
                    body = "<h1>Anmeldung fehlgeschlagen</h1><p>Bitte in der App erneut versuchen.</p>"
                html = ("<!doctype html><meta charset=utf-8><title>Moosi</title><body style='font:16px -apple-system,"
                        "Segoe UI,sans-serif;text-align:center;padding:80px;background:#f5f5f7'>" + body)
                return self._send(200, html.encode("utf-8"), "text/html")
            if self.path == "/api/account":
                if not cfg.get("auth_token") or not cfg.get("server_url"):
                    return self._send(200, {"logged_in": False, "server": bool(cfg.get("server_url"))})
                try:
                    req = urllib.request.Request(cfg["server_url"].rstrip("/") + "/api/me",
                                                 headers={"Authorization": "Bearer " + cfg["auth_token"]})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        me = json.loads(r.read().decode("utf-8"))
                    return self._send(200, {"logged_in": True, "server": True, **me})
                except urllib.error.HTTPError as e:
                    if e.code == 401:
                        cfg.pop("auth_token", None)
                        save_config(cfg)
                    return self._send(200, {"logged_in": False, "server": True, "error": f"Sitzung ungültig ({e.code})"})
                except Exception:
                    return self._send(200, {"logged_in": True, "server": True, "offline": True})
            if self.path.startswith("/api/match"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    det = profile.match_detail(riot_cred(cfg), cfg.get("platform", "euw1"),
                                               q["id"][0], (q.get("puuid") or [""])[0], gd)
                    return self._send(200, det)
                except Exception as e:
                    return self._send(500, {"error": str(e)})
            if self.path.startswith("/api/test-key"):
                return self._test_key(None)
            if self.path.startswith("/api/death-bench"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    b = meta_stats(gd).death_benchmark((q.get("role") or ["JUNGLE"])[0], current_bracket(cfg))
                except Exception:  # noqa: BLE001 - no data yet
                    b = None
                return self._send(200, {"bench": b})
            if self.path.startswith("/api/ranked"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                accs = ranked.accounts()
                acc = ((q.get("account") or [""])[0] or ranked.active_account(cfg.get("riot_id")) or
                       (accs[0] if accs else "")).lower()
                with LOCK:
                    rev = STATE.get("review") or {}
                games = rev.get("games", [])
                ms = meta_stats(gd)
                pool_worst = []
                if ms:
                    for ref in cfg.get("champion_pool", [])[:5]:
                        c = gd.champ(ref)
                        if c:
                            st = ms.champion(c["id"], bracket=current_bracket(cfg))
                            if st.get("enough"):
                                pool_worst.append({"champion": gd.champ_name(c), "worst": st["worst_matchups"][:3]})
                return self._send(200, {"summary": ranked.summary(ranked.load_history(acc)),
                                        "account": acc, "accounts": accs,
                                        "improvements": ranked.improvements(games),
                                        "own_matchups": ranked.own_matchups(games), "games": len(games),
                                        "pool_worst": pool_worst})
            if self.path == "/api/champions":
                return self._send(200, sorted(({"id": c["id"], "name": gd.champ_name(c)} for c in gd.champ_by_id.values()),
                                              key=lambda x: x["name"]))
            if self.path.startswith("/api/tierlist"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                role = (q.get("role") or ["JUNGLE"])[0].upper()
                ms = meta_stats(gd)
                if not ms:
                    return self._send(200, {"role": role, "list": []})
                lst = cached_tierlist(gd, cfg, role, int((q.get("min") or [20])[0]))
                return self._send(200, {"role": role, "list": lst, "patch": ms.current_patch(),
                                        "bracket": current_bracket(cfg), "version": gd.version})
            if self.path.startswith("/api/champion"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ref = (q.get("id") or [""])[0]
                c = gd.champ(ref) or gd.champ_by_name.get(ref.lower())
                if not c:
                    return self._send(404, {"error": f"Champion „{ref}“ nicht gefunden"})
                return self._send(200, cached_champion(gd, cfg, c, (q.get("role") or [""])[0].upper() or None))
            if self.path.startswith("/api/sim"):
                from jc import sim
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                champ_ref = (q.get("champ") or [""])[0]
                c = gd.champ(champ_ref) or gd.champ_by_name.get(champ_ref.lower())
                if not c:
                    return self._send(400, {"error": "Champion unbekannt"})
                enemies = [e["id"] for e in (gd.champ(x) or gd.champ_by_name.get(x.lower())
                                             for x in ((q.get("enemies") or [""])[0]).split(",") if x) if e]
                ms = meta_stats(gd)
                ms2 = meta.MetaStats(gd, ms.db) if ms else None
                if not sim.load():
                    sim.refresh(force=True)
                try:
                    res = sim.recommend(c["id"], enemies, (q.get("role") or [None])[0] or None, meta_stats=ms2,
                                        bracket=current_bracket(cfg))
                except Exception as e:  # noqa: BLE001
                    return self._send(500, {"error": str(e)})
                return self._send(200, res)
            if self.path == "/api/meta":
                ms = meta_stats(gd)
                cr = META["crawler"]
                out = {"crawler": cr.state if cr else {"running": False, "message": "bereit", "matches": 0},
                       "total": ms.total() if ms else 0, "patch": ms.current_patch() if ms else None,
                       "bracket": current_bracket(cfg), "champions": [], "tierlist": [], "cloud": meta_cloud.STATE}
                if ms:
                    b = current_bracket(cfg)
                    out["tierlist"] = [dict(t, name=gd.champ_name(gd.champ(t["champion"])))
                                       for t in ms.tierlist(bracket=b)[:15]]
                    for ref in cfg.get("champion_pool", []):
                        c = gd.champ(ref)
                        if c:
                            out["champions"].append(dict(ms.champion(c["id"], bracket=b), name=gd.champ_name(c)))
                return self._send(200, out)
            if self.path == "/api/config":
                safe = {k: cfg.get(k) for k in EDITABLE}
                key = safe.get("riot_api_key") or ""
                safe["riot_api_key_set"] = key.startswith("RGAPI-") and "xxxx" not in key
                safe["riot_api_key"] = ""
                safe["server_url"] = cfg.get("server_url", "")
                return self._send(200, safe)
            if self.path == "/api/sessions":
                return self._send(200, recorder.list_sessions())
            if self.path.startswith("/clips/"):
                parts = urllib.parse.unquote(self.path.split("?")[0]).split("/")
                if len(parts) == 4 and ".." not in parts[2] + parts[3] and parts[3].endswith((".mp4", ".jpg")):
                    fp = os.path.join(recorder.REC_DIR, parts[2], parts[3])
                    if os.path.exists(fp):
                        return self._send_file(fp, "video/mp4" if fp.endswith(".mp4") else "image/jpeg")
                return self._send(404, {"error": "Clip nicht gefunden"})
            if self.path.startswith("/api/review"):
                if not riot_cred(cfg):
                    return self._send(400, {"error": NO_CRED})
                if "#" not in (ranked.ACTIVE.get("account") or cfg.get("riot_id", "")):
                    return self._send(400, {"error": "Riot ID fehlt – trag sie in den Einstellungen ein (Name#TAG)."})
                try:
                    return self._send(200, run_review(cfg))
                except Exception as e:
                    return self._send(500, {"error": str(e)})
            if self.path == "/api/friends":
                return self._send(200, friends.listing(cfg, gd, riot_cred(cfg), lcu, pub=public_profiles(cfg)))
            if self.path == "/api/my-profile":
                if not cfg.get("auth_token"):
                    return self._send(200, {"login": False})
                r = cloud(cfg, "GET", "/api/sync/profile")
                return self._send(200, {"login": True, "profile": (r or {}).get("profile"), "ok": r is not None,
                                        "history": history_info(ranked.ACTIVE.get("account") or cfg.get("riot_id", "")),
                                        "role_detected": cfg.get("role_detected"), "role_auto": cfg.get("role_auto", True)})
            if self.path.startswith("/api/friend-coach"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                rid = (q.get("riot_id") or [""])[0]
                st = friends.coach_state(rid)
                if q.get("start") and riot_cred(cfg) and friends.valid(rid):
                    st = friends.start_coach(cfg, gd, riot_cred(cfg), rid,
                                             lambda rev: coach_plan(gd, cfg, rev, cache={}))
                    st = {k: v for k, v in st.items() if not k.startswith("_")}
                return self._send(200, st)
            if self.path.startswith("/api/coach"):
                with LOCK:
                    rev = STATE.get("review") or {}
                return self._send(200, coach_plan(gd, cfg, rev))
            self._send(404, {"error": "not found"})

        def _test_key(self, new_key):
            steps = []
            key = (new_key or riot_cred(cfg) or "").strip()
            api = review.RiotAPI(key, cfg.get("platform", "euw1"))
            try:
                api.get_platform("/lol/status/v4/platform-data")
                steps.append({"ok": True, "text": f"API Key gültig, Server {cfg.get('platform', 'euw1')} erreichbar"})
                if new_key and new_key != cfg.get("riot_api_key"):
                    cfg["riot_api_key"] = new_key
                    cfg.pop("_key_from_build", None)
                    save_config(cfg)
                    steps.append({"ok": True, "text": "Neuer Key gespeichert"})
                rid = cfg.get("riot_id", "")
                if "#" in rid:
                    n, t = rid.split("#", 1)
                    try:
                        api.get(f"/riot/account/v1/accounts/by-riot-id/{urllib.parse.quote(n)}/{urllib.parse.quote(t)}")
                        steps.append({"ok": True, "text": f"Account {rid} gefunden"})
                    except review.RiotNotFound:
                        steps.append({"ok": False, "text": f"Account {rid} nicht gefunden – Schreibweise/TAG prüfen"})
                else:
                    steps.append({"ok": False, "text": "Riot ID fehlt (Format Name#TAG)"})
            except Exception as e:
                steps.append({"ok": False, "text": str(e)})
            return self._send(200, {"steps": steps})

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_POST(self):
            if self.path in ("/api/logout", "/api/delete-account"):
                if self.path == "/api/delete-account" and cfg.get("auth_token"):
                    try:
                        req = urllib.request.Request(cfg["server_url"].rstrip("/") + "/api/me", method="DELETE",
                                                     headers={"Authorization": "Bearer " + cfg["auth_token"]})
                        urllib.request.urlopen(req, timeout=10).read()
                    except Exception as e:
                        return self._send(500, {"error": f"Konto konnte nicht gelöscht werden: {e}"})
                cfg.pop("auth_token", None)
                save_config(cfg)
                profile._mem.clear()
                return self._send(200, {"ok": True})
            if self.path == "/api/my-avatar":
                r = cloud(cfg, "PUT", "/api/sync/avatar", {"data": self._body().get("data", "")})
                return self._send(200 if r and r.get("ok") else 400, r or {"error": "Server nicht erreichbar"})
            if self.path == "/api/my-profile":
                prof = self._body().get("profile") or {}
                prof["role"] = cfg.get("main_role") or prof.get("role")  # main role is shown on the profile
                r = cloud(cfg, "PUT", "/api/sync/profile", {"profile": prof})
                return self._send(200 if r is not None else 502, r or {"error": "Server nicht erreichbar"})
            if self.path == "/api/friends":
                body = self._body()
                if body.get("add"):
                    if not friends.add(cfg, body["add"]):
                        return self._send(400, {"error": "Riot ID im Format Name#TAG eingeben."})
                if body.get("remove"):
                    friends.remove(cfg, body["remove"])
                cfg["friends_updated"] = int(time.time())
                save_config(cfg)
                cloud_bg(cfg, "PUT", "/api/sync/friends", {"friends": cfg.get("friends", [])})
                return self._send(200, friends.listing(cfg, gd, riot_cred(cfg), lcu, pub=public_profiles(cfg)))
            if self.path == "/api/recording":  # clips on/off, sound, how many games are kept
                b = self._body()
                rc = cfg.setdefault("recording", {})
                for k in ("enabled", "audio"):
                    if isinstance(b.get(k), bool):
                        rc[k] = b[k]
                if isinstance(b.get("keep_games"), int):
                    rc["keep_games"] = max(1, min(10, b["keep_games"]))
                save_config(cfg)
                if REC.get("obj"):
                    REC["obj"].cfg.update({k: rc[k] for k in ("enabled", "audio", "keep_games") if k in rc})
                return self._send(200, {"enabled": rc.get("enabled", True), "audio": rc.get("audio", True),
                                        "keep_games": rc.get("keep_games", 3)})
            if self.path == "/api/overlay-settings":
                b = self._body()
                oc = cfg.setdefault("overlay", {})
                if b.get("mode") in ("always", "tab"):
                    oc["mode"] = b["mode"]
                if isinstance(b.get("size"), (int, float)):
                    oc["size"] = max(0.7, min(1.6, float(b["size"])))
                if isinstance(b.get("opacity"), (int, float)):
                    oc["bg_opacity"] = max(0.2, min(1.0, float(b["opacity"])))
                if isinstance(b.get("x"), int) and isinstance(b.get("y"), int):
                    oc["x"], oc["y"], oc["abs"] = b["x"], b["y"], True
                if "move" in b:
                    OVERLAY["move"] = bool(b["move"])
                hide = set(oc.get("hide") or [])
                for key in ("gold_diff", "enemy_jg", "timers"):   # single overlay parts on/off
                    if key in b:
                        (hide.discard if b[key] else hide.add)(key)
                if "riot_safe" in b:   # one switch: only what Riot allows for overlays
                    # objective timers stay: dragon/baron kills are announced to everyone, the timer is not hidden info
                    hide = (hide | {"gold_diff", "enemy_jg"}) if b["riot_safe"] else (hide - {"gold_diff", "enemy_jg"})
                oc["hide"] = sorted(hide)
                if "pick" in b:  # item chosen in the Tab picker (None = back to the build path)
                    OVERLAY["pick"] = int(b["pick"]) if str(b.get("pick") or "").isdigit() else None
                save_config(cfg)
                return self._send(200, {"mode": oc.get("mode", "always"), "opacity": oc.get("bg_opacity", 0.72),
                                        "size": oc.get("size", 1.0), "move": OVERLAY["move"], "hide": oc.get("hide", [])})
            if self.path == "/api/clip-feedback":
                b = self._body()
                ok = recorder.set_feedback(b.get("sid"), b.get("file"), b.get("verdict"))
                return self._send(200 if ok else 400, {"ok": ok, "stats": recorder.feedback_stats()})
            if self.path == "/api/test-key":
                return self._test_key(self._body().get("riot_api_key"))
            if self.path == "/api/config":
                body = self._body()
                for k in EDITABLE:
                    if k in body and not (k == "riot_api_key" and not body[k]):
                        cfg[k] = body[k].strip() if isinstance(body[k], str) else body[k]
                if body.get("riot_api_key"):
                    cfg.pop("_key_from_build", None)  # user's own key wins over the built-in one
                    # also store it for this account on the server, so the phone app can use it too
                    k = (cfg.get("riot_api_key") or "").strip()
                    threading.Thread(target=cloud, args=(cfg, "PUT", "/api/sync/riotkey", {"key": k}), daemon=True).start()
                save_config(cfg)
                return self._send(200, {"ok": True})
            if self.path == "/api/meta/crawl":
                if not (cfg.get("riot_api_key") or "").startswith("RGAPI-"):
                    return self._send(400, {"error": "Die Daten kommen automatisch aus der Cloud-Datenbank. Eigenes Sammeln nur mit eigenem Entwickler-Key."})
                cr = META["crawler"] = META["crawler"] or meta.Crawler(cfg)
                if cr.state["running"]:
                    cr.stop()
                    return self._send(200, {"running": False})
                cr.state["matches"] = 0
                cr.start()
                META["stats"] = None  # reopen after new data
                return self._send(200, {"running": True})
            if self.path == "/api/apply-runes":
                with LOCK:
                    cs = STATE.get("champ_select")
                if not cs or not cs.get("runes"):
                    return self._send(400, {"error": "Erst Champion wählen."})
                r = cs["runes"]
                res = lcu.set_rune_page(f"JC {cs['my_champion']}", r["primaryStyleId"], r["subStyleId"],
                                        r["selectedPerkIds"])
                return self._send(200 if res and not res.get("error") else 400, res or {"error": "Client nicht erreichbar"})
            self._send(404, {"error": "not found"})
    return H


def start(cfg=None):
    """Starts data loading, polling and the local server in background threads. Returns URL."""
    cfg = cfg or load_config()
    print("Lade Spieldaten (Data Dragon)…")
    from . import i18n
    i18n.setup(cfg)
    gd = GameData(cfg.get("display_language") or i18n.ddragon_locale())
    print(f"Patch {gd.version} geladen.")
    deep.BENCH = lambda champ, role: (lambda ms: ms.benchmarks(champ, role, current_bracket(cfg)) if ms else None)(meta_stats(gd))
    lcu = LCU(cfg.get("league_path"))
    if os.path.exists(REVIEW_CACHE):
        try:
            with open(REVIEW_CACHE, encoding="utf-8") as f:
                STATE["review"] = json.load(f)
        except (OSError, ValueError):
            pass
    threading.Thread(target=poll_loop, args=(gd, cfg, lcu), daemon=True).start()

    CFG_REF[0] = cfg

    def cloud_loop():
        from jc import sim
        first = True
        while True:
            sim.refresh()  # patch data for the build simulation (once a day)
            if meta_cloud.update(cfg, max_age_h=1):  # new cloud data every hour (crawler publishes hourly)
                META["stats"] = None  # reopen with the new games
                META["ver"] = META.get("ver", 0) + 1
                MCACHE.clear()
                first = True
            if first:
                warm_meta(gd, cfg)  # Meta tab ready without waiting
                first = False
            time.sleep(900)
    threading.Thread(target=cloud_loop, daemon=True).start()
    threading.Thread(target=auto_review_loop, args=(cfg,), daemon=True).start()
    threading.Thread(target=sync_on_start, args=(cfg,), daemon=True).start()
    threading.Thread(target=sync_loop, args=(cfg,), daemon=True).start()
    port = cfg.get("port", 8765)
    host = "0.0.0.0" if cfg.get("allow_phone", False) else "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), make_handler(gd, cfg, lcu))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://localhost:{port}"
    print(f"Moosi läuft: {url}")
    return url


def main():
    start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
