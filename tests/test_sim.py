"""Tests for the build simulation (formula parser + engine) on small synthetic game data."""
import unittest

import importlib

from jc.sim import engine as E

D = importlib.import_module("jc.sim.data")


def champ(name, key, ad=60, hp=600, rng=125, tags=("Fighter",), q_ap=0.0, q_bad=0.0, q_base=100.0, onhit_pct=0.0):
    lv = [9, 11, 13, 15, 17]
    f = {f"{r}:{l}": {"base": q_base * r, "ap": q_ap, "bad": q_bad} for r in range(7) for l in lv}
    ab = {"Q": {"cd": [8, 8, 7, 6, 5, 4, 4], "cast": 0.25, "dmg": [{"type": "magic" if q_ap else "physical",
                                                                     "kind": "flat", "dot": False, "f": f}]},
          "W": {"cd": [10] * 7, "cast": 0.25, "dmg": []}, "E": {"cd": [10] * 7, "cast": 0.25, "dmg": []},
          "R": {"cd": [100] * 7, "cast": 0.25, "dmg": []}}
    if onhit_pct:
        ab["P"] = {"cd": [], "cast": 0, "onhit": True,
                   "dmg": [{"type": "magic", "kind": "pct_max", "dot": False,
                            "f": {f"{r}:{l}": {"base": onhit_pct} for r in range(7) for l in lv}}]}
    return {"name": name, "key": key, "tags": list(tags), "partype": "Mana",
            "stats": {"hp": hp, "hp_l": 100, "ad": ad, "ad_l": 3, "armor": 30, "armor_l": 4, "mr": 30, "mr_l": 2,
                      "as": 0.65, "as_ratio": 0.65, "as_l": 2, "range": rng, "ms": 340, "crit_mult": 2},
            "abilities": ab}


def item(name, gold, boots=False, tags=(), **stats):
    return {"name": name, "name_de": name, "gold": gold, "tags": list(tags), "boots": boots, "stats": stats, "dv": {}}


ITEMS = {
    "9001": item("AP Big", 3000, ap=110), "9002": item("AP Pen", 3000, ap=80, mpen_pct=40),
    "9003": item("AD Big", 3000, ad=70), "9004": item("Tank", 3000, hp=600, armor=40),
    "9005": item("AP HP", 3000, ap=70, hp=300), "9006": item("Boots", 1100, boots=True, mpen=12),
    "9007": item("AD Crit", 3000, ad=50, crit=25),
}
LEVELS = [9, 11, 13, 15, 17]


class FormulaTest(unittest.TestCase):
    def test_named_values_and_stat_ratios(self):
        spell = {"DataValues": [{"name": "Base", "values": [0, 10, 20, 30, 40, 50, 60]},
                                {"name": "Ratio", "values": [0.5] * 7}]}
        calc = {"__type": "GameCalculation", "mFormulaParts": [
            {"__type": "NamedDataValueCalculationPart", "mDataValue": "Base"},
            {"__type": "StatByNamedDataValueCalculationPart", "mDataValue": "Ratio"},             # AP
            {"__type": "StatByCoefficientCalculationPart", "mStat": 2, "mStatFormula": 2, "mCoefficient": 0.8}]}
        self.assertEqual(D.evaluate(calc, spell, {}, 3, 9), {"base": 30.0, "ap": 0.5, "bad": 0.8})

    def test_modified_calculation_and_level_scaling(self):
        calcs = {"Inner": {"__type": "GameCalculation", "mFormulaParts": [
            {"__type": "ByCharLevelInterpolationCalculationPart", "mStartValue": 10, "mEndValue": 180}]}}
        mod = {"__type": "GameCalculationModified", "mModifiedGameCalculation": "Inner",
               "mMultiplier": {"__type": "NumberCalculationPart", "mNumber": 0.5}}
        self.assertAlmostEqual(D.evaluate(mod, {}, calcs, 1, 18)["base"], 90.0)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.ap = champ("Apper", 1, q_ap=1.6, q_base=60, onhit_pct=0.01)
        self.ad = champ("Adder", 2, q_bad=1.2, q_base=60, rng=550, tags=("Marksman",))
        self.enemies = [champ("Tanky", 3, hp=700, tags=("Tank",)), champ("Squish", 4, hp=520, tags=("Mage",))]

    def test_ap_items_raise_ap_champion_damage(self):
        off0, _, _ = E.evaluate(self.ap, [], ITEMS, self.enemies, LEVELS, 11, 1)
        off1, _, _ = E.evaluate(self.ap, ["9001"], ITEMS, self.enemies, LEVELS, 11, 1)
        off2, _, _ = E.evaluate(self.ap, ["9003"], ITEMS, self.enemies, LEVELS, 11, 1)
        self.assertGreater(off1, off2)
        self.assertGreater(off1, off0)

    def test_tank_item_raises_defense(self):
        _, d0, _ = E.evaluate(self.ad, [], ITEMS, self.enemies, LEVELS, 11, 1)
        _, d1, _ = E.evaluate(self.ad, ["9004"], ITEMS, self.enemies, LEVELS, 11, 1)
        self.assertGreater(d1, d0 * 1.2)

    def test_best_build_follows_kit(self):
        r = E.best_build(self.ap, ITEMS, self.enemies, LEVELS, n_items=3, beam=6, pool=list(ITEMS)[:5] + ["9007"])
        self.assertEqual(len(r["items"]), 3)
        self.assertNotIn("9003", r["items"][:2])          # AD item is not core for an AP champion
        self.assertEqual(r["boots"], "9006")
        self.assertIn(r["items"][0], r["explain"])

    def test_live_build_keeps_owned_items(self):
        r = E.best_build(self.ap, ITEMS, self.enemies, LEVELS, n_items=3, beam=6, pool=list(ITEMS), fixed=["9005"])
        self.assertEqual(r["items"][0], "9005")

    def test_real_enemy_items_change_targets(self):
        E.REAL_ENEMY_ITEMS.clear()
        t0 = E.enemy_target(self.enemies[1], 11, 2, ITEMS)
        E.REAL_ENEMY_ITEMS["Squish"] = ["9004", "9004"]
        t1 = E.enemy_target(self.enemies[1], 11, 2, ITEMS)
        E.REAL_ENEMY_ITEMS.clear()
        self.assertGreater(t1["armor"], t0["armor"])

    def test_item_class_pool_uses_popularity(self):
        pool = E.item_pool(ITEMS, self.ap, {"9001": 40, "9002": 30, "9005": 20})
        self.assertIn("9002", pool)
        self.assertNotIn("9007", pool)

    def test_penetration_and_resist(self):
        st = {"apen_pct": 0, "lethality": 0, "mpen_pct": 0.4, "mpen": 0}
        self.assertAlmostEqual(E.mitigate("magic", 100, {"armor": 0, "mr": 100}, st), 100 * 100 / 160)
        self.assertEqual(E.mitigate("true", 100, {"armor": 300, "mr": 300}, st), 100)


if __name__ == "__main__":
    unittest.main()
