"""In-game overlay: a separate, always-on-top, click-through window (tkinter + Win32).

Nothing is injected into League – it is just a normal window drawn above the game,
like the Discord overlay. Requires League in "Borderless" or "Windowed" display mode.
Hotkey Ctrl+Shift+J: show/hide.  Ctrl+Shift+K: move mode (drag with mouse, press again to lock).
"""
import ctypes
import json
import sys
import urllib.request

ORANGE, GREEN, RED, TXT, MUTED, BG = "#ffb547", "#3ddc97", "#ff5d5d", "#e6e9ef", "#8b93a1", "#0e1116"


def format_lines(state):
    """Pure function: dashboard state -> list of (text, color). Empty list = hide overlay."""
    l = (state or {}).get("live")
    if not l:
        return []
    out = [(f"⏱ {l['game_time']}   CS/min {l['cs_per_min']}", GREEN if l["cs_per_min"] >= 6 else ORANGE)]
    ej = l.get("enemy_jungler") or {}
    if ej.get("champion"):
        seen = ej.get("last_seen") or []
        where = f" · zuletzt {seen[0]['where']} {seen[0]['time']}" if seen else " · noch nicht gesehen"
        out.append((f"Gegner-JG {ej['champion']} Lvl {ej['level']}{where}", TXT))
        for w in (ej.get("warnings") or [])[:2]:
            out.append(("⚠ " + w, ORANGE))
    b = l.get("build") or {}
    nxt = (b.get("situational") or []) + (b.get("core") or [])
    if nxt:
        out.append((f"Nächstes Item: {nxt[0]['name']} – {nxt[0]['why']}", TXT))
    if b.get("boots"):
        out.append((f"Schuhe: {b['boots']['name']}", MUTED))
    for f in (l.get("enemy", {}).get("fed") or [])[:2]:
        out.append((f"Fed: {f['name']} {f['kda']}", RED))
    objs = l.get("objectives") or []
    if objs:
        out.append(("  ·  ".join(f"{o['name']} {o['respawn_in']}" for o in objs), MUTED))
    return out


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

    ocfg = {"x": -470, "y": 60, "alpha": 0.85, "width": 450, "font_size": 11, **cfg.get("overlay", {})}
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", ocfg["alpha"])
    root.configure(bg=BG)
    sw = root.winfo_screenwidth()
    x = ocfg["x"] if ocfg["x"] >= 0 else sw + ocfg["x"]
    root.geometry(f"{ocfg['width']}x40+{x}+{ocfg['y']}")
    frame = tk.Frame(root, bg=BG, padx=10, pady=6)
    frame.pack(fill="both", expand=True)
    labels = []
    st = {"visible": True, "move": False, "drag": (0, 0), "shown": False}

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

    def poll_hotkeys():
        if win:
            msg = MSG()
            while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
                if msg.wParam == 1:
                    st["visible"] = not st["visible"]
                elif msg.wParam == 2:
                    st["move"] = not st["move"]
                    _set_clickthrough(hwnd, not st["move"])
                    frame.configure(highlightthickness=2 if st["move"] else 0, highlightbackground=GREEN)
        root.after(50, poll_hotkeys)

    def refresh():
        try:
            with urllib.request.urlopen(url + "/api/state", timeout=2) as r:
                lines = format_lines(json.loads(r.read().decode("utf-8")))
        except Exception:
            lines = []
        if st["move"] and not lines:
            lines = [("Overlay verschieben – Ctrl+Shift+K zum Fixieren", GREEN)]
        show = bool(lines) and st["visible"]
        if show:
            while len(labels) < len(lines):
                lb = tk.Label(frame, bg=BG, anchor="w", justify="left",
                              font=("Segoe UI", ocfg["font_size"]), wraplength=ocfg["width"] - 20)
                lb.pack(fill="x")
                labels.append(lb)
            for lb, (text, color) in zip(labels, lines):
                lb.configure(text=text, fg=color)
            for lb in labels[len(lines):]:
                lb.pack_forget()
            del labels[len(lines):]
            root.update_idletasks()
            root.geometry(f"{ocfg['width']}x{frame.winfo_reqheight()}")
            if not st["shown"]:
                root.deiconify(); st["shown"] = True
                if win:
                    _set_clickthrough(hwnd, not st["move"])
            root.attributes("-topmost", True)  # re-assert above the game
        elif st["shown"]:
            root.withdraw(); st["shown"] = False
        root.after(1000, refresh)

    root.withdraw()
    poll_hotkeys()
    refresh()
    root.mainloop()
