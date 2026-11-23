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
        webview.create_window("Jungle Coach", url, width=1200, height=820, min_size=(420, 500),
                              background_color="#0e1116")
        webview.start()
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
