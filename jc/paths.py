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
        built_in = default.get("riot_api_key", "")
        if built_in and (not user.get("riot_api_key") or user.get("_key_from_build") and user["riot_api_key"] != built_in):
            user["riot_api_key"], user["_key_from_build"] = built_in, True
            changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(user, f, ensure_ascii=False, indent=2)
    except (OSError, ValueError):
        pass
    return path
