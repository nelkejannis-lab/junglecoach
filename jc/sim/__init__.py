"""Build simulation: loads the patch data (sim.json.gz) and recommends builds for a concrete matchup.

The theory part (engine.py) decides WHICH items fit the champion's kit against this enemy team;
real games (meta DB) decide the item class a champion plays and veto items that clearly lose.
"""
import gzip
import json
import os
import threading
import time
import urllib.request

from jc import paths
from jc.sim import engine

SIM_URL = "https://github.com/nelkejannis-lab/junglecoach-data/releases/download/gamedata-latest/sim.json.gz"
_STATE = {"data": None, "loaded": 0, "lock": threading.Lock(), "cache": {}}
GENERIC = {  # stand-in enemies until real picks are known (common picks per role)
    "TOP": "Ornn", "JUNGLE": "Viego", "MIDDLE": "Ahri", "BOTTOM": "Jinx", "UTILITY": "Thresh"}


def _path():
    return os.path.join(paths.DATA_DIR, "sim.json.gz")


def refresh(force=False):
    """Download the current patch data at most once a day."""
    p = _path()
    if not force and os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400:
        return False
    try:
        req = urllib.request.Request(SIM_URL, headers={"User-Agent": "JungleCoach/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read()
        json.loads(gzip.decompress(blob))  # validate before replacing
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "wb") as f:
            f.write(blob)
        os.replace(p + ".tmp", p)
        _STATE["data"] = None
        return True
    except Exception:  # noqa: BLE001 - offline is fine, we keep the old file
        return False


def load():  # cached patch data (named load so it never shadows the jc.sim.data module)
    with _STATE["lock"]:
        if _STATE["data"] is None and os.path.exists(_path()):
            with gzip.open(_path(), "rt", encoding="utf-8") as f:
                _STATE["data"] = json.load(f)
            _STATE["cache"].clear()
        return _STATE["data"]


def _champ(d, ident):
    if ident is None:
        return None
    if isinstance(ident, str) and ident in d["champions"]:
        return d["champions"][ident]
    try:
        k = int(ident)
    except (TypeError, ValueError):
        k = None
    for name, c in d["champions"].items():
        if c["key"] == k or c["name"] == ident:
            return c
    return None


def _validate(meta_stats, champ_name, items, bracket=None, role=None):
    """Real-game check per item: pickrate, winrate and P(item better than the champion's average)."""
    if not meta_stats:
        return {}
    try:
        rows, scope = meta_stats._rows(champ_name, bracket, min_games=40, role=role)
    except Exception:  # noqa: BLE001
        return {}
    n_all = len(rows)
    if n_all < 20:
        return {}
    from jc.meta import _beta_prob_better
    out = {}
    for iid in items:
        with_i = [r for r in rows if int(iid) in r[1]]
        wo = [r for r in rows if int(iid) not in r[1]]
        n, w = len(with_i), sum(r[0] for r in with_i)
        n2, w2 = len(wo), sum(r[0] for r in wo)
        pb, up = _beta_prob_better(w, n, w2, n2, draws=1500) if n and n2 else (0.5, 0.0)
        out[iid] = {"games": n, "pickrate": round(100 * n / n_all, 1),
                    "winrate": round(100 * w / n, 1) if n else None, "prob_better": round(pb, 2),
                    "uplift": round(100 * up, 1), "scope": scope}
    return out


def recommend_async(*args, **kw):
    """Non-blocking: returns the cached result or starts the simulation in the background."""
    key = repr((args, sorted((k, repr(v)) for k, v in kw.items() if k != "meta_stats")))
    done = _STATE["cache"].get(("async", key))
    if done:
        return done
    if key not in _STATE.setdefault("running", set()):
        _STATE["running"].add(key)

        ms = kw.get("meta_stats")
        if ms is not None:  # own stats object: the poll loop changes .role on the shared one
            kw["meta_stats"] = type(ms)(ms.gd, ms.db)

        def work():
            try:
                with _STATE.setdefault("sim_lock", threading.Lock()):
                    _STATE["cache"][("async", key)] = recommend(*args, **kw)
            except Exception as e:  # noqa: BLE001
                _STATE["cache"][("async", key)] = {"available": False, "reason": f"Simulation fehlgeschlagen: {e}"}
            finally:
                _STATE["running"].discard(key)
        threading.Thread(target=work, daemon=True).start()
    return {"available": False, "pending": True, "reason": "Simulation läuft …"}


def recommend(champ, enemies, role="JUNGLE", meta_stats=None, bracket=None, champ_name=None,
              owned=None, enemy_items=None):
    """Simulated best build for `champ` against `enemies` (ids or names), checked against real games.
    owned: item ids already bought (live) · enemy_items: {enemy name: [item ids]} (live)."""
    d = load()
    if not d:
        return {"available": False, "reason": "Spieldaten werden geladen …"}
    c = _champ(d, champ)
    if not c:
        return {"available": False, "reason": "Champion nicht in den Spieldaten"}
    ens = [e for e in (_champ(d, x) for x in (enemies or [])) if e]
    known = len(ens)
    for r, name in GENERIC.items():
        if len(ens) >= 5:
            break
        g = d["champions"].get(name)
        if g and g not in ens and g is not c:
            ens.append(g)
    owned = [str(i) for i in (owned or []) if str(i) in d["items"]]
    real = None
    if enemy_items:
        real = {}
        for ident, its in enemy_items.items():
            e = _champ(d, ident)
            if e:
                real[e["name"]] = [str(i) for i in its]
    key = (c["name"], tuple(sorted(e["name"] for e in ens)), role, bracket, tuple(owned),
           tuple(sorted((k, tuple(sorted(v))) for k, v in (real or {}).items())))
    if key in _STATE["cache"]:
        return _STATE["cache"][key]
    if role == "UTILITY":
        res = {"available": False, "reason": "Support-Items wirken auf Verbündete – dafür zählen hier die echten Daten."}
        _STATE["cache"][key] = res
        return res
    if not role and meta_stats:
        try:
            role = meta_stats.main_role(_ddragon_id(c))
        except Exception:  # noqa: BLE001
            role = None
    role = role or "JUNGLE"
    pop = None
    if meta_stats:
        try:
            rows, _ = meta_stats._rows(_ddragon_id(c), bracket, min_games=40, role=role)
            pop = {}
            for r in rows:
                for i in r[1]:
                    pop[str(i)] = pop.get(str(i), 0) + 1
        except Exception:  # noqa: BLE001
            pop = None
    mix = {"physical": 0.0, "magic": 0.0, "true": 0.0}
    for e in ens:
        for k, v in engine.damage_profile(e, d["levels"]).items():
            mix[k] += v / len(ens)
    melee = c["stats"]["range"] < 300
    jungle = d["jungle"] if role == "JUNGLE" else None
    best = engine.best_build(c, d["items"], ens, d["levels"], mix, melee, jungle=jungle, popularity=pop,
                             fixed=owned, real_enemy_items=real)
    items = d["items"]
    # veto: items that clearly lose in real games are replaced by the next best alternative
    check = _validate(meta_stats, _ddragon_id(c), best["items"] + [a for alt in best["alternatives"] for a in alt],
                      bracket, role)
    vetoed = [i for i in best["items"] if i not in owned
              and check.get(i, {}).get("games", 0) >= 30 and check[i]["prob_better"] < 0.15]
    final = [i for i in best["items"] if i not in vetoed]
    for alt in best["alternatives"]:
        for i in alt:
            if len(final) >= 5:
                break
            if i not in final and i not in vetoed and not any(engine.conflicts(i, j) for j in final):
                final.append(i)
    out_items = []
    for i in final[:5]:
        v = check.get(i)
        ex = best["explain"].get(i)
        if v and v["pickrate"] >= 15:
            badge = "Meta + Sim"
        elif v and v["games"] >= 15 and v["prob_better"] >= 0.5:
            badge = "Geheimtipp"
        elif v and v["games"] >= 15:
            badge = "Sim"
        else:
            badge = "Theorie"
        why = []
        if ex:
            if ex["dmg"] > 0:
                why.append(f"+{ex['dmg']}% Schaden")
            if ex["tank"] > 0:
                why.append(f"+{ex['tank']}% Überleben")
        if v and v["games"]:
            why.append(f"{v['winrate']}% WR in {v['games']} Games ({v['pickrate']}% Pickrate)")
        elif v is not None:
            why.append("kaum gespielt – rein rechnerisch stark")
        if i in owned:
            badge = "gekauft"
        out_items.append({"id": int(i), "name": items[i]["name"], "name_de": items[i]["name_de"], "badge": badge,
                          "why": " · ".join(why), "dmg": ex["dmg"] if ex else None, "tank": ex["tank"] if ex else None,
                          "data": v})
    res = {
        "available": True, "patch": d["version"], "champion": c["name"], "role": role,
        "enemies": [e["name"] for e in ens], "known_enemies": known,
        "enemy_mix": {k: round(100 * v) for k, v in mix.items()},
        "items": out_items,
        "boots": ({"id": int(best["boots"]), "name": items[best["boots"]]["name"],
                   "name_de": items[best["boots"]]["name_de"]} if best["boots"] else None),
        "vetoed": [{"id": int(i), "name": items[i]["name"], "data": check.get(i)} for i in vetoed],
        "skill_order": best["order"],
        "clear_time": (round(engine.clear_time(c, final[:1], items, jungle, d["levels"], level=6))
                       if jungle else None),
    }
    _STATE["cache"][key] = res
    return res


def _ddragon_id(c):
    """Meta DB stores champion ids as Data Dragon ids (e.g. 'MonkeyKing')."""
    d = load()
    for name, x in d["champions"].items():
        if x is c:
            return name
    return c["name"]
