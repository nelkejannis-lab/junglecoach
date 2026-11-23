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


def detect_mistakes(data, me_names, my_team, ej_names, name_team, name_pos):
    """Pure function: Live Client data -> list of mistake events (game seconds)."""
    out = []
    for ev in data.get("events", {}).get("Events", []):
        n, t = ev.get("EventName"), ev.get("EventTime", 0)
        if n == "ChampionKill":
            killer, victim = ev.get("KillerName"), ev.get("VictimName")
            assist = ev.get("Assisters", [])
            if victim in me_names:
                enemies = 1 + len(assist)
                jg = bool(ej_names & ({killer} | set(assist)))
                why = f"Tod gegen {enemies} Gegner" + (" inkl. Gegner-Jungler" if jg else "")
                out.append({"t": t, "type": "death", "title": "Eigener Tod", "why": why, "pre": 20, "post": 6})
            elif name_team.get(victim) == my_team and ej_names & ({killer} | set(assist)) and t < 15 * 60:
                out.append({"t": t, "type": "enemy_gank", "pre": 25, "post": 4,
                            "title": f"Gegner-Jungler-Gank {name_pos.get(victim) or ''}".strip(),
                            "why": "Hättest du den Gank vorhersehen / counter-ganken können?"})
        elif n in OBJECTIVES:
            killer = ev.get("KillerName")
            if killer and name_team.get(killer) and name_team.get(killer) != my_team:
                label = OBJECTIVES[n] + (f" ({ev['DragonType']})" if ev.get("DragonType") else "")
                out.append({"t": t, "type": "objective_lost", "title": f"{label} verloren",
                            "why": "Setup/Prio vor dem Objective prüfen", "pre": 35, "post": 5})
    return out


_PIPE = {}


def _probe(exe, args):
    flags = 0x08000000 if sys.platform == "win32" else 0
    try:
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error"] + args + ["-t", "0.6", "-f", "null", "-"],
                           capture_output=True, timeout=20, creationflags=flags)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def pick_pipeline(exe, fps, width):
    """Like Mobalytics/Overwolf: capture the screen on the GPU (Desktop Duplication, 'ddagrab') and
    encode on the GPU (NVENC / AMF / QuickSync) – the frames never touch the CPU, so the game does not lag.
    Falls back step by step to classic capture + x264 if the graphics card / driver can't do it."""
    key = (fps, width)
    if key in _PIPE:
        return _PIPE[key]
    if sys.platform != "win32":
        _PIPE[key] = (["-f", "x11grab", "-framerate", str(fps), "-i", os.environ.get("DISPLAY", ":0")],
                      ["-vf", f"scale={width}:-2"], "libx264", "yuv420p")
        return _PIPE[key]
    dda = f"ddagrab=output_idx=0:framerate={fps}"
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
    _PIPE[key] = (["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1", "-i", "desktop"],
                  ["-vf", f"scale={width}:-2"], enc, "nv12" if enc == "h264_qsv" else "yuv420p")
    return _PIPE[key]


class Recorder:
    def __init__(self, cfg):
        rc = dict(cfg.get("recording", {}))
        if rc.get("fps") == 30 and rc.get("encoder") == "libx264":  # old defaults -> new light defaults
            rc.update(fps=15, encoder="auto")
        self.cfg = {"enabled": True, "fps": 15, "crf": 30, "encoder": "auto", "width": 1280, "keep_full": True,
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
        inp, vf, enc, pix = pick_pipeline(exe, int(self.cfg["fps"]), int(self.cfg.get("width") or 1280))
        if self.cfg["encoder"] not in ("auto", enc) and not inp[0] == "-filter_complex":
            enc = self.cfg["encoder"]
        q = str(self.cfg["crf"])
        venc = {"libx264": ["-preset", "ultrafast", "-tune", "zerolatency", "-crf", q, "-threads", "2"],
                "h264_nvenc": ["-preset", "p1", "-tune", "ll", "-rc", "vbr", "-cq", q],
                "h264_amf": ["-usage", "lowlatency", "-quality", "speed", "-rc", "cqp", "-qp_i", q, "-qp_p", q],
                "h264_qsv": ["-preset", "veryfast", "-global_quality", q]}.get(enc, [])
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y"] + inp + vf + ["-c:v", enc] + venc + \
              ["-g", str(int(self.cfg["fps"]) * 2)] + (["-pix_fmt", pix] if pix else []) + [full]
        # no console window + below-normal priority: the game always wins the CPU
        flags = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=flags)
        self.session = {"id": sid, "dir": d, "full": full, "start_wall": time.time(),
                        "game_time_at_start": game_time, "mistakes": [], "clips": []}
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
        s["clips"] = cut_clips(s["full"], s["mistakes"], s["game_time_at_start"], s["dir"])
        if not self.cfg["keep_full"] and s["clips"]:
            os.remove(s["full"]); s["full"] = None
        with open(os.path.join(s["dir"], "session.json"), "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        prune(self.cfg["keep_games"])
        return s


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
                            "-t", str(dur), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an", path],
                           capture_output=True, creationflags=0x08000000 if sys.platform == "win32" else 0)
        if r.returncode == 0 and os.path.exists(path):
            clips.append({**m, "time": _fmt(m["t"]), "file": name})
    return clips


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
