import tempfile
import unittest
from jc.learning import update_task

class LearningTests(unittest.TestCase):
    def test_task_is_stable_and_only_counts_future_matching_games(self):
        plan = {"focus": [{"metric": "deaths", "title": "Risiko", "target_val": 4, "target": "4", "higher": False, "last10": "2/10"}]}
        with tempfile.TemporaryDirectory() as root:
            first = update_task("a", ("Gwen", "JUNGLE"), plan, [], root, 100)
            games = [{"match_id": "one", "champion": "Gwen", "role": "JUNGLE", "game_start": 101, "stats": {"deaths": 3}},
                     {"match_id": "old", "champion": "Gwen", "role": "JUNGLE", "game_start": 99, "stats": {"deaths": 3}},
                     {"match_id": "support", "champion": "Gwen", "role": "UTILITY", "game_start": 102, "stats": {"deaths": 1}}]
            later = update_task("a", ("Gwen", "JUNGLE"), {"focus": []}, games, root, 200)
            self.assertEqual(first["id"], later["id"])
            self.assertEqual(later["matches"], ["one"])
            self.assertEqual(later["measured_hits"], 1)
            other = update_task("b", ("Gwen", "JUNGLE"), plan, [], root, 200)
            self.assertNotEqual(first["id"], other["id"])
