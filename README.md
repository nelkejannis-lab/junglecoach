# Jungle Coach – League of Legends

Lokales Tool für Jungler: Champ-Select-Analyse, Runen und Build gegen das Gegnerteam, Live-Build-Anpassung, Tracking des Gegner-Junglers und Review nach dem Spiel.

**Vanguard-sicher:** nutzt ausschließlich offizielle Riot-Schnittstellen – LCU API (Client), Live Client Data API (Port 2999), Riot API (match-v5) und Data Dragon. Kein Memory-Reading, keine Injection, keine Infos, die du im Spiel nicht selbst sehen könntest.

## Bereiche der App
| Tab | Was du dort findest |
|---|---|
| **Ranked** | Rang, LP-Verlauf, LP heute/7 Tage, Serie, Ø LP pro Sieg/Niederlage, was besser werden muss (Trend), schlechteste Matchups |
| **Champ Select** | Gegnerteam-Analyse, Pick-Empfehlung, Runen + Build – automatisch gesetzt und bei jedem neuen Gegner-Pick neu berechnet |
| **Live** | Live-Build, Gegner-Jungler-Infos, Objectives, Fehler-Liste, Minimap-Tracker (nur Custom) |
| **Analyse** | Deep-Analyse, Map-Replay, Fehler-Clips |
| **Meta** | Datensammlung, Meta-Builds, Geheimtipps, Tierlist |
| **Einstellungen** | Riot ID, API Key, Champion-Pool, Elo für Statistiken, Auto-Runen |

## Meta, Geheimtipps & Team-Fit
- **Datensammlung** (Meta → „Daten sammeln“): Ranked-Games aus **allen Elos**, gleichmäßig von Iron bis Challenger, über die offizielle Riot API. Gespeichert lokal in `meta.sqlite`.
- **Elo-Filter:** Statistiken standardmäßig aus deinem eigenen Elo-Bereich; bei zu wenig Daten wird automatisch breiter gesucht (Scope steht immer dabei).
- **Team-Fit:** Runen und Items werden gegen das **komplette Gegnerteam** bewertet: Gesamt-Winrate der Option + Zusatz-Effekt gegen jeden einzelnen Gegner (mit Shrinkage bei wenig Daten, max ±8 % pro Gegner).
- **Runen automatisch:** Seite wird bei jedem neuen Gegner-Pick aktualisiert, final wenn alle 5 sichtbar sind. Es werden nur Seiten namens „JC …“ überschrieben.
- **Geheimtipps:** seltene Items/Keystones (1,5–15 % Pickrate) werden per Monte-Carlo-Simulation (Bayes, 4.000 Ziehungen) gegen den Rest verglichen. Empfehlung nur bei ≥ 90 % Wahrscheinlichkeit besser und ≥ 2 % Winrate-Plus – und nur, wenn der Vorteil nicht nur gegen einen einzelnen Gegner besteht (sonst taucht es als Matchup-Tipp auf).
- Ehrlich: Das ist Statistik auf echten Games, kein Kampf-Simulator. Mit Dev-Key dauern ~1.500 Games ca. 1 Stunde.

## LP-Tracker
Liest Rang und LP alle 60 s aus dem League Client (außerhalb von Spielen) und speichert jede Änderung in `lp_history.json`. Funktioniert ohne API Key.

## Installation (Windows)
**Variante A – auf deinem PC (empfohlen):**
1. Ordner entpacken.
2. **`Installer bauen.bat`** doppelklicken. Installiert bei Bedarf Python und Inno Setup automatisch (winget), baut die App und erzeugt `dist\JungleCoach-Setup.exe`. Dauert beim ersten Mal ca. 5–10 Minuten.
3. `JungleCoach-Setup.exe` ausführen → Jungle Coach steht im Startmenü (optional Desktop-Verknüpfung), Deinstallation über Windows-Einstellungen.
4. App öffnen → Einstellungen: Riot ID, API Key (kostenlos auf developer.riotgames.com), Champion-Pool.

**Variante B – in der Cloud:** Ordner als GitHub-Repo hochladen → Actions → „Build installer“ → Run → `JungleCoach-Setup.exe` als Artefakt herunterladen.

Hinweise:
- Windows SmartScreen warnt beim ersten Start (nicht signiert): „Weitere Informationen“ → „Trotzdem ausführen“.
- Installation ohne Admin-Rechte möglich. Einstellungen, Statistiken und Clips liegen in `%APPDATA%\JungleCoach` und bleiben bei Updates erhalten.
- Das App-Fenster braucht die Microsoft-WebView2-Runtime (bei Windows 10/11 meist vorinstalliert).
- Zum schnellen Testen ohne Installation: `Ohne Installation starten.bat`.

## Profil – ohne League
Startseite der App: Rang (Solo/Flex), Level, Mastery, Match-History mit Items, Champion- und Rollen-Statistik – direkt über die Riot API, League muss nicht laufen. Andere Spieler per Riot ID suchbar (z. B. deine Schüler). Dein eigener Rang füttert dabei auch den LP-Tracker.

## Overlay im Spiel
- Startet automatisch mit der App und erscheint nur, wenn ein Spiel läuft.
- **League auf „Randlos“ (Borderless) oder „Fenster“ stellen** – über echtem Vollbild kann kein Fenster liegen.
- Klicks gehen durch das Overlay ins Spiel.
- `Ctrl+Shift+J` ein/aus · `Ctrl+Shift+K` Verschieben-Modus (mit Maus ziehen, nochmal drücken zum Fixieren).
- Position/Größe/Transparenz dauerhaft in `config.json` → `overlay` (negatives `x` = Abstand vom rechten Rand).
- Technisch ein normales Fenster über dem Spiel (wie Discord-Overlay), nichts wird ins Spiel injiziert.

Tipp: Dashboard auf zweitem Monitor. Mit `"allow_phone": true` auch vom Handy im selben WLAN (`http://<PC-IP>:8765`).

## Features
| Phase | Was passiert |
|---|---|
| Champ Select | Gegner-Comp (AD/AP, Tanks, Heiler, CC), Gegner-Jungler-Profil (Early/Scaling), Pick-Empfehlung aus deinem Pool, Runen + Build + Jungle-Pet. Button „Runen in Client übernehmen“ (legt Seite „JC …“ an, löscht nie deine eigenen Seiten). |
| Im Spiel | Build passt sich an Gegner-Items an (Rüstung/MR-Stacking → Pen, Fed-Gegner → Defensive, Heilung → Anti-Heal), Gegner-Jungler: Level, Items, zuletzt im Killfeed gesehen (welche Lane), Gank-Warnungen, Objective-Respawns, CS/min. |
| Review | Letzte X Ranked-Jungle-Games: Early-Seite (Vorhersehbarkeit), Gold-Diff @10 vs Gegner-Jungler, Jungle-CS @5, Ganks pro Lane, Objectives vor Min 20, konkrete Tipps. |

## Map-Replay (Review-Tab)
Game antippen → Karte mit den Wegen von dir (grün) und dem Gegner-Jungler (rot) über die ersten 15 Minuten, Minute für Minute abspielbar, Kills/Tode als ✕. Daneben: Bereich des Gegner-Junglers pro Minute und wo er bei Kills aufgetaucht ist. Datenquelle: offizielle match-v5-Timeline.

## Minimap-Tracker (Coaching, nur Custom Games)
- Live-Tab → „Einschalten“ / „Ausschalten“. Standard: aus.
- Erkennt per Screenshot die Icons der Gegner auf der Minimap (Template-Matching mit den offiziellen Champion-Portraits, rote Umrandung = Gegner). Kein Zugriff auf Spielspeicher.
- **Fest gesperrt außerhalb von Custom Games** – in Ranked/Normals zeigt er nur „gesperrt“.
- Zeigt im Dashboard und im Overlay, wo der Gegner-Jungler zuletzt gesehen wurde; alle Sichtungen werden nach dem Spiel in `sessions/` gespeichert.
- Kalibrierung: „Minimap-Ausschnitt prüfen“ zeigt den erfassten Bereich. Passt er nicht, in `config.json` → `minimap_tracker` `x`, `y`, `size` (Pixel) setzen. Erkennt er Icons nicht oder falsch: `icon_ratio` (Icon-Größe relativ zur Minimap) bzw. `threshold` anpassen.

## Aufnahme & Fehler-Clips
- Jedes Spiel wird automatisch aufgenommen (Bildschirmaufnahme wie Outplayed/Insights Capture, „● REC“ oben).
- Nach dem Spiel werden Clips geschnitten: eigene Tode (20 s davor), verlorene Objectives (35 s davor), Gegner-Jungler-Ganks bis Min 15.
- **Nur die letzten 3 Games** werden behalten, ältere automatisch gelöscht (`recording.keep_games`).
- Clips im Review-Tab; mit API-Key direkt beim passenden Game und bei jedem Tod verlinkt.
- Performance: `recording.encoder` auf `h264_nvenc` (NVIDIA) stellen, wenn die FPS leiden. Aus: `"enabled": false`.
- Speicher: ca. 0,5–1 GB pro Game inkl. Vollaufnahme; `keep_full: false` behält nur die Clips.

## Deep-Analyse (Review → Game antippen)
- Noten A–D für Tempo & Farm, Gank-Impact, Objectives, Vision, Überleben + Gesamtnote.
- Top-3-Prioritäten mit konkreten Zahlen und Handlungsempfehlung.
- Gold-Differenz-Kurve zum Gegner-Jungler, jedes Objective (geholt/verloren, warst du da?).
- Jeder Tod mit Kontext: Unterzahl, gegnerischer Jungle, Gegner-Jungler beteiligt, keine Wards davor, Objective danach verloren, Gold-Rückstand – inkl. Clip-Link.
- Benchmarks (z. B. 60 Jungle-CS @10) sind Richtwerte; anpassbar in `jc/deep.py`.

## Eigene Builds festlegen
In `config.json` → `champion_presets`:
```json
"LeeSin": { "keystone": "Conqueror", "secondary": "Domination", "core": ["Eclipse", "Black Cleaver", "Sterak"] }
```
Presets haben Vorrang vor der Regel-Engine. Item-Namen auf Englisch (Teilstring reicht).

## Grenzen (ehrlich)
- Empfehlungen sind regelbasiert, keine Winrate-Statistik aus Millionen Games wie bei iTero.
- Live-Daten enthalten keine Positionen – „zuletzt gesehen“ kommt nur aus dem Killfeed.
- Objective-Timer nach großen Patches in `config.json` → `timers` prüfen.
- Veröffentlichen/teilen erfordert Riot-Freigabe (Production Key) und den Riot-Disclaimer.

## Tests
`python -m unittest discover tests`
