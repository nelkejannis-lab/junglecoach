"""Local dashboard server: http://localhost:8765 (second monitor / phone in same Wi-Fi)."""
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib.parse

from . import champselect, deep, live, meta, meta_cloud, minimap, profile, ranked, recorder, review
from .ddragon import GameData
from .lcu import LCU

from .paths import DATA_DIR as ROOT, STATIC

STATE = {"phase": "Startet…", "champ_select": None, "live": None, "review": None, "error": None,
         "minimap": {"on": False, "status": "aus", "data": None}}
TRACKER = {"obj": None}
REC = {"obj": None}
META = {"stats": None, "crawler": None}
REVIEW_CACHE = os.path.join(ROOT, "review_cache.json")
EDITABLE = ["riot_id", "platform", "riot_api_key", "champion_pool", "auto_import_runes", "review_games", "profile_games",
            "meta_bracket", "meta_max_matches"]


def save_config(cfg):
    with open(os.path.join(ROOT, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def current_bracket(cfg):
    b = cfg.get("meta_bracket", "auto")
    if b == "auto":
        h = ranked.load_history()
        return meta.bracket_of(h[-1]["tier"]) if h else None
    return None if b in ("all", "", None) else b


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
    last_runes_key = None
    last_import = None
    last_rank = 0
    while True:
        try:
            phase = lcu.phase()
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
            mm_status, mm_data = "aus", None
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
            with LOCK:
                mm_on = STATE["minimap"]["on"]
            if mm_on:
                if not live_state:
                    mm_status = "wartet auf Spiel"
                elif not minimap.is_custom_game(lcu):
                    mm_status = "gesperrt – nur in Custom Games aktiv"
                else:
                    tr = TRACKER["obj"] = TRACKER["obj"] or minimap.MinimapTracker(gd, cfg)
                    team = live_state["_team_id"]
                    mm_data = tr.update(data, live_state["_t"], live_state["_enemy_targets"],
                                        lambda x, y: review.region(x, y, team))
                    mm_status = "aktiv"
            # ---- automatic recording + mistake clips
            rec = REC["obj"] = REC["obj"] or recorder.Recorder(cfg)
            if live_state and not rec.active and rec.cfg["enabled"]:
                rec.start(live_state["_t"])
            if live_state and rec.active:
                rec.note(live_state["mistakes"], {"champion": live_state["me"]})
            if not live_state and rec.active:
                threading.Thread(target=rec.stop, daemon=True).start()  # cutting clips takes a moment
            if not live_state and TRACKER["obj"] and TRACKER["obj"].sightings:
                TRACKER["obj"].save_session()
                TRACKER["obj"].reset()
            with LOCK:
                STATE["phase"] = "InGame" if live_state else (phase or "Client nicht gefunden")
                if cs:
                    STATE["champ_select"] = cs
                elif phase not in ("ChampSelect", "GameStart", "InProgress") and not live_state:
                    STATE["champ_select"] = None  # keep last champ select visible during the game
                STATE["live"] = live_state
                STATE["minimap"]["status"], STATE["minimap"]["data"] = mm_status, mm_data
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
            if self.path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html")
            if self.path == "/api/state":
                with LOCK:
                    return self._send(200, STATE)
            if self.path.startswith("/api/profile"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                rid = (q.get("riot_id") or [cfg.get("riot_id", "")])[0].strip()
                if not cfg.get("riot_api_key") or "xxxx" in cfg["riot_api_key"]:
                    return self._send(400, {"error": "Riot API Key fehlt – trag ihn in den Einstellungen ein."})
                if "#" not in rid:
                    return self._send(400, {"error": "Riot ID im Format Name#TAG eingeben."})
                queue = (q.get("queue") or ["all"])[0]
                if q.get("all"):
                    profile.load_all(cfg["riot_api_key"], cfg.get("platform", "euw1"), rid, gd, queue)
                try:
                    prof = profile.fetch(cfg["riot_api_key"], cfg.get("platform", "euw1"), rid, gd,
                                         int(cfg.get("profile_games", 20)), queue, more=bool(q.get("more")))
                except review.RiotNotFound:
                    return self._send(404, {"error": f"Spieler „{rid}“ nicht gefunden. Riot ID genau wie im Client "
                                                     f"(Name#TAG) und Region {cfg.get('platform', 'euw1')} prüfen."})
                except Exception as e:
                    return self._send(500, {"error": str(e)})
                solo = prof["ranks"].get("solo")
                if solo and rid.lower() == cfg.get("riot_id", "").lower():  # feed LP tracker without client
                    class _FromApi:
                        def get(self, _p):
                            return {"queueMap": {"RANKED_SOLO_5x5": {"tier": solo["tier"], "division": solo["division"],
                                    "leaguePoints": solo["lp"], "wins": solo["wins"], "losses": solo["losses"]}}}
                    ranked.snapshot(_FromApi())
                res = {k: v for k, v in prof.items() if not k.startswith("_")}
                res["load_all"] = profile.LOAD_ALL.get((rid.lower(), queue if queue in profile.FILTERS else "all"))
                return self._send(200, res)
            if self.path.startswith("/api/match"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    det = profile.match_detail(cfg["riot_api_key"], cfg.get("platform", "euw1"),
                                               q["id"][0], (q.get("puuid") or [""])[0], gd)
                    return self._send(200, det)
                except Exception as e:
                    return self._send(500, {"error": str(e)})
            if self.path.startswith("/api/test-key"):
                return self._test_key(None)
            if self.path == "/api/ranked":
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
                return self._send(200, {"summary": ranked.summary(ranked.load_history()),
                                        "improvements": ranked.improvements(games),
                                        "own_matchups": ranked.own_matchups(games), "games": len(games),
                                        "pool_worst": pool_worst})
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
            if self.path == "/api/minimap-debug":
                try:
                    import cv2
                    tr = TRACKER["obj"] or minimap.MinimapTracker(gd, cfg)
                    ok, png = cv2.imencode(".png", tr.grab())
                    return self._send(200, png.tobytes(), "image/png")
                except Exception as e:
                    return self._send(500, {"error": f"Screenshot fehlgeschlagen: {e}"})
            if self.path.startswith("/api/review"):
                if not cfg.get("riot_api_key") or "xxxx" in cfg["riot_api_key"]:
                    return self._send(400, {"error": "Riot API Key fehlt – trag ihn in den Einstellungen ein."})
                if "#" not in cfg.get("riot_id", ""):
                    return self._send(400, {"error": "Riot ID fehlt – trag sie in den Einstellungen ein (Name#TAG)."})
                try:
                    res = review.run(cfg["riot_api_key"], cfg.get("platform", "euw1"), cfg["riot_id"],
                                     cfg.get("review_games", 20))
                    attach_clips(res["games"], recorder.list_sessions())
                    with open(REVIEW_CACHE, "w", encoding="utf-8") as f:
                        json.dump(res, f)
                    with LOCK:
                        STATE["review"] = res
                    return self._send(200, res)
                except Exception as e:
                    return self._send(500, {"error": str(e)})
            self._send(404, {"error": "not found"})

        def _test_key(self, new_key):
            steps = []
            key = (new_key or cfg.get("riot_api_key", "")).strip()
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
                if not cfg.get("riot_api_key") or "xxxx" in cfg["riot_api_key"]:
                    return self._send(400, {"error": "Erst Riot API Key in den Einstellungen eintragen."})
                cr = META["crawler"] = META["crawler"] or meta.Crawler(cfg)
                if cr.state["running"]:
                    cr.stop()
                    return self._send(200, {"running": False})
                cr.state["matches"] = 0
                cr.start()
                META["stats"] = None  # reopen after new data
                return self._send(200, {"running": True})
            if self.path == "/api/minimap-toggle":
                with LOCK:
                    STATE["minimap"]["on"] = not STATE["minimap"]["on"]
                    return self._send(200, {"on": STATE["minimap"]["on"]})
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
    gd = GameData(cfg.get("display_language", "de_DE"))
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
        while True:
            if meta_cloud.update(cfg):
                META["stats"] = None  # reopen with the new games
            time.sleep(1800)
    threading.Thread(target=cloud_loop, daemon=True).start()
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
