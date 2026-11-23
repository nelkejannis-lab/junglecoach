"""Game plan for champ select: early duel vs. the enemy jungler, best gank lane, start side,
objective priority and power spikes – from the simulation (game files) and real matchup data."""
import threading

from jc import sim
from jc.sim import engine

_CACHE, _RUNNING = {}, set()


def build_async(my_id, enemy_jg_id, allies, enemies, meta_stats=None, bracket=None):
    """Non-blocking for the poll loop: cached plan or None while it is being computed."""
    key = (my_id, enemy_jg_id, tuple(allies), tuple(enemies), bracket)
    if key in _CACHE:
        return _CACHE[key]
    if key not in _RUNNING:
        _RUNNING.add(key)
        ms = type(meta_stats)(meta_stats.gd, meta_stats.db) if meta_stats is not None else None

        def work():
            try:
                _CACHE[key] = build(my_id, enemy_jg_id, list(allies), list(enemies), ms, bracket)
            except Exception as e:  # noqa: BLE001
                _CACHE[key] = {"error": str(e), "steps": []}
            finally:
                _RUNNING.discard(key)
        threading.Thread(target=work, daemon=True).start()
    return {"pending": True, "steps": []}

LANES = [("TOP", "Top"), ("MIDDLE", "Mid"), ("BOTTOM", "Bot")]


def _duel(me, enemy, level, n_items, d):
    """Fighting strength ratio me vs. enemy jungler at a level (damage x survivability, 1.0 = even)."""
    a_off, a_def, _ = engine.evaluate(me, [], d["items"], [enemy], d["levels"], level, n_items)
    b_off, b_def, _ = engine.evaluate(enemy, [], d["items"], [me], d["levels"], level, n_items)
    return (a_off * a_def) / max(b_off * b_def, 1e-9)


def _verdict(r):
    if r >= 1.25:
        return "klar stärker"
    if r >= 1.08:
        return "etwas stärker"
    if r > 0.92:
        return "ausgeglichen"
    if r > 0.8:
        return "etwas schwächer"
    return "klar schwächer"


def build(my_id, enemy_jg_id, allies, enemies, meta_stats=None, bracket=None):
    """allies / enemies: lists of (champion id, role or None). Returns the plan or None."""
    d = sim.load()
    if not d or not my_id:
        return None
    me = sim._champ(d, my_id)
    ej = sim._champ(d, enemy_jg_id) if enemy_jg_id else None
    if not me:
        return None
    plan = {"steps": []}

    # ---- 1) early game duel vs. the enemy jungler (simulated, no items)
    if ej:
        r3, r6 = _duel(me, ej, 3, 0, d), _duel(me, ej, 6, 0, d)
        duel = {"enemy": ej["name"], "lvl3": round(r3, 2), "lvl6": round(r6, 2),
                "lvl3_txt": _verdict(r3), "lvl6_txt": _verdict(r6)}
        if meta_stats:
            try:
                mu = meta_stats.matchup(sim._ddragon_id(me), sim._ddragon_id(ej), bracket)
                if mu.get("enough"):
                    duel["winrate"] = mu["winrate"]
                    duel["games"] = mu["games"]
            except Exception:  # noqa: BLE001
                pass
        if r3 >= 1.08:
            duel["advice"] = (f"Auf Level 3 bist du {duel['lvl3_txt']} als {ej['name']}: Scuttle um 3:30 umkämpfen, "
                              f"Invade auf seinen zweiten Buff ist möglich, wenn eine Lane Prio hat.")
        elif r3 <= 0.92:
            duel["advice"] = (f"{ej['name']} ist auf Level 3 {duel['lvl3_txt'].replace('schwächer', 'stärker')}: "
                              f"nicht 1v1 um die Scuttle kämpfen, Deep Wards an deinem Buff, Seite gegenüber von ihm farmen.")
        else:
            duel["advice"] = "Frühe Duelle sind offen – entscheidet sich über Lane-Prio und Smite."
        if r6 >= r3 * 1.15 and r6 >= 1.0:
            duel["advice"] += " Ab Level 6 kippt es zu deinen Gunsten – dann aktiv nach ihm suchen."
        elif r6 <= r3 * 0.87:
            duel["advice"] += f" Ab Level 6 wird {ej['name']} stärker – vor seiner Ult Duelle abschließen."
        plan["duel"] = duel

    # ---- 2) gank lanes: who wins the lane (real games), weighted by how easy the target is
    lanes = []
    if meta_stats:
        by_role_a = {r: c for c, r in allies if r}
        by_role_e = {r: c for c, r in enemies if r}
        for role, label in LANES:
            a, e = by_role_a.get(role), by_role_e.get(role)
            if not a or not e:
                continue
            wr = None
            try:
                old = getattr(meta_stats, "role", None)
                meta_stats.role = role
                mu = meta_stats.matchup(a, e, bracket)
                meta_stats.role = old
                if mu.get("enough"):
                    wr = mu["winrate"]
            except Exception:  # noqa: BLE001
                pass
            ec = sim._champ(d, e)
            squishy = bool(ec) and ("Tank" not in ec.get("tags", []))
            score = (wr if wr is not None else 50) + (4 if squishy else -4) + {"BOTTOM": 1, "TOP": 0.5}.get(role, 0)
            lanes.append({"role": role, "lane": label, "ally": a, "enemy": e, "winrate": wr, "score": round(score, 1)})
    lanes.sort(key=lambda x: -x["score"])
    plan["lanes"] = lanes
    if lanes:
        best = lanes[0]
        why = (f"{best['ally']} gewinnt das Matchup ({best['winrate']}% WR)" if best["winrate"] and best["winrate"] >= 51
               else f"{best['enemy']} ist das leichteste Ziel")
        plan["gank"] = {"lane": best["lane"], "why": why}
        start = "unten (Bot-Seite)" if best["role"] == "TOP" else "oben (Top-Seite)" if best["role"] == "BOTTOM" else \
            "auf der Seite mit dem schwächeren eigenen Laner"
        end_side = "oben" if best["role"] == "TOP" else "unten" if best["role"] == "BOTTOM" else "Richtung Mid"
        plan["start"] = {"side": start, "why": f"Clear endet {end_side} – erster Gank auf {best['lane']} um ~3:15."}
        bot = next((x for x in lanes if x["role"] == "BOTTOM"), None)
        top = next((x for x in lanes if x["role"] == "TOP"), None)
        if bot and (not top or bot["score"] >= top["score"]):
            plan["objective"] = {"first": "Drache", "why": "Bot-Seite ist deine stärkere Seite – Drachen mit Bot-Prio setzen."}
        elif top:
            plan["objective"] = {"first": "Void-Grubs", "why": "Top-Seite ist deine stärkere Seite – Grubs mit Top-Prio holen."}

    # ---- 3) power spikes from the simulation
    try:
        rec = sim.recommend(my_id, [c for c, _ in enemies], "JUNGLE", meta_stats=meta_stats, bracket=bracket)
        if rec.get("available"):
            spikes = []
            for i, it in enumerate(rec["items"][:3]):
                spikes.append({"item": it["name_de"] or it["name"], "id": it["id"], "dmg": it.get("dmg"),
                               "when": ["1. Item", "2. Items", "3. Items"][i]})
            plan["spikes"] = spikes
            plan["skill_order"] = rec.get("skill_order")
    except Exception:  # noqa: BLE001
        pass

    # ---- 4) the 3-step checklist
    if plan.get("start"):
        plan["steps"].append(f"Start {plan['start']['side']} → {plan['start']['why']}")
    if plan.get("duel"):
        plan["steps"].append(plan["duel"]["advice"])
    if plan.get("objective"):
        plan["steps"].append(f"Erstes Objective: {plan['objective']['first']} – {plan['objective']['why']}")
    if plan.get("spikes"):
        s = plan["spikes"][0]
        plan["steps"].append(f"Power-Spike: {s['item']}" + (f" (+{s['dmg']}% Schaden)" if s.get("dmg") else "")
                             + " – danach aktiv Fights und Objectives suchen.")
    return plan
