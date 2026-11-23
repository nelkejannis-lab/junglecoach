"""Live Client Data API (https://127.0.0.1:2999) – Riot's official in-game API.

Only contains what you could also see in-game (scoreboard, kill feed).
No memory reading, no hidden info -> Vanguard-safe.
"""
import json
import ssl
import urllib.error
import urllib.request

from . import analyzer
from .recorder import detect_mistakes

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE  # localhost self-signed cert only

URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"

# Respawn timers after a kill (seconds). Check after big patches -> config.json "timers".
DEFAULT_TIMERS = {"Dragon": 300, "Baron": 360, "Elder": 360}


def fetch():
    try:
        with urllib.request.urlopen(URL, context=_CTX, timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
        return None


def _names(p):
    return {x for x in (p.get("summonerName"), p.get("riotIdGameName"), p.get("riotId")) if x}


def _is_jungler(p):
    if (p.get("position") or "").upper() == "JUNGLE":
        return True
    spells = p.get("summonerSpells", {})
    return any("smite" in (spells.get(k, {}).get("displayName", "") or "").lower()
               or "smite" in (spells.get(k, {}).get("rawDisplayName", "") or "").lower()
               for k in ("summonerSpellOne", "summonerSpellTwo"))


def _champ(gd, p):
    # rawChampionName = "game_character_displayname_LeeSin" (language independent)
    raw = (p.get("rawChampionName") or "").rsplit("_", 1)[-1]
    return gd.champ(raw) or gd.champ(p.get("championName"))


def _fmt(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def analyze(gd, data, timers=None, preset_lookup=None):
    timers = {**DEFAULT_TIMERS, **(timers or {})}
    t = data.get("gameData", {}).get("gameTime", 0)
    me_name = data.get("activePlayer", {}).get("riotIdGameName") or data.get("activePlayer", {}).get("summonerName")
    players = data.get("allPlayers", [])
    me = next((p for p in players if me_name in _names(p)), players[0] if players else None)
    if not me:
        return None
    my_team = me["team"]
    allies = [p for p in players if p["team"] == my_team]
    enemies = [p for p in players if p["team"] != my_team]
    name_to_player = {n: p for p in players for n in _names(p)}

    # ---- enemy item analysis -> live build
    enemy_champs = [_champ(gd, p) for p in enemies]
    prof = analyzer.team_profile(gd, enemy_champs)
    armor_st = mr_st = 0
    fed = []
    for p in enemies:
        armor = mr = 0
        for it in p.get("items", []):
            stats = gd.items.get(it.get("itemID"), {}).get("stats", {})
            armor += stats.get("FlatArmorMod", 0)
            mr += stats.get("FlatSpellBlockMod", 0)
        armor_st += armor >= 60
        mr_st += mr >= 60
        sc = p.get("scores", {})
        c = _champ(gd, p)
        if c and sc.get("kills", 0) - sc.get("deaths", 0) >= 4:
            fed.append({"name": gd.champ_name(c), "dmg": analyzer.damage_type(c),
                        "kda": f"{sc.get('kills', 0)}/{sc.get('deaths', 0)}/{sc.get('assists', 0)}"})
    my_champ = _champ(gd, me)
    owned = [it.get("itemID") for it in me.get("items", [])]
    live_ctx = {"armor_stackers": armor_st, "mr_stackers": mr_st, "fed": fed}
    preset = preset_lookup(my_champ["id"]) if (preset_lookup and my_champ) else None
    build = analyzer.recommend_build(gd, my_champ, prof, owned=owned, enemy_live=live_ctx, preset=preset) \
        if my_champ else None
    # simulation with the real items: my completed items stay, enemies' real armor / MR / HP count
    sim_res = None
    if my_champ:
        try:
            from jc import sim
            pos = (me.get("position") or "").upper() or None
            pos = {"MID": "MIDDLE", "BOT": "BOTTOM", "ADC": "BOTTOM", "SUPPORT": "UTILITY"}.get(pos, pos)
            if _is_jungler(me):
                pos = "JUNGLE"
            ecs = [(c, p) for p in enemies for c in [_champ(gd, p)] if c]
            sim_res = sim.recommend_async(
                my_champ["id"], tuple(c["id"] for c, _ in ecs), pos,
                owned=tuple(sorted(i for i in owned if i)),
                enemy_items={c["id"]: tuple(sorted(it.get("itemID") for it in p.get("items", []) if it.get("itemID")))
                             for c, p in ecs})
        except Exception as e:  # noqa: BLE001
            sim_res = {"available": False, "reason": str(e)}

    # ---- enemy jungler tracking (kill feed + scoreboard only)
    ej = next((p for p in enemies if _is_jungler(p)), None)
    jungle = {}
    if ej:
        ej_names = _names(ej)
        seen = []
        for ev in data.get("events", {}).get("Events", []):
            if ev.get("EventName") != "ChampionKill":
                continue
            involved = {ev.get("KillerName")} | set(ev.get("Assisters", [])) | {ev.get("VictimName")}
            if ej_names & involved:
                victim = name_to_player.get(ev.get("VictimName"), {})
                lane = victim.get("position") or "?"
                if ej_names & {ev.get("VictimName")}:
                    lane = "gestorben"
                seen.append({"time": _fmt(ev.get("EventTime", 0)), "t": ev.get("EventTime", 0), "where": lane})
        lvl = ej.get("level", 1)
        warnings = []
        if lvl >= 3 and t < 300 and not seen:
            warnings.append("Gegner-Jungler Lvl 3+ ohne Aktion → Gank-Fenster offen, Lanes warnen.")
        if lvl >= 6:
            warnings.append("Gegner-Jungler hat Ult (Lvl 6+).")
        if seen and t - seen[-1]["t"] < 45 and seen[-1]["where"] not in ("gestorben", "?"):
            warnings.append(f"Gerade {seen[-1]['where']} gesehen → gegenüberliegende Seite ist sicher für dich.")
        ec = _champ(gd, ej)
        jungle = {"champion": gd.champ_name(ec) if ec else ej.get("championName"), "level": lvl,
                  "kda": "{kills}/{deaths}/{assists}".format(**{k: ej.get("scores", {}).get(k, 0)
                                                               for k in ("kills", "deaths", "assists")}),
                  "items": [gd.item_name(i.get("itemID")) for i in ej.get("items", [])],
                  "last_seen": seen[-3:][::-1], "warnings": warnings}

    # ---- objectives (from kill feed, like the native in-game timers)
    objectives = []
    last = {}
    for ev in data.get("events", {}).get("Events", []):
        n = ev.get("EventName")
        if n == "DragonKill":
            last["Elder" if ev.get("DragonType") == "Elder" else "Dragon"] = ev["EventTime"]
        elif n == "BaronKill":
            last["Baron"] = ev["EventTime"]
    for obj, kt in last.items():
        respawn = kt + timers[obj]
        objectives.append({"name": obj, "respawn_in": _fmt(respawn - t) if respawn > t else "jetzt"})

    name_team = {n: p["team"] for p in players for n in _names(p)}
    name_pos = {n: p.get("position") for p in players for n in _names(p)}
    mistakes = detect_mistakes(data, _names(me) | {me_name}, my_team, _names(ej) if ej else set(),
                               name_team, name_pos)
    my_cs = me.get("scores", {}).get("creepScore", 0)
    cs_min = round(my_cs / max(1, t / 60), 1) if t > 60 else 0
    return {"game_time": _fmt(t), "me": gd.champ_name(my_champ) if my_champ else "?",
            "cs_per_min": cs_min, "cs_hint": "Farm ok" if cs_min >= 6 or t < 180 else "Unter 6 CS/min → mehr Camps zwischen Ganks",
            "enemy": {"profile": prof, "fed": fed, "armor_stackers": armor_st, "mr_stackers": mr_st},
            "build": build, "sim": sim_res, "enemy_jungler": jungle, "objectives": objectives,
            "allies": [gd.champ_name(_champ(gd, p)) for p in allies],
            "mistakes": [{**m, "time": _fmt(m["t"])} for m in mistakes],
            "_team_id": 100 if my_team == "ORDER" else 200, "_t": t,
            "_owned": [i for i in owned if i], "_gold": (data.get("activePlayer") or {}).get("currentGold"),
            "_enemy_targets": [(c["id"], gd.champ_name(c), _is_jungler(p))
                               for p in enemies for c in [_champ(gd, p)] if c]}
