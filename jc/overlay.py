"""In-game overlay: a separate, always-on-top, click-through window (tkinter + Win32).

Nothing is injected into League – it is just a normal window drawn above the game,
like the Discord overlay. Requires League in "Borderless" or "Windowed" display mode.
Hotkey Ctrl+Shift+J: show/hide.  Ctrl+Shift+K: move mode (drag with mouse, press again to lock).
"""
import ctypes
import json
import sys
import urllib.request

BG, CARD, CHIP, TXT, MUTED = "#0a0e18", "#131a2e", "#1b2440", "#eef2ff", "#8b93a7"
CYAN, GREEN, ORANGE, RED, GOLD, VIOLET = "#22d3ee", "#34d399", "#fbbf24", "#fb7185", "#f5c451", "#a78bfa"
TONE = {"warn": ORANGE, "bad": RED, "good": GREEN, "info": CYAN, "calm": VIOLET}
KEY = "#010203"  # transparent key colour for the rounded corners

L = {"de": {"now": "JETZT", "item": "Nächstes Item", "timer": "TIMER", "ejg": "GEGNER-JUNGLER", "fed": "GEFÜTTERT",
            "focus": "DEIN FOKUS", "seen": "zuletzt", "unseen": "noch nicht gesehen", "target": "Ziel",
            "missing": "fehlen", "buy": "Kaufbar", "ejg_s": "Gegner-JG", "fed_s": "Gefüttert", "focus_s": "Dein Fokus", "left": "noch {} g", "game": "SPIELZEIT", "team": "Team-Gold", "jg": "Jungle-Gold", "lane": "{} vs {}", "lanes": {"TOP": "Top", "JUNGLE": "JG", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Sup"}, "gpm": "Gold/min", "r_good": "gut", "r_bad": "niedrig", "itemg": "Item-Wert", "next_s": "Nächster Fokus", "move": "Mit der Maus ziehen – Strg+Shift+K oder „Fertig“ in der App speichert die Position"},
     "en": {"next_s": "Next focus", "now": "NOW", "item": "NEXT ITEM", "timer": "TIMERS", "ejg": "ENEMY JUNGLER", "fed": "FED",
            "focus": "YOUR FOCUS", "seen": "last", "unseen": "not seen yet", "target": "target",
            "missing": "missing", "buy": "Affordable", "ejg_s": "Enemy JG", "fed_s": "Fed", "focus_s": "Your focus", "left": "{} g left", "game": "GAME TIME", "team": "Team gold", "jg": "Jungle gold", "lane": "{} vs {}", "lanes": {"TOP": "Top", "JUNGLE": "JG", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Sup"}, "gpm": "Gold/min", "r_good": "good", "r_bad": "low", "itemg": "item value", "move": "Drag with the mouse – Ctrl+Shift+K or “Done” in the app saves the position"}}


def layout(state):
    """Pure function: dashboard state -> overlay blocks. Empty list = hide overlay.
    Block: (kind, text, colour) with kind in head | now | more | sep | label | row."""
    live = (state or {}).get("live")
    if not live:
        return []
    h = live.get("hud") or {}
    lang = "en" if (state or {}).get("lang") == "en" else "de"
    t = L[lang]
    gold = h.get("gold")
    head = f"{h.get('time') or live.get('game_time')}   ·   {h.get('cs', live.get('cs_per_min'))} CS/min"
    if gold is not None:
        head += f"   ·   {gold:,} g".replace(",", ".")
    out = [("head", head, GREEN if h.get("cs_ok", True) else ORANGE)]
    now = h.get("now")
    if now:
        out += [("label", t["now"], TONE.get(now.get("tone"), CYAN)), ("now", now["text"], TONE.get(now.get("tone"), TXT))]
        for m in h.get("more") or []:
            out.append(("more", "• " + m["text"], MUTED))
    out.append(("sep", "", ""))
    it = h.get("item")
    if it:
        extra = ""
        if it.get("missing") is not None:
            extra = f"   {t['buy']}" if it["missing"] == 0 else f"   {it['missing']} g {t['missing']}"
        out.append(("row", f"{t['item']}|{it['name']}{extra}", GREEN if it.get("missing") == 0 else TXT))
    if it and it.get("components"):
        parts = " + ".join(it["components"])
        out.append(("row", f"{'Rezept' if lang == 'de' else 'Recipe'}|{parts} ({it['component_spend']} g)", CYAN))
    timers = h.get("timers") or []
    if timers:
        out.append(("row", f"{t['timer']}|" + "   ".join(f"{o['label']} {o['in']}" for o in timers),
                    ORANGE if any(o.get("soon") for o in timers) else TXT))
    ej = h.get("enemy_jg")
    if ej and ej.get("champion"):
        seen = f"{t['seen']} {ej['seen']}" if ej.get("seen") else t["unseen"]
        out.append(("row", f"{t['ejg']}|{ej['champion']} Lvl {ej.get('level')} · {seen}", TXT))
    if h.get("fed"):
        out.append(("row", f"{t['fed']}|" + ", ".join(h["fed"]), RED))
    if h.get("focus"):
        out.append(("row", f"{t['focus']}|{h['focus']['title']} · {t['target']} {h['focus']['target']}", VIOLET))
    return out


def model(state):
    """Pure function: dashboard state -> compact card model for the overlay (None = hide)."""
    live = (state or {}).get("live")
    if not live:
        return None
    h = live.get("hud") or {}
    lang = "en" if (state or {}).get("lang") == "en" else "de"
    t = L[lang]
    now = h.get("now") or None
    ej = h.get("enemy_jg") or {}
    it = h.get("item") or None
    hide = set((state or {}).get("ov_hide") or [])   # parts switched off in the settings (Riot-safe mode)
    if "enemy_jg" in hide:
        ej = {}
    return {
        "t": t, "time": h.get("time") or live.get("game_time"), "cs": h.get("cs", live.get("cs_per_min")),
        "cs_ok": h.get("cs_ok", True), "gold": h.get("gold"),
        "now": {"text": now["text"], "col": TONE.get(now.get("tone"), CYAN)} if now else None,
        "more": [m["text"] for m in (h.get("more") or [])][:1],
        "item": {"id": it.get("id"), "name": it["name"], "missing": it.get("missing"), "cost": it.get("cost"),
                 "components": it.get("components", []), "component_spend": it.get("component_spend")} if it else None,
        "timers": [] if "timers" in hide else [(o["label"], o["in"], bool(o.get("soon"))) for o in (h.get("timers") or [])][:3],
        "ej": (f"{ej['champion']} · Lvl {ej.get('level')} · " +
               (f"{ej['seen']}" if ej.get("seen") else t["unseen"])) if ej.get("champion") else None,
        "fed": [] if "enemy_jg" in hide else (h.get("fed") or []),
        "focus": f"{h['focus']['title']} · {h['focus']['target']}" if h.get("focus") else None,
        "mood": (now or {}).get("tone") or "calm",
        "diff": None if "gold_diff" in hide else h.get("gold_diff"),
        "gpm": h.get("gpm"),
        "choices": h.get("choices") or [],
        "next": (h.get("next") or {}).get("short") or (h.get("next") or {}).get("text"),
        "goals": [(g["text"], g["kind"], g.get("soon")) for g in (h.get("goals") or [])][:3],
        "focus_t": (h.get("focus") or {}).get("title"),
        "focus_v": (f"{t['target']} {h['focus']['target']}" if (h.get("focus") or {}).get("target") else None),
    }


def format_lines(state):
    """Backwards compatible plain list of (text, colour)."""
    return [(txt.replace("|", ": "), col) for kind, txt, col in layout(state) if kind != "sep"]


# ---------------------------------------------------------------- Win32 helpers
GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x80, 0x8000000, 0x8
MOD_CONTROL, MOD_SHIFT, WM_HOTKEY, PM_REMOVE = 0x2, 0x4, 0x312, 0x1


def _set_clickthrough(hwnd, on):
    u = ctypes.windll.user32
    st = u.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
    st = st | WS_EX_TRANSPARENT if on else st & ~WS_EX_TRANSPARENT
    u.SetWindowLongW(hwnd, GWL_EXSTYLE, st)


def _topmost(hwnd):
    """Keep the window above the game WITHOUT Tk's -topmost (Tk rewrites the extended style and the layered
    window then shows white)."""
    ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # NOSIZE|NOMOVE|NOACTIVATE


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_uint), ("pt", ctypes.c_long * 2)]


# ---------------------------------------------------------------- pixel renderer (Pillow), Apple style
# Drawn into an RGBA image: translucent dark glass, crisp text (Windows: per-pixel alpha via
# UpdateLayeredWindow). Style like an iOS Live Activity: continuous rounded corners, system colours,
# tinted status card with symbol (colour + shape), thin progress bar, grouped rows with hairlines.
SS = 2  # supersampling
A_GREEN, A_ORANGE, A_RED, A_BLUE, A_PURPLE, A_YELLOW = "#30d158", "#ff9f0a", "#ff453a", "#0a84ff", "#bf5af2", "#ffd60a"
A_TONE = {RED: A_RED, ORANGE: A_ORANGE, GREEN: A_GREEN, CYAN: A_BLUE, VIOLET: A_PURPLE}


def _rgb(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def pil_fonts(scale=1.0):
    """Segoe UI Variable (Windows 11) – fallbacks Segoe UI, DM Sans, DejaVu."""
    from PIL import ImageFont
    cands = ["C:/Windows/Fonts/SegUIVar.ttf", "C:/Windows/Fonts/segoeui.ttf",
             "/usr/share/fonts/truetype/google-fonts/ofl/dmsans/DMSans[opsz,wght].ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    bold = {"C:/Windows/Fonts/segoeui.ttf": "C:/Windows/Fonts/seguisb.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"}

    def f(px, weight):
        for path in cands:
            p = bold.get(path, path) if weight >= 600 else path
            try:
                ft = ImageFont.truetype(p, int(px * scale * SS))
            except OSError:
                continue
            try:
                vals = []
                for a in ft.get_variation_axes():
                    n = a["name"] if isinstance(a["name"], str) else a["name"].decode()
                    n = n.lower()
                    vals.append(max(a["minimum"], min(a["maximum"], weight)) if ("weight" in n or n == "wght")
                                else max(a["minimum"], min(a["maximum"], px)) if ("optical" in n or n == "opsz")
                                else a.get("default", a["minimum"]))
                ft.set_variation_by_axes(vals)
            except Exception:  # noqa: BLE001 - static font
                pass
            return ft
        return ImageFont.load_default(int(px * scale * SS))
    return {"title": f(17, 650), "cap": f(11, 500), "capb": f(11, 650), "now": f(14.5, 600), "body": f(13, 500),
            "bodyb": f(13, 650), "val": f(15, 650), "icon": f(13, 800)}


def _wrap(d, text, font, maxw):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


_MOOSI = {}


def _moosi_img(mood, px):
    """Moosi artwork (rendered from the app's SVG) for the overlay; None if the file is missing."""
    name = {"good": "proud", "bad": "worried"}.get(mood, "happy")
    key = ("head", px)
    if key not in _MOOSI:
        try:
            import os
            from PIL import Image as _I
            f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "moosi", "head.png")
            _MOOSI[key] = _I.open(f).convert("RGBA").resize((px, px), _I.LANCZOS)
        except Exception:  # noqa: BLE001 - fall back to the drawn monster
            _MOOSI[key] = None
    return _MOOSI[key]


def _monster_pil(d, x, y, s, mood):
    body = _rgb({"bad": A_RED, "warn": A_ORANGE, "good": A_GREEN}.get(mood, "#2dd4bf"))
    dark = _rgb("#0b0f1a")
    d.polygon([(x + s * .2, y + s * .36), (x + s * .02, y + s * .02), (x + s * .44, y + s * .2)], fill=body)
    d.polygon([(x + s * .8, y + s * .36), (x + s * .98, y + s * .02), (x + s * .56, y + s * .2)], fill=body)
    d.ellipse([x, y + s * .12, x + s, y + s], fill=body)
    d.ellipse([x + s * .18, y + s * .34, x + s * .48, y + s * .64], fill=(255, 255, 255, 255))
    d.ellipse([x + s * .52, y + s * .34, x + s * .82, y + s * .64], fill=(255, 255, 255, 255))
    for ex in (.29, .6):
        d.ellipse([x + s * ex, y + s * .42, x + s * (ex + .11), y + s * .56], fill=dark)
    w = max(2, int(s / 14))
    if mood == "bad":
        d.arc([x + s * .34, y + s * .74, x + s * .66, y + s * .96], 200, 340, fill=dark, width=w)
    else:
        d.arc([x + s * .32, y + s * .58, x + s * .68, y + s * .86], 20, 160, fill=dark, width=w)
        d.polygon([(x + s * .45, y + s * .8), (x + s * .5, y + s * .88), (x + s * .55, y + s * .8)], fill=(255, 255, 255, 255))


def render_image(m, W=300, fonts=None, item_icon=None, bg_opacity=0.72, move=False, scale=1.0):
    """Overlay card as RGBA image. bg_opacity: 0 = invisible background, 1 = solid."""
    from PIL import Image, ImageDraw
    F = fonts or pil_fonts(scale)
    k = SS * scale
    Wp = int(W * k)
    fg = Image.new("RGBA", (Wp, int(1000 * k)), (0, 0, 0, 0))
    under = Image.new("RGBA", fg.size, (0, 0, 0, 0))
    d, u = ImageDraw.Draw(fg), ImageDraw.Draw(under)
    pad = 14 * k
    W1, SEC, HAIR = _rgb("#ffffff"), _rgb("#ebebf5", 150), _rgb("#ffffff", 26)
    fill2 = _rgb("#ffffff", int(20 + 14 * bg_opacity))  # secondary fill (iOS tertiarySystemFill on dark)

    def lh(f):
        b = f.getbbox("ÄAg")
        return b[3] - b[1] + 3 * k

    # ---- header: Moosi avatar · game time · CS/min and gold
    y = 13 * k
    av = 30 * k
    u.ellipse([pad, y, pad + av, y + av], fill=fill2)
    mi = _moosi_img(m["mood"], int(av + 4 * k))
    if mi is not None:
        fg.alpha_composite(mi, (int(pad - 2 * k), int(y - 4 * k)))
    else:
        _monster_pil(d, pad + 5 * k, y + 5 * k, av - 10 * k, m["mood"])
    tx = pad + av + 10 * k
    x = Wp - pad
    gp = m.get("gpm") or {}
    gcol = {"good": A_GREEN, "ok": A_ORANGE, "bad": A_RED}.get(gp.get("rate"), "#ebebf5")
    gcap = "GPM" + ({"good": " " + m["t"]["r_good"], "ok": " ok", "bad": " " + m["t"]["r_bad"]}.get(gp.get("rate"), ""))  # word, not only colour
    for val, cap, col in ((str(gp["value"]) if gp.get("value") is not None else "–", gcap, gcol),
                          (str(m["cs"]), "CS/min", A_GREEN if m["cs_ok"] else A_ORANGE)):
        wv = max(d.textlength(val, font=F["val"]), d.textlength(cap, font=F["cap"]))
        d.text((x, y), val, font=F["val"], fill=_rgb(col), anchor="ra")
        d.text((x, y + lh(F["val"])), cap, font=F["cap"], fill=SEC, anchor="ra")
        x -= wv + 16 * k
    # your coaching focus at the very top (what to train this game), next to Moosi
    if m.get("focus_t"):
        d.text((tx, y - 1 * k), m["t"]["focus_s"], font=F["capb"], fill=_rgb(A_PURPLE))
        yy = y - 1 * k + lh(F["capb"])
        lines = _wrap(d, m["focus_t"], F["bodyb"], x - tx)
        for ln in lines[:2]:
            d.text((tx, yy), ln, font=F["bodyb"], fill=W1)
            yy += lh(F["bodyb"])
        if m.get("focus_v"):
            d.text((tx, yy), m["focus_v"], font=F["cap"], fill=SEC)
            yy += lh(F["cap"])
        y = max(y + av, yy) - av
    y += av + 12 * k

    # ---- gold (item value from the Tab scoreboard): team + your own lane, one compact card
    df = m.get("diff") or {}
    LN = m["t"].get("lanes", {})
    mine = df.get("mine") or {}
    own = (m["t"]["lane"].format(LN.get(mine["pos"], mine["pos"]), LN.get(mine["pos"], mine["pos"])), mine["v"]) if mine.get("v") is not None \
        else (m["t"]["jg"], df.get("jungle"))
    cols = [c for c in [(m["t"]["team"], df.get("team")), own] if c[1] is not None]
    if cols:
        ch = lh(F["bodyb"]) + 24 * k
        u.rounded_rectangle([pad, y, Wp - pad, y + ch], 12 * k, fill=fill2)
        cw = (Wp - 2 * pad) / len(cols)
        for i, (lab, v) in enumerate(cols):
            x0, x1 = pad + i * cw + 11 * k, pad + (i + 1) * cw - 11 * k
            if i:
                u.line([(pad + i * cw, y + 8 * k), (pad + i * cw, y + ch - 8 * k)], fill=HAIR, width=max(1, int(k)))
            col = A_GREEN if v > 150 else A_RED if v < -150 else "#ebebf5"
            txt = ("+" if v > 0 else "−" if v < 0 else "±") + f"{abs(v):,}".replace(",", ".")
            ty = y + 7 * k
            d.text((x0, ty + (lh(F["bodyb"]) - lh(F["cap"])) * .6), lab, font=F["cap"], fill=SEC)
            d.text((x1, ty), txt, font=F["bodyb"], fill=_rgb(col), anchor="ra")
            by, mid = y + ch - 8 * k, (x0 + x1) / 2   # thin bar: centre = even, ±3000 g = full
            u.rounded_rectangle([x0, by, x1, by + 3 * k], 1.5 * k, fill=_rgb("#ffffff", 26))
            ext = max(-1.0, min(1.0, v / 3000)) * (x1 - x0) / 2
            if abs(ext) > 2 * k:
                u.rounded_rectangle([min(mid, mid + ext), by, max(mid, mid + ext), by + 3 * k], 1.5 * k, fill=_rgb(col))
        y += ch + 12 * k

    # ---- NOW: tinted card with a symbol (colour + shape)
    if m["now"]:
        col = A_TONE.get(m["now"]["col"], A_BLUE)
        top = y
        ic = 24 * k
        tx = pad + 12 * k + ic + 10 * k
        yy = y + 11 * k
        d.text((tx, yy), m["t"]["now"].capitalize(), font=F["capb"], fill=_rgb(col))
        yy += lh(F["capb"])
        for ln in _wrap(d, m["now"]["text"], F["now"], Wp - pad - 12 * k - tx):
            d.text((tx, yy), ln, font=F["now"], fill=W1)
            yy += lh(F["now"])
        for more in m["more"][:1]:
            yy += 2 * k
            for ln in _wrap(d, more, F["cap"], Wp - pad - 12 * k - tx):
                d.text((tx, yy), ln, font=F["cap"], fill=SEC)
                yy += lh(F["cap"])
        bottom = yy + 9 * k
        u.rounded_rectangle([pad, top, Wp - pad, bottom], 14 * k, fill=_rgb(col, 46))
        cx, cy = pad + 12 * k + ic / 2, top + 11 * k + ic / 2
        u.ellipse([cx - ic / 2, cy - ic / 2, cx + ic / 2, cy + ic / 2], fill=_rgb(col))
        w_ = max(2, int(2.2 * k))
        dark = _rgb("#000000", 220)
        if col == A_GREEN:
            d.line([(cx - 5 * k, cy), (cx - 1.5 * k, cy + 3.8 * k), (cx + 5 * k, cy - 4 * k)], fill=dark, width=w_)
        elif col == A_RED:
            d.line([(cx - 4 * k, cy - 4 * k), (cx + 4 * k, cy + 4 * k)], fill=dark, width=w_)
            d.line([(cx + 4 * k, cy - 4 * k), (cx - 4 * k, cy + 4 * k)], fill=dark, width=w_)
        else:
            d.text((cx, cy), "i" if col == A_BLUE else "!", font=F["icon"], fill=dark, anchor="mm")
        y = bottom + 12 * k

    # ---- next item: rounded icon, name, gold left, thin progress bar
    if m["item"]:
        itm = m["item"]
        ready = itm.get("missing") == 0
        isz = 38 * k
        if item_icon is not None:
            ic_ = item_icon.convert("RGBA").resize((int(isz), int(isz)))
            mask = Image.new("L", ic_.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, ic_.size[0] - 1, ic_.size[1] - 1], 9 * k, fill=255)
            fg.paste(ic_, (int(pad), int(y)), mask)
        else:
            u.rounded_rectangle([pad, y, pad + isz, y + isz], 9 * k, fill=fill2)
        tx = pad + isz + 11 * k
        d.text((tx, y), m["t"]["item"], font=F["cap"], fill=SEC)
        d.text((tx, y + lh(F["cap"])), itm["name"], font=F["bodyb"], fill=W1)
        right = m["t"]["buy"] if ready else (m["t"]["left"].format(itm["missing"]) if itm.get("missing") is not None else "")
        d.text((Wp - pad, y), right, font=F["capb"] if ready else F["cap"], fill=_rgb(A_GREEN) if ready else SEC, anchor="ra")
        if itm.get("cost") and itm.get("missing") is not None:
            frac = max(0.0, min(1.0, 1 - (itm.get("missing") or 0) / itm["cost"]))
            by = y + isz - 5 * k
            u.rounded_rectangle([tx, by, Wp - pad, by + 5 * k], 3 * k, fill=fill2)
            if frac > 0.02:
                u.rounded_rectangle([tx, by, tx + max(6 * k, (Wp - pad - tx) * frac), by + 5 * k], 3 * k,
                                    fill=_rgb(A_GREEN if ready else A_BLUE))
        y += isz + 12 * k
        if itm["components"]:
            purchase = " + ".join(itm["components"]) + f" ({itm['component_spend']} g)"
            for line in _wrap(d, purchase, F["cap"], Wp - 2 * pad):
                d.text((pad, y), line, font=F["cap"], fill=SEC)
                y += lh(F["cap"])

    # ---- objective timers: capsules, the next one tinted orange
    goals = m.get("goals")
    if goals is None:   # older payloads: focus + timers
        goals = ([(m["next"], "focus", True)] if m.get("next") else []) + [(f"{l} {w}", "obj", so) for l, w, so in m["timers"]]
    if goals:   # the next 3 goals (focus, objectives, towers) – chips that wrap, never cut off
        x, hgt = pad, lh(F["capb"]) + 9 * k
        for txt, kind, soon in goals[:3]:
            while d.textlength(txt, font=F["capb"]) > Wp - 2 * pad - 40 * k and len(txt) > 4:
                txt = txt[:-2].rstrip() + "…"
            icon = kind in ("focus", "tower")
            w = d.textlength(txt, font=F["capb"]) + (30 if icon else 18) * k
            if x + w > Wp - pad and x > pad:
                x, y = pad, y + hgt + 6 * k
            if kind == "focus":
                bg, fgc, ic = _rgb(A_PURPLE, 70), _rgb("#d9c8ff"), _rgb(A_PURPLE)
            elif soon:
                bg, fgc, ic = _rgb(A_ORANGE, 60), _rgb(A_ORANGE), None
            else:
                bg, fgc, ic = fill2, W1, SEC
            u.rounded_rectangle([x, y, x + w, y + hgt], hgt / 2, fill=bg)
            cx, cy = x + 12 * k, y + hgt / 2
            if kind == "focus":   # target symbol
                d.ellipse([cx - 5 * k, cy - 5 * k, cx + 5 * k, cy + 5 * k], outline=ic, width=max(1, int(1.4 * k)))
                d.ellipse([cx - 1.6 * k, cy - 1.6 * k, cx + 1.6 * k, cy + 1.6 * k], fill=ic)
            elif kind == "tower":   # small tower
                d.rectangle([cx - 2.5 * k, cy - 3 * k, cx + 2.5 * k, cy + 5 * k], fill=ic)
                d.polygon([(cx - 4.5 * k, cy - 3 * k), (cx + 4.5 * k, cy - 3 * k), (cx, cy - 7 * k)], fill=ic)
            d.text((x + (21 if icon else 9) * k, y + 4 * k), txt, font=F["capb"], fill=fgc)
            x += w + 6 * k
        y += hgt + 12 * k

    # ---- grouped rows: label left, value right, hairline separators (only rows that have content)
    rows = []   # enemy jungler / fed enemies: removed on request – keeps the overlay small
    rows = [r for r in rows if r[1]]
    if rows:
        top = y
        y += 3 * k
        for i, (lab, text, col) in enumerate(rows):
            if i:
                u.line([(pad + 12 * k, y), (Wp - pad, y)], fill=HAIR, width=max(1, int(k)))
            y += 7 * k
            d.text((pad + 12 * k, y + 1 * k), lab, font=F["cap"], fill=SEC)
            lx = pad + 24 * k + max(d.textlength(r[0], font=F["cap"]) for r in rows)
            for ln in _wrap(d, text, F["body"], Wp - pad - 12 * k - lx):
                d.text((Wp - pad - 12 * k, y), ln, font=F["body"], fill=col, anchor="ra")
                y += lh(F["body"])
            y += 5 * k
        y += 3 * k
        u.rounded_rectangle([pad, top, Wp - pad, y], 14 * k, fill=_rgb("#ffffff", int(8 + 8 * bg_opacity)))
        y += 12 * k
    H = int(y + 2 * k)

    # ---- glass panel: continuous corners, translucent, hairline border
    base = Image.new("RGBA", (Wp, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(base)
    bd.rounded_rectangle([0, 0, Wp - 1, H - 1], 24 * k, fill=_rgb("#1c1c1e", int(255 * bg_opacity)),
                         outline=_rgb(A_BLUE) if move else _rgb("#ffffff", 40), width=max(1, int((2 if move else 1) * k)))
    out = Image.alpha_composite(base, under.crop((0, 0, Wp, H)))
    out = Image.alpha_composite(out, fg.crop((0, 0, Wp, H)))
    return out.resize((int(W * scale), int(H / SS)), Image.LANCZOS)


def render_picker(choices, current_id, icons, scale=1.0, lang="de", fonts=None, notes=""):
    """Tab view: your own focus notes (sticky note) + item icons of your build; click an item to make it the
    'next item' in the overlay. Returns (RGBA image, [(x0, y0, x1, y1, item_id)] click areas in image pixels)."""
    from PIL import Image, ImageDraw
    F = fonts or pil_fonts(scale)
    k = SS * scale
    cols = min(4, max(1, len(choices))) if choices else 4
    tile, gap, pad = 66 * k, 8 * k, 14 * k
    rows_n = (len(choices) + cols - 1) // cols if choices else 0
    name_h = 30 * k
    Wp = int(2 * pad + max(cols, 3) * tile + (max(cols, 3) - 1) * gap)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    note_lines = []
    for para in (notes or "").strip().splitlines()[:8]:
        para = para.strip()
        if para:
            note_lines += _wrap(probe, ("• " if not para.startswith(("•", "-")) else "") + para.lstrip("-• ").strip(), F["body"], Wp - 2 * pad - 24 * k)
    note_lines = note_lines[:10]
    line_h = probe.textbbox((0, 0), "Äg", font=F["body"])[3] + 5 * k
    note_h = (36 * k + len(note_lines) * line_h + 14 * k) if note_lines else 0
    head = 44 * k if choices else 0
    Hp = int(pad + note_h + (pad if note_lines and choices else 0) + head + rows_n * (tile + name_h) + max(0, rows_n - 1) * gap + (pad if choices else 0))
    im = Image.new("RGBA", (Wp, Hp), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, Wp - 1, Hp - 1], 24 * k, fill=_rgb("#1c1c1e", 235), outline=_rgb("#ffffff", 40), width=max(1, int(k)))
    y = pad
    if note_lines:   # sticky note
        d.rounded_rectangle([pad, y, Wp - pad, y + note_h], 16 * k, fill=_rgb("#fde68a", 235))
        d.text((pad + 14 * k, y + 11 * k), {"de": "DEINE NOTIZEN", "en": "YOUR NOTES"}[lang], font=F["capb"], fill=_rgb("#78350f"))
        yy = y + 34 * k
        for ln in note_lines:
            d.text((pad + 14 * k, yy), ln, font=F["body"], fill=_rgb("#1c1917"))
            yy += line_h
        y += note_h + pad
    t = {"de": ("Nächstes Item wählen", "Tab halten · Item anklicken"), "en": ("Choose next item", "Hold Tab · click an item")}[lang]
    if choices:
        d.text((pad, y - 2 * k), t[0], font=F["bodyb"], fill=_rgb("#ffffff"))
        d.text((Wp - pad, y), t[1], font=F["cap"], fill=_rgb("#ebebf5", 150), anchor="ra")
        head = y + 32 * k
    rects = []
    for i, c in enumerate(choices):
        r, col = divmod(i, cols)
        x0 = pad + col * (tile + gap)
        y0 = head + r * (tile + name_h + gap)
        sel = c["id"] == current_id
        ic = icons(c["id"]) if callable(icons) else None
        box = [x0, y0, x0 + tile, y0 + tile]
        if ic is not None:
            ic = ic.convert("RGBA").resize((int(tile), int(tile)))
            mask = Image.new("L", ic.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, ic.size[0] - 1, ic.size[1] - 1], 14 * k, fill=255)
            if not sel:  # others slightly dimmed, the chosen one stands out
                ic = Image.blend(Image.new("RGBA", ic.size, (28, 28, 30, 255)), ic, 0.82)
            im.paste(ic, (int(x0), int(y0)), mask)
        else:
            d.rounded_rectangle(box, 14 * k, fill=_rgb("#ffffff", 30))
        if sel:
            d.rounded_rectangle([x0 - 3 * k, y0 - 3 * k, x0 + tile + 3 * k, y0 + tile + 3 * k], 16 * k,
                                outline=_rgb(A_BLUE), width=int(3 * k))
            cx, cy, rr = x0 + tile - 4 * k, y0 + 4 * k, 10 * k
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=_rgb(A_BLUE), outline=_rgb("#1c1c1e"), width=int(2 * k))
            d.line([(cx - 4.5 * k, cy), (cx - 1.2 * k, cy + 3.5 * k), (cx + 4.8 * k, cy - 3.8 * k)], fill=_rgb("#ffffff"), width=int(2.2 * k))
        elif c.get("core"):
            d.rounded_rectangle([x0 + 5 * k, y0 + 5 * k, x0 + 5 * k + d.textlength("Build", font=F["cap"]) + 10 * k, y0 + 5 * k + 16 * k],
                                8 * k, fill=_rgb("#000000", 150))
            d.text((x0 + 10 * k, y0 + 6 * k), "Build", font=F["cap"], fill=_rgb("#ffffff"))
        name = _wrap(d, c["name"], F["cap"], tile + 4 * k)[:2]
        ny = y0 + tile + 4 * k
        for ln in name:
            d.text((x0 + tile / 2, ny), ln, font=F["cap"], fill=_rgb("#ffffff") if sel else _rgb("#ebebf5", 170), anchor="ma")
            ny += 13 * k
        rects.append((x0, y0, x0 + tile, y0 + tile + name_h, c["id"]))
    out = im.resize((int(Wp / SS), int(Hp / SS)), Image.LANCZOS)
    return out, [(a / SS, b / SS, c / SS, e / SS, iid) for a, b, c, e, iid in rects]


def premultiplied_bgra(im):
    """RGBA (PIL) -> premultiplied BGRA bytes for UpdateLayeredWindow."""
    from PIL import ImageChops
    r, g, b, a = im.split()
    r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))
    from PIL import Image
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def _gdi():
    from ctypes import wintypes
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    H = ctypes.c_void_p
    u.GetDC.restype = H; u.GetDC.argtypes = [H]
    u.ReleaseDC.argtypes = [H, H]
    g.CreateCompatibleDC.restype = H; g.CreateCompatibleDC.argtypes = [H]
    g.CreateDIBSection.restype = H
    g.CreateDIBSection.argtypes = [H, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), H, wintypes.DWORD]
    g.SelectObject.restype = H; g.SelectObject.argtypes = [H, H]
    g.DeleteObject.argtypes = [H]; g.DeleteDC.argtypes = [H]
    u.UpdateLayeredWindow.argtypes = [H, H, ctypes.c_void_p, ctypes.c_void_p, H, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD]
    return u, g, wintypes


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


class _BLEND(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


def _ulw(hwnd, im, x, y):
    """Shows an RGBA image as the window content with per-pixel alpha (Win32 UpdateLayeredWindow)."""
    u, g, wt = _gdi()
    w, h = im.size
    screen = u.GetDC(None)
    mem = g.CreateCompatibleDC(screen)
    bmi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    hbmp = g.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    data = premultiplied_bgra(im)
    ctypes.memmove(bits, data, len(data))
    old = g.SelectObject(mem, hbmp)
    pt, size, src = wt.POINT(int(x), int(y)), wt.SIZE(w, h), wt.POINT(0, 0)
    blend = _BLEND(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
    u.UpdateLayeredWindow(hwnd, screen, ctypes.byref(pt), ctypes.byref(size), mem, ctypes.byref(src), 0,
                          ctypes.byref(blend), 2)  # ULW_ALPHA
    g.SelectObject(mem, old)
    g.DeleteObject(hbmp)
    g.DeleteDC(mem)
    u.ReleaseDC(None, screen)


def _chime_wav(path, kind="remind", volume=0.5):
    """Soft reminder sound (two rising notes) or a lower one after a death – generated, no sound files."""
    import math
    import struct
    import wave
    rate = 44100
    notes = [(880, .0, .16), (1320, .12, .30)] if kind == "remind" else [(523, .0, .22), (392, .16, .42)]
    n = int(rate * (max(e for _, _, e in notes) + .12))
    buf = [0.0] * n
    for f, a, e in notes:
        for i in range(int(rate * a), min(n, int(rate * (e + .1)))):
            t = i / rate - a
            env = min(1.0, t / .01) * math.exp(-t * 7)
            buf[i] += env * (math.sin(2 * math.pi * f * t) + .25 * math.sin(4 * math.pi * f * t))
    peak = max(1e-6, max(abs(x) for x in buf))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(32767 * .9 * volume * x / peak)) for x in buf))
    return path


def play_sound(kind, volume):
    """Plays the reminder asynchronously (Windows); silently does nothing elsewhere."""
    try:
        import os
        import tempfile
        import winsound
        path = os.path.join(tempfile.gettempdir(), f"moosi_{kind}_{int(volume * 100)}.wav")
        if not os.path.exists(path):
            _chime_wav(path, kind, volume)
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:  # noqa: BLE001 - no sound device / not Windows
        pass


def sound_due(snd, live, mem):
    """Decides whether to play a reminder now: after every own death and about every `every` seconds of
    game time (not during the first 90 s). mem keeps state between calls. Returns "death", "remind" or None."""
    if not snd or not snd.get("on") or not live or live.get("t") is None:
        mem.clear()
        return None
    t, deaths = float(live.get("t") or 0), int(live.get("deaths") or 0)
    if "deaths" not in mem or t + 5 < mem.get("t", 0):   # new game or first call
        mem.update(deaths=deaths, t=t, last=t)
        return None
    mem["t"] = t
    if deaths > mem["deaths"]:
        mem["deaths"], mem["last"] = deaths, t
        return "death" if snd.get("death", True) else None
    if t >= 90 and t - mem["last"] >= max(30, int(snd.get("every") or 60)) and not live.get("dead"):
        mem["last"] = t
        return "remind"
    return None


def run(url, cfg):
    import io
    import tkinter as tk

    win = sys.platform == "win32"
    scale = 1.0
    if win:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # sharp text on 125 % / 150 % displays
        except Exception:  # noqa: BLE001
            pass
        try:
            scale = max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96)
        except Exception:  # noqa: BLE001
            scale = 1.0
    ocfg = {"x": -330, "y": 70, "width": 300, "bg_opacity": 0.72, **cfg.get("overlay", {})}
    if ocfg.get("width") == 390:  # old default -> new compact card
        ocfg["width"], ocfg["x"] = 300, -330
    W = ocfg["width"]
    base_scale = scale
    fcache = {}

    def fonts_for(sz):
        if sz not in fcache:
            fcache[sz] = pil_fonts(base_scale * sz)
        return fcache[sz]
    icons = {}

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    sw = root.winfo_screenwidth()
    if ocfg.get("abs"):  # saved by dragging (physical pixels)
        pos = {"x": int(ocfg["x"]), "y": int(ocfg["y"])}
    else:
        pos = {"x": ocfg["x"] if ocfg["x"] >= 0 else sw + int(ocfg["x"] * scale), "y": int(ocfg["y"] * scale)}
    root.geometry(f"{int(W * scale)}x60+{pos['x']}+{pos['y']}")
    root.withdraw()   # never show the empty (white) Tk window – it only appears once an image is ready
    icons = {}
    label = None if win else tk.Label(root, bd=0, bg=BG)
    if label:
        label.pack()
    st = {"visible": True, "move": False, "drag": (0, 0), "shown": False, "last": None, "img": None,
          "mode": ocfg.get("mode", "always"), "opacity": ocfg["bg_opacity"], "has": False,
          "size": float(ocfg.get("size", 1.0)), "choices": [], "cur": None, "lang": "de", "pshown": False,
          "pimg": None, "prects": [], "pkey": None, "tab": False}

    # ---- Tab picker window (item icons of the build, click = next item)
    pick_win = tk.Toplevel(root, bg="black")
    pick_win.overrideredirect(True)
    pick_win.attributes("-topmost", True)
    plabel = None if win else tk.Label(pick_win, bd=0, bg=BG)
    if plabel:
        plabel.pack()
    phwnd = None
    if win:
        pick_win.update_idletasks()
        phwnd = ctypes.windll.user32.GetParent(pick_win.winfo_id()) or pick_win.winfo_id()
        _set_clickthrough(phwnd, False)  # layered + no-activate, but clickable
    pick_win.withdraw()
    ppos = {"x": 0, "y": 0}

    def post_settings(body):
        try:
            req = urllib.request.Request(url + "/api/overlay-settings", method="POST", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2).read()
        except Exception:  # noqa: BLE001
            pass

    def on_pick(e):
        for x0, y0, x1, y1, iid in st["prects"]:
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                st["cur"] = iid
                st["pkey"] = None
                post_settings({"pick": iid})
                draw_picker()
                root.after(150, refresh_now)
                break
    pick_win.bind("<Button-1>", on_pick)

    def draw_picker():
        key = (json.dumps(st["choices"], sort_keys=True), st["cur"], st["size"], st["lang"], st.get("notes", ""))
        if key == st["pkey"] or not (st["choices"] or st.get("notes")):
            return
        st["pkey"] = key
        im, rects = render_picker(st["choices"], st["cur"], icon, base_scale * st["size"], st["lang"], fonts_for(st["size"]),
                                  st.get("notes", ""))
        st["pimg"], st["prects"] = im, rects
        if st["pshown"]:
            place_picker()

    def place_picker():
        im = st["pimg"]
        mw = st["img"].size[0] if st["img"] is not None else int(W * base_scale)
        sw_ = root.winfo_screenwidth()
        x = pos["x"] - im.size[0] - int(10 * base_scale)
        if x < 0:
            x = pos["x"] + mw + int(10 * base_scale)
        x = max(0, min(x, sw_ - im.size[0]))
        ppos["x"], ppos["y"] = x, pos["y"]
        pick_win.geometry(f"{im.size[0]}x{im.size[1]}+{x}+{pos['y']}")
        if win:
            _ulw(phwnd, im.convert("RGBA"), x, pos["y"])
        else:
            from PIL import ImageTk
            ph = ImageTk.PhotoImage(im)
            plabel.configure(image=ph)
            plabel.image = ph

    def save_pos():
        try:
            req = urllib.request.Request(url + "/api/overlay-settings", method="POST",
                                         data=json.dumps({"x": pos["x"], "y": pos["y"], "move": False}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2).read()
        except Exception:  # noqa: BLE001
            pass

    def set_move(on):
        if on == st["move"]:
            return
        st["move"] = on
        if win:
            _set_clickthrough(hwnd, not on)
        st["last"] = None
        if not on:
            st["srv_move"] = False
            save_pos()

    def apply_visibility():
        tab = bool(win and ctypes.windll.user32.GetAsyncKeyState(0x09) & 0x8000)  # Tab held (like the scoreboard)
        pwant = tab and st["has"] and st["visible"] and (bool(st["choices"]) or bool(st.get("notes"))) and st["pimg"] is not None and not st["move"]
        if pwant and not st["pshown"]:
            st["pshown"] = True
            pick_win.deiconify()
            if win:   # Tk rewrites the window style on deiconify -> layered style again, else it shows white
                _set_clickthrough(phwnd, False)
                _topmost(phwnd)
            else:
                pick_win.attributes("-topmost", True)
            place_picker()
        elif not pwant and st["pshown"]:
            pick_win.withdraw()
            st["pshown"] = False
        want = st["has"] and st["visible"] and (st["mode"] != "tab" or tab or st["move"])
        if want and st["img"] is not None and not st["shown"]:
            root.deiconify()
            st["shown"] = True
            if win:   # after Tk's own style change on deiconify, so the window stays layered
                _set_clickthrough(hwnd, not st["move"])
                _topmost(hwnd)
            else:
                root.attributes("-topmost", True)
            show(st["img"])
        elif not want and st["shown"]:
            root.withdraw()
            st["shown"] = False

    hwnd = None
    if win:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        _set_clickthrough(hwnd, True)
        ctypes.windll.user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_SHIFT, 0x4A)  # J
        ctypes.windll.user32.RegisterHotKey(None, 2, MOD_CONTROL | MOD_SHIFT, 0x4B)  # K

    def show(im):
        st["img"] = im
        root.geometry(f"{im.size[0]}x{im.size[1]}+{pos['x']}+{pos['y']}")
        if win:
            _ulw(hwnd, im.convert("RGBA"), pos["x"], pos["y"])
        else:
            from PIL import ImageTk
            ph = ImageTk.PhotoImage(im)
            label.configure(image=ph)
            label.image = ph

    def start_drag(e):
        st["drag"] = (e.x_root - pos["x"], e.y_root - pos["y"])

    def do_drag(e):
        if st["move"] and st["img"] is not None:
            dx, dy = st["drag"]
            pos["x"], pos["y"] = e.x_root - dx, e.y_root - dy
            show(st["img"])
    root.bind("<Button-1>", start_drag)
    root.bind("<B1-Motion>", do_drag)

    def icon(iid):
        if not iid:
            return None
        if iid not in icons:
            try:
                from PIL import Image
                with urllib.request.urlopen(f"{url}/icon/item/{int(iid)}", timeout=3) as r:
                    icons[iid] = Image.open(io.BytesIO(r.read())).convert("RGBA")
            except Exception:  # noqa: BLE001
                icons[iid] = None
        return icons[iid]

    def poll_hotkeys():
        if win:
            msg = MSG()
            while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
                if msg.wParam == 1:
                    st["visible"] = not st["visible"]
                elif msg.wParam == 2:
                    set_move(not st["move"])
        apply_visibility()
        root.after(40, poll_hotkeys)

    def refresh_now():
        try:
            with urllib.request.urlopen(url + "/api/overlay", timeout=2) as r:
                state = json.loads(r.read().decode("utf-8"))
            ov = state.get("ov") or {}
            st["mode"] = ov.get("mode", st["mode"])
            if ov.get("opacity") is not None and ov["opacity"] != st["opacity"]:
                st["opacity"], st["last"] = ov["opacity"], None
            if ov.get("size") and float(ov["size"]) != st["size"]:
                st["size"], st["last"], st["pkey"] = float(ov["size"]), None, None
            st["cur"] = ov.get("pick") or None
            if "move" in ov and bool(ov["move"]) != st.get("srv_move"):  # only react to changes from the app
                st["srv_move"] = bool(ov["move"])
                set_move(st["srv_move"])
            m = model(state)
            lang = "en" if state.get("lang") == "en" else "de"
            if (state.get("notes") or "") != st.get("notes", ""):
                st["notes"], st["pkey"] = state.get("notes") or "", None
            snd = state.get("sound") or {}
            due = sound_due(snd, state.get("live"), st.setdefault("snd", {}))
            if due:
                play_sound(due, float(snd.get("volume", .5)))
        except Exception:  # noqa: BLE001
            m, lang = None, "de"
        st["lang"] = lang
        st["choices"] = (m or {}).get("choices") or []
        if m and m.get("item"):
            st["cur"] = m["item"].get("id")
        draw_picker()
        if st["move"] and not m:  # preview card so it can be placed outside a game
            m = {"t": L[lang], "time": "", "cs": "–", "cs_ok": True, "gold": None,
                 "now": {"text": L[lang]["move"], "col": CYAN}, "more": [], "item": None, "timers": [], "ej": None,
                 "fed": [], "focus": None, "mood": "info", "diff": None, "gpm": None, "focus_t": None, "focus_v": None}
        st["has"] = bool(m)
        if m:
            key = (json.dumps(m, sort_keys=True, default=str), st["move"], st["opacity"], st["size"])
            if key != st["last"]:
                im = render_image(m, W, fonts_for(st["size"]), icon((m.get("item") or {}).get("id")), st["opacity"],
                                  st["move"], base_scale * st["size"])
                st["img"] = im
                if st["shown"]:
                    show(im)
                else:
                    root.geometry(f"{im.size[0]}x{im.size[1]}+{pos['x']}+{pos['y']}")
                st["last"] = key
            if st["shown"]:   # re-assert above the game
                if win:
                    _topmost(hwnd)
                else:
                    root.attributes("-topmost", True)
        apply_visibility()

    def refresh_loop():
        try:
            refresh_now()
        except Exception:  # noqa: BLE001 - never leave a blank (white) window: hide it and log why
            from .paths import log_error
            log_error("overlay refresh")
            st["has"], st["img"], st["last"] = False, None, None
            try:
                root.withdraw()
                st["shown"] = False
            except Exception:  # noqa: BLE001
                pass
        root.after(1000, refresh_loop)

    root.withdraw()
    poll_hotkeys()
    refresh_loop()
    root.mainloop()
