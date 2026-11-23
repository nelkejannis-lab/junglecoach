"""Desktop app entry point: backend + app window + in-game overlay.

  JungleCoach.exe            -> app window (dashboard) + overlay process
  JungleCoach.exe --overlay  -> overlay only (started automatically)
"""
import atexit
import subprocess
import sys
import webbrowser


def _overlay_cmd():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--overlay"]
    return [sys.executable, "-m", "jc.launcher", "--overlay"]


def _follow_game(window, url):
    """Brings the app window to the front when champ select starts and after the game – never during the
    game (that would minimise League). The page itself switches to Champ Select / Live / Coach."""
    import json
    import time
    import urllib.request
    last = None
    while True:
        try:
            with urllib.request.urlopen(url + "/api/state", timeout=2) as r:
                st = json.loads(r.read().decode("utf-8"))
            phase = "InGame" if st.get("live") else st.get("phase")
            if phase != last:
                if phase in ("ChampSelect", "EndOfGame", "PreEndOfGame"):
                    try:
                        window.restore()
                        window.show()
                        window.on_top = True
                        time.sleep(0.4)
                        window.on_top = False
                    except Exception:  # noqa: BLE001 - older pywebview versions
                        pass
                last = phase
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)


def main():
    from jc.app import load_config
    cfg = load_config()
    port = cfg.get("port", 8765)
    url = f"http://localhost:{port}"

    if "--overlay" in sys.argv:
        from jc import overlay
        overlay.run(url, cfg)
        return

    import urllib.request
    try:  # already running? then only open another window, no second backend/overlay
        urllib.request.urlopen(url + "/api/state", timeout=1)
        already = True
    except Exception:
        already = False

    from jc import app
    if not already:
        app.start(cfg)

    if not already and cfg.get("overlay", {}).get("enabled", True):
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        proc = subprocess.Popen(_overlay_cmd(), creationflags=flags)
        atexit.register(proc.terminate)

    try:
        import webview  # pywebview -> native app window
        window = webview.create_window("Jungle Coach", url, width=1280, height=860, min_size=(420, 500),
                                       background_color="#07090f")
        webview.start(_follow_game, (window, url))
    except ImportError:
        webbrowser.open(url)  # fallback without pywebview
        print("App-Fenster braucht 'pip install pywebview' – Dashboard im Browser geöffnet. Strg+C beendet.")
        try:
            import threading
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
