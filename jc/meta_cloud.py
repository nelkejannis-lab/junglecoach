"""Daily meta database from the cloud crawler (public GitHub repo junglecoach-data).

A GitHub Actions job collects ranked games around the clock (EUW, NA, KR) and publishes
a compressed SQLite file. The app downloads it every few hours and merges it into the
local statistics, so recommendations are based on tens of thousands of current games.
"""
import gzip
import os
import shutil
import tempfile
import time
import urllib.request

from . import meta
from .paths import DATA_DIR

DEFAULT_URL = "https://github.com/nelkejannis-lab/junglecoach-data/releases/download/meta-latest/meta.sqlite.gz"
STAMP = os.path.join(DATA_DIR, "meta_cloud_stamp.txt")
STATE = {"status": "noch nicht geladen", "games": 0, "updated": None}


def update(cfg, force=False, max_age_h=6):
    url = cfg.get("meta_cloud_url", DEFAULT_URL)
    if not url:
        STATE["status"] = "aus"
        return False
    try:
        last = float(open(STAMP).read()) if os.path.exists(STAMP) else 0
    except (OSError, ValueError):
        last = 0
    if not force and time.time() - last < max_age_h * 3600:
        STATE.update(status="aktuell", updated=last)
        return False
    STATE["status"] = "lädt…"
    tmp = tempfile.mkdtemp()
    try:
        gz = os.path.join(tmp, "meta.sqlite.gz")
        req = urllib.request.Request(url, headers={"User-Agent": "JungleCoach"})
        with urllib.request.urlopen(req, timeout=120) as r, open(gz, "wb") as f:
            shutil.copyfileobj(r, f)
        raw = os.path.join(tmp, "meta.sqlite")
        with gzip.open(gz, "rb") as src, open(raw, "wb") as dst:
            shutil.copyfileobj(src, dst)
        db = meta.connect()
        added = meta.merge_from(db, raw)
        meta.prune(db)
        total = db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        db.close()
        with open(STAMP, "w") as f:
            f.write(str(time.time()))
        STATE.update(status=f"aktualisiert (+{added})", games=total, updated=time.time())
        return True
    except Exception as e:
        STATE["status"] = f"nicht erreichbar ({type(e).__name__})"
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
