import unittest
from unittest import mock
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from test_all import GD
from jc import profile


def match(i, win, champ="LeeSin", pos="JUNGLE", queue=420, dur=1800):
    me = {"puuid": "me", "championName": champ, "win": win, "kills": 5, "deaths": 2, "assists": 7, "teamId": 100,
          "totalMinionsKilled": 30, "neutralMinionsKilled": 150, "visionScore": 30, "teamPosition": pos,
          **{f"item{j}": 3071 for j in range(3)}, "champLevel": 16}
    mates = [{"puuid": f"p{j}", "kills": 4, "teamId": 100} for j in range(4)]
    return {"metadata": {"matchId": f"EUW1_{i}"}, "info": {"queueId": queue, "gameDuration": dur, "gameEndTimestamp": 1_700_000_000_000,
                                                           "participants": [me] + mates}}


class ProfileTests(unittest.TestCase):
    def test_summary_and_aggregate(self):
        g = profile.summarize_match(match(1, True), "me", GD)
        self.assertEqual(g["cs_min"], 6.0); self.assertEqual(g["kp"], 57); self.assertEqual(g["queue"], "Solo/Duo")
        games = [profile.summarize_match(match(i, i % 2 == 0, "LeeSin" if i < 3 else "Viego"), "me", GD) for i in range(5)]
        games.append(profile.summarize_match(match(9, False, dur=200), "me", GD))  # remake ignored
        champs, roles = profile.aggregate(games)
        self.assertEqual(champs[0]["champion"], "LeeSin"); self.assertEqual(champs[0]["games"], 3)
        self.assertEqual(roles[0], {"role": "Jungle", "share": 100})

    def test_fetch(self):
        class FakeAPI:
            def __init__(self, *a): pass
            def get(self, path, params=None, host=None):
                if "accounts" in path: return {"puuid": "me", "gameName": "Jannis", "tagLine": "EUW"}
                if path.endswith("/ids"): return ["EUW1_1", "EUW1_2"]
                return match(int(path[-1]), path.endswith("1"))
            def get_platform(self, path, params=None):
                if "summoners" in path: return {"summonerLevel": 321, "profileIconId": 29}
                if "league" in path: return [{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II", "leaguePoints": 55, "wins": 30, "losses": 25}]
                return [{"championId": 64, "championLevel": 7, "championPoints": 250000}]
        with mock.patch.object(profile, "RiotAPI", FakeAPI), mock.patch.object(profile, "_match", lambda api, mid: match(int(mid[-1]), mid.endswith("1"))):
            p = profile.fetch("k", "euw1", "Jannis#EUW", GD)
        self.assertEqual(p["ranks"]["solo"]["winrate"], 55); self.assertEqual(p["level"], 321)
        self.assertEqual(p["masteries"][0]["champion"], "LeeSin"); self.assertEqual(p["recent"]["wins"], 1)


if __name__ == "__main__":
    unittest.main()
