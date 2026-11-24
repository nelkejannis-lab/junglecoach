"""Turns an LCU champ-select session into analysis + recommendations."""
import time
from . import analyzer


def _cid(member):
    return member.get("championId") or member.get("championPickIntent") or 0


def _page_display(gd, page):
    return {"primary": gd.rune_disp.get(page["primary"], str(page["primary"])),
            "secondary": gd.rune_disp.get(page["sub"], str(page["sub"])),
            "runes": [gd.rune_disp.get(r, str(r)) for r in page["perks"][:6]]}


def apply_meta(gd, out, my_champ, enemy_jg, meta_stats, bracket, enemy_ids=None):
    """Overrides rule-based runes/build with real data:
    whole enemy team > jungle matchup > hidden gem > meta."""
    cm = meta_stats.champion(my_champ["id"], bracket=bracket)
    mu = meta_stats.matchup(my_champ["id"], enemy_jg["id"], bracket) if enemy_jg else None
    tf = meta_stats.team_fit(my_champ["id"], enemy_ids or [], bracket) if enemy_ids else None
    out["meta"] = {"champion": cm, "matchup": mu, "team_fit": tf}
    ename = gd.champ_name(enemy_jg) if enemy_jg else None
    page, src = None, None
    full = len(enemy_ids or []) >= 5
    if tf and tf.get("enough") and tf.get("rune_page"):
        page = tf["rune_page"]
        ks = tf["keystones"][0]
        vs = ", ".join(f"{c['enemy']} {c['delta']:+}%" for c in ks["vs"]) or "keine starken Einzel-Effekte"
        src = (f"{'Final – alle 5 Gegner' if full else f'{len(enemy_ids)}/5 Gegner'} berücksichtigt: "
               f"{ks['name']} ~{ks['score']}% erwartete WR ({vs})")
    elif mu and mu.get("enough") and mu.get("rune_page"):
        page = mu["rune_page"]
        ks = mu["keystones"][0]
        src = f"Matchup vs {ename}: {ks['name']} {ks['adj_winrate']}% WR ({mu['games']} Games, {mu['scope']})"
    elif cm.get("enough") and cm.get("rune_page"):
        page = cm["rune_page"]
        gem = cm["hidden_keystones"][0] if cm.get("hidden_keystones") else None
        src = (f"Geheimtipp: {gem['name']} – {gem['verdict']}" if gem and gem["id"] == page["perks"][0]
               else f"Meta-Seite: {page['winrate']}% WR in {page['games']} Games ({cm['scope']})")
    if page and len(page["perks"]) == 9:
        out["runes"] = {"primaryStyleId": page["primary"], "subStyleId": page["sub"],
                        "selectedPerkIds": page["perks"], "display": _page_display(gd, page),
                        "why": [src], "source": "data"}
    b = out.get("build")
    if b is None:
        return out
    # Core = the ordered path real players build (coherent, consensus). Team-fit / matchup / gem
    # items are offered as switches for the 3rd slot or situational – never a random mix.
    bp = meta_stats.build_path(my_champ["id"], bracket=bracket)
    core, seen, situ = [], set(), []
    if bp.get("enough"):
        for c in bp["core"]:
            core.append({"id": c["id"], "name": c["name"], "badge": f"{c['step']}. Item",
                         "why": f"{c['share']}% bauen es als {c['step']}. Item" +
                                (f" (fertig ~{c['minute']}. Min)" if c.get("minute") else "") +
                                (f" · {c['winrate']}% WR ({c['games']} Games)" if c.get("winrate") is not None else "")})
            seen.add(c["id"])
        for o in bp.get("late", [])[:3]:
            situ.append({"id": o["id"], "name": o["name"], "why": f"häufiges 4./5. Item ({o['share']}%)"}); seen.add(o["id"])
        if bp.get("boots"):
            bt = bp["boots"]
            b["boots"] = {"id": bt["id"], "name": bt["name"], "why": f"meistgebaute Schuhe ({bt['share']}%)"}
    swap = None
    if tf and tf.get("enough"):
        scores = {it["id"]: it["score"] for it in tf["items"]}
        ref = scores.get(core[2]["id"]) if len(core) == 3 else None
        for it in sorted(tf["items"], key=lambda x: -x["score"]):
            if it["id"] in seen or it.get("games", 0) < 40:
                continue
            # only adapt when clearly better vs THIS team than the standard 3rd item (or no path at all)
            if (ref is None and len(core) < 3 and it["score"] >= it["overall"] + 1.5) or \
                    (ref is not None and it["score"] >= ref + 1.5 and it["score"] >= it["overall"] + 1.0):
                vs = ", ".join(f"{c['enemy']} {c['delta']:+}%" for c in it["vs"]) or "solide gegen diese Comp"
                swap = {"id": it["id"], "name": it["name"], "badge": "Team-Fit",
                        "why": f"~{it['score']}% erwartete WR gegen dieses Team ({vs})"}
                break
        if tf.get("boots"):
            bt = tf["boots"][0]
            if bt.get("score", 0) >= bt.get("overall", 0) + 1.5:
                b["boots"] = {"id": bt["id"], "name": bt["name"], "why": f"beste Schuhe gegen diese Comp (~{bt['score']}% WR)"}
    if not swap and mu and mu.get("enough"):
        for it in mu["items"]:
            if it["prob_better"] >= 0.9 and it["games"] >= 30 and it["id"] not in seen:
                swap = {"id": it["id"], "name": it["name"], "badge": "Matchup",
                        "why": f"vs {ename}: {it['adj_winrate']}% WR ({it['games']} Games)"}
                break
    if swap:
        if len(core) == 3:  # first two items are the champion's backbone, adapt the third
            situ.insert(0, dict(core[2], why="Standard-3. Item: " + core[2]["why"]))
            core[2] = swap
        else:
            core.append(swap)
        seen.add(swap["id"])
    if cm.get("enough"):
        for g in cm.get("hidden_items", [])[:1]:
            if g["id"] not in seen:
                situ.append({"id": g["id"], "name": g["name"], "why": "Geheimtipp: " + g["verdict"]}); seen.add(g["id"])
        if not core:  # no path data -> old meta list
            for m in cm["meta_core"][:3]:
                core.append({"id": m["id"], "name": m["name"], "badge": "Meta",
                             "why": f"{m['pickrate']}% Pickrate, {m['winrate']}% WR"}); seen.add(m["id"])
    if core:
        b["core"] = core[:3]
        b["source"] = (f"echte Kaufreihenfolge aus {bp.get('games', 0)} Games ({bp.get('scope', '')})" if bp.get("exact")
                       else f"geschätzt aus {bp.get('games', 0)} Games ({bp.get('scope', '')})") if bp.get("enough") else "Meta"
        b["situational"] = situ + [x for x in b.get("situational", []) if x["id"] not in seen]
    return out


_CACHE = {}
ROLE_DE = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot (ADC)", "UTILITY": "Support"}


def role_picks(gd, config, role, allies, prof, taken, meta_stats=None, bracket=None, history=None):
    """Pick help for the position the client assigned you: your own champions on that role (from your game
    history), your pool champions that are really played there, and the strongest meta picks of that role.
    Every option gets: your games/winrate on it, meta tier/winrate in your elo, team fit."""
    pool = config.get("champion_pool", [])
    if not role:
        return analyzer.pick_suggestions(gd, pool, allies, prof, taken)
    cand = {}  # id -> reasons/sources
    mine = {}
    for g in history or []:
        if g.get("role") == role and g.get("champion"):
            c = gd.champ(g["champion"])
            if c:
                st = mine.setdefault(c["id"], [0, 0])
                st[0] += 1; st[1] += 1 if g.get("win") else 0
    for cid, (n, _) in sorted(mine.items(), key=lambda kv: -kv[1][0])[:6]:
        cand.setdefault(cid, set()).add("mine")
    tiers = {}
    if meta_stats is not None:
        try:
            key = ("tier", role, bracket, id(meta_stats.db))
            if key not in _CACHE or time.time() - _CACHE[key][0] > 600:  # champ select polls every second
                _CACHE[key] = (time.time(), meta_stats.tierlist(bracket=bracket, role=role))
            tl = _CACHE[key][1]
            tiers = {t["champion"]: t for t in tl}
            for t in tl[:6]:
                cand.setdefault(t["champion"], set()).add("meta")
        except Exception:  # noqa: BLE001
            tiers = {}
    for ref in pool:
        c = gd.champ(ref)
        if not c:
            continue
        plays_role = c["id"] in mine or c["id"] in tiers
        if not plays_role and meta_stats is not None:
            try:
                k2 = ("main", c["id"], id(meta_stats.db))
                if k2 not in _CACHE:
                    _CACHE[k2] = (time.time(), meta_stats.main_role(c["id"]))
                plays_role = _CACHE[k2][1] == role
            except Exception:  # noqa: BLE001
                plays_role = False
        if plays_role:
            cand.setdefault(c["id"], set()).add("pool")
    out = []
    for cid, src in cand.items():
        c = gd.champ(cid)
        if not c or cid in taken:
            continue
        p = analyzer.score_pick(gd, c, allies, prof)
        why = [w for w in p["why"] if w != "neutral gegen diese Comp"]
        score = p["score"]
        if cid in mine:
            n, w = mine[cid]
            wr = round(100 * w / n)
            score += min(20, n * 2) + (wr - 50) * 0.3  # comfort pick: what you actually play
            why.insert(0, f"deine Games als {ROLE_DE[role]}: {n} · {wr}% WR")
        t = tiers.get(cid)
        if t:
            score += {"S": 12, "A": 8, "B": 4, "C": 0, "D": -4}.get(t.get("tier"), 0)
            why.append(f"Meta {ROLE_DE[role]}: Tier {t.get('tier')} · {t.get('winrate')}% WR")
        if "pool" in src:
            score += 6
            why.append("in deinem Champion-Pool")
        out.append({"champion": gd.champ_name(c), "id": cid, "score": round(score), "why": why or ["neutral gegen diese Comp"],
                    "source": "mine" if "mine" in src else "pool" if "pool" in src else "meta"})
    return sorted(out, key=lambda x: -x["score"])[:8]


def analyze(gd, session, config, meta_stats=None, bracket=None, history=None):
    if not session:
        return None
    me_cell = session.get("localPlayerCellId")
    my_team = session.get("myTeam", [])
    their = session.get("theirTeam", [])
    me = next((m for m in my_team if m.get("cellId") == me_cell), {})
    my_champ = gd.champ(_cid(me)) if _cid(me) else None
    allies = [gd.champ(_cid(m)) for m in my_team if m.get("cellId") != me_cell and _cid(m)]
    enemies = [gd.champ(m["championId"]) for m in their if m.get("championId")]

    taken = {c["id"] for c in allies + enemies if c}
    for b in session.get("bans", {}).get("myTeamBans", []) + session.get("bans", {}).get("theirTeamBans", []):
        c = gd.champ(b) if b else None
        if c:
            taken.add(c["id"])
    for act_row in session.get("actions", []):
        for a in act_row:
            if a.get("type") == "ban" and a.get("completed") and a.get("championId"):
                c = gd.champ(a["championId"])
                if c:
                    taken.add(c["id"])

    prof = analyzer.team_profile(gd, enemies)
    enemy_jg = None
    for m, c in zip([m for m in their if m.get("championId")], enemies):
        if (m.get("assignedPosition") or "").lower() == "jungle":
            enemy_jg = c
    if not enemy_jg:  # blind pick / no positions: guess from champs with Smite spell id 11
        for m, c in zip([m for m in their if m.get("championId")], enemies):
            if 11 in (m.get("spell1Id"), m.get("spell2Id")):
                enemy_jg = c

    # lane opponent: enemy in my position (Gwen top -> enemy top laner), else the enemy jungler
    my_pos = (me.get("assignedPosition") or "").lower()
    lane_enemy = None
    for m, c in zip([m for m in their if m.get("championId")], enemies):
        if my_pos and (m.get("assignedPosition") or "").lower() == my_pos:
            lane_enemy = c
    lane_enemy = lane_enemy or enemy_jg
    role = {"top": "TOP", "jungle": "JUNGLE", "middle": "MIDDLE", "bottom": "BOTTOM", "utility": "UTILITY"}.get(my_pos)
    if meta_stats is not None:
        meta_stats.role = role

    out = {
        "my_position": me.get("assignedPosition") or "unbekannt",
        "lane_enemy": gd.champ_name(lane_enemy) if lane_enemy else None,
        "my_champion": gd.champ_name(my_champ) if my_champ else None,
        "my_champion_id": my_champ["id"] if my_champ else None,
        "allies": [gd.champ_name(c) for c in allies],
        "enemies": [gd.champ_name(c) for c in enemies],
        "enemy_jungler": gd.champ_name(enemy_jg) if enemy_jg else None,
        "enemy_profile": prof,
        "notes": analyzer.enemy_notes(gd, prof, enemy_jg),
        "pick_suggestions": role_picks(gd, config, role, allies, prof, taken, meta_stats, bracket, history),
        "assigned_role": role,
    }
    if my_champ:
        preset = config.get("champion_presets", {}).get(my_champ["id"])
        out["runes"] = analyzer.recommend_runes(gd, my_champ, prof, preset)
        out["runes"]["source"] = "rules"
        out["build"] = analyzer.recommend_build(gd, my_champ, prof, preset=preset)
        if meta_stats and not (preset and (preset.get("core") or preset.get("keystone"))):
            try:
                apply_meta(gd, out, my_champ, lane_enemy, meta_stats, bracket, [c["id"] for c in enemies if c])
            except Exception as e:  # never break champ select because of stats
                out["meta_error"] = str(e)
    # game plan (start side, gank lane, early duel, objective, spikes) – background, appears on the next poll
    if my_champ and (role in (None, "JUNGLE")):
        try:
            from jc import gameplan
            pos = {"top": "TOP", "jungle": "JUNGLE", "middle": "MIDDLE", "bottom": "BOTTOM", "utility": "UTILITY"}
            ally_roles = tuple((gd.champ(_cid(m))["id"], pos.get((m.get("assignedPosition") or "").lower()))
                               for m in my_team if m.get("cellId") != me_cell and _cid(m) and gd.champ(_cid(m)))
            enemy_roles = []
            for m, c in zip([m for m in their if m.get("championId")], enemies):
                if not c:
                    continue
                r = pos.get((m.get("assignedPosition") or "").lower())
                if not r and meta_stats is not None:
                    try:
                        r = meta_stats.main_role(c["id"])
                    except Exception:  # noqa: BLE001
                        r = None
                enemy_roles.append((c["id"], r))
            out["gameplan"] = gameplan.build_async(my_champ["id"], enemy_jg["id"] if enemy_jg else None, ally_roles,
                                                   tuple(enemy_roles), meta_stats, bracket)
        except Exception as e:  # noqa: BLE001
            out["gameplan"] = {"error": str(e), "steps": []}
    # meta-aware pick suggestions: matchup winrate vs the enemy jungler
    if meta_stats and lane_enemy:
        for p in out["pick_suggestions"]:
            try:
                mu = meta_stats.matchup(p["id"], lane_enemy["id"], bracket)
            except Exception:
                continue
            if mu.get("enough"):
                p["score"] += round((mu["winrate"] - 50) * 1.5)
                p["why"].append(f"vs {gd.champ_name(lane_enemy)}: {mu['winrate']}% WR ({mu['games']} Games)")
        out["pick_suggestions"].sort(key=lambda x: -x["score"])
    out["enemy_jungler_id"] = enemy_jg["id"] if enemy_jg else None
    if meta_stats is not None:
        meta_stats.role = None
    out["enemies_locked"] = sum(1 for m in their if m.get("championId"))
    return out
