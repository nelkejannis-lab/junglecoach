"""Paths that work from source and inside the installed app.

Installed (PyInstaller): program files are read-only, so user data (config, cache,
recordings, statistics) lives in %APPDATA%\\JungleCoach.
"""
import os
import shutil
import sys

if getattr(sys, "frozen", False):
    BUNDLE = sys._MEIPASS
    DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "JungleCoach")
else:
    BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = BUNDLE
STATIC = os.path.join(BUNDLE, "jc", "static")
DEFAULT_CONFIG = os.path.join(BUNDLE, "config.json")
os.makedirs(DATA_DIR, exist_ok=True)

# Cloudflare (moosi.app) blocks the default "Python-urllib" user agent with 403 -> every request of the app
# (login check, login polling, sync) carries a normal one. Requests that set their own keep it.
import urllib.request as _ur  # noqa: E402
_op = _ur.build_opener()
_op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Moosi/1.0 (+https://moosi.app)")]
_ur.install_opener(_op)


def log_error(where, exc=None):
    """Small error log (%APPDATA%\\JungleCoach\\moosi.log) so problems on a user's PC can be found."""
    import time
    import traceback
    try:
        path = os.path.join(DATA_DIR, "moosi.log")
        if os.path.exists(path) and os.path.getsize(path) > 300_000:
            os.replace(path, path + ".old")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {where}\n{traceback.format_exc() if exc is None else repr(exc)}\n")
    except Exception:  # noqa: BLE001
        pass


def ensure_config():
    """First start of the installed app: copy the default config into the user folder.
    A Riot API key built into the installer is used whenever the user has none set,
    and a newer built-in key replaces an older built-in one after an update."""
    import json
    path = os.path.join(DATA_DIR, "config.json")
    if DEFAULT_CONFIG == path or not os.path.exists(DEFAULT_CONFIG):
        return path
    if not os.path.exists(path):
        shutil.copy(DEFAULT_CONFIG, path)
        return path
    try:
        with open(DEFAULT_CONFIG, encoding="utf-8") as f:
            default = json.load(f)
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        changed = False
        for k, v in default.items():  # new settings from updates
            if k not in user or (k in ("champion_pool", "server_url") and not user.get(k)):
                user[k] = v
                changed = True
        # Riot's policy: no API key inside a distributed app. Older versions shipped one – remove it again
        # (a key the user typed in himself stays). Riot data then comes through the Moosi server after login.
        if user.get("server_url", "").rstrip("/") == "https://junglecoach.nelkejannis.workers.dev":
            user["server_url"] = "https://moosi.app"   # own domain since v1.9.2 (login token stays valid)
            changed = True
        if user.get("_key_from_build"):
            user.pop("_key_from_build", None)
            user["riot_api_key"] = ""
            changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(user, f, ensure_ascii=False, indent=2)
    except (OSError, ValueError):
        pass
    return path
