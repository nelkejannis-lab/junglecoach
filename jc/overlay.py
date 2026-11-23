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

L = {"de": {"now": "JETZT", "item": "NÄCHSTES ITEM", "timer": "TIMER", "ejg": "GEGNER-JUNGLER", "fed": "GEFÜTTERT",
            "focus": "DEIN FOKUS", "seen": "zuletzt", "unseen": "noch nicht gesehen", "target": "Ziel",
            "missing": "fehlen", "buy": "kaufbar!", "ejg_s": "JUNGLER", "fed_s": "GEFÜTTERT", "focus_s": "FOKUS", "left": "noch {} g", "game": "SPIELZEIT", "move": "Overlay verschieben – Ctrl+Shift+K zum Fixieren"},
     "en": {"now": "NOW", "item": "NEXT ITEM", "timer": "TIMERS", "ejg": "ENEMY JUNGLER", "fed": "FED",
            "focus": "YOUR FOCUS", "seen": "last", "unseen": "not seen yet", "target": "target",
            "missing": "missing", "buy": "affordable!", "ejg_s": "JUNGLER", "fed_s": "FED", "focus_s": "FOCUS", "left": "{} g left", "game": "GAME TIME", "move": "Move overlay – Ctrl+Shift+K to lock"}}


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
    return {
        "t": t, "time": h.get("time") or live.get("game_time"), "cs": h.get("cs", live.get("cs_per_min")),
        "cs_ok": h.get("cs_ok", True), "gold": h.get("gold"),
        "now": {"text": now["text"], "col": TONE.get(now.get("tone"), CYAN)} if now else None,
        "more": [m["text"] for m in (h.get("more") or [])][:1],
        "item": {"id": it.get("id"), "name": it["name"], "missing": it.get("missing"), "cost": it.get("cost")} if it else None,
        "timers": [(o["label"], o["in"], bool(o.get("soon"))) for o in (h.get("timers") or [])][:3],
        "ej": (f"{ej['champion']} · Lvl {ej.get('level')} · " +
               (f"{t['seen']} {ej['seen']}" if ej.get("seen") else t["unseen"])) if ej.get("champion") else None,
        "fed": h.get("fed") or [],
        "focus": f"{h['focus']['title']} · {t['target']} {h['focus']['target']}" if h.get("focus") else None,
        "mood": (now or {}).get("tone") or "calm",
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


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_uint), ("pt", ctypes.c_long * 2)]


# ---------------------------------------------------------------- pixel renderer (Pillow), HUD style
# Drawn into an RGBA image: the background is see-through while text stays crisp (Windows: per-pixel
# alpha via UpdateLayeredWindow). Style: chamfered panels, thin neon accent lines, clear hierarchy,
# tone icons in addition to colours (readable without relying on colour alone).
SS = 2  # supersampling


def _rgb(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def pil_fonts(scale=1.0):
    """Bahnschrift (Windows 10/11, DIN-like HUD font) – fallbacks: Roboto Condensed, Segoe UI, DejaVu."""
    from PIL import ImageFont
    cands = [("C:/Windows/Fonts/bahnschrift.ttf", True), ("/usr/share/fonts/truetype/google-fonts/ofl/roboto/Roboto[wdth,wght].ttf", True),
             ("C:/Windows/Fonts/segoeui.ttf", False), ("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf", False)]

    def f(px, weight, cond=False):
        for path, var in cands:
            try:
                ft = ImageFont.truetype(path, int(px * scale * SS))
            except OSError:
                continue
            if var:
                try:
                    axes = {a["name"].lower() if isinstance(a.get("name"), str) else a["name"].decode().lower(): a
                            for a in ft.get_variation_axes()}
                    vals = []
                    for a in ft.get_variation_axes():
                        n = a["name"] if isinstance(a["name"], str) else a["name"].decode()
                        n = n.lower()
                        if "weight" in n or n == "wght":
                            vals.append(max(a["minimum"], min(a["maximum"], weight)))
                        elif "width" in n or n == "wdth":
                            vals.append(max(a["minimum"], min(a["maximum"], 78 if cond else 100)))
                        else:
                            vals.append(a.get("default", a["minimum"]))
                    ft.set_variation_by_axes(vals)
                except Exception:  # noqa: BLE001 - static font
                    pass
            return ft
        return ImageFont.load_default(int(px * scale * SS))
    return {"big": f(21, 700), "now": f(15.5, 650), "row": f(12.5, 550), "more": f(11.5, 400),
            "label": f(9.5, 700, True), "stat": f(14, 700), "statl": f(8.5, 600, True), "tile": f(13, 700), "icon": f(12, 800)}


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


def _cham(x1, y1, x2, y2, c, corners="tl,br"):
    """Polygon of a rectangle with cut (chamfered) corners."""
    tl, tr, br, bl = ("tl" in corners) * c, ("tr" in corners) * c, ("br" in corners) * c, ("bl" in corners) * c
    return [(x1 + tl, y1), (x2 - tr, y1), (x2, y1 + tr), (x2, y2 - br), (x2 - br, y2), (x1 + bl, y2), (x1, y2 - bl), (x1, y1 + tl)]


def _monster_pil(d, x, y, s, mood):
    body = _rgb({"bad": RED, "warn": ORANGE, "good": GREEN}.get(mood, "#2dd4bf"))
    dark = _rgb(BG)
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
    """Overlay HUD as RGBA image. bg_opacity: 0 = invisible background, 1 = solid."""
    from PIL import Image, ImageDraw, ImageFilter
    F = fonts or pil_fonts(scale)
    k = SS * scale
    Wp = int(W * k)
    fg = Image.new("RGBA", (Wp, int(1000 * k)), (0, 0, 0, 0))   # text + icons
    under = Image.new("RGBA", fg.size, (0, 0, 0, 0))             # tiles / bars below the text
    glow = Image.new("RGBA", fg.size, (0, 0, 0, 0))              # neon lines, blurred later
    d, u, g = ImageDraw.Draw(fg), ImageDraw.Draw(under), ImageDraw.Draw(glow)
    pad = 14 * k
    acc = _rgb(CYAN)
    a_tile = int(255 * min(1.0, bg_opacity * 0.55 + 0.25))

    def lh(f):
        b = f.getbbox("ÄAg")
        return b[3] - b[1] + 2 * k

    def section(y, text, col):
        """HUD section label: small caps label + thin line to the right edge."""
        d.text((pad, y), text, font=F["label"], fill=_rgb(col))
        x = pad + d.textlength(text, font=F["label"]) + 8 * k
        ym = y + lh(F["label"]) / 2
        u.line([(x, ym), (Wp - pad, ym)], fill=_rgb(col, 70), width=max(1, int(k)))
        u.rectangle([Wp - pad - 3 * k, ym - 1.5 * k, Wp - pad, ym + 1.5 * k], fill=_rgb(col, 160))
        return y + lh(F["label"]) + 5 * k

    # ---- header: Moosi in a diamond, game time, CS/min and gold as stat blocks
    y = 12 * k
    cx, cy, r = pad + 17 * k, y + 17 * k, 17 * k
    u.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=_rgb(CHIP, a_tile))
    g.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], outline=acc, width=max(1, int(1.5 * k)))
    _monster_pil(d, cx - 11 * k, cy - 11 * k, 22 * k, m["mood"])
    d.text((pad + 44 * k, y + 1 * k), str(m["time"]), font=F["big"], fill=_rgb(TXT))
    d.text((pad + 45 * k, y + lh(F["big"]) + 1 * k), m["t"]["game"], font=F["statl"], fill=_rgb(MUTED))
    x = Wp - pad
    for val, lab, col in ((f"{m['gold']:,}".replace(",", ".") if m["gold"] is not None else "–", "GOLD", GOLD),
                          (str(m["cs"]), "CS/MIN", GREEN if m["cs_ok"] else ORANGE)):
        wv = max(d.textlength(val, font=F["stat"]), d.textlength(lab, font=F["statl"]))
        d.text((x, y + 3 * k), val, font=F["stat"], fill=_rgb(col), anchor="ra")
        d.text((x, y + 3 * k + lh(F["stat"])), lab, font=F["statl"], fill=_rgb(MUTED), anchor="ra")
        u.line([(x - wv - 9 * k, y + 4 * k), (x - wv - 9 * k, y + 30 * k)], fill=_rgb(TXT, 40), width=max(1, int(k)))
        x -= wv + 18 * k
    y += 44 * k

    # ---- NOW: the one thing that matters, with tone icon (colour + shape)
    if m["now"]:
        col = m["now"]["col"]
        y = section(y, m["t"]["now"], col)
        top = y
        ts = 22 * k
        u.polygon(_cham(pad, y, pad + ts, y + ts, 5 * k), fill=_rgb(col, 60))
        g.polygon(_cham(pad, y, pad + ts, y + ts, 5 * k), outline=_rgb(col), width=max(1, int(1.5 * k)))
        cx_, cy_, w_ = pad + ts / 2, y + ts / 2, max(2, int(2 * k))
        if col == GREEN:  # shapes instead of font glyphs: identical on every PC
            d.line([(cx_ - 5 * k, cy_), (cx_ - 1.5 * k, cy_ + 4 * k), (cx_ + 5.5 * k, cy_ - 4.5 * k)], fill=_rgb(col), width=w_)
        elif col == RED:
            d.line([(cx_ - 4.5 * k, cy_ - 4.5 * k), (cx_ + 4.5 * k, cy_ + 4.5 * k)], fill=_rgb(col), width=w_)
            d.line([(cx_ + 4.5 * k, cy_ - 4.5 * k), (cx_ - 4.5 * k, cy_ + 4.5 * k)], fill=_rgb(col), width=w_)
        else:
            d.text((cx_, cy_), "i" if col == CYAN else "!", font=F["icon"], fill=_rgb(col), anchor="mm")
        tx = pad + ts + 10 * k
        yy = y - 1 * k
        for ln in _wrap(d, m["now"]["text"], F["now"], Wp - pad - tx):
            d.text((tx, yy), ln, font=F["now"], fill=_rgb(TXT))
            yy += lh(F["now"])
        for more in m["more"][:1]:
            yy += 2 * k
            for ln in _wrap(d, "› " + more, F["more"], Wp - pad - tx):
                d.text((tx, yy), ln, font=F["more"], fill=_rgb(MUTED))
                yy += lh(F["more"])
        y = max(yy, top + ts) + 12 * k

    # ---- next item: icon in a chamfered frame + segmented gold progress
    if m["item"]:
        itm = m["item"]
        ready = itm.get("missing") == 0
        col = GREEN if ready else CYAN
        y = section(y, m["t"]["item"], col)
        isz = 40 * k
        frame = _cham(pad, y, pad + isz, y + isz, 7 * k)
        u.polygon(frame, fill=_rgb(CHIP, a_tile))
        if item_icon is not None:
            ic = item_icon.convert("RGBA").resize((int(isz - 6 * k), int(isz - 6 * k)))
            mask = Image.new("L", ic.size, 0)
            ImageDraw.Draw(mask).polygon(_cham(0, 0, ic.size[0] - 1, ic.size[1] - 1, 5 * k), fill=255)
            fg.paste(ic, (int(pad + 3 * k), int(y + 3 * k)), mask)
        g.polygon(frame, outline=_rgb(col), width=max(1, int(1.5 * k)))
        tx = pad + isz + 10 * k
        d.text((tx, y + 1 * k), itm["name"], font=F["row"], fill=_rgb(TXT))
        right = m["t"]["buy"] if ready else (m["t"]["left"].format(itm["missing"]) if itm.get("missing") is not None else "")
        d.text((Wp - pad, y + 2 * k), right.upper(), font=F["statl"], fill=_rgb(GREEN if ready else MUTED), anchor="ra")
        if itm.get("cost"):
            frac = max(0.0, min(1.0, 1 - (itm.get("missing") or 0) / itm["cost"]))
            n, gap = 12, 2 * k
            bx0, bx1, by = tx, Wp - pad, y + isz - 10 * k
            sw = (bx1 - bx0 - gap * (n - 1)) / n
            for i in range(n):
                sx = bx0 + i * (sw + gap)
                on = (i + 1) / n <= frac + 1e-6 or (i == 0 and frac > 0.02)
                poly = [(sx + 2 * k, by), (sx + sw, by), (sx + sw - 2 * k, by + 7 * k), (sx, by + 7 * k)]
                if on:
                    u.polygon(poly, fill=_rgb(col))
                    g.polygon(poly, fill=_rgb(col, 200))
                else:
                    u.polygon(poly, fill=_rgb(TXT, 28))
        y += isz + 12 * k

    # ---- objective timers as tiles
    if m["timers"]:
        y = section(y, m["t"]["timer"], VIOLET)
        n = len(m["timers"])
        gap = 6 * k
        tw = (Wp - 2 * pad - gap * (n - 1)) / n
        th = 34 * k
        for i, (label, when, soon) in enumerate(m["timers"]):
            x0 = pad + i * (tw + gap)
            poly = _cham(x0, y, x0 + tw, y + th, 6 * k)
            col = ORANGE if soon else TXT
            u.polygon(poly, fill=_rgb(ORANGE, 55) if soon else _rgb(CHIP, a_tile))
            (g if soon else u).polygon(poly, outline=_rgb(ORANGE) if soon else _rgb(TXT, 35), width=max(1, int(k)))
            d.text((x0 + 8 * k, y + 4 * k), label.upper(), font=F["statl"], fill=_rgb(ORANGE if soon else MUTED))
            d.text((x0 + 8 * k, y + 4 * k + lh(F["statl"])), str(when), font=F["tile"], fill=_rgb(col))
        y += th + 12 * k

    # ---- context lines (only when there is something to say)
    rows = [(m["t"]["ejg_s"], m["ej"], TXT, CYAN), (m["t"]["fed_s"], ", ".join(m["fed"]) if m["fed"] else None, RED, RED),
            (m["t"]["focus_s"], m["focus"], VIOLET, VIOLET)]
    for lab, text, col, mark in rows:
        if not text:
            continue
        u.rectangle([pad, y + 2 * k, pad + 2 * k, y + lh(F["row"]) - 1 * k], fill=_rgb(mark))
        d.text((pad + 8 * k, y + 3 * k), lab, font=F["label"], fill=_rgb(MUTED))
        lx = pad + 70 * k
        for ln in _wrap(d, text, F["row"], Wp - pad - lx):
            d.text((lx, y), ln, font=F["row"], fill=_rgb(col))
            y += lh(F["row"])
        y += 5 * k
    H = int(y + 8 * k)

    # ---- panel: chamfered, translucent, neon edge + corner brackets
    base = Image.new("RGBA", (Wp, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(base)
    c = 14 * k
    shape = _cham(0, 0, Wp - 1, H - 1, c)
    bd.polygon(shape, fill=_rgb(BG, int(255 * bg_opacity)))
    top = Image.new("RGBA", (Wp, H), (0, 0, 0, 0))  # subtle top light
    ImageDraw.Draw(top).polygon(_cham(0, 0, Wp - 1, int(H * .45), c, "tl"), fill=_rgb(CYAN, int(18 * bg_opacity + 6)))
    base = Image.alpha_composite(base, top.filter(ImageFilter.GaussianBlur(20 * k)))
    bd = ImageDraw.Draw(base)
    edge = _rgb(CYAN if move else "#3b4a6b", 255 if move else 150)
    bd.line(shape + [shape[0]], fill=edge, width=max(1, int(k)))
    gl = ImageDraw.Draw(glow)
    L_ = 26 * k
    gl.line([(0, c + L_), (0, c), (c, 0), (c + L_, 0)], fill=acc, width=int(2 * k))               # top-left bracket
    gl.line([(Wp - 1, H - 1 - c - L_), (Wp - 1, H - 1 - c), (Wp - 1 - c, H - 1), (Wp - 1 - c - L_, H - 1)], fill=acc, width=int(2 * k))
    glow_c = glow.crop((0, 0, Wp, H))
    halo = glow_c.filter(ImageFilter.GaussianBlur(4 * k))
    out = Image.alpha_composite(base, halo)
    out = Image.alpha_composite(out, under.crop((0, 0, Wp, H)))
    out = Image.alpha_composite(out, glow_c)
    out = Image.alpha_composite(out, fg.crop((0, 0, Wp, H)))
    return out.resize((int(W * scale), int(H / SS)), Image.LANCZOS)


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
    fonts_ = pil_fonts(scale)
    icons = {}

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    sw = root.winfo_screenwidth()
    pos = {"x": ocfg["x"] if ocfg["x"] >= 0 else sw + int(ocfg["x"] * scale), "y": int(ocfg["y"] * scale)}
    root.geometry(f"{int(W * scale)}x60+{pos['x']}+{pos['y']}")
    label = None if win else tk.Label(root, bd=0, bg=BG)
    if label:
        label.pack()
    st = {"visible": True, "move": False, "drag": (0, 0), "shown": False, "last": None, "img": None}

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
            _ulw(hwnd, im, pos["x"], pos["y"])
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
                    st["move"] = not st["move"]
                    _set_clickthrough(hwnd, not st["move"])
                    st["last"] = None
        root.after(50, poll_hotkeys)

    def refresh():
        try:
            with urllib.request.urlopen(url + "/api/overlay", timeout=2) as r:
                state = json.loads(r.read().decode("utf-8"))
            m = model(state)
            lang = "en" if state.get("lang") == "en" else "de"
        except Exception:  # noqa: BLE001
            m, lang = None, "de"
        if st["move"] and not m:
            m = {"t": L[lang], "time": L[lang]["move"], "cs": "–", "cs_ok": True, "gold": None, "now": None,
                 "more": [], "item": None, "timers": [], "ej": None, "fed": [], "focus": None, "mood": "info"}
        if m and st["visible"]:
            key = (json.dumps(m, sort_keys=True, default=str), st["move"])
            if key != st["last"]:
                show(render_image(m, W, fonts_, icon((m.get("item") or {}).get("id")), ocfg["bg_opacity"],
                                  st["move"], scale))
                st["last"] = key
            if not st["shown"]:
                root.deiconify()
                st["shown"] = True
                if win:
                    _set_clickthrough(hwnd, not st["move"])
                    if st["img"] is not None:
                        _ulw(hwnd, st["img"], pos["x"], pos["y"])
            root.attributes("-topmost", True)  # re-assert above the game
        elif st["shown"]:
            root.withdraw()
            st["shown"] = False
        root.after(1000, refresh)

    root.withdraw()
    poll_hotkeys()
    refresh()
    root.mainloop()
