"""Saved friends: quick stats, online status from the League client and a coach analysis per friend.

Everything comes from official sources: Riot API (match-v5, league-v4) and the local LCU chat list
(the same friend list you see in the client). Data is cached; loading runs in the background so the
UI never waits on the network.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import profile, review

SUMMARY = {}      # riot_id.lower() -> summary dict
COACH = {}        # riot_id.lower() -> {"status": "running"|"done"|"error", "plan": ..., "at": ts, "error": str}
_pool = ThreadPoolExecutor(max_workers=3)
_lock = threading.Lock()
_busy = set()


def norm(rid):
    return (rid or "").strip()


def valid(rid):
    rid = norm(rid)
    return "#" in rid and 3 <= len(rid) <= 40 and not rid.startswith("#") and not rid.endswith("#")


def add(cfg, rid):
    rid = norm(rid)
    if not valid(rid):
        return False
    lst = cfg.setdefault("friends", [])
    if rid.lower() not in (f.lower() for f in lst) and len(lst) < 50:
        lst.append(rid)
    return True


def remove(cfg, rid):
    cfg["friends"] = [f for f in cfg.get("friends", []) if f.lower() != norm(rid).lower()]


def _avg_score(games):
    # Moosi-Score: average game score of the last 10 real games – same rule as moosiScore() in the apps
    sc = [g["score"] for g in sorted(games, key=lambda g: -(g.get("ended") or 0))[:20]
          if g.get("score") is not None and not g.get("remake")][:10]
    return int(sum(round(s * 10) for s in sc) / len(sc) + 0.5) / 10 if sc else None


def summarize(prof):
    solo = (prof.get("ranks") or {}).get("solo") or (prof.get("ranks") or {}).get("flex")
    champs = prof.get("champions") or []
    roles = prof.get("roles") or []
    valid_games = [g for g in sorted(prof.get("games") or [], key=lambda g: -(g.get("ended") or 0))[:20] if not g.get("remake")]
    rec = {"games": len(valid_games), "wins": sum(bool(g.get("win")) for g in valid_games),
           "kda": round(sum(g.get("kills", 0) + g.get("assists", 0) for g in valid_games) /
                        max(1, sum(g.get("deaths", 0) for g in valid_games)), 2)} if valid_games else {}

    return {"riot_id": prof.get("riot_id"), "icon": prof.get("icon"), "level": prof.get("level"),
            "rank": {"tier": solo["tier"], "division": solo["division"], "lp": solo["lp"], "winrate": solo["winrate"]}
            if solo else None,
            "top": [c.get("champion") for c in champs[:3] if c.get("champion")],
            "role": (roles[0].get("role") if roles and isinstance(roles[0], dict) else None),
            "recent": {"games": rec.get("games", 0), "wins": rec.get("wins", 0), "kda": rec.get("kda"),
                       "score": _avg_score(prof.get("games") or []),
                       "score_matches": [g.get("match_id") for g in valid_games if g.get("score") is not None][:10]},
            "at": time.time()}


def _load_summary(cred, platform, rid, gd):
    key = rid.lower()
    try:
        prof = profile.fetch(cred, platform, rid, gd, 20, "all")
        with _lock:
            SUMMARY[key] = summarize(prof)
    except review.RiotNotFound:
        with _lock:
            SUMMARY[key] = {"riot_id": rid, "error": "nicht gefunden", "at": time.time()}
    except Exception as e:  # noqa: BLE001 - shown in the list, retried later
        with _lock:
            SUMMARY[key] = {**SUMMARY.get(key, {}), "riot_id": rid, "error": str(e)[:120], "at": time.time()}
    finally:
        _busy.discard(key)


def presence(lcu):
    """Online status of the client friend list: riot_id.lower() -> {status, game}."""
    out, names = {}, []
    try:
        fl = lcu.get("/lol-chat/v1/friends") if lcu else None
    except Exception:  # noqa: BLE001
        fl = None
    for f in fl or []:
        name, tag = f.get("gameName"), f.get("gameTag")
        if not name or not tag:
            continue
        rid = f"{name}#{tag}"
        av = f.get("availability") or "offline"
        game = ((f.get("lol") or {}).get("gameStatus") or "")
        status = ("ingame" if game in ("inGame", "championSelect") or av == "dnd" else
                  "online" if av in ("chat", "mobile") else "away" if av == "away" else "offline")
        out[rid.lower()] = {"status": status, "game": game}
        names.append({"riot_id": rid, "status": status})
    return out, names


def listing(cfg, gd, cred, lcu=None, max_age=300, pub=None, self_id=None):
    platform = cfg.get("platform", "euw1")
    pres, client = presence(lcu)
    saved = cfg.get("friends", [])
    own = norm(self_id or cfg.get("riot_id"))
    own = own if valid(own) else None
    roster = list(dict.fromkeys([*saved, *([own] if own else [])]))
    res = []
    for rid in roster:
        key = rid.lower()
        s = SUMMARY.get(key)
        if cred and (not s or time.time() - s.get("at", 0) > max_age) and key not in _busy:
            _busy.add(key)
            _pool.submit(_load_summary, cred, platform, rid, gd)
        c = COACH.get(key) or {}
        jc = (pub or {}).get(key)
        p_ = dict(pres.get(key, {"status": "unknown"}))
        if jc and jc.get("status") and jc["status"].get("state") in ("ingame", "champselect"):
            p_ = {"status": "ingame", "game": jc["status"]["state"], "champ": jc["status"].get("champ")}
        res.append({**(s or {"riot_id": rid, "loading": True}), "riot_id": rid, "jc": jc,
                    "presence": p_,
                    "coach": {"status": c.get("status"), "score": (c.get("plan") or {}).get("score")}})
    order = {"ingame": 0, "online": 1, "away": 2, "offline": 3, "unknown": 4}
    res.sort(key=lambda f: order.get(f["presence"]["status"], 5))
    saved_l = {f.lower() for f in saved}
    suggestions = [c for c in client if c["riot_id"].lower() not in saved_l][:12]
    me = next((dict(f, me=True, presence={"status": "online"}) for f in res if own and f["riot_id"].lower() == own.lower()), None)
    return {"me": me, "friends": [f for f in res if not own or f["riot_id"].lower() != own.lower()], "client": suggestions}


def start_coach(cfg, gd, cred, rid, plan_fn):
    """Analyses the friend's recent ranked games in the background (same coach as for you)."""
    key = norm(rid).lower()
    with _lock:
        if (COACH.get(key) or {}).get("status") == "running":
            return COACH[key]
        COACH[key] = {**COACH.get(key, {}), "status": "running", "started": time.time()}

    def job():
        try:
            prev = (COACH.get(key) or {}).get("_review")
            rev = review.run(cred, cfg.get("platform", "euw1"), norm(rid), 20, prev=prev)
            rev["account"] = norm(rid)
            plan = plan_fn(rev)
            with _lock:
                COACH[key] = {"status": "done", "plan": plan, "at": time.time(), "_review": rev}
        except Exception as e:  # noqa: BLE001
            with _lock:
                COACH[key] = {**COACH.get(key, {}), "status": "error", "error": str(e)[:200]}
    threading.Thread(target=job, daemon=True).start()
    return COACH[key]


def coach_state(rid):
    c = COACH.get(norm(rid).lower()) or {}
    return {k: v for k, v in c.items() if not k.startswith("_")}

