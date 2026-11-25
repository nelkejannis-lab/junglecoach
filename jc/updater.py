"""Version notifications using the project's published Windows release."""
import json
import re
import threading
import time
import urllib.request
from . import __version__

REPOSITORY = "https://github.com/nelkejannis-lab/junglecoach"
API = "https://api.github.com/repos/nelkejannis-lab/junglecoach/releases/latest"
_lock = threading.Lock()
_cached = {}
_at = 0

def version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value))
    return tuple(map(int, match.groups())) if match else None

def release_info(data):
    tag = data.get("tag_name", "")
    newer = version(tag)
    asset = next((a for a in data.get("assets", []) if a.get("name") == "Moosi-Setup.exe"), {})
    expected = f"{REPOSITORY}/releases/download/{tag}/Moosi-Setup.exe"
    available = bool(newer and newer > version(__version__) and not data.get("draft")
                     and not data.get("prerelease") and asset.get("browser_download_url") == expected)
    return {"current": __version__, "latest": tag.lstrip("v") if newer else None,
            "available": available, "url": expected if available else None}

def check():
    global _cached, _at
    with _lock:
        if _cached and time.time() - _at < 900:
            return dict(_cached)
        try:
            req = urllib.request.Request(API, headers={"User-Agent": "Moosi-Updater", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                result = release_info(json.load(response))
        except Exception:
            result = {"current": __version__, "available": False, "error": "Update-Prüfung gerade nicht möglich."}
        _cached, _at = result, time.time()
        return dict(result)
