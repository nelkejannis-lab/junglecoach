"""Game score v2: a 1-10 performance score for all 10 players + ranking (MVP = best of the winning team).

Built so the SAME performance always gets the SAME number:
* no win bonus (the result is shown separately), no stretching around the lobby average – every player is
  compared with a fixed reference of a good Silver–Platinum player of HIS role (5.5 = that reference);
* five independent categories, every stat counted once: resources (CS, gold share), fighting (damage share,
  kill participation – supports: kill participation + heals/shields), objectives (damage to structures/monsters),
  vision, survival (deaths per 10 min – the only place deaths count);
* role weights: support farm is not judged, an ADC is judged on resources and damage, a jungler on objectives …;
* damage TAKEN is not rewarded (can be bad play), ARAM/Arena and other modes get no score, unknown roles
  use neutral weights instead of pretending to be Mid.
The same formula runs in the mobile/web app (server/public/m/index.html, gameScores) – keep both in sync,
tests/test_gamescore.py has shared reference cases.
"""
VERSION = 3
SR_QUEUES = {400, 420, 430, 440, 490, 700, 0}   # Summoner's Rift (draft, solo, blind, flex, quickplay, clash, custom)

REF = {   # "good" = 1.0 per role
    "TOP": {"csm": 6.6, "gold": .22, "dmg": .23, "kp": .40, "obj": .25, "vpm": .55, "d10": 1.6},
    "JUNGLE": {"csm": 5.5, "gold": .21, "dmg": .16, "kp": .58, "obj": .32, "vpm": .95, "d10": 1.5},
    "MIDDLE": {"csm": 6.9, "gold": .22, "dmg": .26, "kp": .52, "obj": .16, "vpm": .60, "d10": 1.5},
    "BOTTOM": {"csm": 7.1, "gold": .23, "dmg": .27, "kp": .52, "obj": .22, "vpm": .45, "d10": 1.45},
    "UTILITY": {"csm": 1.0, "gold": .13, "dmg": .09, "kp": .58, "obj": .05, "vpm": 1.9, "d10": 1.75, "sup": 350},
    "NONE": {"csm": 5.5, "gold": .20, "dmg": .20, "kp": .52, "obj": .20, "vpm": .8, "d10": 1.55},
}
W = {   # category weights per role (sum 1)
    "TOP": {"res": .25, "fight": .25, "obj": .20, "vis": .08, "surv": .22},
    "JUNGLE": {"res": .18, "fight": .25, "obj": .25, "vis": .12, "surv": .20},
    "MIDDLE": {"res": .25, "fight": .30, "obj": .10, "vis": .10, "surv": .25},
    "BOTTOM": {"res": .30, "fight": .30, "obj": .12, "vis": .05, "surv": .23},
    "UTILITY": {"res": .00, "fight": .35, "obj": .05, "vis": .32, "surv": .28},
    "NONE": {"res": .22, "fight": .28, "obj": .15, "vis": .12, "surv": .23},
}
LABEL = {"res": "Farm & Gold", "fight": "Kämpfe", "obj": "Objectives", "vis": "Vision", "surv": "Überleben"}


def _r(v, ref):
    return max(0.0, min(2.0, v / ref)) if ref else 1.0


def scores(info):
    """{puuid: {"score", "rank", "badge", "cats": {cat: 0..2}, "why": [best, worst]}}; {} for other modes."""
    P = info.get("participants") or []
    if len(P) < 2 or (info.get("queueId") not in SR_QUEUES and info.get("queueId") is not None) or info.get("gameMode") in ("ARAM", "CHERRY"):
        return {}
    mins = max(1.0, info.get("gameDuration", 0) / 60)
    team = {}
    for p in P:
        t = team.setdefault(p["teamId"], {"k": 0, "dmg": 0, "gold": 0, "obj": 0})
        t["k"] += p.get("kills", 0)
        t["dmg"] += p.get("totalDamageDealtToChampions", 0)
        t["gold"] += p.get("goldEarned", 0)
        t["obj"] += p.get("damageDealtToObjectives", 0)
    out = {}
    for p in P:
        t = team[p["teamId"]]
        role = p.get("teamPosition") if p.get("teamPosition") in W else "NONE"
        R, w = REF[role], W[role]
        k, d, a = p.get("kills", 0), p.get("deaths", 0), p.get("assists", 0)
        kp = (k + a) / max(1, t["k"])
        dmg = p.get("totalDamageDealtToChampions", 0) / max(1, t["dmg"])
        cs = (p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)) / mins
        c = {
            "res": .6 * _r(cs, R["csm"]) + .4 * _r(p.get("goldEarned", 0) / max(1, t["gold"]), R["gold"]),
            "obj": _r(p.get("damageDealtToObjectives", 0) / max(1, t["obj"]), R["obj"]),
            "vis": _r(p.get("visionScore", 0) / mins, R["vpm"]),
            "surv": _r(R["d10"], max(0.5, d / mins * 10)),
        }
        if role == "UTILITY":   # engage AND enchanter supports: whichever contribution is bigger
            sup = (p.get("totalHealsOnTeammates", 0) + p.get("totalDamageShieldedOnTeammates", 0)) / mins
            c["fight"] = .6 * _r(kp, R["kp"]) + .4 * max(_r(dmg, R["dmg"]), _r(sup, R["sup"]))
        else:
            c["fight"] = .5 * _r(dmg, R["dmg"]) + .5 * _r(kp, R["kp"])
        mates = [q for q in P if q["teamId"] == p["teamId"]]
        def has(fields):
            return all(p.get(f) is not None for f in fields)
        def team_has(fields):
            return all(all(q.get(f) is not None for f in fields) for q in mates)
        available = {
            "res": has(("totalMinionsKilled", "neutralMinionsKilled", "goldEarned")) and team_has(("goldEarned",)) and t["gold"] > 0,
            "fight": has(("kills", "assists", "totalDamageDealtToChampions")) and team_has(("kills", "totalDamageDealtToChampions")) and t["k"] > 0 and t["dmg"] > 0,
            "obj": has(("damageDealtToObjectives",)) and team_has(("damageDealtToObjectives",)) and t["obj"] > 0,
            "vis": has(("visionScore",)),
            "surv": has(("deaths",)),
        }
        if role == "UTILITY":
            available["fight"] = available["fight"] and has(("totalHealsOnTeammates", "totalDamageShieldedOnTeammates"))
        active = {x: w[x] for x in w if w[x] > 0 and available[x]}
        coverage = sum(active.values())
        if not coverage:
            continue
        rr = sum(active[x] * c[x] for x in active) / coverage
        used = sorted(active, key=lambda x: w[x] * (c[x] - 1))
        out[p["puuid"]] = {"score": round(max(1.0, min(10.0, 5.5 + (rr - 1) * 4.5)), 1), "win": bool(p.get("win")),
                           "version": VERSION, "coverage": round(coverage, 2), "reference": "fixed_role_model",
                           "unavailable": [LABEL[x] for x in w if w[x] > 0 and not available[x]],
                           "cats": {x: round(c[x], 2) for x in active},
                           "why": [LABEL[used[-1]], LABEL[used[0]]]}
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
