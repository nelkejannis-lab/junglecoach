"""Coach engine: turns your analysed games into a coaching plan.

  * Benchmarks  – every detail value compared to players of the same champion / role / elo (percentiles)
  * Konstanz    – how stable your games are (spread of a per-game performance score, your floor)
  * Tilt        – performance by position in a session and after losses
  * Muster      – what your wins have that your losses don't
  * Schwerpunkte – the 3 things that move your winrate most, with target value, drill and progress
"""
import statistics
import time

from .meta import METRICS, percentile, quantile

# what to actually practise per weakness (jungle focused, concrete)
DRILLS = {
    "jcs10": ("Clear-Tempo", "Übungstool: Full Clear unter 3:15 (Ziel ~{t} Jungle-CS bei 10 Min). Nach jedem Gank sofort "
                             "die nächsten 2 Camps nachholen statt in der Lane zu bleiben."),
    "cs10": ("Lane-Farm", "Übungstool: 10 Min nur last-hitten, Ziel {t} CS. Im Spiel: Waves vor dem Back fertig farmen, "
                          "nach dem Back zuerst die Wave holen."),
    "kp": ("Präsenz an Kämpfen", "Vor jedem Camp kurz auf die Map: Wo kämpft gleich jemand? Bei Prio auf einer Lane "
                                  "hingehen. Ziel: ~{t} Kill-Beteiligung."),
    "vpm": ("Vision", "Trinket nie voll liegen lassen, bei jedem Back eine Control Ward, ab Min 14 Sweeper. "
                      "Ziel: {t} Vision/Min."),
    "cw": ("Control Wards", "Jeder Back = 1 Control Ward, an Objective-Eingängen. Ziel: {t} pro Spiel."),
    "deaths": ("Weniger sterben", "Vor jedem Invade/Facecheck: Weiß ich, wo der Gegner-Jungler ist? Wenn nein → "
                                  "nicht reingehen. Ziel: höchstens {t} Tode."),
    "dead_share": ("Weniger tote Zeit", "Späte Tode kosten am meisten: ab Min 20 nur mit Sicht in den Fluss. "
                                       "Ziel: höchstens {t} der Zeit tot."),
    "early_kills": ("Früher Gank-Impact", "Erster Gank vor 4:00 auf eine Lane mit CC/Prio. Ziel: {t} frühe Kills/Assists."),
    "scuttles": ("Scuttle-Kontrolle", "Nach dem ersten Clear auf die Seite mit Lane-Prio zur Scuttle (3:30). Ziel: {t}."),
    "drag": ("Objectives", "60–90 s vor Drache: Bot-Wave pushen lassen, Reset, Sicht setzen. Ziel: {t} Drachen-Beteiligungen."),
    "gpm": ("Gold-Effizienz", "Keine Leerlauf-Wege: zwischen Ganks immer ein Camp oder eine Wave. Ziel: {t} Gold/Min."),
    "dpm": ("Schaden in Kämpfen", "In Fights früher Schaden machen statt auf den perfekten Engage zu warten. Ziel: {t} Schaden/Min."),
    "enemy_jungle": ("Counter-Jungle", "Wenn der Gegner-Jungler auf der anderen Seite gezeigt wird: seine Camps nehmen. "
                                       "Ziel: {t} gegnerische Camps."),
    "dmg_share": ("Schadensanteil", "Mehr Zeit in Teamfights statt beim Farmen zu verbringen. Ziel: {t}."),
    "cc": ("CC einsetzen", "CC auf den gefährlichsten Gegner, nicht den nächsten. Ziel: {t} s CC."),
}
# what to practise depends on the ROLE: an ADC is not told to "take camps between ganks", a support not to farm
DRILLS_ROLE = {
    "TOP": {
        "cs10": ("Lane-Farm", "Übungstool: 10 Min nur last-hitten, Ziel {t} CS. Im Spiel: vor dem Back die Wave in den Turm pushen, "
                              "nach dem Back zuerst zur Wave – keine Wave am eigenen Turm verlieren."),
        "kp": ("Map-Einfluss", "Teleport für Drachen-, Grubs- und Herald-Fights aufheben, nach einem Turm zum Fluss rotieren. Ziel: {t} Kill-Beteiligung."),
        "deaths": ("Gank-Schutz", "Ohne Sicht im Fluss/Tri-Busch nicht über die Lane-Mitte pushen – Gegner-Jungler-Ganks sind der häufigste "
                                  "Top-Tod. Ziel: höchstens {t} Tode."),
        "vpm": ("Vision", "Beim Pushen Ward im Fluss oder Tri-Busch, Control Ward im Fluss-Busch. Ziel: {t} Vision/Min."),
        "cw": ("Control Wards", "Jeden Back eine Control Ward in den Fluss-Busch auf deiner Seite. Ziel: {t} pro Spiel."),
        "gpm": ("Gold-Effizienz", "Plates holen, nach der Lane-Phase die Side-Lane farmen, keine Wave verfallen lassen. Ziel: {t} Gold/Min."),
        "dpm": ("Trades & Duelle", "Kurz traden, wenn die wichtige Fähigkeit des Gegners auf Cooldown ist; in Fights Frontline oder Flanke. Ziel: {t} Schaden/Min."),
        "drag": ("Objectives", "Mit Lane-Prio zu Grubs/Herald; macht dein Team Drache, Splitpush auf der anderen Seite. Ziel: {t}."),
        "dmg_share": ("Schadensanteil", "In Fights nicht nur tanken: Fähigkeiten auf Gegner in Reichweite, Flanke suchen. Ziel: {t}."),
    },
    "MIDDLE": {
        "cs10": ("Lane-Farm", "Übungstool: 10 Min last-hitten, Ziel {t} CS. Wave nie für einen Roam verfallen lassen – erst pushen, dann gehen."),
        "kp": ("Roams", "Nach jeder gepushten Wave: Fluss/Scuttle mit dem Jungler oder Roam bot/top. Ziel: {t} Kill-Beteiligung."),
        "deaths": ("Weniger sterben", "Mid hat zwei Gank-Seiten: ohne Sicht in beiden Flüssen nicht über die Mitte pushen. Ziel: höchstens {t} Tode."),
        "vpm": ("Vision", "Je ein Ward links und rechts im Fluss, Control Ward im Mid-Busch. Ziel: {t} Vision/Min."),
        "cw": ("Control Wards", "Jeden Back eine Control Ward in einen Mid-Busch oder Fluss-Eingang. Ziel: {t} pro Spiel."),
        "gpm": ("Gold-Effizienz", "Mid-Wave halten und freie Side-Waves nehmen, wenn das Team nichts startet. Ziel: {t} Gold/Min."),
        "dpm": ("Schaden in Kämpfen", "Poke/Burst auf erreichbare Carries – nicht nur Waves clearen. Ziel: {t} Schaden/Min."),
        "drag": ("Objectives", "Mid-Prio = Objective-Prio: vor Drache/Grubs die Wave pushen und mit dem Jungler hin. Ziel: {t}."),
        "dmg_share": ("Schadensanteil", "Du bist einer der zwei Hauptschadensquellen: Fights nur mit Ultimate und Items starten. Ziel: {t}."),
    },
    "BOTTOM": {
        "cs10": ("Last-Hitting", "Übungstool: 10 Min nur last-hitten, Ziel {t} CS. In der Lane: erst die Wave, dann traden – CS ist dein wichtigster Wert."),
        "kp": ("In Fights dabei", "Ab dem Mid-Game mit Support und Team gruppieren (Drache, Mid) statt alleine vor dem gegnerischen Turm zu farmen. "
                                  "Ziel: {t} Kill-Beteiligung."),
        "deaths": ("Positionierung", "In Fights hinten bleiben und den nächsten erreichbaren Gegner schlagen (Front-to-Back); nie ohne Sicht "
                                     "alleine in eine Side-Lane. Ziel: höchstens {t} Tode."),
        "vpm": ("Vision", "Trinket immer benutzen (Tri-Busch, Fluss) und jeden Back eine Control Ward – den Großteil macht dein Support. "
                          "Ziel: {t} Vision/Min."),
        "cw": ("Control Wards", "Jeden Back eine Control Ward: Bot-Tri-Busch oder Drachen-Eingang. Ziel: {t} pro Spiel."),
        "gpm": ("Farm-Effizienz", "Mid-Game: freie Side-Waves farmen, aber 60 s vor Objectives zurück beim Team. Ziel: {t} Gold/Min."),
        "dpm": ("Dauerschaden", "In Fights durchgehend autoattacken und kiten – auf den nächsten erreichbaren Gegner, nicht nur auf den Carry hinten. "
                                "Ziel: {t} Schaden/Min."),
        "dmg_share": ("Schadensanteil", "Du bist der Hauptschaden: Kämpfe dort nehmen, wo du sicher schlagen kannst, und mit fertigen Items. Ziel: {t}."),
        "drag": ("Drachen & Türme", "Nach gewonnener Lane (Push + Prio) sofort Turm-Plates oder Drache mit dem Jungler. Ziel: {t}."),
    },
    "UTILITY": {
        "kp": ("Präsenz", "Nach der Lane-Phase mit dem Jungler Sicht setzen und roamen – bei jedem Fight dabei sein. Ziel: {t} Kill-Beteiligung."),
        "deaths": ("Weniger sterben", "Facechecks mit Sweeper oder Ward statt mit dem Körper; nie alleine tief in den Gegner-Jungle. "
                                      "Ziel: höchstens {t} Tode."),
        "vpm": ("Vision", "Support-Item-Wards nie voll liegen lassen, ab Level 9 Sweeper, vor Objectives 60–90 s früher warden. Ziel: {t} Vision/Min."),
        "cw": ("Control Wards", "Immer 1–2 Control Wards auf der Map: Fluss + nächstes Objective. Ziel: {t} pro Spiel."),
        "dpm": ("Lane-Druck", "Harass/Poke, wenn der Gegner last-hittet; in Fights Fähigkeiten auf den gefährlichsten Gegner. Ziel: {t} Schaden/Min."),
        "drag": ("Objectives", "Sicht um Drache/Baron 60–90 s vorher mit dem Jungler aufbauen, gegnerische Wards räumen. Ziel: {t}."),
    },
}


def drill(metric, role):
    return (DRILLS_ROLE.get(role or "JUNGLE") or {}).get(metric) or DRILLS.get(metric)


# values that make no sense for a role (junglers and supports don't farm lane minions, laners no jungle camps)
NOT_FOR_ROLE = {
    "JUNGLE": {"cs10"},
    "UTILITY": {"cs10", "jcs10", "scuttles", "enemy_jungle", "early_kills", "gpm", "dmg_share", "solo_kills"},
    "TOP": {"jcs10", "scuttles", "enemy_jungle", "early_kills"},
    "MIDDLE": {"jcs10", "scuttles", "enemy_jungle", "early_kills"},
    "BOTTOM": {"jcs10", "scuttles", "enemy_jungle", "early_kills", "solo_kills"},
}
# depends on the champion's kit, not on how well you play (Jinx has almost no CC, Leona a lot) -> never a coaching goal
KIT_METRICS = {"cc"}
LAST_GAMES = 10
CORE = ["jcs10", "cs10", "kp", "vpm", "deaths", "gpm", "dpm", "early_kills", "drag", "cw", "scuttles", "dead_share"]


def _good(metric, p):
    """Percentile where higher always means better."""
    if p is None:
        return None
    return p if METRICS.get(metric, ("", True))[1] else 100 - p


def game_score(stats, dist):
    """0-100 performance of one game: average 'better than x %' over the core metrics."""
    vals = [_good(m, percentile(dist[m], stats[m])) for m in CORE if m in stats and m in dist]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals)) if vals else None


def _fmt(metric, v):
    if v is None:
        return "–"
    unit = METRICS.get(metric, ("", True, ""))[2]
    return f"{round(v, 1) if isinstance(v, float) and v < 10 else round(v)}{unit}"


def sessions(games, gap_min=45):
    """Groups games (with game_start, duration) into play sessions."""
    out, cur, last_end = [], [], None
    for g in sorted(games, key=lambda g: g.get("game_start") or 0):
        start = g.get("game_start") or 0
        if last_end is not None and start - last_end > gap_min * 60:
            out.append(cur)
            cur = []
        cur.append(g)
        last_end = start + (g.get("duration") or 1800)
    if cur:
        out.append(cur)
    return out


def _wr(gs):
    return round(100 * sum(1 for g in gs if g.get("win")) / len(gs)) if gs else None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs)) if xs else None


def analyze(games, bench, now=None, bench_by_champ=None):
    """games: own games (newest first) with 'stats', 'win', 'game_start', optional 'duration', 'first_action_min'.
    bench: benchmark of the main champion (MetaStats.benchmarks); bench_by_champ: {champion: benchmark}
    so every game is compared with players of the champion actually played."""
    now = now or time.time()
    games = sorted([g for g in games if g.get("stats")], key=lambda g: -(g.get("game_start") or 0))[:LAST_GAMES]
    for g in games:  # only values that fit the role played
        skip = NOT_FOR_ROLE.get(g.get("role") or "JUNGLE", set()) | KIT_METRICS
        g["stats"] = {k: v for k, v in g["stats"].items() if k not in skip}
    if not games or not bench:
        return {"ready": False, "reason": "Analysiere zuerst ein paar Ranked-Games (Analyse-Tab) – "
                                          "und es braucht Vergleichsdaten aus der Meta-Datenbank."}
    dist = bench["dist"]
    by = bench_by_champ or {}
    n = len(games)

    def dist_of(g):  # compare with players of the same champion in the same role
        b = by.get((g.get("champion"), g.get("role") or "JUNGLE")) or by.get(g.get("champion"))
        return b["dist"] if b else dist
    for g in games:
        g["score"] = game_score(g["stats"], dist_of(g))

    # ---- profile: per game "better than x %" (vs. the champion played), median over your games
    profile = []
    for m, (label, higher, unit) in METRICS.items():
        vals = [g["stats"][m] for g in games if m in g["stats"]]
        pcts = [_good(m, percentile(dist_of(g)[m], g["stats"][m])) for g in games
                if m in g["stats"] and m in dist_of(g)]
        if m not in dist or len(pcts) < max(3, n // 3):
            continue
        med = statistics.median(vals)
        p = round(statistics.median(pcts))
        target = quantile(dist[m], 0.65 if higher else 0.35)
        profile.append({"metric": m, "label": label, "you": _fmt(m, med), "percentile": p,
                        "top": _fmt(m, quantile(dist[m], 0.75 if higher else 0.25)), "target": target,
                        "target_txt": _fmt(m, target)})
    profile.sort(key=lambda x: -x["percentile"])
    strengths = [x for x in profile if x["percentile"] >= 60][:3]
    weaknesses = sorted([x for x in profile if x["percentile"] <= 45], key=lambda x: x["percentile"])[:4]

    # ---- consistency
    scores = [g["score"] for g in games if g["score"] is not None]
    cons = None
    if len(scores) >= 4:
        sd = statistics.pstdev(scores)
        floor = sorted(scores)[len(scores) // 4]
        cons = {"score": max(0, min(100, round(100 - sd * 2.2))), "spread": round(sd), "floor": floor,
                "avg": round(sum(scores) / len(scores)),
                "bad_games": sum(1 for s in scores if s < 35), "great_games": sum(1 for s in scores if s >= 65)}
        per_metric = []
        for m in CORE:
            ps = [_good(m, percentile(dist_of(g)[m], g["stats"][m])) for g in games
                  if m in g["stats"] and m in dist_of(g)]
            if len(ps) >= 4:
                per_metric.append((statistics.pstdev(ps), m))
        if per_metric:
            sd_m, m = max(per_metric)
            cons["wobbly"] = {"metric": m, "label": METRICS[m][0], "spread": round(sd_m)}
        cons["verdict"] = ("Sehr konstant – dein Niveau hängt kaum von der Tagesform ab." if cons["score"] >= 75 else
                           "Mittel – gute und schlechte Games liegen weit auseinander." if cons["score"] >= 55 else
                           "Unkonstant – deine schwachen Games kosten dich die meisten LP.")

    # ---- tilt / sessions
    sess = sessions(games)
    early, late, after_loss, after_win, after_2l = [], [], [], [], []
    for s in sess:
        for i, g in enumerate(s):
            (early if i < 2 else late).append(g)
            if i >= 1:
                (after_win if s[i - 1].get("win") else after_loss).append(g)
            if i >= 2 and not s[i - 1].get("win") and not s[i - 2].get("win"):
                after_2l.append(g)
    tilt = {"sessions": len(sess), "avg_games_per_session": round(n / max(len(sess), 1), 1),
            "first_games": {"games": len(early), "winrate": _wr(early), "score": _avg(g["score"] for g in early)},
            "later_games": {"games": len(late), "winrate": _wr(late), "score": _avg(g["score"] for g in late)},
            "after_loss": {"games": len(after_loss), "winrate": _wr(after_loss), "score": _avg(g["score"] for g in after_loss)},
            "after_win": {"games": len(after_win), "winrate": _wr(after_win), "score": _avg(g["score"] for g in after_win)},
            "after_2_losses": {"games": len(after_2l), "winrate": _wr(after_2l)}}
    stop = None
    if len(late) >= 3 and (tilt["later_games"]["score"] or 0) <= (tilt["first_games"]["score"] or 0) - 8:
        stop = (f"Ab dem 3. Game einer Session fällt deine Leistung von {tilt['first_games']['score']} auf "
                f"{tilt['later_games']['score']} Punkte → nach 2 Games Pause oder Schluss.")
    elif len(late) >= 5 and (tilt["later_games"]["winrate"] or 0) <= (tilt["first_games"]["winrate"] or 0) - 10:
        stop = (f"Ab dem 3. Game einer Session sinkt deine Winrate von {tilt['first_games']['winrate']}% auf "
                f"{tilt['later_games']['winrate']}% → nach 2 Games Pause machen.")
    elif len(after_2l) >= 3 and (tilt["after_2_losses"]["winrate"] or 100) < 40:
        stop = f"Nach 2 Niederlagen in Folge gewinnst du nur {tilt['after_2_losses']['winrate']}% → dann aufhören."
    if stop:
        stop += f" (vorläufiges Muster aus {n} Games / {len(sess)} Sessions)"
    tilt["rule"] = stop

    # ---- what wins look like
    wins = [g for g in games if g.get("win")]
    losses = [g for g in games if not g.get("win")]
    patterns = []
    if len(wins) >= 3 and len(losses) >= 3:
        for m in CORE:
            w = [_good(m, percentile(dist_of(g)[m], g["stats"][m])) for g in wins if m in g["stats"] and m in dist_of(g)]
            lo = [_good(m, percentile(dist_of(g)[m], g["stats"][m])) for g in losses
                  if m in g["stats"] and m in dist_of(g)]
            if len(w) >= 3 and len(lo) >= 3:
                wv = statistics.median([g["stats"][m] for g in wins if m in g["stats"]])
                lv = statistics.median([g["stats"][m] for g in losses if m in g["stats"]])
                patterns.append({"metric": m, "label": METRICS[m][0], "gap": round(sum(w) / len(w) - sum(lo) / len(lo)),
                                 "wins": _fmt(m, wv), "losses": _fmt(m, lv)})
        for p in patterns:
            p["evidence"] = f"{len(wins)} Siege vs. {len(losses)} Niederlagen – vorläufig"
        patterns.sort(key=lambda x: -x["gap"])
        patterns = [p for p in patterns if p["gap"] >= 8][:4]
        early_g = [g for g in games if g.get("first_action_min") is not None and g["first_action_min"] <= 4.5]
        late_g = [g for g in games if g.get("first_action_min") is None or g["first_action_min"] > 4.5]
        if len(early_g) >= 3 and len(late_g) >= 3 and (_wr(early_g) or 0) - (_wr(late_g) or 0) >= 8:
            patterns.append({"metric": "first_action", "label": "Erste Aktion vor 4:30",
                             "gap": (_wr(early_g) or 0) - (_wr(late_g) or 0),
                             "wins": f"{_wr(early_g)}% WR ({len(early_g)} Games)",
                             "losses": f"{_wr(late_g)}% WR später ({len(late_g)} Games)", "is_wr": True})

    # ---- focus areas: weakness x how much it separates your wins from losses
    impact = {p["metric"]: p["gap"] for p in patterns}
    cands = []
    roles = [g.get("role") or "JUNGLE" for g in games]
    main_role = max(set(roles), key=roles.count) if roles else "JUNGLE"
    for w in weaknesses:
        m = w["metric"]
        if not drill(m, main_role):
            continue
        weight = (50 - w["percentile"]) + max(0, impact.get(m, 0)) * 1.5
        if cons and cons.get("wobbly", {}).get("metric") == m:
            weight += 10
        cands.append((weight, w))
    cands.sort(key=lambda x: -x[0])
    focus = []
    week_start = now - ((time.localtime(now).tm_wday * 86400) + time.localtime(now).tm_hour * 3600
                        + time.localtime(now).tm_min * 60)
    for _, w in cands[:3]:
        m = w["metric"]
        title, drill_txt = drill(m, main_role)
        higher = METRICS[m][1]
        hit = [g for g in games[:10] if m in g["stats"]]
        ok = [g for g in hit if (g["stats"][m] >= w["target"]) == higher or g["stats"][m] == w["target"]]
        week = [g for g in games if (g.get("game_start") or 0) >= week_start and m in g["stats"]]
        week_ok = [g for g in week if (g["stats"][m] >= w["target"]) == higher or g["stats"][m] == w["target"]]
        why = f"Du: {w['you']} · besser als {w['percentile']}% · Ziel {w['target_txt']} (Top 25%: {w['top']})"
        if impact.get(m, 0) >= 8:
            why += f" · in Siegen deutlich besser als in Niederlagen"
        focus.append({"metric": m, "title": title, "why": why, "drill": drill_txt.format(t=w["target_txt"]),
                      "target": w["target_txt"], "target_val": w["target"], "higher": higher, "last10": f"{len(ok)}/{len(hit)}",
                      "week": {"done": len(week_ok), "games": len(week)}})
    if tilt.get("rule") and len(focus) < 3:
        focus.append({"metric": "tilt", "title": "Session-Disziplin", "why": tilt["rule"],
                      "drill": "Nach 2 Games oder 2 Niederlagen in Folge: Pause, 1 Replay-Fehler anschauen, dann erst weiter.",
                      "target": "max. 2 Games am Stück", "last10": "", "week": None})
    if cons and cons["score"] < 55 and cons.get("wobbly") and len(focus) < 3 \
            and not any(f["metric"] == cons["wobbly"]["metric"] for f in focus):
        m = cons["wobbly"]["metric"]
        focus.append({"metric": m, "title": f"Konstanz: {METRICS[m][0]}",
                      "why": f"Dieser Wert schwankt bei dir am stärksten (±{cons['wobbly']['spread']} Perzentil-Punkte).",
                      "drill": "Jedes Game gleich starten: gleiche Route, gleicher erster Back, feste Checkliste.",
                      "target": "gleichmäßiger", "last10": "", "week": None})

    # ---- player type (fun + useful)
    top = {x["metric"]: x["percentile"] for x in profile}
    lane = main_role in ("TOP", "MIDDLE", "BOTTOM")
    if lane and top.get("cs10", 0) >= 65 and top.get("kp", 50) < 50:
        ptype = ("Farm-Maschine", "Du farmst stark, bist aber selten an Kills beteiligt – den Vorsprung in Türme und Teamfights umsetzen.")
    elif lane and top.get("kp", 0) >= 65 and top.get("cs10", 50) < 45:
        ptype = ("Teamfighter", "Du bist bei jedem Fight dabei, verlierst aber Farm – zwischen den Fights Side-Waves mitnehmen.")
    elif main_role == "UTILITY" and top.get("vpm", 0) >= 65 and top.get("kp", 0) >= 55:
        ptype = ("Map-Kontrolle", "Viel Sicht und überall dabei – genau das, was ein Support liefern soll.")
    elif main_role == "JUNGLE" and top.get("jcs10", 0) >= 65 and top.get("kp", 50) < 50:
        ptype = ("Farm-Maschine", "Du farmst stark, bist aber selten an Kills beteiligt – nutze dein Tempo für mehr Ganks.")
    elif main_role == "JUNGLE" and top.get("kp", 0) >= 65 and top.get("jcs10", 50) < 45:
        ptype = ("Aggro-Ganker", "Du bist überall dabei, vernachlässigst aber den Farm – Ganks mit Camps verbinden.")
    elif top.get("vpm", 0) >= 65 and top.get("drag", 0) >= 55:
        ptype = ("Objective-Kontrolleur", "Du spielst um Sicht und Objectives – das gewinnt Games, weiter so.")
    elif top.get("deaths", 50) <= 35:
        ptype = ("Draufgänger", "Viele Tode – mehr Infos sammeln, bevor du in den Fight gehst."
                 if main_role != "BOTTOM" else "Viele Tode – in Fights weiter hinten stehen und Front-to-Back spielen.")
    elif cons and cons["score"] >= 75:
        ptype = ("Fels in der Brandung", "Sehr konstant – jetzt gezielt deine Schwächen anheben.")
    else:
        ptype = ("Allrounder", "Ausgeglichen – die Schwerpunkte unten bringen dir am meisten.")

    # one learning task for the next session instead of many numbers
    task = None
    if focus:
        f0 = focus[0]
        task = {"metric": f0["metric"], "title": f0["title"], "drill": f0["drill"], "target": f0["target"],
                "check": f"Nach der Session prüft der Coach: {f0['target']} erreicht? (zuletzt {f0['last10'] or '–'})"}
    confidence = ("sehr vorläufig" if n < 5 else "vorläufig") + f" – Basis {n} Games, Vergleich {bench['games']} Games ({bench['scope']})"

    return {"ready": True, "games": n, "scope": bench["scope"], "bench_games": bench["games"],
            "session_task": task, "confidence": confidence,
            "player_type": {"name": ptype[0], "text": ptype[1]},
            "score": _avg(scores), "profile": profile, "strengths": strengths, "weaknesses": weaknesses,
            "consistency": cons, "tilt": tilt, "patterns": patterns, "focus": focus,
            "trend": [{"t": g.get("game_start"), "score": g["score"], "win": g.get("win"), "champion": g.get("champion")}
                      for g in sorted(games, key=lambda g: g.get("game_start") or 0)]}


def progress(history, focus, now=None, days=30):
    """30-day progress per focus area from the stored game history:
    every game's value, weekly averages, the target and whether the trend goes the right way."""
    now = now or time.time()
    start = now - days * 86400
    out = []
    for f in focus or []:
        m = f.get("metric")
        if m not in METRICS or f.get("target_val") is None:
            continue
        pts = sorted(((g.get("game_start") or 0, g["stats"][m], bool(g.get("win"))) for g in history
                      if (g.get("game_start") or 0) >= start and m in (g.get("stats") or {})), key=lambda x: x[0])
        if len(pts) < 2:
            out.append({"metric": m, "title": f["title"], "points": [], "enough": False})
            continue
        weeks = []
        for w in range(days // 7 + 1):
            a, b = start + w * 7 * 86400, start + (w + 1) * 7 * 86400
            vals = [v for t, v, _ in pts if a <= t < b]
            weeks.append(round(sum(vals) / len(vals), 2) if vals else None)
        half = len(pts) // 2
        first = sum(v for _, v, _ in pts[:half]) / max(1, half)
        last = sum(v for _, v, _ in pts[half:]) / max(1, len(pts) - half)
        better = (last > first) == f.get("higher", True)
        change = round(last - first, 2)
        hit = sum(1 for _, v, _ in pts if (v >= f["target_val"]) == f.get("higher", True) or v == f["target_val"])
        out.append({"metric": m, "title": f["title"], "enough": True, "target": f["target_val"],
                    "target_txt": f.get("target"), "higher": f.get("higher", True),
                    "points": [{"t": t, "v": v, "win": w} for t, v, w in pts], "weeks": weeks,
                    "change": change, "better": better if abs(change) > 1e-9 else None,
                    "hit": f"{hit}/{len(pts)}", "games": len(pts)})
    return out
