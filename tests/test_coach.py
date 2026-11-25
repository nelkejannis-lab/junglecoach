"""Coach engine: benchmarks, consistency, tilt, patterns and focus areas on synthetic games."""
import random
import time
import unittest

from jc import coach, meta


def bench():
    rng = random.Random(1)
    dist = {"jcs10": sorted(rng.gauss(60, 10) for _ in range(400)), "kp": sorted(rng.gauss(55, 10) for _ in range(400)),
            "vpm": sorted(rng.gauss(1.0, 0.25) for _ in range(400)), "deaths": sorted(rng.gauss(5, 2) for _ in range(400)),
            "gpm": sorted(rng.gauss(400, 40) for _ in range(400)), "dpm": sorted(rng.gauss(600, 120) for _ in range(400)),
            "drag": sorted(rng.gauss(2, 1) for _ in range(400))}
    return {"scope": "Gwen Jungle", "games": 400, "dist": dist}


def games(now):
    out = []
    t = now - 3 * 86400
    for s in range(4):                      # 4 sessions of 4 games
        for i in range(4):
            win = i < 2                     # always wins the first two, loses later -> tilt pattern
            out.append({"champion": "Gwen", "win": win, "game_start": t, "duration": 1800,
                        "first_action_min": 3.5 if win else 6.0,
                        "stats": {"jcs10": 45 if not win else 50, "kp": 70 if win else 40, "vpm": 1.2,
                                  "deaths": 3 if win else 9, "gpm": 420, "dpm": 650, "drag": 3 if win else 1}})
            t += 2400
        t += 6 * 3600
    return sorted(out, key=lambda g: -g["game_start"])


class CoachTest(unittest.TestCase):
    def test_plan(self):
        now = time.time()
        plan = coach.analyze(games(now), bench(), now=now)
        self.assertTrue(plan["ready"])
        weak = [w["metric"] for w in plan["weaknesses"]]
        self.assertIn("jcs10", weak)                                   # clear speed below benchmark
        self.assertIn("vpm", [s["metric"] for s in plan["strengths"]])  # vision above benchmark
        self.assertIsNotNone(plan["tilt"]["rule"])                     # later games of a session are worse
        self.assertTrue(any(p["metric"] == "kp" for p in plan["patterns"]))
        self.assertLessEqual(len(plan["focus"]), 3)
        self.assertTrue(plan["focus"][0]["drill"])
        self.assertEqual(len(plan["trend"]), 16)
        self.assertLess(plan["consistency"]["score"], 75)

    def test_role_specific_values(self):
        now = time.time()
        gs = games(now)
        for g in gs:
            g["role"] = "JUNGLE"; g["stats"]["cs10"] = 5
        gs[0]["role"] = "UTILITY"
        plan = coach.analyze(gs, bench(), now=now)
        self.assertNotIn("cs10", [p["metric"] for p in plan["profile"]])     # no lane CS for junglers/supports
        self.assertEqual(plan["games"], 15)                                   # only the last 10 games

    def test_not_ready_without_data(self):
        self.assertFalse(coach.analyze([], bench())["ready"])

    def test_metrics_and_percentile(self):
        p = {"challenges": {"jungleCsBefore10Minutes": 62, "killParticipation": 0.61, "visionScorePerMinute": 1.13},
             "deaths": 4, "totalTimeSpentDead": 90, "visionScore": 30}
        m = meta.metrics_of(p, 1800)
        self.assertEqual((m["jcs10"], m["kp"], m["deaths"], m["dead_share"]), (62, 61.0, 4, 5.0))
        self.assertEqual(meta.percentile([1, 2, 3, 4], 3), 62)


if __name__ == "__main__":
    unittest.main()
