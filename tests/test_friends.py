import unittest

from jc import friends


class FriendTests(unittest.TestCase):
    def test_add_remove(self):
        cfg = {}
        self.assertTrue(friends.add(cfg, " Faker#KR1 "))
        self.assertFalse(friends.add(cfg, "nohash"))
        friends.add(cfg, "faker#kr1")  # no duplicates
        self.assertEqual(cfg["friends"], ["Faker#KR1"])
        friends.remove(cfg, "FAKER#KR1")
        self.assertEqual(cfg["friends"], [])

    def test_presence_and_summary(self):
        class L:
            def get(self, p):
                return [{"gameName": "A", "gameTag": "1", "availability": "dnd", "lol": {"gameStatus": "inGame"}},
                        {"gameName": "B", "gameTag": "2", "availability": "chat"},
                        {"gameName": "C", "gameTag": "3", "availability": "offline"}]
        pres, names = friends.presence(L())
        self.assertEqual(pres["a#1"]["status"], "ingame")
        self.assertEqual(pres["b#2"]["status"], "online")
        out = friends.listing({"friends": ["B#2"]}, None, None, L())
        self.assertEqual(out["friends"][0]["presence"]["status"], "online")
        self.assertEqual([c["riot_id"] for c in out["client"]], ["A#1", "C#3"])
        s = friends.summarize({"riot_id": "B#2", "ranks": {"solo": {"tier": "GOLD", "division": "II", "lp": 40, "winrate": 53}},
                               "champions": [{"champion": "Graves"}], "roles": [{"role": "Jungle", "share": 80}],
                               "recent": {"games": 10, "wins": 6, "kda": 3.1}})
        self.assertEqual(s["rank"]["tier"], "GOLD")
        self.assertEqual(s["top"], ["Graves"])


if __name__ == "__main__":
    unittest.main()
