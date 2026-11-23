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
    """First start of the installed app: copy the default config into the user folder."""
    path = os.path.join(DATA_DIR, "config.json")
    if not os.path.exists(path) and os.path.exists(DEFAULT_CONFIG) and DEFAULT_CONFIG != path:
        shutil.copy(DEFAULT_CONFIG, path)
    return path
