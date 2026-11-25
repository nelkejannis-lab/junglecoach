"""Persistent, account- and context-bound practice tasks, separate from measured success."""
import hashlib
import json
import os
import time
import threading
_LOCK = threading.RLock()
from .paths import DATA_DIR

VERSION = 1
BEHAVIOUR = {
    "jcs10": "Vor einem Gank die verfügbare Camp-Route und den Zeitverlust abwägen.",
    "cs10": "Vor einem Trade oder Recall prüfen, welche Minions dadurch verloren gehen könnten.",
    "kp": "Vor einer Rotation Nutzen, Erreichbarkeit und aufgegebene Ressourcen vergleichen.",
    "vpm": "Vor einer Bewegung festlegen, welche Information ein Ward liefern soll.",
    "cw": "Vor dem Kauf einer Control Ward ihren geplanten Nutzen gegen das nächste Item abwägen.",
    "deaths": "Vor einem riskanten Engage bekannte Gegnerpositionen, Hilfe und Rückweg prüfen.",
    "dead_share": "Vor einem späten Risiko überlegen, welche Ziele während eines Todes offen wären.",
    "gpm": "Bei einem längeren Laufweg prüfen, welche erreichbare Ressource oder Teamaktion ihn rechtfertigt.",
    "dpm": "Im Replay eine verpasste und eine genutzte sichere Schadensmöglichkeit vergleichen.",
    "dmg_share": "Im Replay prüfen, ob Ressourcen, Champion-Aufgabe oder verpasste Chancen den Schaden erklären.",
    "drag": "Vor einem Objective Ressourcen, Teampositionen und mögliche Gegenziele vergleichen.",
    "early_kills": "Vor dem ersten Gank Erfolgschance und Kosten gegenüber dem nächsten Camp abwägen.",
    "scuttles": "Vor einer Scuttle-Entscheidung Lane-Priorität und verfügbare Alternativen prüfen.",
}
def update_task(account, context, plan, games, directory=None, now=None):
    with _LOCK:
        return _update_task(account, context, plan, games, directory, now)

def _update_task(account, context, plan, games, directory=None, now=None):
    now = time.time() if now is None else now
    root = directory or os.path.join(DATA_DIR, "learning")
    identity = json.dumps([account or "", *context], ensure_ascii=False)
    key = hashlib.sha256(identity.encode()).hexdigest()
    path = os.path.join(root, key + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            task = json.load(f)
    except (OSError, ValueError):
        task = None
    if not task:
        focus = next((f for f in plan.get("focus", []) if f.get("target_val") is not None), None)
        if not focus:
            return None
        task = dict(focus, id=hashlib.sha256((identity + str(now)).encode()).hexdigest()[:24],
                    version=VERSION, started_at=now, role=context[1], champion=context[0],
                    baseline=focus.get("last10"), status="practising")
        task["drill"] = BEHAVIOUR.get(task["metric"], "Zwei passende Szenen nach dem Match vergleichen.") + " In drei passenden Spielen üben."
    eligible = [g for g in games if (g.get("game_start") or 0) > task["started_at"]
                and g.get("champion") == task["champion"]
                and (g.get("role") or "JUNGLE") == task["role"]
                and task["metric"] in (g.get("stats") or {})]
    unique = {g.get("match_id") or str(g.get("game_start")): g for g in eligible}
    task["matches"] = sorted(unique)
    vals = [g["stats"][task["metric"]] for g in unique.values()]
    task["measured_hits"] = sum((v >= task["target_val"] if task["higher"] else v <= task["target_val"]) for v in vals)
    task["status"] = "review_due" if len(unique) >= 3 else "practising"
    task["check"] = (f"{len(unique)}/3 passende Spiele; Messziel {task['measured_hits']}/{len(unique)} erreicht. "
                     "Im Replay begründen, ob die Entscheidung sinnvoll war. Ohne passende Szene: nicht bewertbar. "
                     "Ein Haken bestätigt nur deine eigene Übung, keine Beherrschung.")
    task["evidence"] = [{"match_id": g.get("match_id"), "game_start": g.get("game_start"),
                         "value": g["stats"].get(task["metric"])} for g in sorted(games, key=lambda g: -(g.get("game_start") or 0))[:2]]
    os.makedirs(root, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(task, f, ensure_ascii=False)
    os.replace(temporary, path)
    return task
