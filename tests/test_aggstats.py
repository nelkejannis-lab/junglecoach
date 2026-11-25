"""Server statistics: the crawler exports agg + per-champion shards, the app reads them like the full database."""
import gzip
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from jc import meta  # noqa: E402

EXPORT = os.path.join(os.path.dirname(__file__), "..", "..", "junglecoach-data", "export.py")


@unittest.skipUnless(os.path.exists(EXPORT), "data repo not checked out")
class AggStatsTests(unittest.TestCase):
    def test_roundtrip(self):
        import importlib.util
        t = tempfile.mkdtemp()
        db = meta.connect(os.path.join(t, "big.sqlite"))
        rows = []
        for i in range(120):
            win = i % 3 != 0
            rows.append((f"M{i}", "Gwen", 100, int(win), "[]", 0, "Viego", "16.23", 1000 + i, "GOLD", None,
                         '["Viego", "Ahri"]', "JUNGLE", '{"gpm": %d}' % (300 + i), None))
            rows.append((f"M{i}", "Viego", 200, int(not win), "[]", 0, "Gwen", "16.23", 1000 + i, "GOLD", None,
                         '["Gwen", "Lux"]', "JUNGLE", '{"gpm": %d}' % (280 + i), None))
        db.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        db.close()
        sys.path.insert(0, os.path.dirname(EXPORT))
        spec = importlib.util.spec_from_file_location("export", EXPORT)
        ex = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ex)
        out = os.path.join(t, "out")
        ex.export(os.path.join(t, "big.sqlite"), out)
        agg = os.path.join(t, "agg.sqlite")
        with gzip.open(os.path.join(out, "agg.sqlite.gz")) as a, open(agg, "wb") as b:
            shutil.copyfileobj(a, b)
        old = (meta.AGG_PATH, meta.SHARD_PATH, meta.SHARD_URL)
        meta.AGG_PATH, meta.SHARD_PATH = agg, os.path.join(t, "shards.sqlite")
        meta.SHARD_URL = Path(out).resolve().as_uri() + "/shard-{}.sqlite.gz"
        try:
            ms = meta.AggStats(object())
            d = ms.draft_parts("Gwen", "JUNGLE", "Viego", ["Viego", "Ahri"], "mid")
            self.assertEqual(d["games"], 120)
            self.assertGreater(d["wr"], 0)            # Gwen won 2/3 -> above 50 %, but shrunk
            self.assertLess(d["wr"], 10)
            self.assertEqual(ms.lane_counters("Viego", "JUNGLE", "mid")[0]["champion"], "Gwen")
            self.assertEqual(ms.main_role("gwen"), "JUNGLE")
            self.assertEqual(ms.tierlist(bracket="mid", role="JUNGLE")[0]["champion"], "Gwen")
            self.assertIn("gpm", ms.benchmarks(None, "JUNGLE", "mid")["dist"])
            ms.ensure("Gwen")
            self.assertEqual(ms.db.execute("SELECT COUNT(*) FROM games WHERE champ='Gwen'").fetchone()[0], 120)
        finally:
            meta.AGG_PATH, meta.SHARD_PATH, meta.SHARD_URL = old
            meta._AGG.update(db=None, mtime=0)


if __name__ == "__main__":
    unittest.main()
