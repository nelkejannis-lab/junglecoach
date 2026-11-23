"""Static game data from Riot's public Data Dragon CDN (champions, items, runes).

Logic always runs on en_US data (stable names/keys); a second language is
loaded only for display names.
"""
import json
import os
import urllib.request

from .paths import DATA_DIR

BASE = "https://ddragon.leagueoflegends.com"
CACHE_DIR = os.path.join(DATA_DIR, "cache")


def _get_json(url, cache_name=None):
    path = os.path.join(CACHE_DIR, cache_name) if cache_name else None
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    if path:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    return data


class GameData:
    def __init__(self, display_lang="de_DE", version=None, raw=None):
        """raw: optional dict with champion/item/runes (+ *_disp) for offline tests."""
        if raw is None:
            version = version or _get_json(f"{BASE}/api/versions.json")[0]
            raw = {}
            for lang, suffix in (("en_US", ""), (display_lang, "_disp")):
                for kind, fname in (("champion", "champion.json"), ("item", "item.json"),
                                    ("runes", "runesReforged.json")):
                    url = f"{BASE}/cdn/{version}/data/{lang}/{fname}"
                    raw[kind + suffix] = _get_json(url, f"{version}_{lang}_{fname}")
        self.version = version
        champs = raw["champion"]["data"]
        self.champ_by_key = {int(c["key"]): c for c in champs.values()}   # numeric id -> champ
        self.champ_by_id = {c["id"]: c for c in champs.values()}          # "LeeSin" -> champ
        self.champ_by_name = {c["name"].lower(): c for c in champs.values()}
        disp = raw.get("champion_disp", raw["champion"])["data"]
        self.champ_disp = {c["id"]: c["name"] for c in disp.values()}

        self.items = {int(k): v for k, v in raw["item"]["data"].items()}
        disp_items = raw.get("item_disp", raw["item"])["data"]
        self.item_disp = {int(k): v["name"] for k, v in disp_items.items()}

        self.runes = raw["runes"]
        self.rune_disp = {}
        for tree in raw.get("runes_disp", raw["runes"]):
            self.rune_disp[tree["id"]] = tree["name"]
            for slot in tree["slots"]:
                for r in slot["runes"]:
                    self.rune_disp[r["id"]] = r["name"]

    # ---- champions -------------------------------------------------------
    def champ(self, ref):
        """Accepts numeric key, id ("LeeSin") or display name ("Lee Sin")."""
        if ref is None:
            return None
        if isinstance(ref, int):
            return self.champ_by_key.get(ref)
        return self.champ_by_id.get(ref) or self.champ_by_name.get(str(ref).lower())

    def champ_name(self, champ):
        return self.champ_disp.get(champ["id"], champ["name"]) if champ else "?"

    # ---- items -----------------------------------------------------------
    def item_name(self, item_id):
        return self.item_disp.get(int(item_id), str(item_id))

    def completed_items(self, map_id="11"):
        """Purchasable, finished Summoner's Rift items (no further upgrades)."""
        out = {}
        for iid, it in self.items.items():
            gold = it.get("gold", {})
            if not gold.get("purchasable", False) or not it.get("maps", {}).get(map_id, False):
                continue
            if it.get("into") or it.get("requiredChampion") or it.get("inStore") is False:
                continue
            if it.get("requiredAlly") or "Consumable" in it.get("tags", []):
                continue
            boots = "Boots" in it.get("tags", [])
            if boots or gold.get("total", 0) >= 2200:
                out[iid] = it
        return out

    def find_item(self, name_part, pool=None):
        pool = pool if pool is not None else self.items
        name_part = name_part.lower()
        for iid, it in pool.items():
            if name_part in it["name"].lower():
                return iid
        return None
