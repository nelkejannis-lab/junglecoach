import json
import random
import unittest

from jc import meta
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from test_all import GD

ITEMS_META = [6692, 3071, 3053]   # Eclipse, Black Cleaver, Sterak
GEM = 6333                         # Death's Dance: rare but strong
ANTI_ELISE = 3156                  # Maw: great vs Elise in particular


def page(ks):
    return {"primary": 8000, "sub": 8100, "perks": [ks, 80101, 80201, 80301, 81101, 81201, 5008, 5008, 5011]}


def fake_match(i, rng):
    enemy = "Elise" if i % 3 == 0 else rng.choice(["Amumu", "XinZhao", "Viego"])
    items = list(ITEMS_META)
    wp = 0.50
    if rng.random() < 0.08:
        items[2] = GEM; wp = 0.66
    r_ = rng.random()
    ks = 80003 if r_ < 0.80 else 81000 if r_ < 0.90 else 84001  # Conqueror meta; Electrocute, Aftershock rare
    leona = rng.random() < 0.15
    if leona:
        wp -= 0.05
        if ks == 84001:
            wp += 0.35
    if enemy == "Elise":
        wp -= 0.12
        if rng.random() < 0.4:
            items.append(ANTI_ELISE); wp += 0.32
        if ks == 81000:
            wp += 0.1
    win = rng.random() < wp
    me = {"championName": "LeeSin", "teamId": 100, "win": win, "teamPosition": "JUNGLE",
          **{f"item{j}": it for j, it in enumerate(items)},
          "perks": {"styles": [{"style": 8000, "selections": [{"perk": ks}, {"perk": 80101}, {"perk": 80201}, {"perk": 80301}]},
                               {"style": 8100, "selections": [{"perk": 81101}, {"perk": 81201}]}],
                    "statPerks": {"offense": 5008, "flex": 5008, "defense": 5011}}}
    other = {"championName": enemy, "teamId": 200, "win": not win, "teamPosition": "JUNGLE", "item0": 3089,
             "perks": {"styles": [{"style": 8100, "selections": [{"perk": 81001}]}, {"style": 8000, "selections": []}]}}
    rest = [{"championName": c, "teamId": 200, "teamPosition": "UTILITY"}
            for c in (["Leona"] if leona else ["Soraka"]) + ["Jinx", "Vladimir", "Malphite"]]
    if ks == 84001:
        me["perks"]["styles"][0] = {"style": 8400, "selections": [{"perk": 84001}, {"perk": 840101}, {"perk": 840201}, {"perk": 840301}]}
    return {"metadata": {"matchId": f"EUW1_{i}"},
            "info": {"gameDuration": 1800, "gameVersion": "26.23.1.1", "gameCreation": i, "participants": [me, other] + rest}}


class MetaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = meta.connect(":memory:")
        rng = random.Random(3)
        for i in range(2400):
            meta.store_match(cls.db, fake_match(i, rng), rng.choice(meta.ALL_TIERS))
        cls.ms = meta.MetaStats(GD, cls.db)

    def test_meta_and_gem(self):
        c = self.ms.champion("LeeSin")
        self.assertTrue(c["enough"])
        self.assertEqual({m["id"] for m in c["meta_core"]} & set(ITEMS_META), set(ITEMS_META))
        self.assertIn(GEM, [g["id"] for g in c["hidden_items"]])
        self.assertNotIn(ANTI_ELISE, [g["id"] for g in c["hidden_items"]])  # matchup-specific, not general
        self.assertEqual(c["meta_keystone"]["id"], 80003)
        self.assertEqual(c["rune_page"]["perks"][0], c["meta_keystone"]["id"])
        wm = [m["adj_winrate"] for m in c["worst_matchups"]]
        self.assertEqual(wm, sorted(wm))

    def test_matchup(self):
        m = self.ms.matchup("LeeSin", "Elise")
        self.assertTrue(m["enough"])
        self.assertEqual(m["items"][0]["id"], ANTI_ELISE)
        self.assertEqual(m["keystones"][0]["id"], 81000)
        self.assertEqual(m["rune_page"]["perks"][0], 81000)
        self.assertEqual(len(m["rune_page"]["perks"]), 9)

    def test_bracket_and_tierlist(self):
        c = self.ms.champion("LeeSin", bracket="low")
        self.assertIn("Elo low", c["scope"])
        self.assertEqual(meta.bracket_of("gold"), "mid")
        self.assertTrue(any(t["champion"] == "LeeSin" for t in self.ms.tierlist(bracket="high")))
        self.assertTrue(any(t["champion"] == "LeeSin" for t in self.ms.tierlist()))

    def test_monte_carlo(self):
        p, up = meta._beta_prob_better(70, 100, 50, 100)
        self.assertGreater(p, 0.95); self.assertGreater(up, 0.1)


if __name__ == "__main__":
    unittest.main()


class ChampSelectMetaTests(unittest.TestCase):
    def test_apply(self):
        from jc import champselect
        MetaTests.setUpClass()
        session = {"localPlayerCellId": 1, "myTeam": [{"cellId": 1, "championId": 64}],
                   "theirTeam": [{"cellId": 5, "championId": 60, "assignedPosition": "jungle"}], "bans": {}, "actions": []}
        out = champselect.analyze(GD, session, {"champion_pool": ["LeeSin", "Viego"]}, MetaTests.ms)
        self.assertEqual(out["runes"]["source"], "data")
        self.assertEqual(out["runes"]["selectedPerkIds"][0], 81000)       # vs Elise
        self.assertIn("1/5 Gegner", out["runes"]["why"][0])
        self.assertEqual(out["build"]["core"][0]["id"], ANTI_ELISE)
        # full team with Leona -> Aftershock wins
        session["theirTeam"] = [{"cellId": 5, "championId": 32, "assignedPosition": "jungle"},
                                {"cellId": 6, "championId": 89}, {"cellId": 7, "championId": 222},
                                {"cellId": 8, "championId": 8}, {"cellId": 9, "championId": 54}]
        out = champselect.analyze(GD, session, {"champion_pool": ["LeeSin"]}, MetaTests.ms)
        self.assertEqual(out["runes"]["selectedPerkIds"][0], 84001)
        self.assertIn("Final – alle 5 Gegner", out["runes"]["why"][0])
        self.assertIn("Leona", out["runes"]["why"][0])
        session["theirTeam"] = [{"cellId": 5, "championId": 60, "assignedPosition": "jungle"}]
        out = champselect.analyze(GD, session, {"champion_pool": ["LeeSin", "Viego"]}, MetaTests.ms)
        self.assertTrue(any("vs Elise" in w for w in out["pick_suggestions"][0]["why"] + out["pick_suggestions"][-1]["why"]))


class MergeTests(unittest.TestCase):
    def test_merge_prune(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        a, b = meta.connect(os.path.join(d, "a.sqlite")), meta.connect(os.path.join(d, "b.sqlite"))
        rng = random.Random(1)
        for i in range(50):
            meta.store_match(a, fake_match(i, rng), "GOLD")
        for i in range(30, 90):
            meta.store_match(b, fake_match(i, rng), "GOLD")
        b.close()
        added = meta.merge_from(a, os.path.join(d, "b.sqlite"))
        self.assertGreaterEqual(added, 80)  # 40 new matches x 2 junglers
        meta.prune(a, max_rows=100)
        self.assertEqual(a.execute("SELECT COUNT(*) FROM games").fetchone()[0], 100)


class FocusTests(unittest.TestCase):
    def test_focus_and_migration(self):
        import sqlite3, tempfile, os
        path = os.path.join(tempfile.mkdtemp(), "old.sqlite")
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE games(match_id TEXT, champ TEXT, team INT, win INT, items TEXT, keystone INT, enemy TEXT, "
                    "patch TEXT, created INT, PRIMARY KEY(match_id, team))")
        old.execute("INSERT INTO games VALUES ('M1','LeeSin',100,1,'[]',8010,'Elise','26.23',1)")
        old.commit(); old.close()
        db = meta.connect(path)
        self.assertEqual(db.execute("SELECT role FROM games").fetchone()[0], "JUNGLE")
        m = fake_match(5, random.Random(1))
        m["info"]["participants"][2]["championName"] = "Gwen"; m["info"]["participants"][2]["teamPosition"] = "TOP"
        m["info"]["participants"][2]["teamId"] = 200; m["info"]["participants"][2]["win"] = False
        m["info"]["participants"].append({"championName": "Malphite", "teamId": 100, "teamPosition": "TOP", "win": True})
        n = meta.store_match(db, m, "GOLD", focus={"Gwen", "Graves"})
        self.assertEqual(n, len(m["info"]["participants"]))  # all players stored
        row = db.execute("SELECT champ, role, enemy FROM games WHERE champ='Gwen'").fetchone()
        self.assertEqual(row, ("Gwen", "TOP", "Malphite"))
