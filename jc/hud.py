"""What the in-game overlay shows: one clear 'do this now' prompt plus a compact status block.

Only information you also see in game (scoreboard, kill feed, your gold) – no hidden info.
"""
from . import i18n

FIRST_DRAGON = 300  # first dragon spawn (s) – stable for many seasons


def T(de, en):
    return en if i18n.LANG["code"] == "en" else de


POS = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Bot", "gestorben": "†"}


def _sec(txt):
    if not txt or ":" not in txt:
        return 0
    m, s = txt.split(":")
    return int(m) * 60 + int(s)


def build(live, gd, gold, owned, focus=None, plan_step=None):
    """live: result of live.analyze (without hud). Returns {now, rows, item, timers, focus}."""
    t = live.get("_t", 0)
    prompts = []  # (priority, text, color)

    # ---- objectives: timers incl. first dragon
    timers = [{"name": o["name"], "in": o["respawn_in"]} for o in live.get("objectives") or []]
    if not any(o["name"] in ("Dragon", "Elder") for o in timers) and t < FIRST_DRAGON:
        m, s = divmod(int(FIRST_DRAGON - t), 60)
        timers.append({"name": "Dragon", "in": f"{m}:{s:02d}"})
    names = {"Dragon": T("Drache", "Dragon"), "Baron": "Baron", "Elder": T("Elder-Drache", "Elder dragon")}
    for o in timers:
        o["label"] = names.get(o["name"], o["name"])
        left = 0 if o["in"] in ("jetzt", "now") else _sec(o["in"])
        o["soon"] = left <= 60
        if o["in"] in ("jetzt", "now"):
            o["in"] = T("jetzt", "now")
        if left <= 60:
            prompts.append((1, T(f"{o['label']} in {o['in']} – Wave pushen lassen, Sicht setzen, mit vollem Leben hin",
                                  f"{o['label']} in {o['in']} – have lanes push, set vision, arrive with full health"), "warn"))
        elif left <= 90:
            prompts.append((3, T(f"{o['label']} in {o['in']} – jetzt zurück und Items kaufen",
                                  f"{o['label']} in {o['in']} – back and buy now"), "info"))

    # ---- next item and whether you can afford it
    item = None
    if not item:
        b = live.get("build") or {}
        nxt = (b.get("situational") or []) + (b.get("core") or [])
        if nxt:
            iid = nxt[0].get("id")
            cost = ((gd.items.get(iid) or {}).get("gold") or {}).get("total") if (gd and iid) else None
            item = {"id": iid, "name": nxt[0]["name"], "cost": cost, "why": nxt[0].get("why", "")}
    if item and item.get("cost") and gold is not None:
        item["missing"] = max(0, item["cost"] - int(gold))
        if gold >= item["cost"]:
            prompts.append((2, T(f"Genug Gold für {item['name']} – zurück, wenn kein Objective ansteht",
                                  f"Enough gold for {item['name']} – back if no objective is up"), "good"))

    # ---- enemy jungler (kill feed)
    ej = live.get("enemy_jungler") or {}
    seen = (ej.get("last_seen") or [])
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
    if ms and t - ms[-1].get("t", 0) <= 30:
        prompts.append((1, T(f"Fehler: {ms[-1]['title']} – {ms[-1]['why']}", f"Mistake: {ms[-1]['title']} – {ms[-1]['why']}"), "bad"))

    # ---- farm
    cs = live.get("cs_per_min", 0)
    if t > 360 and cs < 5.5:
        prompts.append((4, T(f"Farm {cs} CS/min – nach jedem Gank 2 Camps nachholen",
                              f"Farm {cs} CS/min – take 2 camps after every gank"), "warn"))
    if plan_step and t < 240:
        prompts.append((3, plan_step, "info"))

    prompts.sort(key=lambda p: p[0])
    now = {"text": prompts[0][1], "tone": prompts[0][2]} if prompts else \
        {"text": T("Alles im Griff – Camps, Lanes mit Prio, nächstes Objective vorbereiten",
                   "All good – camps, lanes with priority, prepare the next objective"), "tone": "calm"}
    return {"now": now, "more": [{"text": p[1], "tone": p[2]} for p in prompts[1:3]],
            "item": item, "timers": timers, "gold": int(gold) if gold is not None else None,
            "focus": focus, "cs": cs, "cs_ok": cs >= 6 or t < 180, "time": live.get("game_time"),
            "enemy_jg": {"champion": ej.get("champion"), "level": ej.get("level"),
                         "seen": (f"{POS.get(seen[0]['where'], seen[0]['where'])} {seen[0]['time']}" if seen else None)}
            if ej else None,
            "fed": [f"{f['name']} {f['kda']}" for f in (live.get("enemy", {}).get("fed") or [])[:2]]}
