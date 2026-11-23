import os
import subprocess
import tempfile
import unittest
from unittest import mock

from jc import deep, recorder, app


class RecorderTests(unittest.TestCase):
    def test_detect_mistakes(self):
        data = {"events": {"Events": [
            {"EventName": "ChampionKill", "EventTime": 200, "KillerName": "ej", "VictimName": "me", "Assisters": ["e2"]},
            {"EventName": "ChampionKill", "EventTime": 300, "KillerName": "ej", "VictimName": "ally", "Assisters": []},
            {"EventName": "DragonKill", "EventTime": 400, "KillerName": "ej", "DragonType": "Fire"},
            {"EventName": "DragonKill", "EventTime": 900, "KillerName": "me", "DragonType": "Air"}]}}
        team = {"me": "ORDER", "ally": "ORDER", "ej": "CHAOS", "e2": "CHAOS"}
        m = recorder.detect_mistakes(data, {"me"}, "ORDER", {"ej"}, team, {"ally": "TOP"})
        self.assertEqual([x["type"] for x in m], ["death", "enemy_gank", "objective_lost"])
        self.assertIn("2 Gegner", m[0]["why"]); self.assertIn("Fire", m[2]["title"])

    def test_cut_and_prune(self):
        exe = recorder.ffmpeg_exe()
        with tempfile.TemporaryDirectory() as d:
            full = os.path.join(d, "full.mp4")
            subprocess.run([exe, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10",
                            "-t", "60", "-pix_fmt", "yuv420p", full], check=True)
            clips = recorder.cut_clips(full, [{"t": 130, "type": "death", "pre": 10, "post": 5}], 100, d)
            self.assertEqual(len(clips), 1)
            out = subprocess.run([exe, "-i", os.path.join(d, clips[0]["file"])], capture_output=True, text=True).stderr
            self.assertIn("Duration: 00:00:15", out)
            rec_dir = os.path.join(d, "rec")
            for i in range(5):
                os.makedirs(os.path.join(rec_dir, f"2026-11-2{i}_10-00-00"))
            with mock.patch.object(recorder, "REC_DIR", rec_dir):
                recorder.prune(3)
                self.assertEqual(sorted(os.listdir(rec_dir)),
                                 ["2026-11-22_10-00-00", "2026-11-23_10-00-00", "2026-11-24_10-00-00"])

    def test_attach_clips(self):
        games = [{"game_start": 1000}, {"game_start": 5000}]
        app.attach_clips(games, [{"id": "s1", "start_wall": 1090, "clips": [{"file": "a.mp4"}]}])
        self.assertEqual(games[0]["session_id"], "s1"); self.assertNotIn("clips", games[1])


class DeepTests(unittest.TestCase):
    def test_deep(self):
        def frame(m):
            pf = {str(i): {"position": {"x": 9500, "y": 4500}, "totalGold": 500 + (450 if i == 1 else 400) * m,
                           "xp": 300 * m, "jungleMinionsKilled": 6 * m if i == 1 else 5 * m} for i in range(1, 11)}
            return {"participantFrames": pf, "events": []}
        frames = [frame(m) for m in range(25)]
        frames[8]["events"] = [
            {"type": "CHAMPION_KILL", "timestamp": 470000, "killerId": 6, "victimId": 1,
             "assistingParticipantIds": [7], "position": {"x": 12000, "y": 7000}},
            {"type": "ELITE_MONSTER_KILL", "timestamp": 520000, "killerTeamId": 200, "monsterType": "DRAGON",
             "monsterSubType": "FIRE_DRAGON"}]
        frames[16]["events"] = [{"type": "ELITE_MONSTER_KILL", "timestamp": 950000, "killerTeamId": 100,
                                 "monsterType": "DRAGON", "monsterSubType": "AIR_DRAGON"}]
        parts = [{"puuid": "me" if i == 1 else f"p{i}", "participantId": i, "teamId": 100 if i <= 5 else 200,
                  "teamPosition": ["JUNGLE", "TOP", "MIDDLE", "BOTTOM", "UTILITY"][(i - 1) % 5],
                  "championName": "X", "win": True, "kills": 2, "deaths": 1, "assists": 5, "visionScore": 24,
                  "detectorWardsPlaced": 3, "challenges": {"killParticipation": 0.55}} for i in range(1, 11)]
        r = deep.deep_analyze({"info": {"participants": parts, "gameDuration": 1500, "gameStartTimestamp": 1_000_000}},
                              {"info": {"frames": frames}}, "me")
        self.assertEqual(r["curve"][10]["gold_diff"], 500)
        d = r["deaths"][0]
        self.assertTrue(any(t.startswith("Unterzahl") for t in d["tags"]))
        self.assertIn("im gegnerischen Jungle", d["tags"])
        self.assertTrue(any("Objective danach verloren" in t for t in d["tags"]))
        self.assertEqual(len(r["categories"]), 5); self.assertEqual(len(r["priorities"]), 3)
        self.assertTrue(all(o["present"] for o in r["objectives"]))
        self.assertEqual(r["game_start"], 1000)


if __name__ == "__main__":
    unittest.main()
