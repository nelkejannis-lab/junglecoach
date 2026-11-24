"""Who played best in one game: a 1-10 performance score for all 10 players + ranking (MVP = best of the winning team).

Only uses the finished match (Riot match-v5). Role-aware: supports are judged more on kill participation
and vision, junglers more on objectives, tops also on damage soaked. Each metric is turned into 0..1 against a
"very good" reference value, weighted per role, minus a penalty for the player's share of the team's deaths.
The same formula runs in the mobile/web app (server/public/m/index.html, gameScores) – keep both in sync.
"""

W = {  # weights per role (deaths is a penalty)
    "DEFAULT": {"kp": .20, "kda": .18, "dmg": .20, "gold": .10, "cs": .10, "vis": .07, "tank": .05, "obj": .05, "deaths": .12},
    "TOP": {"kp": .15, "kda": .16, "dmg": .18, "gold": .10, "cs": .12, "vis": .06, "tank": .12, "obj": .06, "deaths": .12},
    "JUNGLE": {"kp": .22, "kda": .16, "dmg": .14, "gold": .08, "cs": .08, "vis": .08, "tank": .08, "obj": .14, "deaths": .12},
    "UTILITY": {"kp": .26, "kda": .18, "dmg": .08, "gold": .04, "cs": .0, "vis": .24, "tank": .08, "obj": .04, "deaths": .10},
}
REF = {"kp": .8, "kda": 8, "dmg": .4, "gold": .3, "cs": 9, "tank": .35, "obj": .4, "deaths": .4}


def _cap(x):
    return max(0.0, min(1.0, x))


def scores(info):
    """{puuid: {"score": 7.4, "rank": 1, "badge": "MVP"|None}} for every participant of match info."""
    P = info.get("participants") or []
    if len(P) < 2:
        return {}
    mins = max(1.0, info.get("gameDuration", 0) / 60)
    team = {}
    for p in P:
        t = team.setdefault(p["teamId"], {"k": 0, "d": 0, "dmg": 0, "gold": 0, "tank": 0, "obj": 0})
        t["k"] += p.get("kills", 0)
        t["d"] += p.get("deaths", 0)
        t["dmg"] += p.get("totalDamageDealtToChampions", 0)
        t["gold"] += p.get("goldEarned", 0)
        t["tank"] += p.get("totalDamageTaken", 0) + p.get("damageSelfMitigated", 0)
        t["obj"] += p.get("damageDealtToObjectives", 0)
    out = {}
    for p in P:
        t = team[p["teamId"]]
        role = p.get("teamPosition") or ""
        w = W.get(role, W["DEFAULT"])
        k, d, a = p.get("kills", 0), p.get("deaths", 0), p.get("assists", 0)
        cs = p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
        x = {
            "kp": _cap((k + a) / max(1, t["k"]) / REF["kp"]),
            "kda": _cap((k + a) / max(1, d) / REF["kda"]),
            "dmg": _cap(p.get("totalDamageDealtToChampions", 0) / max(1, t["dmg"]) / REF["dmg"]),
            "gold": _cap(p.get("goldEarned", 0) / max(1, t["gold"]) / REF["gold"]),
            "cs": _cap(cs / mins / REF["cs"]),
            "vis": _cap(p.get("visionScore", 0) / mins / (2.5 if role == "UTILITY" else 1.5)),
            "tank": _cap((p.get("totalDamageTaken", 0) + p.get("damageSelfMitigated", 0)) / max(1, t["tank"]) / REF["tank"]),
            "obj": _cap(p.get("damageDealtToObjectives", 0) / max(1, t["obj"]) / REF["obj"]),
        }
        pos = sum(v for kk, v in w.items() if kk != "deaths")
        raw = sum(w[kk] * x[kk] for kk in x) - w["deaths"] * _cap(d / max(1, t["d"]) / REF["deaths"])
        s = 8.4 * raw / pos + 1.2 + (0.4 if p.get("win") else 0)   # an average game lands around 5-6, a stomp ~9.5
        out[p["puuid"]] = {"score": round(max(1.0, min(10.0, s)), 1), "win": bool(p.get("win"))}
    order = sorted(out, key=lambda u: -out[u]["score"])
    for i, u in enumerate(order):
        out[u]["rank"] = i + 1
        out[u]["badge"] = None
    best_win = next((u for u in order if out[u]["win"]), None)
    if best_win:
        out[best_win]["badge"] = "MVP"
    for v in out.values():
        v.pop("win", None)
    return out
