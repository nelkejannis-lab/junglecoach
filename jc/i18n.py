"""Language of the app: German or English (setting "language"; empty = Windows language)."""
import locale

LANG = {"code": "de"}


def detect():
    try:
        loc = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or "").lower()
    except Exception:  # noqa: BLE001
        loc = ""
    return "de" if loc.startswith(("de", "german")) else "en"


def setup(cfg):
    code = (cfg.get("language") or "").lower()
    LANG["code"] = code if code in ("de", "en") else detect()
    return LANG["code"]


def ddragon_locale():
    return "de_DE" if LANG["code"] == "de" else "en_US"
