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
        import tempfile
        profile._mem.clear()
        patcher = mock.patch.object(profile, "PROFILES", tempfile.mkdtemp())
        patcher.start()
        self.addCleanup(patcher.stop)
        class FakeAPI:
            def __init__(self, *a): pass
            def get(self, path, params=None, host=None):
                if "accounts" in path: return {"puuid": "me", "gameName": "Jannis", "tagLine": "EUW"}
                if path.endswith("/ids"): return ["EUW1_1", "EUW1_2"] if params.get("start", 0) == 0 else (["EUW1_3"] if params["start"] == 2 else [])
                return match(int(path[-1]), path.endswith("1"))
            def get_platform(self, path, params=None):
                if "summoners" in path: return {"summonerLevel": 321, "profileIconId": 29}
                if "league" in path: return [{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II", "leaguePoints": 55, "wins": 30, "losses": 25}]
                return [{"championId": 64, "championLevel": 7, "championPoints": 250000}]
        with mock.patch.object(profile, "RiotAPI", FakeAPI), mock.patch.object(profile, "_match", lambda api, mid: match(int(mid[-1]), mid.endswith("1"))):
            p = profile.fetch("k", "euw1", "Jannis#EUW", GD)
        self.assertEqual(p["ranks"]["solo"]["winrate"], 55); self.assertEqual(p["level"], 321)
        self.assertEqual(p["masteries"][0]["champion"], "LeeSin"); self.assertEqual(p["recent"]["wins"], 1)
        with mock.patch.object(profile, "RiotAPI", FakeAPI), mock.patch.object(profile, "_match", lambda api, mid: match(int(mid[-1]), mid.endswith("1"))):
            p2 = profile.fetch("k", "euw1", "Jannis#EUW", GD, more=True)
        self.assertEqual(len(p2["games"]), 3); self.assertTrue(p2["has_more"])
        # next app start: profile comes instantly from disk (marked stale, refreshed in the background)
        profile._mem.clear()
        with mock.patch.object(profile, "RiotAPI", FakeAPI), mock.patch.object(profile, "_match", lambda api, mid: match(int(mid[-1]), mid.endswith("1"))):
            p3 = profile.fetch("k", "euw1", "Jannis#EUW", GD, max_age=0)
        self.assertTrue(p3.get("stale")); self.assertEqual(len(p3["games"]), 3)

    def test_match_detail(self):
        m = match(1, True)
        m["info"]["teams"] = [{"teamId": 100, "win": True, "bans": [{"championId": 64}], "objectives": {"dragon": {"kills": 3}}},
                              {"teamId": 200, "win": False, "bans": [], "objectives": {}}]
        for p_ in m["info"]["participants"]:
            p_.setdefault("championName", "Viego"); p_.setdefault("deaths", 1); p_.setdefault("assists", 0)
            p_.setdefault("totalMinionsKilled", 100); p_["summoner1Id"] = 4; p_["summoner2Id"] = 11
        m["info"]["participants"].append({"puuid": "e1", "championName": "Elise", "teamId": 200, "win": False, "kills": 2,
                                          "deaths": 5, "assists": 1, "totalMinionsKilled": 20, "teamPosition": "JUNGLE"})
        with mock.patch.object(profile, "_match", lambda api, mid: m), mock.patch.object(profile, "RiotAPI", lambda *a: None):
            d = profile.match_detail("k", "euw1", "EUW1_1", "me", GD)
        self.assertEqual(len(d["teams"]), 2); self.assertEqual(d["teams"][0]["objectives"]["dragon"], 3)
        me = next(r for r in d["teams"][0]["players"] if r["me"])
        self.assertEqual(me["spells"], ["SummonerFlash", "SummonerSmite"]); self.assertEqual(d["teams"][0]["bans"], ["LeeSin"])
        self.assertTrue(any(r.get("mvp") for t in d["teams"] for r in t["players"]))


if __name__ == "__main__":
    unittest.main()
