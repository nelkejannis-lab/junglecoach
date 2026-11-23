"""Records your own game (screen capture via ffmpeg) and cuts mistake clips afterwards.

Same principle as Outplayed / Insights Capture: plain screen recording, nothing touches the game.
Mistake events come from the official Live Client API (your deaths, lost objectives, ganks).
"""
import json
import os
import shutil
import subprocess
import sys
import time

from .paths import DATA_DIR

REC_DIR = os.path.join(DATA_DIR, "recordings")
OBJECTIVES = {"DragonKill": "Drache", "BaronKill": "Baron", "HeraldKill": "Herald", "HordeKill": "Voidgrubs"}


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def _fmt(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


TIPS = {
    "death": "Vor dem Fight zählen: Wie viele Gegner sind sichtbar, wo ist der Gegner-Jungler? Ohne Info nicht nachrücken.",
    "outnumbered": "Mehrere Gegner beteiligt: Vor dem Nachrücken zählen, wie viele sichtbar sind – ohne Info zurückziehen.",
    "enemy_gank": "Gegner-Jungler-Position tracken (letzter Sichtpunkt + seine Camps), Lane rechtzeitig pingen oder Counter-Gank laufen.",
    "objective_lost": "60–90 s vorher: Lanes mit Prio pushen, Wards in den Fluss, mit vollem Leben und Items ankommen.",
    "trade": "Möglicher Tausch: Prüfe, ob das Geholte mehr wert war als das Verlorene (Drache-Seele? Baron? Türme?).",
    "kill": "Stark! Was hat den Kill möglich gemacht (Timing, CC, Überzahl)? Genau das wiederholen.",
    "multikill": "Highlight! Achte darauf, wie der Fight aufgebaut war – das ist dein Gewinnmuster.",
    "objective_taken": "Gutes Objective – Setup merken: Wer hatte Prio, wo standen die Wards?",
}


def detect_mistakes(data, me_names, my_team, ej_names, name_team, name_pos, name_champ=None):
    """Pure function: Live Client data -> list of clip events (game seconds): mistakes and highlights,
    with the champions involved and a coaching tip (like Mobalytics / Outplayed clip cards)."""
    champ = (lambda n: (name_champ or {}).get(n, n))
    out = []
    for ev in data.get("events", {}).get("Events", []):
        n, t = ev.get("EventName"), ev.get("EventTime", 0)
        if n == "ChampionKill":
            killer, victim = ev.get("KillerName"), ev.get("VictimName")
            assist = ev.get("Assisters", [])
            if victim in me_names:
                enemies = 1 + len(assist)
                jg = bool(ej_names & ({killer} | set(assist)))
                why = (f"{enemies} Gegner beteiligt" if enemies >= 2 else "1 Gegner beteiligt") + \
                    (" inkl. Gegner-Jungler" if jg else "")
                out.append({"t": t, "type": "death", "kind": "mistake", "title": "Eigener Tod", "why": why,
                            "pre": 20, "post": 6, "killer": champ(killer), "assisters": [champ(a) for a in assist],
                            "tip": TIPS["outnumbered" if enemies >= 2 else "death"]})
            elif killer in me_names or (me_names & set(assist)):
                out.append({"t": t, "type": "kill", "kind": "highlight",
                            "title": "Kill" if killer in me_names else "Assist",
                            "why": f"gegen {champ(victim)}" + (f" mit {', '.join(champ(a) for a in assist if a not in me_names)}"
                                                               if [a for a in assist if a not in me_names] else ""),
                            "pre": 12, "post": 4, "victim": champ(victim), "assisters": [champ(a) for a in assist],
                            "tip": TIPS["kill"]})
            elif name_team.get(victim) == my_team and ej_names & ({killer} | set(assist)) and t < 15 * 60:
                out.append({"t": t, "type": "enemy_gank", "kind": "mistake", "pre": 25, "post": 4,
                            "title": f"Gegner-Jungler-Gank {name_pos.get(victim) or ''}".strip(),
                            "why": "Hättest du den Gank vorhersehen / counter-ganken können?",
                            "victim": champ(victim), "killer": champ(killer), "tip": TIPS["enemy_gank"]})
        elif n == "Multikill" and ev.get("KillerName") in me_names:
            k = ev.get("KillStreak", 2)
            label = {2: "Double Kill", 3: "Triple Kill", 4: "Quadra Kill", 5: "Penta Kill"}.get(k, f"{k}er Kill")
            out = [o for o in out if not (o["type"] == "kill" and t - 12 <= o["t"] <= t)]  # merge into the multikill
            out.append({"t": t, "type": "multikill", "kind": "highlight", "title": label, "why": "Mehrfach-Kill",
                        "pre": 18, "post": 5, "tip": TIPS["multikill"]})
        elif n in OBJECTIVES:
            killer = ev.get("KillerName")
            label = OBJECTIVES[n] + (f" ({ev['DragonType']})" if ev.get("DragonType") else "")
            if killer and name_team.get(killer) and name_team.get(killer) != my_team:
                trade = _our_take_near(data, t, my_team, name_team)
                if trade:  # a lost objective is not automatically a mistake – maybe a trade
                    out.append({"t": t, "type": "objective_trade", "kind": "review", "title": f"{label} abgegeben",
                                "why": f"Gleichzeitig geholt: {trade} – war der Tausch es wert?", "pre": 35, "post": 5,
                                "tip": TIPS["trade"]})
                else:
                    out.append({"t": t, "type": "objective_lost", "kind": "mistake", "title": f"{label} verloren",
                                "why": "Kein Gegenwert in ±60 s – Setup/Prio vor dem Objective prüfen", "pre": 35,
                                "post": 5, "tip": TIPS["objective_lost"]})
            elif killer in me_names or (me_names & set(ev.get("Assisters", []))):
                out.append({"t": t, "type": "objective_taken", "kind": "highlight", "title": f"{label} geholt",
                            "why": "Objective für dein Team", "pre": 20, "post": 4, "tip": TIPS["objective_taken"]})
    return out


def _our_take_near(data, t, my_team, name_team, window=60):
    """What did our team take within ±window s (turrets, inhibitors, other epic monsters)?"""
    got = []
    for ev in data.get("events", {}).get("Events", []):
        et, n = ev.get("EventTime", 0), ev.get("EventName")
        if abs(et - t) > window:
            continue
        if n in ("TurretKilled", "InhibKilled"):
            tag = ev.get("TurretKilled") or ev.get("InhibKilled") or ""
            # Turret_T1_* / Barracks_T1_* belong to ORDER (blue): destroyed by the other team
            owner = "ORDER" if "_T1_" in tag else "CHAOS" if "_T2_" in tag else None
            if owner and owner != my_team:
                got.append("Turm" if n == "TurretKilled" else "Inhibitor")
        elif n in OBJECTIVES and name_team.get(ev.get("KillerName")) == my_team:
            got.append(OBJECTIVES[n])
    return ", ".join(sorted(set(got)))


_PIPE = {}
GAME_TITLE = "League of Legends (TM) Client"


def game_region():
    """Monitor index and rectangle of the League game window (Windows), so only the game is recorded –
    not every screen. Returns dict(idx, x, y, w, h, full) relative to that monitor, or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        try:
            u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # physical pixels
        except Exception:  # noqa: BLE001
            pass
        hwnd = u.FindWindowW(None, GAME_TITLE) or u.FindWindowW("RiotWindowClass", None)
        if not hwnd or u.IsIconic(hwnd):
            return None
        rc = wintypes.RECT()
        u.GetClientRect(hwnd, ctypes.byref(rc))
        pt = wintypes.POINT(0, 0)
        u.ClientToScreen(hwnd, ctypes.byref(pt))
        win = (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom)
        mons = []
        MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.POINTER(wintypes.RECT), ctypes.c_double)

        def cb(hmon, hdc, lprc, data):
            r = lprc.contents
            mons.append((hmon, (r.left, r.top, r.right, r.bottom)))
            return 1
        u.EnumDisplayMonitors(None, None, MonitorEnumProc(cb), 0)
        target = u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        idx = next((i for i, (h, _) in enumerate(mons) if h == target), 0)
        m = mons[idx][1]
        x, y = max(0, win[0] - m[0]), max(0, win[1] - m[1])
        w = min(win[2], m[2]) - max(win[0], m[0])
        h = min(win[3], m[3]) - max(win[1], m[1])
        w, h = w - w % 2, h - h % 2
        full = w >= (m[2] - m[0]) - 2 and h >= (m[3] - m[1]) - 2
        return {"idx": idx, "x": x, "y": y, "w": w, "h": h, "full": full} if w > 200 and h > 200 else None
    except Exception:  # noqa: BLE001
        return None


def _probe(exe, args):
    flags = 0x08000000 if sys.platform == "win32" else 0
    try:
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error"] + args + ["-t", "0.6", "-f", "null", "-"],
                           capture_output=True, timeout=20, creationflags=flags)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def pick_pipeline(exe, fps, width, region=None):
    """Like Mobalytics/Overwolf: capture the screen on the GPU (Desktop Duplication, 'ddagrab') and
    encode on the GPU (NVENC / AMF / QuickSync) – the frames never touch the CPU, so the game does not lag.
    Falls back step by step to classic capture + x264 if the graphics card / driver can't do it."""
    key = (fps, width, tuple(sorted((region or {}).items())))
    if key in _PIPE:
        return _PIPE[key]
    if sys.platform != "win32":
        _PIPE[key] = (["-f", "x11grab", "-framerate", str(fps), "-i", os.environ.get("DISPLAY", ":0")],
                      ["-vf", f"scale={width}:-2"], "libx264", "yuv420p")
        return _PIPE[key]
    r = region or {}
    dda = f"ddagrab=output_idx={r.get('idx', 0)}:framerate={fps}"
    if r and not r.get("full"):  # windowed mode: only the game window
        dda += f":offset_x={r['x']}:offset_y={r['y']}:video_size={r['w']}x{r['h']}"
    candidates = [
        # zero-copy: GPU capture -> GPU encoder
        (["-filter_complex", dda], [], "h264_nvenc", None),
        (["-filter_complex", dda], [], "h264_amf", None),
        (["-filter_complex", dda + ",hwmap=derive_device=qsv,format=qsv"], [], "h264_qsv", None),
        # GPU capture, frames copied once, GPU or CPU encoder
        (["-filter_complex", dda + f",hwdownload,format=bgra,scale={width}:-2"], [], "h264_nvenc", "yuv420p"),
        (["-filter_complex", dda + f",hwdownload,format=bgra,scale={width}:-2"], [], "h264_amf", "yuv420p"),
        (["-filter_complex", dda + f",hwdownload,format=bgra,scale={width}:-2"], [], "libx264", "yuv420p"),
    ]
    for inp, vf, enc, pix in candidates:
        if _probe(exe, inp + ["-c:v", enc] + (["-pix_fmt", pix] if pix else [])):
            _PIPE[key] = (inp, vf, enc, pix)
            return _PIPE[key]
    # classic GDI capture (slowest, works everywhere)
    enc = "libx264"
    for e in ("h264_nvenc", "h264_amf", "h264_qsv"):
        if _probe(exe, ["-f", "lavfi", "-i", "color=black:s=320x240"] + ["-c:v", e]):
            enc = e
            break
    target = f"title={GAME_TITLE}" if r else "desktop"  # only the game window, never all screens
    _PIPE[key] = (["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1", "-i", target],
                  ["-vf", f"scale={width}:-2"], enc, "nv12" if enc == "h264_qsv" else "yuv420p")
    return _PIPE[key]


class Recorder:
    def __init__(self, cfg):
        rc = dict(cfg.get("recording", {}))
        if rc.get("fps") == 30 and rc.get("encoder") == "libx264":  # old defaults -> new light defaults
            rc.update(fps=15, encoder="auto")
        if rc.get("fps") == 15 and rc.get("crf") == 30:  # 1.2.x light defaults -> automatic quality
            rc.update(fps="auto", crf="auto")
        self.cfg = {"enabled": True, "fps": "auto", "crf": "auto", "encoder": "auto", "width": 1280, "keep_full": True,
                    "keep_games": 3, **rc}
        self.proc = None
        self.session = None

    @property
    def active(self):
        return self.proc is not None

    def start(self, game_time):
        exe = ffmpeg_exe()
        if not exe or not self.cfg["enabled"]:
            return False
        sid = time.strftime("%Y-%m-%d_%H-%M-%S")
        d = os.path.join(REC_DIR, sid)
        os.makedirs(d, exist_ok=True)
        full = os.path.join(d, "full.mp4")
        region = game_region()
        exe_ok = exe
        fps = 30 if self.cfg["fps"] == "auto" else int(self.cfg["fps"])
        inp, vf, enc, pix = pick_pipeline(exe_ok, fps, int(self.cfg.get("width") or 1280), region)
        gpu = enc != "libx264"
        if self.cfg["fps"] == "auto" and not gpu:  # CPU encoding: keep the game smooth
            fps = 15
            inp, vf, enc, pix = pick_pipeline(exe_ok, fps, int(self.cfg.get("width") or 1280), region)
        self.cfg["_fps"] = fps
        if self.cfg["encoder"] not in ("auto", enc) and not inp[0] == "-filter_complex":
            enc = self.cfg["encoder"]
        q = str(self.cfg["crf"] if self.cfg["crf"] != "auto" else (23 if gpu else 28))
        venc = {"libx264": ["-preset", "ultrafast", "-tune", "zerolatency", "-crf", q, "-threads", "2"],
                "h264_nvenc": ["-preset", "p1", "-tune", "ll", "-rc", "vbr", "-cq", q],
                "h264_amf": ["-usage", "lowlatency", "-quality", "speed", "-rc", "cqp", "-qp_i", q, "-qp_p", q],
                "h264_qsv": ["-preset", "veryfast", "-global_quality", q]}.get(enc, [])
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y"] + inp + vf + ["-c:v", enc] + venc + \
              ["-g", str(fps)] + (["-pix_fmt", pix] if pix else []) + [full]
        # no console window + below-normal priority: the game always wins the CPU
        flags = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=flags)
        self.audio = None
        if self.cfg.get("audio", True):  # game sound (what you hear), muxed in after the game
            from .audio import LoopbackAudio
            self.audio = LoopbackAudio(os.path.join(d, "audio.wav"))
            if not self.audio.start():
                self.audio = None
        self.session = {"id": sid, "dir": d, "full": full, "start_wall": time.time(),
                        "game_time_at_start": game_time, "mistakes": [], "clips": [], "region": region}
        return True

    def note(self, mistakes, meta=None):
        if self.session:
            self.session["mistakes"] = mistakes
            if meta:
                self.session.update(meta)

    def stop(self):
        """Stops recording, cuts clips, writes session.json. Returns the session dict."""
        if not self.proc:
            return None
        try:
            self.proc.stdin.write(b"q"); self.proc.stdin.flush()
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
        self.proc = None
        s, self.session = self.session, None
        if getattr(self, "audio", None):
            self.audio.stop()
            mux_audio(s["full"], self.audio.path)
            self.audio = None
        s["clips"] = cut_clips(s["full"], s["mistakes"], s["game_time_at_start"], s["dir"])
        if not self.cfg["keep_full"] and s["clips"]:
            os.remove(s["full"]); s["full"] = None
        with open(os.path.join(s["dir"], "session.json"), "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        prune(self.cfg["keep_games"])
        return s


def mux_audio(video, wav, exe=None):
    """Puts the recorded sound into the video (video stream is copied, not re-encoded)."""
    exe = exe or ffmpeg_exe()
    if not exe or not os.path.exists(video) or not os.path.exists(wav) or os.path.getsize(wav) < 1000:
        return False
    out = video[:-4] + "_av.mp4"
    flags = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0
    r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-i", wav, "-map", "0:v",
                        "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", out],
                       capture_output=True, creationflags=flags)
    if r.returncode == 0 and os.path.exists(out):
        os.replace(out, video)
        os.remove(wav)
        return True
    return False


def prune(keep):
    """Keep only the newest `keep` game folders (clips + full recording)."""
    if not os.path.isdir(REC_DIR):
        return
    sessions = sorted(d for d in os.listdir(REC_DIR) if os.path.isdir(os.path.join(REC_DIR, d)))
    for old in sessions[:-keep] if keep > 0 else []:
        shutil.rmtree(os.path.join(REC_DIR, old), ignore_errors=True)


def cut_clips(full, mistakes, g0, out_dir, exe=None):
    exe = exe or ffmpeg_exe()
    clips = []
    if not exe or not os.path.exists(full):
        return clips
    for i, m in enumerate(sorted(mistakes, key=lambda x: x["t"]), 1):
        start = max(0.0, m["t"] - g0 - m.get("pre", 20))
        dur = m.get("pre", 20) + m.get("post", 6)
        name = f"{i:02d}_{_fmt(m['t']).replace(':', '-')}_{m['type']}.mp4"
        path = os.path.join(out_dir, name)
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-i", full,
                            "-t", str(dur), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "15",
                            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", path],
                           capture_output=True, creationflags=(0x08000000 | 0x00004000) if sys.platform == "win32" else 0)
        if r.returncode == 0 and os.path.exists(path):
            thumb = name[:-4] + ".jpg"
            subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(max(0, dur - m.get("post", 6) - 1)),
                            "-i", path, "-frames:v", "1", "-vf", "scale=480:-2", os.path.join(out_dir, thumb)],
                           capture_output=True, creationflags=(0x08000000 | 0x00004000) if sys.platform == "win32" else 0)
            clips.append({**m, "time": _fmt(m["t"]), "file": name,
                          "thumb": thumb if os.path.exists(os.path.join(out_dir, thumb)) else None})
    return clips


def set_feedback(sid, file, verdict):
    """'Bewertung stimmt nicht' – stores the player's verdict on a clip (correct / wrong / unsure),
    so misclassifications can be counted and the rules checked against them."""
    if not sid or "/" in sid or "\\" in sid or verdict not in ("ok", "wrong", "unsure", None):
        return False
    p = os.path.join(REC_DIR, sid, "session.json")
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8") as f:
        s = json.load(f)
    hit = False
    for c in s.get("clips", []):
        if c.get("file") == file:
            c["feedback"] = verdict; hit = True
    if hit:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
    return hit


def feedback_stats():
    """Per clip type: how often the player said the verdict was right / wrong."""
    out = {}
    for se in list_sessions(limit=200):
        for c in se["clips"]:
            if c.get("feedback"):
                d = out.setdefault(c.get("type", "?"), {"ok": 0, "wrong": 0, "unsure": 0})
                d[c["feedback"]] += 1
    return out


def list_sessions(limit=20):
    if not os.path.isdir(REC_DIR):
        return []
    out = []
    for sid in sorted(os.listdir(REC_DIR), reverse=True)[:limit]:
        p = os.path.join(REC_DIR, sid, "session.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
            out.append({"id": s["id"], "start_wall": s["start_wall"], "champion": s.get("champion"),
                        "clips": s["clips"]})
    return out
