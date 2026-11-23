"""In-game overlay: a separate, always-on-top, click-through window (tkinter + Win32).

Nothing is injected into League – it is just a normal window drawn above the game,
like the Discord overlay. Requires League in "Borderless" or "Windowed" display mode.
Hotkey Ctrl+Shift+J: show/hide.  Ctrl+Shift+K: move mode (drag with mouse, press again to lock).
"""
import ctypes
import json
import sys
import urllib.request

BG, CARD, TXT, MUTED = "#0b0f1a", "#121829", "#eef2ff", "#8b93a7"
CYAN, GREEN, ORANGE, RED, GOLD, VIOLET = "#22d3ee", "#34d399", "#fbbf24", "#fb7185", "#f5c451", "#a78bfa"
TONE = {"warn": ORANGE, "bad": RED, "good": GREEN, "info": CYAN, "calm": VIOLET}
KEY = "#010203"  # transparent key colour for the rounded corners

L = {"de": {"now": "JETZT", "item": "NÄCHSTES ITEM", "timer": "TIMER", "ejg": "GEGNER-JUNGLER", "fed": "GEFÜTTERT",
            "focus": "DEIN FOKUS", "seen": "zuletzt", "unseen": "noch nicht gesehen", "target": "Ziel",
            "missing": "fehlen", "buy": "kaufbar!", "move": "Overlay verschieben – Ctrl+Shift+K zum Fixieren"},
     "en": {"now": "NOW", "item": "NEXT ITEM", "timer": "TIMERS", "ejg": "ENEMY JUNGLER", "fed": "FED",
            "focus": "YOUR FOCUS", "seen": "last", "unseen": "not seen yet", "target": "target",
            "missing": "missing", "buy": "affordable!", "move": "Move overlay – Ctrl+Shift+K to lock"}}


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


def run(url, cfg):
    import tkinter as tk
    import tkinter.font as tkfont

    ocfg = {"x": -420, "y": 70, "alpha": 0.93, "width": 390, "font_size": 10, **cfg.get("overlay", {})}
    W = ocfg["width"]
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", ocfg["alpha"])
    root.configure(bg=KEY)
    try:
        root.attributes("-transparentcolor", KEY)  # rounded corners (Windows)
    except tk.TclError:
        pass
    sw = root.winfo_screenwidth()
    x = ocfg["x"] if ocfg["x"] >= 0 else sw + ocfg["x"]
    root.geometry(f"{W}x60+{x}+{ocfg['y']}")
    cv = tk.Canvas(root, width=W, height=60, bg=KEY, highlightthickness=0, bd=0)
    cv.pack(fill="both", expand=True)
    fs = ocfg["font_size"]
    F = {"head": tkfont.Font(family="Segoe UI Semibold", size=fs + 1),
         "label": tkfont.Font(family="Segoe UI", size=fs - 2, weight="bold"),
         "now": tkfont.Font(family="Segoe UI Semibold", size=fs + 3),
         "more": tkfont.Font(family="Segoe UI", size=fs - 1),
         "rowk": tkfont.Font(family="Segoe UI", size=fs - 2, weight="bold"),
         "row": tkfont.Font(family="Segoe UI", size=fs)}
    st = {"visible": True, "move": False, "drag": (0, 0), "shown": False, "last": None}

    win = sys.platform == "win32"
    hwnd = None
    if win:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        _set_clickthrough(hwnd, True)
        ctypes.windll.user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_SHIFT, 0x4A)  # J
        ctypes.windll.user32.RegisterHotKey(None, 2, MOD_CONTROL | MOD_SHIFT, 0x4B)  # K

    def start_drag(e):
        st["drag"] = (e.x_root - root.winfo_x(), e.y_root - root.winfo_y())

    def do_drag(e):
        if st["move"]:
            dx, dy = st["drag"]
            root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")
    root.bind("<Button-1>", start_drag)
    root.bind("<B1-Motion>", do_drag)

    def rrect(x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
               x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    def draw(blocks):
        cv.delete("all")
        pad, y = 16, 14
        items = []
        for kind, text, col in blocks:
            if kind == "sep":
                items.append(("sep", y))
                y += 12
                continue
            if kind == "row":
                k, v = text.split("|", 1)
                items.append(("rowk", pad, y, k, MUTED))
                y += F["rowk"].metrics("linespace")
                tid = cv.create_text(pad, y, text=v, anchor="nw", fill=col, font=F["row"], width=W - 2 * pad)
                y = cv.bbox(tid)[3] + 7
                continue
            font = F[kind]
            x0 = pad + (10 if kind in ("now", "more") else 0)
            tid = cv.create_text(x0, y, text=text, anchor="nw", fill=col, font=font, width=W - x0 - pad)
            bb = cv.bbox(tid)
            if kind == "now":
                items.append(("bar", pad, y + 2, bb[3] - 2, col))
            y = bb[3] + (8 if kind in ("head", "now") else 3)
        h = y + 8
        bg = rrect(1, 1, W - 1, h - 1, 16, fill=BG, outline="#22d3ee" if st["move"] else "#263049", width=1)
        cv.tag_lower(bg)
        for it in items:
            if it[0] == "sep":
                cv.create_line(16, it[1] + 5, W - 16, it[1] + 5, fill="#1e2638")
            elif it[0] == "rowk":
                cv.create_text(it[1], it[2], text=it[3], anchor="nw", fill=it[4], font=F["rowk"])
            elif it[0] == "bar":
                cv.create_rectangle(it[1], it[2], it[1] + 3, it[3], fill=it[4], outline="")
        cv.configure(height=h)
        root.geometry(f"{W}x{h}")

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
            blocks = layout(state)
            lang = "en" if state.get("lang") == "en" else "de"
        except Exception:
            blocks, lang = [], "de"
        if st["move"] and not blocks:
            blocks = [("head", L[lang]["move"], CYAN)]
        show = bool(blocks) and st["visible"]
        if show:
            if blocks != st["last"]:
                draw(blocks)
                st["last"] = blocks
            if not st["shown"]:
                root.deiconify()
                st["shown"] = True
                if win:
                    _set_clickthrough(hwnd, not st["move"])
            root.attributes("-topmost", True)  # re-assert above the game
        elif st["shown"]:
            root.withdraw()
            st["shown"] = False
        root.after(1000, refresh)

    root.withdraw()
    poll_hotkeys()
    refresh()
    root.mainloop()
