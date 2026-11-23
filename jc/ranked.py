"""Ranked companion: LP tracker (from the League client), trends and matchups from analysed games."""
import json
import os
import time

from .paths import DATA_DIR

LP_FILE = os.path.join(DATA_DIR, "lp_history.json")
TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND"]
DIVS = {"IV": 0, "III": 1, "II": 2, "I": 3}


def abs_lp(tier, div, lp):
    tier = (tier or "").upper()
    if tier in TIERS:
        return TIERS.index(tier) * 400 + DIVS.get(div, 0) * 100 + lp
    if tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        return 2800 + lp
    return None


def load_history():
    try:
        with open(LP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def snapshot(lcu, history=None, now=None, persist=True):
    """Reads Solo/Duo rank from the client; appends to history if anything changed. Returns history."""
    history = load_history() if history is None else history
    stats = lcu.get("/lol-ranked/v1/current-ranked-stats") if lcu else None
    if not stats:
        return history
    q = (stats.get("queueMap") or {}).get("RANKED_SOLO_5x5") or \
        next((x for x in stats.get("queues", []) if x.get("queueType") == "RANKED_SOLO_5x5"), None)
    if not q or not q.get("tier") or q.get("tier") in ("NONE", ""):
        return history
    entry = {"ts": now or time.time(), "tier": q["tier"], "division": q.get("division", ""),
             "lp": q.get("leaguePoints", 0), "wins": q.get("wins", 0), "losses": q.get("losses", 0)}
    entry["abs"] = abs_lp(entry["tier"], entry["division"], entry["lp"])
    last = history[-1] if history else None
    if not last or any(last[k] != entry[k] for k in ("tier", "division", "lp", "wins", "losses")):
        history.append(entry)
        if persist:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(LP_FILE, "w", encoding="utf-8") as f:
                json.dump(history[-2000:], f)
    return history


def summary(history, now=None):
    if not history:
        return None
    now = now or time.time()
    cur = history[-1]
    day0 = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))

    def delta_since(ts):
        before = [h for h in history if h["ts"] < ts]
        ref = before[-1] if before else history[0]
        return (cur["abs"] or 0) - (ref["abs"] or 0)

    games = []  # LP change per game: consecutive snapshots where the game count changed
    for a, b in zip(history, history[1:]):
        if (b["wins"] + b["losses"]) > (a["wins"] + a["losses"]) and a["abs"] is not None and b["abs"] is not None:
            games.append({"ts": b["ts"], "win": b["wins"] > a["wins"], "lp": b["abs"] - a["abs"]})
    streak = 0
    for g in reversed(games):
        if streak == 0 or (streak > 0) == g["win"]:
            streak += 1 if g["win"] else -1
        else:
            break
    wins = [g["lp"] for g in games if g["win"]]
    losses = [g["lp"] for g in games if not g["win"]]
    total = cur["wins"] + cur["losses"]
    return {"tier": cur["tier"], "division": cur["division"], "lp": cur["lp"], "wins": cur["wins"],
            "losses": cur["losses"], "winrate": round(100 * cur["wins"] / total) if total else 0,
            "today": delta_since(day0), "week": delta_since(now - 7 * 86400), "streak": streak,
            "avg_win_lp": round(sum(wins) / len(wins)) if wins else None,
            "avg_loss_lp": round(sum(losses) / len(losses)) if losses else None,
            "recent": games[-10:][::-1],
            "chart": [{"ts": h["ts"], "abs": h["abs"]} for h in history[-60:] if h["abs"] is not None]}


def improvements(games):
    """Average deep-analysis category scores + trend (older half vs newer half)."""
    deep = [g["deep"] for g in games if g.get("deep")]
    if not deep:
        return []
    names = [c["name"] for c in deep[0]["categories"]]
    newest_first = deep  # review lists newest games first
    half = max(1, len(deep) // 2)
    out = []
    for n in names:
        vals = [next(c["score"] for c in d["categories"] if c["name"] == n) for d in newest_first]
        avg = round(sum(vals) / len(vals))
        new = sum(vals[:half]) / half
        old = sum(vals[half:]) / max(1, len(vals) - half) if len(vals) > 1 else new
        out.append({"name": n, "score": avg, "trend": round(new - old) if len(vals) > 1 else 0})
    return sorted(out, key=lambda x: x["score"])


def own_matchups(games):
    by = {}
    for g in games:
        e = g.get("enemy_jungler")
        if not e or e == "?":
            continue
        m = by.setdefault(e, {"enemy": e, "games": 0, "wins": 0, "gd10": []})
        m["games"] += 1
        m["wins"] += bool(g.get("win"))
        if g.get("gold_diff_10") is not None:
            m["gd10"].append(g["gold_diff_10"])
    out = []
    for m in by.values():
        out.append({"enemy": m["enemy"], "games": m["games"], "winrate": round(100 * m["wins"] / m["games"]),
                    "avg_gd10": round(sum(m["gd10"]) / len(m["gd10"])) if m["gd10"] else None})
    return sorted(out, key=lambda x: (x["winrate"], x["avg_gd10"] or 0))
