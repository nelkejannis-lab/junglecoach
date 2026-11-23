"""Synthetic minimap test: draw icons with red/blue rings on a fake map and find them."""
import unittest

import cv2
import numpy as np

from jc.minimap import Detector, MAP_MAX


def fake_icon(seed):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (120, 120, 3), dtype=np.uint8)
    img = cv2.GaussianBlur(cv2.resize(rng.integers(0, 255, (6, 6, 3), dtype=np.uint8), (120, 120),
                                      interpolation=cv2.INTER_CUBIC), (9, 9), 0)
    cv2.putText(img, str(seed), (20, 85), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (255, 255, 255), 6)
    return img


def place(minimap, icon, cx, cy, s, ring):
    t = cv2.resize(icon, (s, s), interpolation=cv2.INTER_AREA)
    mask = np.zeros((s, s), np.uint8); cv2.circle(mask, (s // 2, s // 2), s // 2 - 1, 255, -1)
    x0, y0 = cx - s // 2, cy - s // 2
    roi = minimap[y0:y0 + s, x0:x0 + s]
    roi[mask > 0] = t[mask > 0]
    cv2.circle(minimap, (cx, cy), int(s * 0.47), ring, 2)


class MinimapTests(unittest.TestCase):
    def test_find_enemy_not_ally(self):
        rng = np.random.default_rng(0)
        mm = np.full((280, 280, 3), (40, 70, 45), np.uint8)
        mm = cv2.add(mm, rng.integers(0, 25, mm.shape, dtype=np.uint8))
        enemy, ally, other = fake_icon(1), fake_icon(2), fake_icon(3)
        place(mm, enemy, 200, 90, 24, (40, 40, 220))   # red ring (BGR)
        place(mm, ally, 80, 200, 24, (220, 120, 40))   # blue ring
        d = Detector()
        sizes = [20, 22, 24, 26]
        hit = d.find(mm, "e", enemy, sizes)
        self.assertIsNotNone(hit)
        gx, gy, _ = hit
        self.assertAlmostEqual(gx, 200 / 280 * MAP_MAX, delta=600)
        self.assertAlmostEqual(gy, (1 - 90 / 280) * MAP_MAX, delta=600)
        self.assertIsNone(d.find(mm, "a", ally, sizes))     # blue ring -> not an enemy
        self.assertIsNone(d.find(mm, "o", other, sizes))    # not on map


if __name__ == "__main__":
    unittest.main()


class TrackerTests(unittest.TestCase):
    def test_update_and_lock(self):
        import sys
        sys.path.insert(0, "tests")
        from test_all import GD
        from jc import review, overlay
        from jc.minimap import MinimapTracker, is_custom_game
        rng = np.random.default_rng(0)
        mm = np.full((280, 280, 3), (40, 70, 45), np.uint8)
        mm = cv2.add(mm, rng.integers(0, 25, mm.shape, dtype=np.uint8))
        icon = fake_icon(5)
        place(mm, icon, 150, 170, 24, (40, 40, 220))
        tr = MinimapTracker(GD, {})
        tr.icons = {"Elise": icon, "Leona": fake_icon(9)}
        out = tr.update(None, 200, [("Elise", "Elise", True), ("Leona", "Leona", False)],
                        lambda x, y: review.region(x, y, 100), minimap_img=mm)
        self.assertEqual(out["last_seen"][0]["champion"], "Elise")
        self.assertTrue(out["last_seen"][0]["visible"])
        self.assertEqual(len(out["sightings"]), 1)
        lines = overlay.format_lines({"live": {"game_time": "3:20", "cs_per_min": 6, "enemy": {}},
                                      "minimap": {"on": True, "data": out}})
        self.assertTrue(any("Elise" in t and "jetzt sichtbar" in t for t, _ in lines))
        # lock: no client / ranked -> not custom
        class FakeLCU:
            def __init__(self, s): self.s = s
            def get(self, p): return self.s
        self.assertFalse(is_custom_game(None))
        self.assertFalse(is_custom_game(FakeLCU({"gameData": {"isCustomGame": False, "queue": {"id": 420}}})))
        self.assertTrue(is_custom_game(FakeLCU({"gameData": {"isCustomGame": True, "queue": {"id": -1}}})))
