# Server einrichten (Cloudflare) – einmalig, ca. 30 Minuten

Der Server hält den Riot-API-Key, verwaltet die Konten (Discord/Google) und stellt die Website
mit Datenschutz, Impressum und Nutzungsbedingungen bereit. Kosten: im kostenlosen Cloudflare-Tarif.

## 1. Cloudflare-Konto & Worker
1. Auf dash.cloudflare.com anmelden (oder kostenlos registrieren).
2. **Storage & Databases → D1 → Create database** → Name `junglecoach` → Create.
   Die **Database ID** kopieren und in `server/wrangler.toml` bei `database_id` eintragen (keine geheime Angabe).
3. In der D1-Datenbank → **Console** → Inhalt von `server/migrations/0001_users.sql` einfügen → Execute.
4. **Workers & Pages → Create → Import a repository** → `nelkejannis-lab/junglecoach` wählen
   → Root directory: `server` → Deploy command: `npx wrangler deploy` → Deploy.
   Danach hat der Worker eine Adresse wie `https://junglecoach.<dein-name>.workers.dev`.

## 2. Discord-Anmeldung
1. discord.com/developers/applications → **New Application** → Name „Jungle Coach“.
2. **OAuth2** → Redirects → `https://<worker-adresse>/auth/discord/callback` hinzufügen → Save.
3. Client ID und Client Secret notieren (Secret per „Reset Secret“).

## 3. Google-Anmeldung
1. console.cloud.google.com → neues Projekt „Jungle Coach“.
2. **APIs & Services → OAuth consent screen**: External, App-Name „Jungle Coach“, Support-Mail,
   Links zu `/datenschutz.html` und `/nutzungsbedingungen.html` der Worker-Adresse, Scopes `openid`, `profile`.
3. **Credentials → Create credentials → OAuth client ID** → Web application →
   Authorized redirect URI: `https://<worker-adresse>/auth/google/callback` → Create.
4. Client ID und Client Secret notieren.

## 4. Geheimnisse im Worker hinterlegen
Worker → **Settings → Variables and Secrets → Add** (Typ **Secret**):

| Name | Wert |
|---|---|
| `RIOT_API_KEY` | Riot API Key (Production/Personal Key) |
| `JWT_SECRET` | optional – wird sonst automatisch erzeugt und in D1 gespeichert |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | aus Schritt 2 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | aus Schritt 3 |

## 5. App verbinden
Worker-Adresse in `config.json` bei `server_url` eintragen und neues Release bauen.

## 6. Vor der Veröffentlichung
- Angaben in Impressum und Datenschutz sind eingetragen – bei Umzug oder neuer Mail dort anpassen.
- Rechtstexte prüfen lassen – sie sind sorgfältige Vorlagen, aber keine Rechtsberatung.
- Riot-Produktregistrierung: developer.riotgames.com → Register Product → Production API Key,
  Produkt-URL = Worker-Adresse (oder eigene Domain).
