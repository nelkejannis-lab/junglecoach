"""Minimap tracker for CUSTOM GAMES ONLY (coaching).

Reads the minimap from a screenshot (no game memory access) and finds enemy
champion icons via template matching with their Data Dragon portraits.
Hard-locked to custom games: in any other queue it does nothing.
"""
import json
import os
import time
import urllib.request

from .paths import DATA_DIR

MAP_MAX = 14870
ICON_CACHE = os.path.join(DATA_DIR, "cache", "icons")


def is_custom_game(lcu):
    sess = lcu.get("/lol-gameflow/v1/session") if lcu else None
    if not sess:
        return False
    gd = sess.get("gameData", {})
    return bool(gd.get("isCustomGame")) or gd.get("queue", {}).get("id") == -1


def _icon_path(version, champ_id):
    os.makedirs(ICON_CACHE, exist_ok=True)
    p = os.path.join(ICON_CACHE, f"{version}_{champ_id}.png")
    if not os.path.exists(p):
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champ_id}.png"
        with urllib.request.urlopen(url, timeout=10) as r, open(p, "wb") as f:
            f.write(r.read())
    return p


def _fmt(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class Detector:
    """Pure image logic (testable without a screen)."""

    def __init__(self, threshold=0.62, ring_min=0.35):
        import cv2  # noqa: F401  (fail early with a clear error if missing)
        self.threshold = threshold
        self.ring_min = ring_min
        self._tmpl = {}

    def _templates(self, key, icon_bgr, sizes):
        import cv2
        if key in self._tmpl:
            return self._tmpl[key]
        out = []
        for s in sizes:
            t = cv2.resize(icon_bgr, (s, s), interpolation=cv2.INTER_AREA)
            a, b = int(s * 0.22), int(s * 0.78)  # inner square of the round icon: no ring, no background
            out.append((s, t[a:b, a:b].copy(), b - a))
        self._tmpl[key] = out
        return out

    @staticmethod
    def _red_ring(img, cx, cy, r):
        """Share of ring pixels that are red (enemy icons have a red border)."""
        import numpy as np
        h, w = img.shape[:2]
        hits = total = 0
        for a in np.linspace(0, 2 * np.pi, 48, endpoint=False):
            x, y = int(cx + r * np.cos(a)), int(cy + r * np.sin(a))
            if 0 <= x < w and 0 <= y < h:
                b, g, rr = (int(v) for v in img[y, x])
                total += 1
                hits += rr > 140 and g < 100 and b < 100
        return hits / total if total else 0

    def find(self, minimap_bgr, key, icon_bgr, sizes):
        """Returns (game_x, game_y, score) or None."""
        import cv2
        best = None
        for s, t, c in self._templates(key, icon_bgr, sizes):
            if s >= min(minimap_bgr.shape[:2]):
                continue
            res = cv2.matchTemplate(minimap_bgr, t, cv2.TM_CCOEFF_NORMED)
            _, val, _, loc = cv2.minMaxLoc(res)
            if best is None or val > best[0]:
                best = (val, loc[0] + c / 2, loc[1] + c / 2, s)
        if not best or best[0] < self.threshold:
            return None
        val, cx, cy, s = best
        if self._red_ring(minimap_bgr, cx, cy, s * 0.47) < self.ring_min:
            return None
        h, w = minimap_bgr.shape[:2]
        return cx / w * MAP_MAX, (1 - cy / h) * MAP_MAX, round(float(val), 3)


class MinimapTracker:
    def __init__(self, gd, cfg):
        self.gd = gd
        self.cfg = {"enabled": True, "x": None, "y": None, "size": None, "icon_ratio": 0.085,
                    "threshold": 0.62, "track": "jungler", **cfg.get("minimap_tracker", {})}
        self.detector = None
        self.icons = {}
        self.reset()

    def reset(self):
        self.last = {}        # champ -> {x,y,area,t}
        self.sightings = []   # timeline for coach review
        self.game_id = None

    def save_session(self):
        """Stores the sighting timeline of a finished game for later coach review."""
        d = os.path.join(DATA_DIR, "sessions")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, time.strftime("%Y-%m-%d_%H-%M") + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sightings": self.sightings}, f, ensure_ascii=False, indent=1)
        return path

    # ---- screen ----------------------------------------------------------
    def region(self, screen_w, screen_h):
        c = self.cfg
        size = c["size"] or int(screen_h * 0.26)
        x = c["x"] if c["x"] is not None else screen_w - size - 4
        y = c["y"] if c["y"] is not None else screen_h - size - 4
        return {"left": x, "top": y, "width": size, "height": size}

    def grab(self):
        import mss
        import numpy as np
        with mss.mss() as sct:
            mon = sct.monitors[1]
            reg = self.region(mon["width"], mon["height"])
            reg["left"] += mon["left"]; reg["top"] += mon["top"]
            img = np.array(sct.grab(reg))[:, :, :3]  # BGRA -> BGR
        return img

    def icon(self, champ_id):
        import cv2
        if champ_id not in self.icons:
            self.icons[champ_id] = cv2.imread(_icon_path(self.gd.version, champ_id))
        return self.icons[champ_id]

    # ---- main update (called every ~2 s during a game) --------------------
    def update(self, live_data, game_time, targets, area_fn, minimap_img=None):
        """targets: list of (champ_id, display_name, is_jungler)."""
        if not self.cfg["enabled"]:
            return None
        if self.detector is None:
            self.detector = Detector(self.cfg["threshold"])
        img = minimap_img if minimap_img is not None else self.grab()
        size = img.shape[0]
        base = max(8, int(size * self.cfg["icon_ratio"]))
        sizes = sorted({int(base * f) for f in (0.85, 0.92, 1.0, 1.08, 1.16)})
        if self.cfg["track"] == "jungler":
            targets = [t for t in targets if t[2]] or targets[:0]
        visible = []
        for cid, name, is_jg in targets:
            hit = self.detector.find(img, cid, self.icon(cid), sizes)
            if not hit:
                continue
            gx, gy, score = hit
            area = area_fn(gx, gy)
            prev = self.last.get(name)
            self.last[name] = {"x": gx, "y": gy, "area": area, "t": game_time, "jungler": is_jg}
            visible.append(name)
            if not prev or game_time - prev["t"] > 10 or abs(prev["x"] - gx) + abs(prev["y"] - gy) > 1200:
                self.sightings.append({"time": _fmt(game_time), "t": game_time, "champion": name,
                                       "x": round(gx), "y": round(gy), "area": area, "jungler": is_jg})
        out = []
        for name, s in self.last.items():
            out.append({"champion": name, "x": s["x"], "y": s["y"], "area": s["area"], "jungler": s["jungler"],
                        "seen": _fmt(s["t"]), "ago": _fmt(game_time - s["t"]), "visible": name in visible})
        return {"last_seen": out, "sightings": self.sightings[-30:][::-1]}
