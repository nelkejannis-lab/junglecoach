"""What the in-game overlay shows: one clear 'do this now' prompt plus a compact status block.

Only information you also see in game (scoreboard, kill feed, your gold) – no hidden info.
"""
from . import i18n
from .shop import recipe_plan

from .timing import OBJECTIVES

def T(de, en):
    return en if i18n.LANG["code"] == "en" else de


POS = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Bot", "gestorben": "†"}


def _sec(txt):
    if not txt or ":" not in txt:
        return 0
    m, s = txt.split(":")
    return int(m) * 60 + int(s)


DEFAULT_GPM = {"UTILITY": 290}  # median gold/min at game end if no comparison data; others ~400


def gold_per_min(live, gold, bench=None, role=None):
    """Your gold/min = (value of your items + unspent gold) / minutes – the Live Client API has no
    'total gold earned'. Rated against real games of your champion (median / 25 % at game end),
    scaled down for the early game because gold/min grows over a match."""
    t = live.get("_t", 0)
    if t < 60 or gold is None:
        return None
    gpm = round((live.get("_item_gold", 0) + gold) / (t / 60))
    p50 = (bench or {}).get("p50") or DEFAULT_GPM.get(role, 400)
    p25 = (bench or {}).get("p25") or p50 * 0.85
    f = 0.8 + 0.2 * min(1.0, t / 1800)
    if t < 360:
        rate = "neutral"  # first minutes: starting gold distorts the number
    elif gpm >= p50 * f:
        rate = "good"
    elif gpm >= p25 * f:
        rate = "ok"
    else:
        rate = "bad"
    return {"value": gpm, "rate": rate, "target": round(p50 * f)}


def next_focus(live, timers, t, hide=()):
    """What the team should play for next: objectives and towers, from the event feed everyone sees.
    Returns {"text", "why"} or None."""
    lost = [s for s in live.get("structures") or [] if s["kind"] in ("tower", "inhib")]
    recent = [s for s in lost if t - s["t"] <= 50]
    up = []
    if "timers" not in hide:
        for o in timers:
            sec = 0 if o["in"] in ("jetzt", "now") else _sec(o["in"])
            up.append((sec, o["label"]))
        up.sort()
    if recent:
        s = recent[-1]
        if s["kind"] == "inhib" and not s["ours"]:
            return {"text": T(f"Inhibitor {s['lane']} gefallen – Baron oder nächsten Turm nehmen",
                              f"{s['lane']} inhibitor down – take Baron or the next tower"), "short": T("Baron / Turm", "Baron / tower"), "why": T("Supers drücken die Lane für euch", "super minions push for you")}
        if not s["ours"]:
            nxt = up[0] if up and up[0][0] <= 120 else None
            return {"text": T(f"{s['lane']}-Turm gefallen – " + (f"jetzt {nxt[1]} vorbereiten" if nxt else "Plates/Camps auf der Seite nehmen, dann rotieren"),
                              f"{s['lane']} tower down – " + (f"set up {nxt[1]} now" if nxt else "take plates/camps there, then rotate")),
                    "short": (T(f"{nxt[1]} vorbereiten", f"set up {nxt[1]}") if nxt else T(f"{s['lane']}-Seite nutzen", f"use {s['lane']} side")),
                    "why": T("Map öffnet sich auf dieser Seite", "the map opens on that side")}
        return {"text": T(f"Eigener {s['lane']}-Turm verloren – Sicht im eigenen Jungle auf der Seite",
                          f"Lost our {s['lane']} tower – ward your jungle on that side"), "short": T(f"{s['lane']}-Jungle warden", f"ward {s['lane']} jungle"), "why": T("Gegner kommt jetzt tiefer rein", "enemies can now invade deeper")}
    if up and up[0][0] <= 150:
        sec, label = up[0]
        return {"text": T(f"{label} in {sec // 60}:{sec % 60:02d} – Lanes mit Prio vorbereiten, Reset vorher",
                          f"{label} in {sec // 60}:{sec % 60:02d} – get lane prio, reset before"), "short": T(f"{label}: Prio + Reset", f"{label}: prio + reset"), "why": T("Nächstes Objective", "next objective")}
    ours_down = sum(1 for s in lost if not s["ours"])
    if t < 14 * 60 and ours_down == 0:
        return {"text": T("Erster Turm: Lane mit Prio bei den Plates unterstützen (bis 14:00)", "First tower: help the lane with prio take plates (until 14:00)"), "short": T("Plates mit Prio-Lane", "plates with prio lane"),
                "why": T("Plates geben bis 14:00 extra Gold", "plates give extra gold until 14:00")}
    if t >= 20 * 60 and "timers" not in hide and not any(l == "Baron" for _, l in up):
        return {"text": T("Baron ist oben – nur nach gewonnenem Fight oder Pick", "Baron is up – only after a won fight or a pick"), "short": T("Baron nach Pick", "Baron after a pick"),
                "why": T("Sicht um den Pit vorher setzen", "set vision around the pit first")}
    if ours_down:
        return {"text": T("Nächsten Turm auf der Seite mit Prio pushen – Sicht davor", "push the next tower on the side with prio – vision first"), "short": T("Nächsten Turm pushen", "push next tower"),
                "why": T("Türme öffnen die Map", "towers open the map")}
    return None


def next_goals(live, timers, t, hide=(), focus=None, n=3):
    """The next 3 things to play for, most important first: the focus, objectives with their timer,
    and the next enemy tower (by lane progress). Returns [{"text", "kind": focus|obj|tower, "soon"}]."""
    out = []
    obj = [] if "timers" in hide else [o for o in timers if o.get("in")]
    if focus and focus.get("short"):
        txt = focus["short"]
        hit = next((o for o in obj if o["label"].split()[0].lower() in txt.lower()), None)
        if hit:   # "Drache vorbereiten" + "Drache 0:45" -> one chip
            txt = f"{txt} · {hit['in']}"
            obj = [o for o in obj if o is not hit]
        out.append({"text": txt, "kind": "focus", "soon": True})
    obj = sorted(obj, key=lambda o: 0 if o["in"] in ("jetzt", "now") else _sec(o["in"]))
    for o in obj[:2]:
        out.append({"text": f"{o['label']} {o['in']}", "kind": "obj", "soon": bool(o.get("soon"))})
    # towers: enemy towers already taken per lane -> push the next one where we are furthest
    taken = {"Top": 0, "Mid": 0, "Bot": 0}
    for st in live.get("structures") or []:
        if st["kind"] == "tower" and st.get("ours") is False and st.get("lane") in taken:
            taken[st["lane"]] += 1
    lane = max(taken, key=lambda k: taken[k])
    if taken[lane] < 3 and not any(g["kind"] == "focus" and ("turm" in g["text"].lower() or "tower" in g["text"].lower()) for g in out):
        if taken[lane] == 0:
            out.append({"text": T("Erster Turm", "first tower") if t < 14 * 60 else T("Außenturm pushen", "push outer tower"), "kind": "tower", "soon": False})
        else:
            out.append({"text": T(f"{lane} T{taken[lane] + 1}", f"{lane} T{taken[lane] + 1}"), "kind": "tower", "soon": False})
    return out[:n]


def build(live, gd, gold, owned, focus=None, plan_step=None, gpm_bench=None, role=None, pick=None, hide=(), cue_gate=None):
    """live: result of live.analyze (without hud). Returns {now, rows, item, timers, focus}."""
    t = live.get("_t", 0)
    prompts = []  # (priority, text, color)

    objective_key = None
    # ---- objectives: timers incl. first dragon
    timers = [{"name": o["name"], "in": o["respawn_in"]} for o in live.get("objectives") or []]
    initial = {"DRAGON": "Dragon", "HORDE": "Voidgrubs", "RIFTHERALD": "Herald", "BARON_NASHOR": "Baron"}
    if live.get("_standard_rift"):
        tracked = {timer["name"] for timer in timers}
        if "Elder" in tracked:
            tracked.add("Dragon")
        for objective, start in OBJECTIVES["initial_spawn_seconds"].items():
            name = initial[objective]
            if name not in tracked and 0 < start-t <= 150:
                m, s = divmod(int(start-t), 60)
                timers.append({"name": name, "in": f"{m}:{s:02d}"})
    names = {"Dragon": T("Drache", "Dragon"), "Baron": "Baron", "Elder": T("Elder-Drache", "Elder dragon")}
    for o in timers:
        o["label"] = names.get(o["name"], o["name"])
        left = 0 if o["in"] in ("jetzt", "now") else _sec(o["in"])
        o["soon"] = left <= 60
        if o["in"] in ("jetzt", "now"):
            o["in"] = T("jetzt", "now")
        if "timers" in hide:   # Riot-safe mode: no objective timers, also not inside hints
            continue
        if OBJECTIVES["setup_notice_seconds"] <= left <= OBJECTIVES["setup_notice_seconds"] + 10 and not objective_key:
            objective_key = ("setup", o["name"], round((t+left)/5))
            prompts.append((1, T(f"{o['label']} in {o['in']} – Wave pushen lassen, Sicht setzen, mit vollem Leben hin",
                                  f"{o['label']} in {o['in']} – have lanes push, set vision, arrive with full health"), "warn"))
        elif 0 < left <= 90:
            prompts.append((3, T(f"{o['label']} in {o['in']} – jetzt zurück und Items kaufen",
                                  f"{o['label']} in {o['in']} – back and buy now"), "info"))

    # ---- next item: your pick (Tab picker) or the next item of the build path, and whether you can afford it
    item = None
    choices = []
    own = set(owned or [])
    b = live.get("build") or {}
    for c in (b.get("core") or []) + (b.get("situational") or []):
        iid = c.get("id")
        if iid and iid not in own and all(x["id"] != iid for x in choices):
            cost = ((gd.items.get(iid) or {}).get("gold") or {}).get("total") if gd else None
            choices.append({"id": iid, "name": c.get("name"), "cost": cost, "why": c.get("why", ""),
                            "core": c in (b.get("core") or [])})
    choices = choices[:8]
    sel = next((c for c in choices if pick and c["id"] == pick), None)
    if not sel and pick and gd and pick in gd.items and pick not in own:  # picked earlier, no longer in the list
        sel = {"id": pick, "name": gd.item_name(pick), "cost": ((gd.items[pick].get("gold") or {}).get("total")), "why": ""}
    sel = sel or (choices[0] if choices else None)
    if sel:
        item = {"id": sel["id"], "name": sel["name"], "cost": sel["cost"], "why": sel.get("why", ""),
                "picked": bool(pick and sel["id"] == pick)}
    plan = recipe_plan(gd.items, item["id"], owned or [], int(gold), live.get("_shop_slots")) \
        if item and gd and gold is not None else None
    if plan:
        item["cost"] = plan["cost"]
        item["components"] = [gd.item_name(part) for part in plan["parts"] if part != item["id"]]
        item["component_spend"] = plan["spend"] if item["components"] else 0
    if item and item.get("cost") and gold is not None:
        item["missing"] = max(0, item["cost"] - int(gold))
        if item["missing"] == 0 and plan and not plan["ready"]:
            item["missing"] = None
        if plan and 0 < item["cost"] - int(gold) <= 200:
            prompts.append((4, T(f"Noch {item['missing']} G bis {item['name']}: sichere Ressource prüfen.",
                                  f"{item['missing']} G to {item['name']}: check for safe income."), "info"))
        if plan and plan["ready"]:
            prompts.append((2, T(f"Genug Gold für {item['name']} – zurück, wenn kein Objective ansteht",
                                  f"Enough gold for {item['name']} – back if no objective is up"), "good"))

    # ---- enemy jungler (kill feed)
    ej = live.get("enemy_jungler") or {}
    seen = (ej.get("last_seen") or [])
    if "enemy_jg" in hide:   # Riot-safe mode: nothing about the enemy jungler in hints
        seen, ej = [], {}
    if seen and seen[0]["where"] not in ("gestorben", "?"):
        ago = t - seen[0].get("t", t)
        if ago <= 45:
            side = {"TOP": T("Top", "top"), "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": T("Bot", "bot"),
                    "UTILITY": T("Bot", "bot")}.get(seen[0]["where"], seen[0]["where"])
            # kill participation, not a sighting: the API gives no position -> never claim a side is safe
            prompts.append((3, T(f"Gegner-Jungler vor {int(ago)} s an Kill auf {side} beteiligt – Position unbestätigt",
                                  f"Enemy jungler joined a kill on {side} {int(ago)} s ago – position unconfirmed"),
                            "info"))
    if ej.get("level", 0) >= 6 and not any(p[0] <= 2 for p in prompts):
        prompts.append((5, T("Gegner-Jungler hat Ult – Duelle nur mit Vorteil", "Enemy jungler has ult – duel only with an edge"), "info"))

    # ---- recent mistake -> what to learn from it
    ms = [m for m in (live.get("mistakes") or []) if m.get("kind", "mistake") == "mistake"]
    if live.get("_recent_deaths", 0) >= 2:
        prompts.append((2, T("Kurz sammeln: Welche Ressource erreichst du sicher?", "Reset: which resource can you reach safely?"), "info"))
    elif cue_gate is None and ms and t - ms[-1].get("t", 0) <= 30:
        prompts.append((1, T(f"Fehler: {ms[-1]['title']} – {ms[-1]['why']}", f"Mistake: {ms[-1]['title']} – {ms[-1]['why']}"), "bad"))

    # ---- farm
    cs = live.get("cs_per_min", 0)
    if role == "JUNGLE" and t > 360 and cs < 5.5:
        prompts.append((4, T(f"Farm {cs} CS/min – nach jedem Gank 2 Camps nachholen",
                              f"Farm {cs} CS/min – take 2 camps after every gank"), "warn"))
    if plan_step and t < 240:
        prompts.append((3, plan_step, "info"))

    if live.get("_dead"):
        prompts.clear()
        prompts.append((0, T("Respawn abwarten und Einkauf planen.", "Wait for respawn and plan your purchase."), "calm"))
    prompts.sort(key=lambda p: p[0])
    nxt = next_focus(live, timers, t, hide)
    now = {"text": prompts[0][1], "tone": prompts[0][2]} if prompts else \
        {"text": T("Alles im Griff – Camps, Lanes mit Prio, nächstes Objective vorbereiten",
                   "All good – camps, lanes with priority, prepare the next objective"), "tone": "calm"}
    if cue_gate is not None:
        candidate = None
        if prompts:
            priority, message, tone = prompts[0]
            key = objective_key if priority == 1 and objective_key else ("general", priority, tone)
            candidate = (priority, key, message, tone)
        now = cue_gate.update(t, candidate, live.get("_health"), live.get("_max_health"), live.get("_dead"))
    return {"now": now, "next": nxt, "goals": next_goals(live, timers, t, hide, nxt), "more": [],
            "item": item, "timers": timers, "gold": int(gold) if gold is not None else None,
            "focus": focus, "cs": cs, "cs_ok": role != "JUNGLE" or cs >= 6 or t < 180, "time": live.get("game_time"),
            "enemy_jg": {"champion": ej.get("champion"), "level": ej.get("level"),
                         "seen": (f"{POS.get(seen[0]['where'], seen[0]['where'])} {seen[0]['time']}" if seen else None)}
            if ej else None,
            "fed": [f"{f['name']} {f['kda']}" for f in (live.get("enemy", {}).get("fed") or [])[:2]],
            "gold_diff": live.get("gold_diff"),
            "gpm": gold_per_min(live, gold, gpm_bench, role),
            "choices": choices}
