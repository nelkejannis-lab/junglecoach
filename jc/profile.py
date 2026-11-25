"""Player profile without the League client – everything from the official Riot API.

Rank (Solo/Flex), level, icon, champion mastery, match history, champion and role stats.
Works for any Riot ID (own account, friends, students). Match details are cached on disk.
"""
import json
import os
import threading
import time
import urllib.parse

from .paths import DATA_DIR
from .review import RiotAPI

CACHE = os.path.join(DATA_DIR, "cache", "matches")
QUEUES = {420: "Solo/Duo", 440: "Flex", 400: "Normal Draft", 430: "Normal Blind", 490: "Quickplay",
          450: "ARAM", 1700: "Arena", 1900: "URF", 900: "ARURF", 0: "Custom", 1300: "Nexus Blitz",
          480: "Swiftplay", 720: "ARAM Clash", 700: "Clash"}
# filter -> Riot match-ids parameters (+ optional client-side queue whitelist)
FILTERS = {"all": ({}, None), "solo": ({"queue": 420}, None), "flex": ({"queue": 440}, None),
           "ranked": ({"type": "ranked"}, None),
           "normal": ({"type": "normal"}, {400, 430, 480, 490}), "aram": ({"queue": 450}, None),
           "arena": ({"queue": 1700}, None)}
SPELLS = {1: "SummonerBoost", 3: "SummonerExhaust", 4: "SummonerFlash", 6: "SummonerHaste", 7: "SummonerHeal",
          11: "SummonerSmite", 12: "SummonerTeleport", 13: "SummonerMana", 14: "SummonerDot", 21: "SummonerBarrier",
          32: "SummonerSnowball", 2202: "SummonerFlash", 2201: "SummonerFlash"}
ROLES = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Support"}
_mem = {}


class PuuidMismatch(Exception):
    """The PUUID came from another Riot key (PUUIDs are encrypted per key) – look the Riot ID up again."""


def _match(api, mid, puuid=None):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, mid + ".json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                m = json.load(f)
            # a match saved with another Riot key has other (encrypted) PUUIDs -> fetch it again
            if not puuid or any(x.get("puuid") == puuid for x in m["info"]["participants"]):
                return m
        except Exception:  # noqa: BLE001 - half-written / broken file: fetch again
            pass
    m = api.get(f"/lol/match/v5/matches/{mid}")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f)
    os.replace(tmp, p)   # never leave a half-written file behind
    return m


def summarize_match(m, puuid, gd=None):
    info = m["info"]
    me = next((p for p in info["participants"] if p["puuid"] == puuid), None)
    if me is None:
        raise PuuidMismatch(m["metadata"]["matchId"])
    mins = max(1, info.get("gameDuration", 0) / 60)
    champ = me["championName"]
    if gd:  # normalise odd API names (e.g. "FiddleSticks") to Data Dragon ids for icons
        c = gd.champ(champ) or gd.champ_by_id.get(champ) or next(
            (v for k, v in gd.champ_by_id.items() if k.lower() == champ.lower()), None)
        champ = c["id"] if c else champ
        cname = gd.champ_name(c) if c else champ
    else:
        cname = champ
    team_kills = sum(p["kills"] for p in info["participants"] if p["teamId"] == me["teamId"]) or 1
    team = [p for p in info["participants"] if p["teamId"] == me["teamId"]]
    tsum = lambda k: sum(p.get(k, 0) for p in team) or 1
    try:
        from . import gamescore
        sc = (gamescore.scores(info).get(puuid) or {}).get("score")
    except Exception:  # noqa: BLE001
        sc = None
    return {"match_id": m["metadata"]["matchId"], "queue": QUEUES.get(info.get("queueId"), "Andere"),
            "queue_id": info.get("queueId"), "champion": champ, "name": cname, "win": me["win"],
            "remake": info.get("gameDuration", 0) < 300,
            "kills": me["kills"], "deaths": me["deaths"], "assists": me["assists"],
            "kda": round((me["kills"] + me["assists"]) / max(1, me["deaths"]), 2),
            "kp": round(100 * (me["kills"] + me["assists"]) / team_kills),
            "cs": me["totalMinionsKilled"] + me.get("neutralMinionsKilled", 0),
            "cs_min": round((me["totalMinionsKilled"] + me.get("neutralMinionsKilled", 0)) / mins, 1),
            "vision": me.get("visionScore", 0), "role": ROLES.get(me.get("teamPosition"), ""),
            "items": [me.get(f"item{i}", 0) for i in range(7)], "duration": round(mins),
            "ended": (info.get("gameEndTimestamp") or info.get("gameCreation", 0)) / 1000,
            "level": me.get("champLevel"),
            # for the player analysis (same fields the mobile app builds from the match)
            "pos": me.get("teamPosition") or "", "score": sc,
            "gpm": round(me.get("goldEarned", 0) / mins), "vpm": round(me.get("visionScore", 0) / mins, 2),
            "dmg_share": round(me.get("totalDamageDealtToChampions", 0) / tsum("totalDamageDealtToChampions"), 3),
            "obj_share": round(me.get("damageDealtToObjectives", 0) / tsum("damageDealtToObjectives"), 3)}


def aggregate(games):
    champs, roles = {}, {}
    for g in games:
        if g["remake"]:
            continue
        c = champs.setdefault(g["champion"], {"champion": g["champion"], "name": g.get("name", g["champion"]), "games": 0, "wins": 0, "k": 0, "d": 0, "a": 0, "cs_min": 0})
        c["games"] += 1; c["wins"] += g["win"]; c["k"] += g["kills"]; c["d"] += g["deaths"]; c["a"] += g["assists"]
        c["cs_min"] += g["cs_min"]
        if g["role"]:
            roles[g["role"]] = roles.get(g["role"], 0) + 1
    out = []
    for c in champs.values():
        n = c["games"]
        out.append({"champion": c["champion"], "name": c["name"], "games": n, "winrate": round(100 * c["wins"] / n),
                    "kda": round((c["k"] + c["a"]) / max(1, c["d"]), 2),
                    "avg": f"{c['k'] / n:.1f} / {c['d'] / n:.1f} / {c['a'] / n:.1f}",
                    "cs_min": round(c["cs_min"] / n, 1)})
    total = sum(roles.values()) or 1
    return (sorted(out, key=lambda x: (-x["games"], -x["winrate"])),
            sorted(({"role": r, "share": round(100 * n / total)} for r, n in roles.items()), key=lambda x: -x["share"]))


def _ids(api, puuid, flt, start, count):
    params, allowed = FILTERS.get(flt, FILTERS["all"])
    out, offset = [], start
    while len(out) < count:
        batch = api.get(f"/lol/match/v5/matches/by-puuid/{puuid}/ids", {**params, "start": offset, "count": 20})
        if not batch:
            break
        offset += len(batch)
        out += batch
        if allowed is None:
            break
    return out[:count] if allowed is None else out, offset


PROFILES = os.path.join(DATA_DIR, "cache", "profiles")
_refreshing = set()


def _disk_file(key):
    safe = "".join(ch if ch.isalnum() else "_" for ch in f"{key[0]}_{key[1]}")
    return os.path.join(PROFILES, safe + ".json")


def _save_disk(key, out):
    try:
        os.makedirs(PROFILES, exist_ok=True)
        data = dict(out, _seen=sorted(out.get("_seen", [])))
        with open(_disk_file(key) + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(_disk_file(key) + ".tmp", _disk_file(key))
    except (OSError, TypeError, ValueError):
        pass


def _drop_disk(key):
    try:
        os.remove(_disk_file(key))
    except OSError:
        pass


def _load_disk(key):
    try:
        with open(_disk_file(key), encoding="utf-8") as f:
            out = json.load(f)
        from .gamescore import VERSION
        if out.get("_score_version") != VERSION:
            return None
        out["_seen"] = set(out.get("_seen", []))
        if out.get("games") and "pos" not in out["games"][0]:
            return None   # saved by an older version (no analysis fields)
        return out
    except (OSError, ValueError):
        return None


def _summaries(api, ids, puuid, gd):
    """Match summaries in parallel (disk cache first) – the slow part of loading a profile."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(lambda mid: summarize_match(_match(api, mid, puuid), puuid, gd), ids))


def fetch(api_key, platform, riot_id, gd=None, count=20, queue=None, max_age=120, more=False, allow_stale=True):
    try:
        return _fetch(api_key, platform, riot_id, gd, count, queue, max_age, more, allow_stale)
    except Exception as e:  # noqa: BLE001
        # PUUIDs are encrypted per Riot key: after a key change the saved one gives 400 "decrypting" or
        # matches without the player -> forget the saved PUUID and look the Riot ID up again (once)
        if not (isinstance(e, PuuidMismatch) or "400" in str(e) or "decrypt" in str(e).lower()):
            raise
        flt = queue if queue in FILTERS else "all"
        for f in FILTERS:   # every cached view of this player carries the old PUUID
            _mem.pop((riot_id.lower(), f), None)
            _drop_disk((riot_id.lower(), f))
        return _fetch(api_key, platform, riot_id, gd, count, flt, max_age, False, False)


REFRESH_UNTIL = 0

def _fetch(api_key, platform, riot_id, gd=None, count=20, queue=None, max_age=120, more=False, allow_stale=True):
    """queue: filter key (all/solo/flex/ranked/normal/aram/arena). more=True appends the next page.
    allow_stale: show the last saved profile instantly and refresh it in the background."""
    if time.time() < REFRESH_UNTIL:
        max_age, allow_stale = 0, False
    flt = queue if queue in FILTERS else "all"
    key = (riot_id.lower(), flt)
    from .gamescore import VERSION
    cached = _mem.get(key)
    if cached and cached.get("_score_version") != VERSION:
        _mem.pop(key, None)
        cached = None
    if cached is None and not more:
        disk = _load_disk(key)
        if disk:
            _mem[key] = cached = disk
    if cached and not more and allow_stale and time.time() - cached.get("_t", 0) >= max_age:
        if key not in _refreshing:  # stale-while-revalidate
            _refreshing.add(key)

            def bg():
                try:
                    fetch(api_key, platform, riot_id, gd, count, queue, max_age, allow_stale=False)   # handles a PUUID change
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    _refreshing.discard(key)
            threading.Thread(target=bg, daemon=True).start()
        return dict(cached, stale=True)
    if cached and not more and (time.time() - cached["_t"] < max_age or LOAD_ALL.get(key, {}).get("running")):
        return cached
    api = RiotAPI(api_key, platform)
    if cached and more:
        out = cached
        ids, nxt = _ids(api, out["puuid"], flt, out["_next"], count)
        allowed = FILTERS[flt][1]
        new = _summaries(api, [mid for mid in ids if mid not in out["_seen"]], out["puuid"], gd)
        if allowed:
            new = [g for g in new if g["queue_id"] in allowed]
        out["games"] += new
        out["_seen"].update(ids)
        out["_next"] = nxt
        out["has_more"] = bool(ids)
    else:
        from concurrent.futures import ThreadPoolExecutor
        name, tag = riot_id.split("#", 1)
        if cached and cached.get("puuid"):  # Riot ID -> PUUID never changes: skip the lookup
            acct = {"puuid": cached["puuid"], "gameName": cached["riot_id"].split("#")[0],
                    "tagLine": cached["riot_id"].split("#")[-1]}
        else:
            acct = api.get(f"/riot/account/v1/accounts/by-riot-id/{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}")
        puuid = acct["puuid"]

        def mastery_top():
            try:
                return api.get_platform(f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top", {"count": 5})
            except Exception:  # noqa: BLE001
                return []
        with ThreadPoolExecutor(max_workers=4) as ex:  # the four lookups at the same time
            f_s = ex.submit(api.get_platform, f"/lol/summoner/v4/summoners/by-puuid/{puuid}")
            f_l = ex.submit(api.get_platform, f"/lol/league/v4/entries/by-puuid/{puuid}")
            f_m = ex.submit(mastery_top)
            f_i = ex.submit(_ids, api, puuid, flt, 0, count)
            summ, leagues, mastery, (ids, nxt) = f_s.result(), f_l.result(), f_m.result(), f_i.result()
        allowed = FILTERS[flt][1]
        if cached and cached.get("puuid") == puuid:  # refresh: keep everything loaded, add only new games on top
            new_ids = [i for i in ids if i not in cached["_seen"]]
            games = _summaries(api, new_ids, puuid, gd)
            if allowed:
                games = [g for g in games if g["queue_id"] in allowed]
            games += cached["games"]
            ids, nxt = list(cached["_seen"] | set(ids)), cached["_next"] + len(new_ids)
            has_more = cached.get("has_more", True)
        else:
            games = _summaries(api, ids, puuid, gd)
            if allowed:
                games = [g for g in games if g["queue_id"] in allowed]
            has_more = bool(ids)
        ranks = {}
        for e in leagues:
            q = {"RANKED_SOLO_5x5": "solo", "RANKED_FLEX_SR": "flex"}.get(e.get("queueType"))
            if q:
                n = e["wins"] + e["losses"]
                ranks[q] = {"tier": e["tier"], "division": e.get("rank", ""), "lp": e["leaguePoints"],
                            "wins": e["wins"], "losses": e["losses"], "winrate": round(100 * e["wins"] / n) if n else 0,
                            "hot_streak": e.get("hotStreak", False)}
        masteries = []
        for m in mastery:
            c = gd.champ(m.get("championId")) if gd else None
            masteries.append({"champion": c["id"] if c else str(m.get("championId")),
                              "name": gd.champ_name(c) if c else str(m.get("championId")),
                              "level": m.get("championLevel"), "points": m.get("championPoints")})
        out = {"riot_id": f"{acct.get('gameName', name)}#{acct.get('tagLine', tag)}", "puuid": puuid,
               "level": summ.get("summonerLevel"), "icon": summ.get("profileIconId"), "ranks": ranks,
               "masteries": masteries, "games": games, "filter": flt, "has_more": has_more,
               "version": gd.version if gd else None, "_next": nxt, "_seen": set(ids)}
    champs, roles = aggregate(out["games"])
    valid = [g for g in out["games"] if not g["remake"]]
    out.update(champions=champs, roles=roles, _score_version=VERSION, _t=time.time(),
               recent={"games": len(valid), "wins": sum(g["win"] for g in valid),
                       "kda": round(sum(g["kills"] + g["assists"] for g in valid) / max(1, sum(g["deaths"] for g in valid)), 2),
                       "kp": round(sum(g["kp"] for g in valid) / max(1, len(valid)))})
    out.pop("stale", None)
    _mem[key] = out
    _save_disk(key, out)
    return out


LOAD_ALL = {}  # (riot_id, filter) -> {"running", "loaded", "message"}


def load_all(api_key, platform, riot_id, gd=None, queue="all"):
    """Pages through the whole match history Riot still keeps (match-v5, about the last 2 years)
    in the background. Already downloaded games come from the disk cache, so only the first
    full load is slow (dev key: ~100 games per 2 minutes)."""
    import threading
    flt = queue if queue in FILTERS else "all"
    key = (riot_id.lower(), flt)
    st = LOAD_ALL.setdefault(key, {"running": False, "loaded": 0, "message": ""})
    if st["running"]:
        return st

    def run():
        st.update(running=True, message="lädt…")
        try:
            out = fetch(api_key, platform, riot_id, gd, 20, flt, max_age=10 ** 9)
            while out.get("has_more"):
                out = fetch(api_key, platform, riot_id, gd, 20, flt, more=True)
                st["loaded"] = len(out["games"])
                st["message"] = f"{st['loaded']} Games geladen…"
            st["message"] = f"Alle {len(out['games'])} Games geladen"
        except Exception as e:
            st["message"] = f"Abgebrochen: {e}"
        finally:
            st["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return st


def _timeline(api, mid):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, mid + "_tl.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    t = api.get(f"/lol/match/v5/matches/{mid}/timeline")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(t, f)
    return t


def _rune_icon(gd, perk_id):
    if not gd:
        return None
    for tree in gd.runes:
        if tree["id"] == perk_id:
            return tree.get("icon")
        for slot in tree["slots"]:
            for r in slot["runes"]:
                if r["id"] == perk_id:
                    return r.get("icon")
    return None


def match_detail(api_key, platform, match_id, puuid, gd=None):
    """Full scoreboard of one game; for own jungle games also the deep analysis + map replay."""
    api = RiotAPI(api_key, platform)
    m = _match(api, match_id)
    info = m["info"]
    mins = max(1, info.get("gameDuration", 0) / 60)
    teams = {}
    for t in info.get("teams", []):
        obj = t.get("objectives", {})
        teams[t["teamId"]] = {"win": t.get("win"), "players": [],
                              "bans": [b.get("championId") for b in t.get("bans", []) if b.get("championId", -1) > 0],
                              "objectives": {k: obj.get(k, {}).get("kills", 0) for k in
                                             ("champion", "dragon", "baron", "tower", "horde", "riftHerald", "atakhan", "inhibitor")}}
    for p in info["participants"]:
        c = gd.champ(p["championName"]) if gd else None
        if gd and not c:
            c = next((v for k, v in gd.champ_by_id.items() if k.lower() == p["championName"].lower()), None)
        try:
            styles = p["perks"]["styles"]
            keystone, sub = styles[0]["selections"][0]["perk"], styles[1]["style"]
        except (KeyError, IndexError):
            keystone = sub = None
        cs = p["totalMinionsKilled"] + p.get("neutralMinionsKilled", 0)
        row = {"puuid": p["puuid"], "me": p["puuid"] == puuid,
               "name": p.get("riotIdGameName") or p.get("summonerName") or "?", "tag": p.get("riotIdTagline", ""),
               "champion": c["id"] if c else p["championName"], "champion_name": gd.champ_name(c) if c else p["championName"],
               "level": p.get("champLevel"), "role": ROLES.get(p.get("teamPosition"), ""),
               "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"],
               "kda": round((p["kills"] + p["assists"]) / max(1, p["deaths"]), 2),
               "cs": cs, "cs_min": round(cs / mins, 1), "gold": p.get("goldEarned", 0),
               "damage": p.get("totalDamageDealtToChampions", 0), "taken": p.get("totalDamageTaken", 0),
               "vision": p.get("visionScore", 0), "wards": p.get("wardsPlaced", 0),
               "items": [p.get(f"item{i}", 0) for i in range(7)],
               "spells": [SPELLS.get(p.get("summoner1Id")), SPELLS.get(p.get("summoner2Id"))],
               "keystone_icon": _rune_icon(gd, keystone), "sub_icon": _rune_icon(gd, sub),
               "keystone": gd.rune_disp.get(keystone) if gd and keystone else None,
               "multikill": p.get("largestMultiKill", 0)}
        teams.setdefault(p["teamId"], {"win": p.get("win"), "players": [], "bans": [], "objectives": {}})["players"].append(row)
    order = {"Top": 0, "Jungle": 1, "Mid": 2, "Bot": 3, "Support": 4, "": 5}
    for t in teams.values():
        t["players"].sort(key=lambda r: order.get(r["role"], 5))
        t["kills"] = sum(r["kills"] for r in t["players"])
        t["gold"] = sum(r["gold"] for r in t["players"])
        t["bans"] = [gd.champ(b)["id"] if gd and gd.champ(b) else str(b) for b in t["bans"]]
    allp = [r for t in teams.values() for r in t["players"]]
    maxdmg = max((r["damage"] for r in allp), default=1) or 1
    for r in allp:
        r["damage_pct"] = round(100 * r["damage"] / maxdmg)
    from .gamescore import scores as _scores
    sc = _scores(info)
    for r in allp:
        v = sc.get(r["puuid"], {})
        r["score"], r["rank"], r["badge"] = v.get("score"), v.get("rank"), v.get("badge")
        r["mvp"] = v.get("badge") == "MVP"
    out = {"match_id": match_id, "queue": QUEUES.get(info.get("queueId"), "Andere"), "duration": round(mins),
           "ended": (info.get("gameEndTimestamp") or info.get("gameCreation", 0)) / 1000,
           "teams": [dict(teams[k], team_id=k) for k in sorted(teams)], "version": gd.version if gd else None}
    me = next((p for p in info["participants"] if p["puuid"] == puuid), None)
    if me and me.get("teamPosition") == "JUNGLE" and info.get("gameDuration", 0) >= 600:
        try:
            from . import deep, review
            tl = _timeline(api, match_id)
            r = review.review_match(m, tl, puuid)
            if r:
                r["deep"] = deep.deep_analyze(m, tl, puuid)
                out["analysis"] = r
        except Exception as e:
            out["analysis_error"] = str(e)
    return out

