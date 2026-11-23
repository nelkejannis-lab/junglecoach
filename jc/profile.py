"""Player profile without the League client – everything from the official Riot API.

Rank (Solo/Flex), level, icon, champion mastery, match history, champion and role stats.
Works for any Riot ID (own account, friends, students). Match details are cached on disk.
"""
import json
import os
import time
import urllib.parse

from .paths import DATA_DIR
from .review import RiotAPI

CACHE = os.path.join(DATA_DIR, "cache", "matches")
QUEUES = {420: "Solo/Duo", 440: "Flex", 400: "Normal Draft", 430: "Normal Blind", 490: "Quickplay",
          450: "ARAM", 1700: "Arena", 1900: "URF", 900: "ARURF", 0: "Custom", 1300: "Nexus Blitz",
          480: "Swiftplay", 720: "ARAM Clash", 700: "Clash"}
ROLES = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Support"}
_mem = {}


def _match(api, mid):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, mid + ".json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    m = api.get(f"/lol/match/v5/matches/{mid}")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(m, f)
    return m


def summarize_match(m, puuid, gd=None):
    info = m["info"]
    me = next(p for p in info["participants"] if p["puuid"] == puuid)
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
            "level": me.get("champLevel")}


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


def fetch(api_key, platform, riot_id, gd=None, count=20, queue=None, max_age=120):
    key = (riot_id.lower(), count, queue)
    if key in _mem and time.time() - _mem[key]["_t"] < max_age:
        return _mem[key]
    api = RiotAPI(api_key, platform)
    name, tag = riot_id.split("#", 1)
    acct = api.get(f"/riot/account/v1/accounts/by-riot-id/{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}")
    puuid = acct["puuid"]
    summ = api.get_platform(f"/lol/summoner/v4/summoners/by-puuid/{puuid}")
    leagues = api.get_platform(f"/lol/league/v4/entries/by-puuid/{puuid}")
    try:
        mastery = api.get_platform(f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top", {"count": 5})
    except Exception:
        mastery = []
    params = {"count": count}
    if queue:
        params["queue"] = queue
    ids = api.get(f"/lol/match/v5/matches/by-puuid/{puuid}/ids", params)
    games = [summarize_match(_match(api, mid), puuid, gd) for mid in ids]
    champs, roles = aggregate(games)
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
    valid = [g for g in games if not g["remake"]]
    out = {"riot_id": f"{acct.get('gameName', name)}#{acct.get('tagLine', tag)}", "puuid": puuid,
           "level": summ.get("summonerLevel"), "icon": summ.get("profileIconId"), "ranks": ranks,
           "masteries": masteries, "games": games, "champions": champs, "roles": roles,
           "recent": {"games": len(valid), "wins": sum(g["win"] for g in valid),
                      "kda": round(sum(g["kills"] + g["assists"] for g in valid) / max(1, sum(g["deaths"] for g in valid)), 2),
                      "kp": round(sum(g["kp"] for g in valid) / max(1, len(valid)))},
           "version": gd.version if gd else None, "_t": time.time()}
    _mem[key] = out
    return out
