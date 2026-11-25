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


RELEASE_API = "https://api.github.com/repos/nelkejannis-lab/junglecoach-data/releases/tags/meta-latest"
UA = {"User-Agent": "Mozilla/5.0 Moosi/1.0 (+https://moosi.app)"}
MAX_MATCHES = 250_000   # same cap as the crawler: Silver+ in 6 equal elo buckets


def _get(url, path=None, timeout=120, accept=None):
    h = dict(UA, **({"Accept": accept} if accept else {}))
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        if path is None:
            import json
            return json.loads(r.read().decode("utf-8"))
        with open(path, "wb") as f:
            shutil.copyfileobj(r, f)


def _merge_gz(db, gz, tmp):
    raw = os.path.join(tmp, "x.sqlite")
    with gzip.open(gz, "rb") as src, open(raw, "wb") as dst:
        shutil.copyfileobj(src, dst)
    n = meta.merge_from(db, raw)
    os.remove(raw)
    return n


AGG_URL = "https://github.com/nelkejannis-lab/junglecoach-data/releases/download/meta-latest/agg.sqlite.gz"


def update(cfg, force=False, max_age_h=1):
    """Every hour: the crawler's small pre-computed statistics (a few MB). Champion details are downloaded
    per champion on demand (meta.AggStats.ensure). The old full database (up to 1.5 GB) is not needed anymore."""
    if not cfg.get("meta_cloud_url", DEFAULT_URL):
        STATE["status"] = "aus"
        return False
    try:
        last = os.path.getmtime(meta.AGG_PATH) if os.path.exists(meta.AGG_PATH) else 0
    except OSError:
        last = 0
    if not force and last and time.time() - last < max_age_h * 3600:
        STATE.update(status="aktuell", updated=last)
        return False
    STATE["status"] = "lädt…"
    tmp = tempfile.mkdtemp()
    try:
        gz = os.path.join(tmp, "agg.sqlite.gz")
        _get(cfg.get("meta_agg_url", AGG_URL), gz, timeout=180)
        raw = meta.AGG_PATH + ".new"
        with gzip.open(gz, "rb") as src, open(raw, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(raw, meta.AGG_PATH)
        import sqlite3
        c = sqlite3.connect(meta.AGG_PATH)
        games = int((c.execute("SELECT v FROM meta WHERE k='matches'").fetchone() or [0])[0])
        c.close()
        # free the disk space of the old full download (only a cache; own crawling needs an own key)
        if os.path.exists(meta.DB_PATH) and not (cfg.get("riot_api_key") or "").startswith("RGAPI-"):
            for f in (meta.DB_PATH, meta.DB_PATH + "-wal", meta.DB_PATH + "-shm"):
                try:
                    os.remove(f)
                except OSError:
                    pass
        STATE.update(status="aktualisiert", games=games, updated=time.time())
        return True
    except Exception as e:
        STATE["status"] = f"nicht erreichbar ({type(e).__name__})"
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def prefetch_pool(cfg, ms):
    """Main + backup picks are downloaded in advance, so champ select doesn't wait for them."""
    for ref in (cfg.get("champion_pool") or [])[:3]:
        try:
            ms.ensure(ref)
        except Exception:  # noqa: BLE001
            pass
