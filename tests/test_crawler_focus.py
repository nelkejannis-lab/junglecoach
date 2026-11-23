"""Crawler keeps Gwen/Graves players and starts the next crawl with their games."""
import os
import tempfile
import unittest
from unittest import mock

from jc import meta

POS = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


def match(mid, focus_puuid):
    parts = []
    for t, team in enumerate((100, 200)):
        for i, pos in enumerate(POS):
            pu = focus_puuid if (team == 100 and pos == "JUNGLE") else f"{mid}-{team}-{i}"
            parts.append({"puuid": pu, "teamId": team, "teamPosition": pos, "win": team == 100,
                          "championName": "Gwen" if pu == focus_puuid else f"Champ{t}{i}", "item0": 3115})
    return {"metadata": {"matchId": mid}, "info": {"gameDuration": 1800, "gameVersion": "16.23.1",
                                                   "gameCreation": 1, "participants": parts}}


class FakeAPI:
    calls = []

    def __init__(self, *_):
        pass

    def get_platform(self, path, params=None):
        return {"entries": []} if "leagues" in path else []

    def get(self, path, params=None):
        FakeAPI.calls.append((path, dict(params or {})))
        if path.endswith("/ids"):
            puuid = path.split("/")[-2]
            return [f"M-{puuid}-{len(FakeAPI.calls)}"]
        return match(path.split("/")[-1], "gwen-main")


class CrawlerFocusTest(unittest.TestCase):
    def test_focus_players_are_remembered_and_crawled_first(self):
        path = os.path.join(tempfile.mkdtemp(), "m.sqlite")
        cfg = {"riot_api_key": "x", "platform": "euw1", "meta_focus": ["Gwen"], "meta_max_matches": 3,
               "meta_tiers": [], "meta_focus_matches": 25}
        with mock.patch.object(meta, "RiotAPI", FakeAPI), \
                mock.patch.object(meta.Crawler, "_players", lambda self, api: [("seed", "GOLD")]):
            meta.Crawler(cfg, db_path=path)._run()
            db = meta.connect(path)
            self.assertEqual(db.execute("SELECT puuid, champ FROM focus_players").fetchall(), [("gwen-main", "Gwen")])
            db.close()
            FakeAPI.calls.clear()
            meta.Crawler(cfg, db_path=path)._run()
        first = FakeAPI.calls[0]
        self.assertIn("gwen-main", first[0])          # next crawl starts with the known Gwen player
        self.assertEqual(first[1]["count"], 25)       # and loads more of their games


if __name__ == "__main__":
    unittest.main()
