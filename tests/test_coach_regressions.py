"""Regression cases for the actual app -> benchmark -> coach path."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from jc import app, coach, friends, gamescore, profile

class CoachPipelineTests(unittest.TestCase):
    def test_plan_builds_caches_and_invalidates_without_name_error(self):
        dist = {"jcs10": list(range(30, 100)), "kp": list(range(20, 90)),
                "vpm": [i / 100 for i in range(20, 180)], "deaths": list(range(1, 12))}
        benchmark = {"dist": dist, "games": 100, "scope": "Gwen Jungle"}
        stats = SimpleNamespace(db=SimpleNamespace(total_changes=0), benchmarks=mock.Mock(return_value=benchmark))
        rev = {"account": "Me#EUW", "games": [
            {"match_id": str(i), "game_start": 1800000000 - i * 2000, "duration": 1800,
             "champion": "Gwen", "role": "JUNGLE", "win": i % 2 == 0,
             "stats": {"jcs10": 40 + i, "kp": 45 + i, "vpm": .7, "deaths": 6}}
            for i in range(10)]}
        cache = {}
        with mock.patch.object(app, "meta_stats", return_value=stats), \
             mock.patch.object(app, "current_bracket", side_effect=lambda cfg: cfg.get("elo", "mid")), \
             mock.patch.object(app, "load_history", return_value={}), \
             mock.patch("jc.learning.update_task", return_value=None):
            first = app.coach_plan(None, {"riot_id": "Me#EUW"}, rev, cache)
            self.assertTrue(first["ready"])
            self.assertTrue(first["_cache_key"])
            self.assertIs(app.coach_plan(None, {"riot_id": "Me#EUW"}, rev, cache), first)
            stats.benchmarks.assert_called_once_with("Gwen", "JUNGLE", "mid")
            changed = app.coach_plan(None, {"riot_id": "Me#EUW", "elo": "high"}, rev, cache)
            self.assertNotEqual(first["_cache_key"], changed["_cache_key"])

    def test_unknown_roles_are_not_a_max_empty_error(self):
        stats = SimpleNamespace(db=SimpleNamespace(total_changes=0))
        with mock.patch.object(app, "meta_stats", return_value=stats), \
             mock.patch.object(app, "current_bracket", return_value="mid"):
            result = app.coach_plan(None, {}, {"games": [{"champion": "Gwen", "role": ""}]}, {})
        self.assertFalse(result["ready"])

class ScoreCacheTests(unittest.TestCase):
    def test_disk_summary_from_old_formula_is_not_reused(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(profile, "PROFILES", root):
            key = ("me#euw", "all")
            data = {"games": [{"pos": "JUNGLE", "score": 9.9}], "_seen": set()}
            profile._save_disk(key, data)
            self.assertIsNone(profile._load_disk(key))
            data["_score_version"] = gamescore.VERSION
            profile._save_disk(key, data)
            self.assertIsNotNone(profile._load_disk(key))
            data["_score_version"] = gamescore.VERSION - 1
            profile._save_disk(key, data)
            self.assertIsNone(profile._load_disk(key))

class LeaderboardTests(unittest.TestCase):
    def test_self_exists_without_loading_profile_or_saving_self_as_friend(self):
        with mock.patch.dict(friends.SUMMARY, {}, clear=True):
            result = friends.listing({"riot_id": "Me#EUW", "friends": ["Other#EUW"]}, None, None)
        self.assertEqual(result["me"]["riot_id"], "Me#EUW")
        self.assertTrue(result["me"]["me"])
        self.assertTrue(result["me"]["loading"])
        self.assertEqual([f["riot_id"] for f in result["friends"]], ["Other#EUW"])

    def test_active_account_wins_and_deduplicates_self(self):
        result = friends.listing({"riot_id": "Old#EUW", "friends": ["ME#EUW", "Other#EUW"]},
                                 None, None, self_id="Me#EUW")
        self.assertEqual(result["me"]["riot_id"].lower(), "me#euw")
        self.assertEqual(len(result["friends"]), 1)

    def test_score_uses_fixed_twenty_match_window_and_same_rounding_as_web(self):
        games = [{"ended": 100-i, "score": None if i < 12 else 6.2, "match_id": str(i)} for i in range(20)]
        games += [{"ended": 1, "score": 10}]
        self.assertEqual(friends._avg_score(games), 6.2)
        self.assertEqual(friends._avg_score([{"score": 6.2}, {"score": 6.3}]), 6.3)

