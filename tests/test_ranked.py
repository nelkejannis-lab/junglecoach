import time
import unittest

from jc import ranked


class FakeLCU:
    def __init__(self): self.q = None
    def get(self, p): return {"queueMap": {"RANKED_SOLO_5x5": self.q}}


class RankedTests(unittest.TestCase):
    def test_tracker(self):
        lcu, h, now = FakeLCU(), [], time.time()
        seq = [("GOLD", "II", 40, 10, 10), ("GOLD", "II", 62, 11, 10), ("GOLD", "II", 62, 11, 10),
               ("GOLD", "II", 81, 12, 10), ("GOLD", "I", 1, 13, 10), ("GOLD", "II", 85, 13, 11)]
        for i, (t, d, lp, w, l) in enumerate(seq):
            lcu.q = {"tier": t, "division": d, "leaguePoints": lp, "wins": w, "losses": l}
            h = ranked.snapshot(lcu, h, now - 3600 + i * 60, persist=False)
        self.assertEqual(len(h), 5)  # unchanged snapshot skipped
        s = ranked.summary(h, now)
        self.assertEqual(ranked.abs_lp("GOLD", "I", 1), 3 * 400 + 300 + 1)
        self.assertEqual([g["lp"] for g in s["recent"]], [-16, 20, 19, 22])
        self.assertEqual(s["streak"], -1)
        self.assertEqual(s["avg_loss_lp"], -16)
        self.assertEqual(s["winrate"], 54)

    def test_accounts_are_separate(self):
        import os, tempfile
        old = ranked.LP_FILE
        ranked.LP_FILE = os.path.join(tempfile.mkdtemp(), "lp.json")
        try:
            class Client(FakeLCU):
                def __init__(self, name, q):
                    super().__init__(); self.name = name; self.q = q
                def get(self, p):
                    if p.endswith("current-summoner"):
                        n, t = self.name.split("#"); return {"gameName": n, "tagLine": t}
                    return super().get(p)
            ranked.snapshot(Client("Main#EUW", {"tier": "EMERALD", "division": "II", "leaguePoints": 50, "wins": 30, "losses": 20}))
            ranked.snapshot(Client("Smurf#EUW", {"tier": "GOLD", "division": "IV", "leaguePoints": 5, "wins": 3, "losses": 2}))
            ranked.snapshot(Client("Main#EUW", {"tier": "EMERALD", "division": "II", "leaguePoints": 70, "wins": 31, "losses": 20}))
            self.assertEqual(ranked.accounts(), ["main#euw", "smurf#euw"])
            main = ranked.summary(ranked.load_history("Main#EUW"))
            self.assertEqual((main["tier"], main["lp"], [g["lp"] for g in main["recent"]]), ("EMERALD", 70, [20]))
            self.assertEqual(ranked.summary(ranked.load_history("smurf#euw"))["tier"], "GOLD")
            self.assertEqual(ranked.active_account(), "main#euw")
            ranked.snapshot(FakeLCU())  # account unknown -> nothing written
            self.assertEqual(ranked.accounts(), ["main#euw", "smurf#euw"])
        finally:
            ranked.LP_FILE = old

    def test_improvements_matchups(self):
        def g(enemy, win, gd, scores):
            return {"enemy_jungler": enemy, "win": win, "gold_diff_10": gd,
                    "deep": {"categories": [{"name": n, "score": v} for n, v in scores.items()]}}
        games = [g("Elise", False, -800, {"Vision": 40, "Tempo": 70}), g("Elise", False, -300, {"Vision": 50, "Tempo": 60}),
                 g("Viego", True, 400, {"Vision": 30, "Tempo": 80})]
        imp = ranked.improvements(games)
        self.assertEqual(imp[0]["name"], "Vision")
        mu = ranked.own_matchups(games)
        self.assertEqual(mu[0]["enemy"], "Elise"); self.assertEqual(mu[0]["winrate"], 0)


if __name__ == "__main__":
    unittest.main()
