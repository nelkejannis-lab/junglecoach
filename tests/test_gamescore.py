"""Game score v2: Python and the JS copy in the mobile app give the same numbers; role/plausibility checks."""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jc import gamescore  # noqa: E402

ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


def match(seed, queue=420):
    rnd = random.Random(seed)
    P = []
    for t in (100, 200):
        for i, role in enumerate(ROLES):
            P.append({"puuid": f"{t}-{role}", "teamId": t, "teamPosition": role, "win": t == 100,
                      "kills": rnd.randint(0, 12), "deaths": rnd.randint(0, 10), "assists": rnd.randint(0, 15),
                      "totalDamageDealtToChampions": rnd.randint(3000, 35000), "goldEarned": rnd.randint(6000, 16000),
                      "damageDealtToObjectives": rnd.randint(500, 20000), "visionScore": rnd.randint(5, 90),
                      "totalMinionsKilled": 0 if role in ("JUNGLE", "UTILITY") else rnd.randint(120, 280),
                      "neutralMinionsKilled": rnd.randint(100, 200) if role == "JUNGLE" else rnd.randint(0, 20),
                      "totalHealsOnTeammates": rnd.randint(0, 9000) if role == "UTILITY" else 0,
                      "totalDamageShieldedOnTeammates": rnd.randint(0, 6000) if role == "UTILITY" else 0})
    return {"queueId": queue, "gameMode": "CLASSIC", "gameDuration": rnd.randint(1500, 2200), "participants": P}


class GameScoreTests(unittest.TestCase):
    def test_no_win_bonus_and_fixed_scale(self):
        m = match(1)
        a = gamescore.scores(m)
        for p in m["participants"]:
            p["win"] = not p["win"]
        b = gamescore.scores(m)
        self.assertEqual({k: v["score"] for k, v in a.items()}, {k: v["score"] for k, v in b.items()})

    def test_same_play_same_score_in_any_lobby(self):
        m1, m2 = match(2), match(3)
        me = dict(m1["participants"][3], puuid="me")
        m1["participants"][3] = me
        m2["participants"][3] = dict(me, teamId=100)
        m2["gameDuration"] = m1["gameDuration"]
        # same own stats, same team totals -> same score although the other lobby differs
        for x, y in zip(m1["participants"][:5], m2["participants"][:5]):
            y.update({k: x[k] for k in ("kills", "totalDamageDealtToChampions", "goldEarned", "damageDealtToObjectives")})
        self.assertEqual(gamescore.scores(m1)["me"]["score"], gamescore.scores(m2)["me"]["score"])

    def test_other_modes_not_scored(self):
        self.assertEqual(gamescore.scores(match(4, queue=450)), {})

    def test_js_matches_python(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        html = open(os.path.join(os.path.dirname(__file__), "..", "server", "public", "m", "index.html"), encoding="utf-8").read()
        a = html.index("const GS_Q = ")
        b = html.index("\n}\n", html.index("function gameScores(info)")) + 3
        cases = [match(s) for s in range(8)]
        js = html[a:b] + f"\nconst C = {json.dumps(cases)};\nconsole.log(JSON.stringify(C.map(m => {{ const o = gameScores(m); "
        js += "return Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v.score])); })));"
        out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
        for m, j in zip(cases, out):
            self.assertEqual({k: v["score"] for k, v in gamescore.scores(m).items()}, j)


if __name__ == "__main__":
    unittest.main()
