"""Team-comp analysis, pick suggestions, rune pages and item builds.

Rule-based on purpose: it works offline from Data Dragon only. Add your own
preferences per champion in config.json ("champion_presets") – they win.
"""
import re

HEALERS = {"Aatrox", "Soraka", "Yuumi", "Vladimir", "Sylas", "Warwick", "DrMundo", "Fiddlesticks",
           "Swain", "Briar", "Illaoi", "Nami", "Sona", "Seraphine", "Olaf", "Volibear", "Trundle",
           "Maokai", "Udyr", "Belveth", "Kayn", "Renekton", "Samira", "Senna", "Milio", "Gwen",
           "Nasus", "Viego", "Zac", "Yorick", "Ambessa", "Mel"}
HEAVY_CC = {"Leona", "Nautilus", "Amumu", "Sejuani", "Lissandra", "Morgana", "Malphite", "Rell",
            "Thresh", "Zac", "Maokai", "Rammus", "Alistar", "Annie", "Ashe", "Zyra", "Galio",
            "Ornn", "Skarner", "Poppy", "Vi", "Jax", "Neeko", "Rakan", "Blitzcrank", "Pyke",
            "Sion", "Nunu", "Lillia", "Taliyah", "JarvanIV", "Braum", "Zilean", "Veigar", "Cassiopeia"}
EARLY_JUNGLERS = {"LeeSin", "Elise", "Nidalee", "XinZhao", "JarvanIV", "RekSai", "Shaco", "Rengar",
                  "Briar", "Naafiri", "Graves", "Kindred", "Ekko", "Talon", "Qiyana", "Volibear",
                  "Warwick", "Hecarim", "Pantheon", "MonkeyKing", "Vi", "Ambessa"}
SCALING_JUNGLERS = {"Karthus", "MasterYi", "Belveth", "Shyvana", "Kayn", "Udyr", "Viego",
                    "Fiddlesticks", "Evelynn", "Kindred", "Diana", "Lillia", "Gragas", "Zac"}
AP_DAMAGE = {"Diana", "Evelynn", "Fiddlesticks", "Karthus", "Lillia", "Elise", "Nidalee", "Ekko",
             "Gragas", "Taliyah", "Brand", "Zac", "Amumu", "Sylas", "Mordekaiser", "Gwen", "Shyvana",
             "Nunu", "Rammus", "Zyra", "Morgana", "Teemo", "Kennen", "Kayle"}
ATTACK_SPEED = {"MasterYi", "Belveth", "Kindred", "Kayle", "Shyvana", "Udyr", "Warwick", "Viego"}

KEYSTONE_OVERRIDES = {
    "Lillia": "Conqueror", "Karthus": "DarkHarvest", "Brand": "DarkHarvest", "Evelynn": "Electrocute",
    "Kindred": "LethalTempo", "MasterYi": "LethalTempo", "Belveth": "LethalTempo", "LeeSin": "Conqueror",
    "Elise": "DarkHarvest", "Nidalee": "DarkHarvest", "Shaco": "HailOfBlades", "Rengar": "Electrocute",
    "Khazix": "DarkHarvest", "Graves": "FleetFootwork", "Ekko": "DarkHarvest", "Diana": "Conqueror",
    "Amumu": "VeteranAftershock", "Sejuani": "VeteranAftershock", "Zac": "VeteranAftershock",
    "Rammus": "VeteranAftershock", "Nunu": "PhaseRush", "Hecarim": "PhaseRush", "Gragas": "PhaseRush",
    "Volibear": "Conqueror", "Viego": "Conqueror", "Udyr": "Conqueror", "Warwick": "LethalTempo",
    "Kayn": "Conqueror", "Briar": "Conqueror", "Fiddlesticks": "DarkHarvest", "Taliyah": "ArcaneComet",
    "Nocturne": "Conqueror", "Vi": "Conqueror", "XinZhao": "Conqueror", "JarvanIV": "Conqueror",
    "MonkeyKing": "Conqueror", "RekSai": "Conqueror", "Skarner": "VeteranAftershock", "Ivern": "SummonAery",
    "Naafiri": "Electrocute", "Talon": "Electrocute", "Qiyana": "Electrocute", "Kayle": "LethalTempo",
}
CLASS_KEYSTONE = {"Assassin": "Electrocute", "Fighter": "Conqueror", "Tank": "VeteranAftershock",
                  "Mage": "DarkHarvest", "Marksman": "LethalTempo", "Support": "SummonAery"}

# Preferred minor runes per tree, per slot (keys or names, first match wins).
MINOR_PREFS = {
    "Precision": [["Triumph", "PresenceOfMind"], ["LegendAlacrity", "LegendHaste", "LegendBloodline"],
                  ["CoupDeGrace", "LastStand", "CutDown"]],
    "Domination": [["SuddenImpact", "CheapShot", "TasteOfBlood"], ["SixthSense", "GrislyMementos"],
                   ["TreasureHunter", "RelentlessHunter", "UltimateHunter"]],
    "Resolve": [["Demolish", "ShieldBash", "FontOfLife"], ["Conditioning", "BonePlating", "SecondWind"],
                ["Overgrowth", "Unflinching", "Revitalize"]],
    "Sorcery": [["NimbusCloak", "ManaflowBand", "NullifyingOrb"], ["Celerity", "Transcendence", "AbsoluteFocus"],
                ["Waterwalking", "Scorch", "GatheringStorm"]],
    "Inspiration": [["MagicalFootwear", "HextechFlashtraption", "CashBack"], ["TripleTonic", "TimeWarpTonic"],
                    ["CosmicInsight", "ApproachVelocity", "JackOfAllTrades"]],
}
SHARD_ADAPTIVE, SHARD_AS, SHARD_HASTE = 5008, 5005, 5007
SHARD_MS, SHARD_HP_SCALE, SHARD_HP, SHARD_TENACITY = 5010, 5001, 5011, 5013


def champ_role_class(ch):
    tags = ch.get("tags", [])
    return tags[0] if tags else "Fighter"


def damage_type(ch):
    if ch["id"] in AP_DAMAGE:
        return "AP"
    info = ch.get("info", {})
    return "AP" if info.get("magic", 0) > info.get("attack", 0) else "AD"


# ---------------------------------------------------------------- comp profile
def team_profile(gd, champs):
    champs = [c for c in champs if c]
    p = {"count": len(champs), "ap": 0, "ad": 0, "tanks": 0, "squishies": 0, "assassins": 0,
         "healers": [], "cc": [], "names": [gd.champ_name(c) for c in champs]}
    for c in champs:
        if damage_type(c) == "AP":
            p["ap"] += 1
        else:
            p["ad"] += 1
        tags = c.get("tags", [])
        if "Tank" in tags or c.get("info", {}).get("defense", 0) >= 7:
            p["tanks"] += 1
        elif "Marksman" in tags or "Mage" in tags or "Assassin" in tags:
            p["squishies"] += 1
        if "Assassin" in tags:
            p["assassins"] += 1
        if c["id"] in HEALERS:
            p["healers"].append(gd.champ_name(c))
        if c["id"] in HEAVY_CC:
            p["cc"].append(gd.champ_name(c))
    n = max(1, len(champs))
    p["ap_share"] = round(100 * p["ap"] / n)
    p["ad_share"] = 100 - p["ap_share"] if champs else 0
    return p


def enemy_notes(gd, enemy_profile, enemy_jungler):
    notes = []
    if enemy_profile["count"] == 0:
        return ["Noch keine Gegner sichtbar."]
    if enemy_profile["ap_share"] >= 60:
        notes.append(f"Gegner {enemy_profile['ap_share']}% AP → Magieresistenz lohnt sich.")
    elif enemy_profile["ad_share"] >= 60:
        notes.append(f"Gegner {enemy_profile['ad_share']}% AD → Rüstung lohnt sich.")
    if enemy_profile["tanks"] >= 2:
        notes.append(f"{enemy_profile['tanks']} Tanks → Prozentschaden / Pen einplanen.")
    if len(enemy_profile["healers"]) >= 2:
        notes.append("Viel Heilung (" + ", ".join(enemy_profile["healers"]) + ") → Anti-Heal früh.")
    if len(enemy_profile["cc"]) >= 3:
        notes.append("Viel CC (" + ", ".join(enemy_profile["cc"]) + ") → Tenacity / Merc Treads.")
    if enemy_profile["assassins"] >= 2:
        notes.append("Mehrere Assassinen → nicht alleine facecheck, Ward-Kontrolle.")
    if enemy_jungler:
        jid, jn = enemy_jungler["id"], gd.champ_name(enemy_jungler)
        if jid in EARLY_JUNGLERS:
            notes.append(f"{jn} ist stark im Early → Invade-Gefahr Lvl 1-3, eigene Buffs warden.")
        if jid in SCALING_JUNGLERS:
            notes.append(f"{jn} skaliert → früh Druck machen, sein Farm stören (Counter-Jungle).")
    return notes


# ---------------------------------------------------------------- pick advice
def score_pick(gd, cand, ally_champs, enemy_profile):
    score, why = 50, []
    cls, dmg = champ_role_class(cand), damage_type(cand)
    ally = team_profile(gd, ally_champs)
    if ally["count"] >= 2:
        if ally["ad_share"] >= 75 and dmg == "AP":
            score += 12; why.append("bringt AP ins AD-lastige Team")
        if ally["ap_share"] >= 75 and dmg == "AD":
            score += 12; why.append("bringt AD ins AP-lastige Team")
        if ally["tanks"] == 0 and "Tank" in cand.get("tags", []):
            score += 10; why.append("Team hat keinen Frontliner")
    if enemy_profile["squishies"] >= 3 and cls == "Assassin":
        score += 10; why.append("viele Squishies auf Gegnerseite")
    if enemy_profile["tanks"] >= 2 and cls == "Fighter":
        score += 8; why.append("Bruiser gut gegen Tanks")
    if enemy_profile["assassins"] >= 2 and "Tank" in cand.get("tags", []):
        score += 6; why.append("hält Dive aus")
    if len(enemy_profile["cc"]) >= 3 and cls == "Assassin":
        score -= 8; why.append("riskant gegen viel CC")
    if cand["id"] in HEAVY_CC:
        score += 4; why.append("eigenes CC für Ganks")
    return {"champion": gd.champ_name(cand), "id": cand["id"], "score": score,
            "why": why or ["neutral gegen diese Comp"]}


def pick_suggestions(gd, pool, ally_champs, enemy_profile, banned_or_taken):
    out = []
    for ref in pool:
        c = gd.champ(ref)
        if not c or c["id"] in banned_or_taken:
            continue
        out.append(score_pick(gd, c, ally_champs, enemy_profile))
    return sorted(out, key=lambda x: -x["score"])


# ---------------------------------------------------------------- runes
def _norm(s):
    return re.sub(r"[^a-z]", "", s.lower())


def _find_rune(gd, key):
    k = _norm(key)
    for tree in gd.runes:
        for si, slot in enumerate(tree["slots"]):
            for r in slot["runes"]:
                if _norm(r["key"]) == k or _norm(r["name"]) == k:
                    return tree, si, r
    return None, None, None


def _pick_minor(tree, slot_idx, prefs):
    slot = tree["slots"][slot_idx]["runes"]
    for p in prefs:
        for r in slot:
            if _norm(r["key"]) == _norm(p) or _norm(r["name"]) == _norm(p):
                return r
    return slot[0]


def recommend_runes(gd, my_champ, enemy_profile, preset=None):
    preset = preset or {}
    ks_key = preset.get("keystone") or KEYSTONE_OVERRIDES.get(my_champ["id"]) \
        or CLASS_KEYSTONE.get(champ_role_class(my_champ), "Conqueror")
    tree, _, ks = _find_rune(gd, ks_key)
    if not tree:
        tree, _, ks = _find_rune(gd, "Conqueror")
    why = [f"Keystone {gd.rune_disp.get(ks['id'], ks['name'])} passt zu {gd.champ_name(my_champ)}"]
    prefs = MINOR_PREFS.get(tree["key"], [[], [], []])
    primary = [ks["id"]] + [_pick_minor(tree, i, prefs[i - 1])["id"] for i in (1, 2, 3)]

    # secondary tree
    heavy_cc = len(enemy_profile["cc"]) >= 3
    burst = enemy_profile["assassins"] >= 2
    if preset.get("secondary"):
        sec_key = preset["secondary"]
    elif (heavy_cc or burst) and tree["key"] != "Resolve":
        sec_key = "Resolve"; why.append("Resolve sekundär wegen CC/Burst")
    elif tree["key"] == "Precision":
        sec_key = "Domination"
    else:
        sec_key = "Precision"
    sec = next((t for t in gd.runes if t["key"] == sec_key and t["key"] != tree["key"]), None) \
        or next(t for t in gd.runes if t["key"] != tree["key"])
    sprefs = MINOR_PREFS.get(sec["key"], [[], [], []])
    if sec["key"] == "Resolve" and heavy_cc:
        sprefs = [[], ["BonePlating", "Conditioning"], ["Unflinching", "Overgrowth"]]
    slots = (2, 3) if sec["key"] in ("Resolve", "Precision") else (1, 3)
    secondary = [_pick_minor(sec, i, sprefs[i - 1])["id"] for i in slots]

    shards = [SHARD_AS if my_champ["id"] in ATTACK_SPEED else SHARD_ADAPTIVE,
              SHARD_MS if champ_role_class(my_champ) == "Assassin" else SHARD_ADAPTIVE,
              SHARD_TENACITY if heavy_cc else SHARD_HP]
    if heavy_cc:
        why.append("Tenacity-Shard gegen CC")
    perks = primary + secondary + shards
    return {
        "primaryStyleId": tree["id"], "subStyleId": sec["id"], "selectedPerkIds": perks,
        "display": {"primary": gd.rune_disp.get(tree["id"], tree["name"]),
                    "secondary": gd.rune_disp.get(sec["id"], sec["name"]),
                    "runes": [gd.rune_disp.get(r, str(r)) for r in primary + secondary]},
        "why": why,
    }


# ---------------------------------------------------------------- items
def _has_tag(it, *tags):
    return any(t in it.get("tags", []) for t in tags)


def _desc(it):
    return re.sub(r"<[^>]+>", " ", it.get("description", ""))


def _profile_weights(cls, dmg):
    if cls == "Tank":
        return {"Health": 3, "Armor": 2, "SpellBlock": 2, "AbilityHaste": 1}
    if dmg == "AP":
        return {"SpellDamage": 3, "MagicPenetration": 2, "AbilityHaste": 1,
                "Health": 1 if cls == "Fighter" else 0}
    if cls == "Assassin":
        return {"Damage": 3, "ArmorPenetration": 3, "AbilityHaste": 1}
    if cls == "Marksman":
        return {"Damage": 2, "AttackSpeed": 3, "CriticalStrike": 2, "OnHit": 2}
    return {"Damage": 2, "Health": 2, "AbilityHaste": 2, "ArmorPenetration": 1}  # Fighter


def _boots(gd, pool, enemy_profile, cls, dmg, my_id):
    boots = {iid: it for iid, it in pool.items() if _has_tag(it, "Boots")}
    if len(enemy_profile["cc"]) >= 3 or enemy_profile["ap_share"] >= 65:
        want, why = "Mercury", f"{enemy_profile['ap_share']} % geschätzter Magiedruck oder mehrere CC-Quellen"
    elif enemy_profile["ad_share"] >= 65 or enemy_profile.get("auto_attackers", 0) >= 2:
        want, why = "Steelcaps", f"{enemy_profile['ad_share']} % geschätzter physischer Druck und gegnerische Auto-Attacks"
    elif my_id in ATTACK_SPEED:
        want, why = "Berserker", "Attack-Speed-Champion"
    elif dmg == "AP":
        want, why = "Sorcerer", "Magiedurchdringung"
    else:
        want, why = "Ionian", "Ability Haste"
    iid = gd.find_item(want, boots)
    return (iid, why) if iid else (None, None)


def recommend_build(gd, my_champ, enemy_profile, owned=None, enemy_live=None, preset=None):
    """enemy_live: optional dict from live game (armor_stackers, mr_stackers, fed list)."""
    owned = set(owned or [])
    preset = preset or {}
    pool = gd.completed_items()
    cls, dmg = champ_role_class(my_champ), damage_type(my_champ)
    weights = _profile_weights(cls, dmg)

    scored = []
    for iid, it in pool.items():
        if _has_tag(it, "Boots") or iid in owned:
            continue
        if any(t in it.get("tags", []) for t in ("GoldPer", "Vision", "Jungle", "Trinket")):
            continue
        s = sum(w for tag, w in weights.items() if _has_tag(it, tag))
        if dmg == "AD" and _has_tag(it, "SpellDamage") and not _has_tag(it, "Damage"):
            s -= 5
        if dmg == "AP" and _has_tag(it, "Damage", "CriticalStrike") and not _has_tag(it, "SpellDamage"):
            s -= 5
        if cls != "Marksman" and _has_tag(it, "CriticalStrike"):
            s -= 3
        if s > 0:
            scored.append((s, iid))
    scored.sort(key=lambda x: (-x[0], x[1]))

    core = []
    for name in preset.get("core", []):
        iid = gd.find_item(name, pool)
        if iid and iid not in owned:
            core.append({"id": iid, "name": gd.item_name(iid), "why": "dein Preset"})
    for _, iid in scored:
        if len(core) >= 3:
            break
        if all(c["id"] != iid for c in core):
            core.append({"id": iid, "name": gd.item_name(iid), "why": "passt zum Champion-Profil"})

    situational = []

    def add(pred, why, limit=1):
        n = 0
        for iid, it in pool.items():
            if n >= limit:
                break
            if iid in owned or any(x["id"] == iid for x in core + situational) or _has_tag(it, "Boots"):
                continue
            if pred(it):
                situational.append({"id": iid, "name": gd.item_name(iid), "why": why}); n += 1

    wounds = lambda it: "Grievous Wounds" in _desc(it) or "Wounds" in _desc(it)
    off_ok = (lambda it: _has_tag(it, "SpellDamage")) if dmg == "AP" else \
        (lambda it: _has_tag(it, "Damage") or _has_tag(it, "Health"))
    if len(enemy_profile["healers"]) >= 2 and (enemy_live is None or any(i in pool and not _has_tag(pool[i], "Boots") for i in owned)):
        add(lambda it: wounds(it) and (off_ok(it) or cls == "Tank"), "Anti-Heal gegen " + ", ".join(enemy_profile["healers"]))
    armor_st = (enemy_live or {}).get("armor_stackers", 0)
    mr_st = (enemy_live or {}).get("mr_stackers", 0)
    if dmg == "AD" and (enemy_profile["tanks"] >= 2 or armor_st >= 2):
        add(lambda it: _has_tag(it, "ArmorPenetration"), f"Gegner stacken Rüstung ({max(armor_st, enemy_profile['tanks'])})")
    if dmg == "AP" and (enemy_profile["tanks"] >= 2 or mr_st >= 2):
        add(lambda it: _has_tag(it, "MagicPenetration"), f"Gegner stacken MR ({max(mr_st, enemy_profile['tanks'])})")
    fed = (enemy_live or {}).get("fed", [])
    fed_ap = [f for f in fed if f["dmg"] == "AP"]
    fed_ad = [f for f in fed if f["dmg"] == "AD"]
    if enemy_profile["ap_share"] >= 60 or fed_ap:
        who = ", ".join(f["name"] for f in fed_ap) or "AP-lastiges Team"
        add(lambda it: _has_tag(it, "SpellBlock") and (cls == "Tank" or off_ok(it) or _has_tag(it, "Health")),
            f"Magieresistenz gegen {who}")
    if enemy_profile["ad_share"] >= 60 or fed_ad:
        who = ", ".join(f["name"] for f in fed_ad) or "AD-lastiges Team"
        add(lambda it: _has_tag(it, "Armor") and (cls == "Tank" or off_ok(it) or _has_tag(it, "Health")),
            f"Rüstung gegen {who}")

    boot_id, boot_why = _boots(gd, pool, enemy_profile, cls, dmg, my_champ["id"])
    boots = {"id": boot_id, "name": gd.item_name(boot_id), "why": boot_why} if boot_id else None
    if boot_id in owned:
        boots = None
    return {"core": core, "boots": boots, "situational": situational,
            "companion": jungle_companion(gd, enemy_profile, cls)}


def jungle_companion(gd, enemy_profile, cls):
    if cls == "Tank" or enemy_profile["assassins"] >= 2:
        name, why = "Mosstomper", "Schild – Tank/gegen Burst"
    elif cls == "Assassin" or cls == "Mage":
        name, why = "Scorchclaw", "Burn-Schaden für Ganks"
    else:
        name, why = "Gustwalker", "Movespeed in Busch/Fluss"
    iid = gd.find_item(name)
    return {"id": iid, "name": gd.item_name(iid) if iid else name, "why": why}
