"""League Client (LCU) API – official local REST API of the running client.

Used for champ select and for writing rune pages. Only talks to 127.0.0.1.
"""
import base64
import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.request

# Riot uses a self-signed cert on localhost; verification is skipped ONLY for 127.0.0.1.
_LOCAL_CTX = ssl.create_default_context()
_LOCAL_CTX.check_hostname = False
_LOCAL_CTX.verify_mode = ssl.CERT_NONE

DEFAULT_PATHS = [r"C:\Riot Games\League of Legends", r"D:\Riot Games\League of Legends"]


def _from_lockfile(paths):
    for p in paths:
        lf = os.path.join(p, "lockfile")
        if os.path.exists(lf):
            try:
                with open(lf, encoding="utf-8") as f:
                    _name, _pid, port, pw, proto = f.read().strip().split(":")
                return int(port), pw
            except (OSError, ValueError):
                continue
    return None


def _from_process():
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='LeagueClientUx.exe'\" | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    port = re.search(r"--app-port=(\d+)", out)
    tok = re.search(r"--remoting-auth-token=([\w-]+)", out)
    return (int(port.group(1)), tok.group(1)) if port and tok else None


class LCU:
    def __init__(self, league_path=None):
        self.paths = ([league_path] if league_path else []) + DEFAULT_PATHS
        self.port = self.auth = None

    def connect(self):
        creds = _from_lockfile(self.paths) or _from_process()
        if not creds:
            self.port = None
            return False
        self.port, pw = creds
        self.auth = "Basic " + base64.b64encode(f"riot:{pw}".encode()).decode()
        return True

    def request(self, method, path, body=None):
        if not self.port and not self.connect():
            return None
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"https://127.0.0.1:{self.port}{path}", data=data, method=method,
                                     headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, context=_LOCAL_CTX, timeout=4) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            self.port = None  # client closed or restarted -> reconnect next time
            return None

    def get(self, path):
        return self.request("GET", path)

    # ---- convenience ----------------------------------------------------
    def phase(self):
        return self.get("/lol-gameflow/v1/gameflow-phase")

    def champ_select(self):
        return self.get("/lol-champ-select/v1/session")

    def set_rune_page(self, name, primary, sub, perks):
        pages = self.get("/lol-perks/v1/pages") or []
        mine = [p for p in pages if p.get("name", "").startswith("JC ") and p.get("isDeletable")]
        for p in mine:
            self.request("DELETE", f"/lol-perks/v1/pages/{p['id']}")
        inv = self.get("/lol-perks/v1/inventory") or {}
        editable = [p for p in (self.get("/lol-perks/v1/pages") or []) if p.get("isDeletable")]
        if inv.get("ownedPageCount") is not None and len(editable) >= inv["ownedPageCount"]:
            # never delete the user's own pages
            return {"error": "Alle Runenseiten belegt – lösche eine Seite oder nenne sie 'JC ...', "
                             "dann überschreibt das Tool nur diese."}
        return self.request("POST", "/lol-perks/v1/pages", {
            "name": name[:25], "primaryStyleId": primary, "subStyleId": sub,
            "selectedPerkIds": perks, "current": True})
