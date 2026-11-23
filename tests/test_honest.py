import unittest

from jc import meta, recorder


class HonestTests(unittest.TestCase):
    def test_trade_is_not_a_mistake(self):
        ev = [{"EventName": "DragonKill", "EventTime": 600, "KillerName": "e1", "DragonType": "Fire"},
              {"EventName": "TurretKilled", "EventTime": 620, "TurretKilled": "Turret_T2_R_03_A", "KillerName": "a1"}]
        out = recorder.detect_mistakes({"events": {"Events": ev}}, {"me"}, "ORDER", set(),
                                       {"e1": "CHAOS", "a1": "ORDER", "me": "ORDER"}, {})
        self.assertEqual(out[0]["type"], "objective_trade")
        self.assertEqual(out[0]["kind"], "review")
        out = recorder.detect_mistakes({"events": {"Events": ev[:1]}}, {"me"}, "ORDER", set(),
                                       {"e1": "CHAOS", "me": "ORDER"}, {})
        self.assertEqual(out[0]["type"], "objective_lost")

    def test_item_bias_removed(self):
        # item 1 only appears in long games with 3 items; win depends only on item count, not on item 1
        rows = []
        for i in range(400):
            long_game = i % 2 == 0
            items = [1, 2, 3] if long_game and i % 4 == 0 else [2, 3, 4] if long_game else [2]
            win = 1 if (long_game and i % 3) else 0
            rows.append((win, items, None, "E", None, [], "JUNGLE"))
        raw = sum(r[0] for r in rows if 1 in r[1]) / sum(1 for r in rows if 1 in r[1])
        base = sum(r[0] for r in rows) / len(rows)
        self.assertGreater(raw, base + 0.15)  # naive comparison looks great
        prob, up, _ = meta._stratified_effect(rows, lambda r: 1 in r[1], lambda r: len(r[1]))
        self.assertLess(abs(up), 0.08)  # corrected: no real effect
        self.assertLess(prob, 0.9)


if __name__ == "__main__":
    unittest.main()
