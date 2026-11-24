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


class GoldDiffTests(unittest.TestCase):
    def test_item_gold_diff(self):
        from jc import live

        class G:
            items = {1: {"gold": {"total": 3000}}, 2: {"gold": {"total": 1000}}, 3: {"gold": {"total": 50}}}
        smite = {"summonerSpellOne": {"displayName": "Smite"}}
        allies = [{"items": [{"itemID": 1}], "summonerSpells": smite}, {"items": [{"itemID": 3, "count": 2}]}]
        enemies = [{"items": [{"itemID": 2}], "summonerSpells": smite}, {"items": []}]
        d = live._gold_diff(G(), allies, enemies)
        self.assertEqual(d["team"], 3100 - 1000)
        self.assertEqual(d["jungle"], 2000)


class GpmTests(unittest.TestCase):
    def test_rating(self):
        from jc import hud
        live = {"_t": 1200, "_item_gold": 7000}
        self.assertEqual(hud.gold_per_min(live, 600, {"p25": 330, "p50": 390})["value"], 380)
        self.assertEqual(hud.gold_per_min(live, 600, {"p25": 330, "p50": 390})["rate"], "good")
        self.assertEqual(hud.gold_per_min({"_t": 1200, "_item_gold": 5000}, 0, {"p25": 330, "p50": 390})["rate"], "bad")
        self.assertEqual(hud.gold_per_min({"_t": 200, "_item_gold": 900}, 300)["rate"], "neutral")


class DeathTests(unittest.TestCase):
    def _setup(self, extra_events=(), gold=300):
        parts = [{"participantId": i, "teamId": 100 if i <= 5 else 200, "championName": f"C{i}",
                  "teamPosition": ["JUNGLE", "TOP", "MIDDLE", "BOTTOM", "UTILITY"][(i - 1) % 5]} for i in range(1, 11)]
        pf = {str(i): {"position": {"x": 7000 if i in (1, 6, 7) else 1000, "y": 7000 if i in (1, 6, 7) else 1000},
                       "currentGold": gold if i == 1 else 0, "level": 6} for i in range(1, 11)}
        frames = [{"participantFrames": pf, "events": []} for _ in range(10)]
        ev = {"type": "CHAMPION_KILL", "victimId": 1, "killerId": 6, "assistingParticipantIds": [7],
              "timestamp": 300000, "position": {"x": 7000, "y": 7000}}
        team_of = {p["participantId"]: p["teamId"] for p in parts}
        from jc import deep
        return deep.death_context(ev, 300, frames, parts, 1, 100, list(extra_events), [], team_of)

    def test_outnumbered(self):
        c = self._setup()
        self.assertEqual(c["cause"], "outnumbered")
        self.assertEqual(c["killers"], ["C6", "C7"])
        self.assertEqual(c["allies_near"], 0)

    def test_trade(self):
        c = self._setup([{"type": "BUILDING_KILL", "teamId": 200, "buildingType": "TOWER_BUILDING", "timestamp": 320000}])
        self.assertEqual(c["cause"], "trade")
        self.assertIn("Turm", c["trade"])


class ProgressTests(unittest.TestCase):
    def test_progress(self):
        from jc import coach
        now = 1_800_000_000
        hist = [{"game_start": now - (20 - i) * 86400, "win": i % 2 == 0, "stats": {"kp": 40 + i}} for i in range(20)]
        out = coach.progress(hist, [{"metric": "kp", "title": "Kill-Beteiligung", "target_val": 58, "higher": True, "target": "58%"}], now=now)
        self.assertTrue(out[0]["enough"])
        self.assertTrue(out[0]["better"])
        self.assertEqual(out[0]["games"], 20)
        self.assertEqual(out[0]["hit"], "2/20")


class ExactBuildTests(unittest.TestCase):
    def test_purchases_and_path(self):
        import json
        import os
        import tempfile
        from jc import meta
        tl = {"info": {"frames": [{"events": [
            {"type": "ITEM_PURCHASED", "participantId": 1, "itemId": 1055, "timestamp": 1000},
            {"type": "ITEM_PURCHASED", "participantId": 1, "itemId": 2003, "timestamp": 1000},
            {"type": "ITEM_PURCHASED", "participantId": 1, "itemId": 3006, "timestamp": 400000},
            {"type": "ITEM_UNDO", "participantId": 1, "beforeId": 3006, "afterId": 0, "timestamp": 401000},
            {"type": "ITEM_PURCHASED", "participantId": 1, "itemId": 6692, "timestamp": 600000}]}]}}
        self.assertEqual(meta.purchases(tl)[1], [[1055, 0.0], [6692, 10.0]])

        class G:
            def completed_items(self):
                return {6692: {"tags": []}, 3071: {"tags": []}, 6333: {"tags": []}, 3047: {"tags": ["Boots"]}}

            def item_name(self, i):
                return str(i)
        db = meta.connect(os.path.join(tempfile.mkdtemp(), "t.sqlite"))
        for n in range(60):
            seq = [[6692, 11], [3047, 13], [3071, 19], [6333, 26]] if n % 4 else [[3071, 12], [6692, 20]]
            db.execute("INSERT INTO games (match_id, champ, team, win, items, patch, created, role, build) VALUES (?,?,?,?,?,?,?,?,?)",
                       (f"M{n}", "LeeSin", 100, n % 2, "[]", "16.23", n, "JUNGLE", json.dumps(seq)))
        db.commit()
        ms = meta.MetaStats(G(), db)
        bp = ms.build_path("LeeSin", role="JUNGLE")
        self.assertTrue(bp["exact"])
        self.assertEqual([c["id"] for c in bp["core"]], [6692, 3071, 6333])
        self.assertEqual(bp["core"][0]["minute"], 11)
        self.assertEqual(bp["boots"]["id"], 3047)


class SyncTests(unittest.TestCase):
    def test_last_writer_wins(self):
        from jc import app
        calls = []
        srv = {"friends": ["Luca#EUW"], "updated_at": 2000}

        def fake(cfg, method, path, body=None):
            calls.append((method, body))
            return srv if method == "GET" else {"ok": True}
        orig_cloud, orig_save = app.cloud, app.save_config
        app.cloud, app.save_config = fake, lambda c: None
        try:
            cfg = {"friends": ["Luca#EUW", "Mia#GER"], "friends_updated": 1000}
            app.sync_friends(cfg)  # phone removed Mia later -> stays removed
            self.assertEqual(cfg["friends"], ["Luca#EUW"])
            cfg = {"friends": ["Luca#EUW", "Tobi#0815"], "friends_updated": 3000}
            app.sync_friends(cfg)  # PC changed later -> pushed
            self.assertEqual(calls[-1], ("PUT", {"friends": ["Luca#EUW", "Tobi#0815"]}))
        finally:
            app.cloud, app.save_config = orig_cloud, orig_save


class RoleTests(unittest.TestCase):
    def test_detect_role(self):
        from jc import app
        orig = app.save_config
        app.save_config = lambda c: None
        try:
            hist = {f"M{i}": {"game_start": i, "role": "JUNGLE" if i % 4 else "TOP"} for i in range(20)}
            hist["__meta"] = {"start": 20}
            cfg = {"main_role": "TOP"}
            det = app.detect_role(cfg, hist)
            self.assertEqual(det["role"], "JUNGLE")
            self.assertEqual(cfg["main_role"], "JUNGLE")
            cfg = {"main_role": "TOP", "role_auto": False}
            app.detect_role(cfg, hist)
            self.assertEqual(cfg["main_role"], "TOP")
        finally:
            app.save_config = orig


class RolePickTests(unittest.TestCase):
    def test_role_picks(self):
        from jc import champselect

        class G:
            data = {"Graves": {"id": "Graves", "tags": ["Marksman"]}, "Lulu": {"id": "Lulu", "tags": ["Support"]},
                    "Nami": {"id": "Nami", "tags": ["Support"]}, "Thresh": {"id": "Thresh", "tags": ["Support", "Tank"]}}

            def champ(self, ref):
                return self.data.get(ref)

            def champ_name(self, c):
                return c["id"]

        class MS:
            db = object()

            def tierlist(self, bracket=None, role=None):
                return [{"champion": "Thresh", "tier": "S", "winrate": 53.1}]

            def main_role(self, c):
                return {"Graves": "JUNGLE", "Lulu": "UTILITY"}.get(c)
        prof = {"squishies": 0, "tanks": 0, "assassins": 0, "cc": [], "count": 0}
        hist = [{"role": "UTILITY", "champion": "Nami", "win": i % 2 == 0} for i in range(8)]
        out = champselect.role_picks(G(), {"champion_pool": ["Graves", "Lulu"]}, "UTILITY", [], prof, set(), MS(), None, hist)
        ids = [p["id"] for p in out]
        self.assertEqual(set(ids), {"Nami", "Thresh", "Lulu"})  # Graves (jungle) is not suggested for support
        self.assertEqual(out[0]["id"], "Nami")  # your comfort pick on that role
        self.assertEqual(next(p for p in out if p["id"] == "Lulu")["source"], "pool")


class GameScoreTests(unittest.TestCase):
    def _p(self, puuid, team, win, k, d, a, dmg, gold, cs, vis, role="MIDDLE", taken=15000, obj=3000):
        return {"puuid": puuid, "teamId": team, "win": win, "kills": k, "deaths": d, "assists": a,
                "totalDamageDealtToChampions": dmg, "goldEarned": gold, "totalMinionsKilled": cs, "neutralMinionsKilled": 0,
                "visionScore": vis, "teamPosition": role, "totalDamageTaken": taken, "damageSelfMitigated": 0,
                "damageDealtToObjectives": obj}

    def test_ranking_and_badges(self):
        from jc.gamescore import scores
        P = [self._p("carry", 100, True, 12, 1, 8, 32000, 15000, 240, 25),
             self._p("w2", 100, True, 4, 3, 10, 15000, 10000, 180, 20),
             self._p("w3", 100, True, 3, 4, 6, 12000, 9000, 160, 18),
             self._p("w4", 100, True, 2, 5, 9, 9000, 8500, 150, 15),
             self._p("sup", 100, True, 0, 2, 20, 6000, 7000, 30, 80, role="UTILITY"),
             self._p("l1", 200, False, 8, 4, 3, 26000, 13000, 230, 15),
             self._p("l2", 200, False, 2, 6, 4, 10000, 9000, 170, 12),
             self._p("l3", 200, False, 1, 5, 3, 8000, 8000, 150, 10),
             self._p("feeder", 200, False, 0, 9, 1, 4000, 6000, 100, 5),
             self._p("l5", 200, False, 1, 4, 6, 5000, 6000, 20, 40, role="UTILITY")]
        s = scores({"gameDuration": 1800, "participants": P})
        self.assertEqual(s["carry"]["rank"], 1)
        self.assertEqual(s["carry"]["badge"], "MVP")
        self.assertIsNone(s["l1"]["badge"])
        self.assertEqual(s["feeder"]["rank"], 10)
        self.assertEqual(sorted(v["rank"] for v in s.values()), list(range(1, 11)))
        self.assertTrue(all(0 <= v["score"] <= 10 for v in s.values()))
        self.assertGreater(s["sup"]["score"], s["l5"]["score"])
        print({k: v["score"] for k, v in s.items()})


class DeathCostTests(unittest.TestCase):
    def _game(self):
        parts = [{"participantId": i, "teamId": 100 if i <= 5 else 200, "championName": f"C{i}", "puuid": f"p{i}",
                  "teamPosition": ["JUNGLE", "TOP", "MIDDLE", "BOTTOM", "UTILITY"][(i - 1) % 5]} for i in range(1, 11)]
        pf = {str(i): {"position": {"x": 7000 if i in (1, 6, 7) else 1000, "y": 7000 if i in (1, 6, 7) else 1000},
                       "currentGold": 300, "level": 10} for i in range(1, 11)}
        frames = [{"participantFrames": pf, "events": []} for _ in range(25)]
        frames[20]["events"] = [
            {"type": "CHAMPION_KILL", "victimId": 1, "killerId": 6, "assistingParticipantIds": [7], "timestamp": 1200000,
             "position": {"x": 7000, "y": 7000}, "bounty": 300, "shutdownBounty": 450},
            {"type": "ELITE_MONSTER_KILL", "killerTeamId": 200, "monsterType": "BARON_NASHOR", "timestamp": 1230000},
            {"type": "CHAMPION_KILL", "victimId": 1, "killerId": 6, "assistingParticipantIds": [], "timestamp": 1260000,
             "position": {"x": 1000, "y": 1000}, "bounty": 300}]
        return {"info": {"participants": parts, "gameDuration": 1500}}, {"info": {"frames": frames}}

    def test_cost_and_all_causes(self):
        from jc import deep
        m, tl = self._game()
        frames = tl["info"]["frames"]
        events = frames[20]["events"]
        c = deep.death_cost(events[0], 1200, frames, 1, events, 100, 400)
        self.assertEqual(c["bounty"], 750)
        self.assertIn("Baron", c["lost"])
        self.assertGreater(c["cost"], 750 + 1500)
        allc = deep.death_causes_all(m, tl)
        self.assertEqual(sum(allc[1].values()), 2)
        self.assertEqual(sum(allc[6].values()), 0)


class DeathBenchTests(unittest.TestCase):
    def test_benchmark_from_crawled_games(self):
        import json
        import os
        import tempfile
        from jc import meta
        db = meta.connect(os.path.join(tempfile.mkdtemp(), "m.db"))
        for i in range(50):
            st = {"kp": 50, "dc_outnumbered": 1 if i % 2 else 0, "dc_vision": 0, "dc_duel": 1}
            db.execute("INSERT INTO games (match_id, champ, team, win, items, keystone, enemy, patch, created, tier, perks, enemies, role, stats, build) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (f"M{i}", "Graves", 100, 1, "[]", 0, "", "16.23", 0, "DIAMOND", None, "[]", "JUNGLE", json.dumps(st), None))
        db.commit()
        b = meta.MetaStats(None, db).death_benchmark("JUNGLE", "mid")
        self.assertEqual(b["games"], 50)
        self.assertAlmostEqual(b["per_game"]["outnumbered"], 0.5)
        self.assertAlmostEqual(b["per_game"]["duel"], 1.0)


class OverlayRiotSafeTests(unittest.TestCase):
    def test_hidden_parts(self):
        from jc import overlay
        hud = {"gold_diff": {"team": 900, "jungle": -200}, "timers": [{"label": "Drache", "in": "0:45", "soon": True}],
               "enemy_jg": {"champion": "Elise", "level": 9, "seen": "0:20 Top"}, "fed": ["Zed 6/1/2"], "gpm": {"value": 400}}
        st = {"live": {"hud": hud}}
        m = overlay.model(st)
        self.assertTrue(m["diff"] and m["timers"] and m["ej"] and m["fed"])
        m2 = overlay.model(dict(st, ov_hide=["gold_diff", "enemy_jg", "timers"]))
        self.assertIsNone(m2["diff"]); self.assertEqual(m2["timers"], []); self.assertIsNone(m2["ej"]); self.assertEqual(m2["fed"], [])
        self.assertEqual(m2["gpm"], {"value": 400})
