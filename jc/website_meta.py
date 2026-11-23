"""Current-patch website snapshots, refreshed in the app's background loop."""
import json
import threading
import urllib.request
from datetime import datetime, timezone

URL = "https://github.com/nelkejannis-lab/junglecoach-data/releases/download/website-latest/website-meta.json"
VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
_LOCK = threading.Lock()
_STATE = {"status": "noch nicht geladen", "results": [], "patch": None}
_MAX_BYTES = 8 * 1024 * 1024


def read_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "JungleCoach", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError("Antwort zu gross")
    return json.loads(data)


def current_patch(version):
    parts = str(version).split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
        raise ValueError("Ungueltige Patchnummer")
    return ".".join(parts[:2])


def select_current(payload, patch, now=None):
    now = now or datetime.now(timezone.utc)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("results"), list):
        raise ValueError("Unbekanntes Datenformat")
    accepted = []
    for row in payload["results"]:
        if not isinstance(row, dict) or row.get("status") != "ok" or row.get("patch") != patch:
            continue
        if row.get("source") not in ("mobalytics", "opgg", "lolalytics") or row.get("role") != "jungle":
            continue
        try:
            fetched = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
            age = (now - fetched).total_seconds()
            if not 0 <= age <= 86400:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(row.get("sections"), list):
            continue
        accepted.append(row)
    return accepted


def update():
    global _STATE
    try:
        versions = read_json(VERSIONS)
        patch = current_patch(versions[0])
        payload = read_json(URL)
        rows = select_current(payload, patch)
        state = {"status": "aktuell" if rows else "Warte auf Daten fuer aktuellen Patch",
                 "patch": patch, "results": rows, "checked_at": datetime.now(timezone.utc).isoformat(),
                 "schema_version": 1}
    except Exception as exc:
        # Never keep an unverified previous patch available after a failed refresh.
        state = {"status": "Aktualisierung fehlgeschlagen (" + type(exc).__name__ + ")",
                 "patch": None, "results": [], "schema_version": 1}
    with _LOCK:
        _STATE = state


def snapshot():
    with _LOCK:
        state = dict(_STATE)
    state["results"] = select_current(state, state["patch"]) if state.get("patch") else []
    return state
