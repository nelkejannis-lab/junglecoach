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


class Recorder:
    def __init__(self, cfg):
        self.cfg = {"enabled": True, "fps": 30, "crf": 28, "encoder": "libx264", "keep_full": True, "keep_games": 3,
                    **cfg.get("recording", {})}
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
        if sys.platform == "win32":
            src = ["-f", "gdigrab", "-framerate", str(self.cfg["fps"]), "-draw_mouse", "1", "-i", "desktop"]
        else:  # dev/testing on Linux
            src = ["-f", "x11grab", "-framerate", str(self.cfg["fps"]), "-i", os.environ.get("DISPLAY", ":0")]
        enc = self.cfg["encoder"]
        venc = ["-c:v", enc] + (["-preset", "veryfast", "-crf", str(self.cfg["crf"])] if enc == "libx264"
                                else ["-preset", "p4", "-cq", str(self.cfg["crf"])])
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y"] + src + venc + \
              ["-g", str(self.cfg["fps"] * 2), "-pix_fmt", "yuv420p", full]
        flags = 0x08000000 if sys.platform == "win32" else 0
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
