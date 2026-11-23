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

from . import champselect, deep, live, meta, meta_cloud, profile, ranked, recorder, review
from .ddragon import GameData
from .lcu import LCU

from .paths import DATA_DIR as ROOT, STATIC

STATE = {"phase": "Startet…", "champ_select": None, "live": None, "review": None, "error": None}
REC = {"obj": None}
META = {"stats": None, "crawler": None}
REVIEW_CACHE = os.path.join(ROOT, "review_cache.json")
EDITABLE = ["language", "main_role", "riot_id", "platform", "riot_api_key", "champion_pool", "auto_import_runes", "review_games", "profile_games",
            "meta_bracket", "meta_max_matches"]


def riot_cred(cfg):
    """Own developer key if set, otherwise the logged-in session via the Jungle Coach server."""
    key = (cfg.get("riot_api_key") or "").strip()
    if key.startswith("RGAPI-") and "xxxx" not in key:
        return key
    if cfg.get("auth_token") and cfg.get("server_url"):
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


def run_review(cfg):
    """Loads + analyses the recent ranked games of the active account (or the configured Riot ID)."""
    rid = ranked.ACTIVE.get("account") or cfg.get("riot_id", "")
    with LOCK:
        STATE["review_status"] = {"running": True, "done": 0, "total": 0, "account": rid}

    def progress(done, total):
        with LOCK:
            STATE["review_status"] = {"running": True, "done": done, "total": total, "account": rid}
    try:
        res = review.run(riot_cred(cfg), cfg.get("platform", "euw1"), rid, max(30, int(cfg.get("review_games", 30))),
                         progress=progress)
    except Exception as e:
        with LOCK:
            STATE["review_status"] = {"running": False, "error": str(e), "account": rid}
        raise
    res["account"] = rid
    res["analysed_at"] = time.time()
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
            stale = time.time() - rev.get("analysed_at", 0) > 6 * 3600
            other = (rev.get("account") or "").lower() != (rid or "").lower()
            if riot_cred(cfg) and "#" in (rid or "") and not running and (stale or other or not rev.get("games")):
                run_review(cfg)
        except Exception:  # noqa: BLE001 - retried next round
            pass
        time.sleep(600)


def coach_plan(gd, cfg, rev):
    from . import coach
    games = (rev.get("games") or []) + (rev.get("other_games") or [])
    games.sort(key=lambda g: -(g.get("game_start") or 0))
    if not games:
        return {"ready": False, "reason": "Noch keine Games analysiert. Die Analyse läuft automatisch nach jedem "
                                          "Game – oder jetzt im Analyse-Tab starten."}
    cached = COACH.get("plan")
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
    plan["account"] = rev.get("account")
    plan["_n"], plan["_t"] = len(games), games[0].get("game_start")
    COACH["plan"] = plan
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
        out["gems"] = st.get("hidden_items", []) + [dict(g, icon=gd.rune_icon.get(g["id"])) for g in st.get("hidden_keystones", [])]
        for m in st.get("worst_matchups", []) + st.get("best_matchups", []):
            e = gd.champ(m.get("enemy"))
            if e:
                m["id"] = e["id"]
                m["name"] = gd.champ_name(e)
    ms.role = None
    try:
        rec = sim.recommend(c["id"], [], role, meta_stats=ms, bracket=bracket)
        out["sim"] = rec
        out["skill_order"] = rec.get("skill_order") if rec.get("available") else None
    except Exception as e:  # noqa: BLE001
        out["sim"] = {"available": False, "reason": str(e)}
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
            cs = live_state = None
            if phase == "ChampSelect":
                cs = champselect.analyze(gd, lcu.champ_select(), cfg, meta_stats(gd), current_bracket(cfg))
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
                live_state = live.analyze(gd, data, cfg.get("timers"), presets.get)
                if live_state:
                    try:
                        from . import hud
                        plan = COACH.get("plan") or {}
                        f = (plan.get("focus") or [None])[0]
                        focus = {"title": f["title"], "target": f["target"]} if f else None
                        step = ((last_cs.get("gameplan") or {}).get("steps") or [None])[0]
                        live_state["hud"] = hud.build(live_state, gd, live_state.get("_gold"), live_state.get("_owned") or [],
                                                      focus, step)
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

        def do_GET(self):
            if self.path == "/i18n_en.js":
                with open(os.path.join(STATIC, "i18n_en.js"), "rb") as f:
                    return self._send(200, f.read(), "application/javascript")
            if self.path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html")
            if self.path == "/api/state":
                with LOCK:
                    from . import i18n
                    return self._send(200, dict(STATE, version=gd.version, lang=i18n.LANG["code"]))
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
                    body = "<h1>Angemeldet ✓</h1><p>Du kannst dieses Fenster schließen und zu Jungle Coach zurückkehren.</p>"
                else:
                    body = "<h1>Anmeldung fehlgeschlagen</h1><p>Bitte in der App erneut versuchen.</p>"
                html = ("<!doctype html><meta charset=utf-8><title>Jungle Coach</title><body style='font:16px -apple-system,"
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
                ms = meta.MetaStats(gd, ms.db)
                lst = ms.tierlist(min_games=int((q.get("min") or [20])[0]), bracket=current_bracket(cfg), role=role)
                for t in lst:
                    c = gd.champ(t["champion"])
                    t["name"] = gd.champ_name(c) if c else t["champion"]
                return self._send(200, {"role": role, "list": lst, "patch": ms.current_patch(),
                                        "bracket": current_bracket(cfg), "version": gd.version})
            if self.path.startswith("/api/champion"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                ref = (q.get("id") or [""])[0]
                c = gd.champ(ref) or gd.champ_by_name.get(ref.lower())
                if not c:
                    return self._send(404, {"error": f"Champion „{ref}“ nicht gefunden"})
                return self._send(200, champion_page(gd, cfg, c, (q.get("role") or [""])[0].upper() or None))
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
                parts = self.path.split("/")
                if len(parts) == 4 and ".." not in parts[2] + parts[3] and parts[3].endswith(".mp4"):
                    fp = os.path.join(recorder.REC_DIR, parts[2], parts[3])
                    if os.path.exists(fp):
                        with open(fp, "rb") as f:
                            return self._send(200, f.read(), "video/mp4")
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
            if self.path == "/api/test-key":
                return self._test_key(self._body().get("riot_api_key"))
            if self.path == "/api/config":
                body = self._body()
                for k in EDITABLE:
                    if k in body and not (k == "riot_api_key" and not body[k]):
                        cfg[k] = body[k].strip() if isinstance(body[k], str) else body[k]
                if body.get("riot_api_key"):
                    cfg.pop("_key_from_build", None)  # user's own key wins over the built-in one
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
    lcu = LCU(cfg.get("league_path"))
    if os.path.exists(REVIEW_CACHE):
        try:
            with open(REVIEW_CACHE, encoding="utf-8") as f:
                STATE["review"] = json.load(f)
        except (OSError, ValueError):
            pass
    threading.Thread(target=poll_loop, args=(gd, cfg, lcu), daemon=True).start()

    def cloud_loop():
        from jc import sim
        while True:
            sim.refresh()  # patch data for the build simulation (once a day)
            if meta_cloud.update(cfg):
                META["stats"] = None  # reopen with the new games
            time.sleep(1800)
    threading.Thread(target=cloud_loop, daemon=True).start()
    threading.Thread(target=auto_review_loop, args=(cfg,), daemon=True).start()
    port = cfg.get("port", 8765)
    host = "0.0.0.0" if cfg.get("allow_phone", False) else "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), make_handler(gd, cfg, lcu))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://localhost:{port}"
    print(f"Jungle Coach läuft: {url}")
    return url


def main():
    start()
    threading.Event().wait()


if __name__ == "__main__":
    main()
