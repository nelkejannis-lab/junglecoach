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


def update(cfg, force=False, max_age_h=1):
    """Every hour: download only the small hourly deltas of the crawler (new games since the last check).
    The full database (big) is only loaded the first time or when the app was off for days."""
    url = cfg.get("meta_cloud_url", DEFAULT_URL)
    if not url:
        STATE["status"] = "aus"
        return False
    try:
        last = float(open(STAMP).read()) if os.path.exists(STAMP) else 0
    except (OSError, ValueError):
        last = 0
    if not force and time.time() - last < max_age_h * 3600 and last:
        STATE.update(status="aktuell", updated=last)
        return False
    STATE["status"] = "lädt…"
    tmp = tempfile.mkdtemp()
    try:
        deltas, applied = [], 0
        try:
            rel = _get(RELEASE_API, timeout=30)
            deltas = sorted(((int(a["name"][6:-10]), a["browser_download_url"]) for a in rel.get("assets", [])
                             if a["name"].startswith("delta-") and a["name"].endswith(".sqlite.gz")))
        except Exception:  # noqa: BLE001 - API limit etc.: fall back to the full file
            deltas = []
        db = meta.connect()
        newest = last
        if last and deltas and deltas[0][0] <= last:   # gap-free: only the new hours
            for ts, durl in deltas:
                if ts > last:
                    gz = os.path.join(tmp, f"d{ts}.gz")
                    _get(durl, gz)
                    applied += _merge_gz(db, gz, tmp)
                    newest = max(newest, ts)
        elif not last or not deltas or deltas[0][0] > last:   # first start or off for too long: whole database
            gz = os.path.join(tmp, "meta.sqlite.gz")
            _get(url, gz, timeout=600)
            applied = _merge_gz(db, gz, tmp)
            newest = max([time.time()] + [t for t, _ in deltas])
        if applied:
            meta.prune_balanced(db, max_matches=MAX_MATCHES, enough_current=MAX_MATCHES * 6)
        total = db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        db.close()
        with open(STAMP, "w") as f:
            f.write(str(newest or time.time()))
        STATE.update(status=f"aktualisiert (+{applied})" if applied else "aktuell", games=total, updated=time.time())
        return bool(applied)
    except Exception as e:
        STATE["status"] = f"nicht erreichbar ({type(e).__name__})"
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
