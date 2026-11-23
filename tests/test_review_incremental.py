"""The analysis only downloads games it has not analysed before."""
import unittest
from unittest import mock

from jc import review


class IncrementalReviewTest(unittest.TestCase):
    def test_only_new_games_are_loaded(self):
        calls = []

        class API:
            def __init__(self, *a):
                pass

            def get(self, path, params=None):
                calls.append(path)
                if "accounts" in path:
                    return {"puuid": "me"}
                if path.endswith("/ids"):
                    return ["M3", "M2", "M1"]
                mid = path.split("/")[-1]
                return {"metadata": {"matchId": mid}, "info": {"gameDuration": 1800, "gameStartTimestamp": 1, "participants": [
                    {"puuid": "me", "teamPosition": "TOP", "teamId": 100, "championName": "Gwen", "win": True,
                     "kills": 1, "deaths": 1, "assists": 1}]}}
        prev = {"games": [], "other_games": [{"match_id": m, "champion": "Gwen", "role": "TOP", "stats": {}} for m in ("M1", "M2")]}
        with mock.patch.object(review, "RiotAPI", API):
            r = review.run("k", "euw1", "a#b", prev=prev)
        self.assertEqual([c for c in calls if "/matches/" in c and not c.endswith("ids")], ["/lol/match/v5/matches/M3"])
        self.assertEqual([g["match_id"] for g in r["other_games"]], ["M3", "M2", "M1"])


if __name__ == "__main__":
    unittest.main()
