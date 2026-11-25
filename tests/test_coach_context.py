import time
import unittest
from jc import coach

class CohortTests(unittest.TestCase):
    def test_progress_does_not_mix_role_or_champion(self):
        now = time.time()
        focus = [{"metric": "kp", "title": "Präsenz", "role": "JUNGLE", "champion": "Gwen",
                  "target_val": 50, "higher": True}]
        history = [{"role": r, "champion": c, "game_start": now - i * 1000, "stats": {"kp": value}}
                   for i, (r, c, value) in enumerate([("JUNGLE", "Gwen", 40), ("JUNGLE", "Gwen", 50),
                                                      ("UTILITY", "Gwen", 100), ("JUNGLE", "Graves", 100)])]
        result = coach.progress(history, focus, now=now)[0]
        self.assertEqual(result["games"], 2)
        self.assertEqual(result["hit"], "1/2")
