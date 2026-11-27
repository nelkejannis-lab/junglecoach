import unittest

from jc.shop import recipe_plan
from jc import friends


class ShopCoachingTest(unittest.TestCase):
    def setUp(self):
        self.items = {
            100: {"gold": {"total": 350}},
            200: {"gold": {"total": 900}, "from": ["100", "100"]},
            300: {"gold": {"total": 1200}, "from": ["200"]},
            400: {"gold": {"total": 250}},
        }

    def test_duplicate_recipe_components_are_credited_once_each(self):
        self.assertEqual(recipe_plan(self.items, 300, [100], 500)["cost"], 850)
        self.assertEqual(recipe_plan(self.items, 300, [100, 100], 500)["cost"], 500)
        self.assertEqual(recipe_plan(self.items, 300, [100], 550)["parts"], (200,))

    def test_full_inventory_allows_upgrades_only_when_slots_are_available(self):
        self.assertTrue(recipe_plan(self.items, 300, [200] + [400] * 5, 300)["ready"])
        self.assertFalse(recipe_plan(self.items, 300, [400] * 6, 1200)["ready"])


class MobilePresenceTest(unittest.TestCase):
    def test_mobile_is_not_desktop_online(self):
        class Client:
            def get(self, _):
                return [{"gameName": "Moosi", "gameTag": "EUW", "availability": "mobile"}]

        states, _ = friends.presence(Client())
        self.assertEqual(states["moosi#euw"]["status"], "mobile")


class CueGateTest(unittest.TestCase):
    def test_combat_suppresses_regular_cues_but_keeps_objective_window(self):
        from jc.cue import CueGate

        gate = CueGate()
        ordinary = (3, "shop", "Buy", "good")
        objective = (1, "dragon-1", "Set up", "warn")
        self.assertEqual(gate.update(100, ordinary, 1000, 1000)["text"], "Buy")
        self.assertIsNone(gate.update(102, None, 800, 1000))
        self.assertEqual(gate.update(103, objective, 800, 1000)["text"], "Set up")
        self.assertIsNone(gate.update(111, objective, 800, 1000))
        self.assertIsNone(gate.update(151, (2, "second", "Reset", "info"), dead=True))


if __name__ == "__main__":
    unittest.main()
