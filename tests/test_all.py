"""Offline tests with a small synthetic Data Dragon. Run: python -m unittest discover tests"""
import unittest

from jc import analyzer, champselect, live, overlay, review
from jc.ddragon import GameData


def ch(key, cid, tags, atk, mag, dfn=3):
    return {"key": str(key), "id": cid, "name": cid, "tags": tags,
            "info": {"attack": atk, "magic": mag, "defense": dfn}}


CHAMPS = [ch(64, "LeeSin", ["Fighter", "Assassin"], 8, 3), ch(234, "Viego", ["Assassin", "Fighter"], 6, 2),
          ch(32, "Amumu", ["Tank", "Mage"], 2, 8, 6), ch(876, "Lillia", ["Fighter", "Mage"], 0, 10),
          ch(5, "XinZhao", ["Fighter", "Assassin"], 8, 3), ch(89, "Leona", ["Tank", "Support"], 4, 3, 8),
          ch(8, "Vladimir", ["Mage"], 2, 8), ch(54, "Malphite", ["Tank", "Fighter"], 5, 7, 9),
          ch(222, "Jinx", ["Marksman"], 9, 2), ch(16, "Soraka", ["Support", "Mage"], 2, 7),
          ch(60, "Elise", ["Mage", "Fighter"], 6, 7), ch(11, "MasterYi", ["Assassin", "Fighter"], 10, 2)]


def item(name, tags, total=3000, stats=None, desc="", into=None):
    d = {"name": name, "tags": tags, "gold": {"total": total, "purchasable": True}, "maps": {"11": True},
         "stats": stats or {}, "description": desc}
    if into:
        d["into"] = into
    return d


ITEMS = {
    "6692": item("Eclipse", ["Damage", "ArmorPenetration", "AbilityHaste"]),
    "3071": item("Black Cleaver", ["Damage", "Health", "AbilityHaste", "ArmorPenetration"]),
    "3053": item("Sterak's Gage", ["Damage", "Health"]),
    "6333": item("Death's Dance", ["Damage", "Armor", "AbilityHaste"], stats={"FlatArmorMod": 50}),
    "3033": item("Mortal Reminder", ["Damage", "ArmorPenetration"], desc="applies <status>Grievous Wounds</status>"),
    "3156": item("Maw of Malmortius", ["Damage", "SpellBlock", "AbilityHaste"], stats={"FlatSpellBlockMod": 40}),
    "3075": item("Thornmail", ["Armor", "Health"], stats={"FlatArmorMod": 75}, desc="Grievous Wounds"),
    "3065": item("Spirit Visage", ["Health", "SpellBlock", "AbilityHaste"], stats={"FlatSpellBlockMod": 60}),
    "3068": item("Sunfire Aegis", ["Health", "Armor"], stats={"FlatArmorMod": 50}),
    "3089": item("Rabadon's Deathcap", ["SpellDamage"]),
    "3135": item("Void Staff", ["SpellDamage", "MagicPenetration"]),
    "3165": item("Morellonomicon", ["SpellDamage", "Health"], desc="Grievous Wounds"),
    "3157": item("Zhonya's Hourglass", ["SpellDamage", "Armor"], stats={"FlatArmorMod": 50}),
    "3031": item("Infinity Edge", ["Damage", "CriticalStrike"]),
    "3047": item("Plated Steelcaps", ["Boots", "Armor"], total=1200),
    "3111": item("Mercury's Treads", ["Boots", "SpellBlock"], total=1200),
    "3158": item("Ionian Boots of Lucidity", ["Boots", "AbilityHaste"], total=1000),
    "3006": item("Berserker's Greaves", ["Boots", "AttackSpeed"], total=1100),
    "3020": item("Sorcerer's Shoes", ["Boots", "MagicPenetration"], total=1100),
    "1036": item("Long Sword", ["Damage"], total=350, into=["3071"]),
    "1101": item("Scorchclaw Pup", ["Jungle"], total=450),
    "1102": item("Gustwalker Hatchling", ["Jungle"], total=450),
    "1103": item("Mosstomper Seedling", ["Jungle"], total=450),
}


def tree(tid, key, keystones, slots):
    return {"id": tid, "key": key, "name": key, "slots": [
        {"runes": [{"id": tid * 10 + i, "key": k, "name": k} for i, k in enumerate(keystones)]}] + [
        {"runes": [{"id": tid * 100 + si * 10 + i, "key": k, "name": k} for i, k in enumerate(s)]}
        for si, s in enumerate(slots, 1)]}


RUNES = [
    tree(8000, "Precision", ["PressTheAttack", "LethalTempo", "FleetFootwork", "Conqueror"],
         [["AbsorbLife", "Triumph", "PresenceOfMind"], ["LegendAlacrity", "LegendHaste", "LegendBloodline"],
          ["CoupDeGrace", "CutDown", "LastStand"]]),
    tree(8100, "Domination", ["Electrocute", "DarkHarvest", "HailOfBlades"],
         [["CheapShot", "TasteOfBlood", "SuddenImpact"], ["SixthSense", "GrislyMementos", "DeepWard"],
          ["TreasureHunter", "RelentlessHunter", "UltimateHunter"]]),
    tree(8400, "Resolve", ["GraspOfTheUndying", "VeteranAftershock", "Guardian"],
         [["Demolish", "FontOfLife", "ShieldBash"], ["Conditioning", "SecondWind", "BonePlating"],
          ["Overgrowth", "Revitalize", "Unflinching"]]),
    tree(8200, "Sorcery", ["SummonAery", "ArcaneComet", "PhaseRush"],
         [["NullifyingOrb", "ManaflowBand", "NimbusCloak"], ["Transcendence", "Celerity", "AbsoluteFocus"],
          ["Scorch", "Waterwalking", "GatheringStorm"]]),
]

GD = GameData(raw={"champion": {"data": {c["id"]: c for c in CHAMPS}}, "item": {"data": ITEMS}, "runes": RUNES},
              version="test")
CFG = {"champion_pool": ["LeeSin", "Viego", "Amumu", "Lillia", "XinZhao"], "champion_presets": {}}


class Tests(unittest.TestCase):
    def test_profile(self):
        p = analyzer.team_profile(GD, [GD.champ(x) for x in ("Leona", "Vladimir", "Malphite", "Jinx", "Soraka")])
        self.assertEqual(p["count"], 5)
        self.assertIn("Vladimir", p["healers"]); self.assertIn("Leona", p["cc"])
        self.assertGreaterEqual(p["tanks"], 2)

    def test_runes_valid_structure(self):
        prof = analyzer.team_profile(GD, [GD.champ(x) for x in ("Leona", "Malphite", "Amumu")])
        for c in CHAMPS:
            r = analyzer.recommend_runes(GD, c, prof)
            self.assertEqual(len(r["selectedPerkIds"]), 9, c["id"])
            self.assertNotEqual(r["primaryStyleId"], r["subStyleId"])
        r = analyzer.recommend_runes(GD, GD.champ("LeeSin"), prof)
        self.assertEqual(r["selectedPerkIds"][0], 80003)  # Conqueror
        self.assertEqual(r["subStyleId"], 8400)  # Resolve vs heavy CC
        self.assertEqual(r["selectedPerkIds"][-1], analyzer.SHARD_TENACITY)
        yi = analyzer.recommend_runes(GD, GD.champ("MasterYi"), analyzer.team_profile(GD, []))
        self.assertEqual(yi["selectedPerkIds"][0], 80001)  # Lethal Tempo
        self.assertEqual(yi["selectedPerkIds"][6], analyzer.SHARD_AS)

    def test_build_adapts(self):
        enemy = analyzer.team_profile(GD, [GD.champ(x) for x in ("Vladimir", "Soraka", "Malphite", "Leona", "Amumu")])
        b = analyzer.recommend_build(GD, GD.champ("LeeSin"), enemy)
        names = [i["name"] for i in b["core"]]
        self.assertEqual(len(names), 3)
        self.assertNotIn("Rabadon's Deathcap", names); self.assertNotIn("Infinity Edge", names)
        self.assertTrue(any("Grievous" in GD.items[i["id"]]["description"] for i in b["situational"]))
        self.assertEqual(b["boots"]["name"], "Mercury's Treads")  # AP + CC heavy
        ap = analyzer.recommend_build(GD, GD.champ("Lillia"), enemy)
        self.assertTrue(all("SpellDamage" in GD.items[i["id"]]["tags"] for i in ap["core"]))

    def test_preset(self):
        b = analyzer.recommend_build(GD, GD.champ("LeeSin"), analyzer.team_profile(GD, []),
                                     preset={"core": ["Sterak"]})
        self.assertEqual(b["core"][0]["name"], "Sterak's Gage")

    def test_champselect(self):
        session = {"localPlayerCellId": 1,
                   "myTeam": [{"cellId": 0, "championId": 222}, {"cellId": 1, "championId": 0, "championPickIntent": 64,
                                                                  "assignedPosition": "jungle"}],
                   "theirTeam": [{"cellId": 5, "championId": 60, "assignedPosition": "jungle"},
                                 {"cellId": 6, "championId": 8}, {"cellId": 7, "championId": 0}],
                   "bans": {"myTeamBans": [234], "theirTeamBans": []}, "actions": []}
        out = champselect.analyze(GD, session, CFG)
        self.assertEqual(out["my_champion"], "LeeSin")
        self.assertEqual(out["enemy_jungler"], "Elise")
        self.assertTrue(any("Early" in n for n in out["notes"]))
        self.assertNotIn("Viego", [p["id"] for p in out["pick_suggestions"]])  # banned
        self.assertIn("runes", out); self.assertIn("build", out)

    def test_live(self):
        def pl(name, champ, team, pos, items=(), k=0, d=0, lvl=5, smite=False):
            return {"summonerName": name, "riotIdGameName": name, "championName": champ, "rawChampionName": "game_character_displayname_" + champ.replace(" ", ""), "team": team,
                    "position": pos, "level": lvl, "items": [{"itemID": i} for i in items],
                    "scores": {"kills": k, "deaths": d, "assists": 0, "creepScore": 60},
                    "summonerSpells": {"summonerSpellOne": {"displayName": "Smite" if smite else "Flash"},
                                       "summonerSpellTwo": {"displayName": "Flash"}}}
        data = {"gameData": {"gameTime": 600}, "activePlayer": {"riotIdGameName": "me"},
                "allPlayers": [pl("me", "Lee Sin", "ORDER", "JUNGLE", [3071]), pl("ally", "Jinx", "ORDER", "BOTTOM"),
                               pl("ej", "Elise", "CHAOS", "", [3157], k=6, d=1, lvl=7, smite=True),
                               pl("e2", "Malphite", "CHAOS", "TOP", [3075, 3068]),
                               pl("e3", "Leona", "CHAOS", "UTILITY", [3075, 3068])],
                "events": {"Events": [
                    {"EventName": "ChampionKill", "EventTime": 400, "KillerName": "ej", "VictimName": "ally", "Assisters": []},
                    {"EventName": "DragonKill", "EventTime": 500, "DragonType": "Fire"}]}}
        out = live.analyze(GD, data)
        self.assertEqual(out["enemy_jungler"]["champion"], "Elise")
        self.assertEqual(out["enemy_jungler"]["last_seen"][0]["where"], "BOTTOM")
        self.assertEqual(out["enemy"]["armor_stackers"], 2)
        self.assertEqual(out["objectives"][0]["respawn_in"], "3:20")
        self.assertNotIn("Black Cleaver", [i["name"] for i in out["build"]["core"]])  # already owned
        self.assertTrue(out["enemy"]["fed"])
        self.assertTrue(any("MR" in i["why"] or "Magie" in i["why"] for i in out["build"]["situational"]))
        lines = [t for t, _ in overlay.format_lines({"live": out})]
        self.assertTrue(lines[0].startswith("⏱ 10:00"))
        self.assertTrue(any("Elise" in t and "BOTTOM" in t for t in lines))
        self.assertTrue(any(t.startswith("Nächstes Item") for t in lines))
        self.assertEqual(overlay.format_lines({"live": None}), [])

    def test_review(self):
        def frame(m, x, y, gold, jcs):
            pf = {str(i): {"position": {"x": x, "y": y}, "totalGold": gold if i == 1 else 3000, "xp": 1000,
                           "jungleMinionsKilled": jcs} for i in range(1, 11)}
            return {"participantFrames": pf, "events": []}
        frames = [frame(m, 3000, 9000, 500 * m, 5 * m) for m in range(12)]
        frames[4]["events"] = [{"type": "CHAMPION_KILL", "timestamp": 230000, "killerId": 1,
                                "victimId": 6, "assistingParticipantIds": []},
                               {"type": "ELITE_MONSTER_KILL", "timestamp": 240000, "killerTeamId": 100,
                                "monsterType": "DRAGON"}]
        parts = [{"puuid": "me" if i == 1 else f"p{i}", "participantId": i, "teamId": 100 if i <= 5 else 200,
                  "teamPosition": ["JUNGLE", "TOP", "MIDDLE", "BOTTOM", "UTILITY"][(i - 1) % 5],
                  "championName": "LeeSin" if i == 1 else "X", "win": True, "kills": 1, "deaths": 0, "assists": 0}
                 for i in range(1, 11)]
        r = review.review_match({"metadata": {"matchId": "EUW1_1"}, "info": {"participants": parts}},
                                {"info": {"frames": frames}}, "me")
        self.assertEqual(r["early_side"], "Top-Seite")
        self.assertEqual(r["ganks_pre14"][0]["lane"], "JUNGLE")
        self.assertEqual(r["gold_diff_10"], 2000)
        self.assertEqual(len(r["replay"]["me"]), 12)
        self.assertEqual(r["replay"]["enemy_jg"][3]["area"], "eigener Jungle Top-Seite")
        self.assertEqual(review.region(1000, 8000), "Top-Lane")
        self.assertEqual(review.region(7400, 7400), "Mid-Lane")
        self.assertEqual(review.region(12000, 7000, 100), "gegnerischer Jungle Bot-Seite")
        self.assertEqual(review.region(9500, 5300), "Fluss unten")
        s = review.summarize([r] * 4)
        self.assertTrue(any("vorhersehbar" in t for t in s["tips"]))


if __name__ == "__main__":
    unittest.main()
