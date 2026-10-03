# creatwallet

Ein Tool, mit dem du **Apple-Wallet-Pässe (`.pkpass`)** erstellst, prüfst und signierst – auch in den
**neuen Wallet-Formaten**:

| Format | ab | Vorlage |
|---|---|---|
| Poster-Event-Ticket (Konzert / Live) – großes Artwork, Event-Guide, Schnellaktionen | iOS 26 | `posterEventTicket` |
| Poster-Event-Ticket (Sport) – Heim/Gast-Teams, Sektionsfarbe | iOS 26 | `posterSportsTicket` |
| Dauerkarte / Mehrfach-Event (`upcomingPassInformation`) | iOS 26 | `seasonTicket` |
| Semantischer Flug-Boarding-Pass (Live-Fluginfos, Badges, Airline-Services) | iOS 26 | `semanticBoardingPass` |
| Poster-Generic-Pass (Mitgliedschaft, Kundenkarte … im Poster-Stil) | iOS 27 | `posterGeneric` |
| Mehrere Barcodes als Fallback, neue Formate EAN-13, Code 39, Codabar, ITF | iOS 27 | `posterGeneric` |
| Klassisch: Generic, Kundenkarte, Coupon, Event-Ticket, Bahn/Bus | iOS 6+ | `generic`, `storeCard`, `coupon`, `eventTicket`, `trainTicket` |

Alle Vorlagen für neue Formate enthalten automatisch den **klassischen Fallback**, damit der Pass auch auf
älteren iPhones und der Apple Watch funktioniert.

Funktionen:

- **Web-Editor** im Browser mit Live-Vorschau (Poster-, semantische und klassische Ansicht), Bild-Upload,
  Live-Prüfung und Download der fertigen `.pkpass`
- **Kommandozeile** für Automatisierung (Personalisierung, viele Pässe auf einmal)
- **Validierung** gegen Apples Spezifikation – inkl. Warnung, wenn ein Poster-Ticket oder semantischer
  Boarding-Pass mangels Pflicht-Tags still auf den alten Stil zurückfällt
- **Signierung** (PKCS#7, SHA-256) mit deinem Pass-Type-ID-Zertifikat und Apples WWDR-Zertifikat
- `.pkpass`-Dateien öffnen und analysieren (Manifest-Hashes, Signatur, Inhalt)

Einzige Abhängigkeit: [`cryptography`](https://pypi.org/project/cryptography/). Python ≥ 3.9.

---

## 1. Installation

```bash
git clone https://github.com/justuschm/creatwallet.git
cd creatwallet
python3 -m venv .venv && source .venv/bin/activate   # optional
pip install -e .
creatwallet --help
```

(Ohne Installation geht auch `python3 -m creatwallet …`.)

## 2. Einmalig: Zertifikat von Apple holen

Damit iPhones einen Pass akzeptieren, muss er mit einem Zertifikat aus deinem
**Apple Developer Program** (kostenpflichtig) signiert sein.

1. **Pass Type ID anlegen:** [developer.apple.com → Certificates, IDs & Profiles → Identifiers](https://developer.apple.com/account/resources/identifiers/list/passTypeId)
   → „+“ → *Pass Type IDs* → z. B. `pass.de.meinefirma.ticket`.
2. **Zertifikat erzeugen:** Pass Type ID auswählen → *Create Certificate*. Apple verlangt eine
   Zertifikatsanfrage (CSR):
   - **Mac:** Schlüsselbundverwaltung → Zertifikatsassistent → *Zertifikat einer Zertifizierungsinstanz anfordern*.
   - **Ohne Mac:**
     ```bash
     openssl req -new -newkey rsa:2048 -nodes -keyout pass.key -out pass.csr -subj "/CN=Pass/emailAddress=du@example.com"
     ```
3. Zertifikat (`pass.cer`) herunterladen.
   - **Mac:** Doppelklick → in der Schlüsselbundverwaltung *Zertifikat + Schlüssel* als `.p12` exportieren.
   - **Ohne Mac:** `openssl x509 -inform DER -in pass.cer -out pass.pem` → dann `--cert pass.pem --key pass.key` verwenden.
4. **Apple WWDR-Zwischenzertifikat (G4)** herunterladen:
   <https://www.apple.com/certificateauthority/AppleWWDRCAG4.cer>
5. Deine **Team ID** (10 Zeichen) steht unter [developer.apple.com/account](https://developer.apple.com/account) → Membership.

> Zertifikate und Schlüssel niemals ins Git einchecken – `.gitignore` schließt `*.p12`, `*.pem`, `*.cer`,
> `*.key` und `certs/` bereits aus.

> **Zum Link `com.apple.developer.pass-type-identifiers`:** Dieses Entitlement braucht nur eine **eigene
> iOS-App**, die per PassKit Pässe hinzufügt oder liest. Zum Erstellen und Verteilen von `.pkpass`-Dateien
> (Download, Mail, AirDrop, Webseite) brauchst du es nicht – dafür genügt die Pass Type ID mit Zertifikat.
> Wenn du später eine App baust, trägst du dort dieselbe Pass Type ID (`$(TeamIdentifierPrefix)pass.de.meinefirma.ticket`) ein.

## 3. Web-Editor

```bash
creatwallet serve --p12 certs/pass.p12 --password 'geheim' --wwdr certs/AppleWWDRCAG4.cer
# → http://127.0.0.1:8080 öffnen
```

1. Vorlage wählen (z. B. *Poster-Event-Ticket*) → **Vorlage laden**
2. Grunddaten und `pass.json` bearbeiten – Vorschau und Prüfung aktualisieren sich sofort
3. Platzhalter-Bilder durch eigene PNGs ersetzen (z. B. `artwork@2x.png`, `primaryLogo@3x.png`)
4. **.pkpass erstellen** → Datei per AirDrop, Mail oder Safari aufs iPhone

Ohne Zertifikat startet der Editor auch – dann ist nur das Signieren deaktiviert.
Der Server lauscht standardmäßig nur lokal (`127.0.0.1`).

## 4. Auf dem eigenen Server mit Docker

Voraussetzung: Docker inkl. Compose-Plugin (`docker compose version`).

```bash
git clone https://github.com/justuschm/creatwallet.git
cd creatwallet

# 1. Zertifikate ablegen (Dateinamen genau so)
mkdir certs
cp /pfad/zu/deinem/zertifikat.p12  certs/pass.p12
curl -fsSL -o certs/AppleWWDRCAG4.cer https://www.apple.com/certificateauthority/AppleWWDRCAG4.cer
chmod 644 certs/*          # der Container läuft als eigener Benutzer und muss sie lesen können

# 2. Passwörter festlegen
cp .env.example .env
nano .env                  # .p12-Passwort und Login für den Editor (benutzer:passwort) eintragen

# 3. Starten
docker compose up -d --build
docker compose ps          # Status sollte "healthy" sein
docker compose logs -f     # Logs ansehen
```

Der Editor ist dann **auf dem Server selbst** unter `http://127.0.0.1:8080` erreichbar und mit dem
Login aus `CREATWALLET_AUTH` geschützt.

**Von außen erreichbar machen (mit HTTPS):** Am einfachsten mit [Caddy](https://caddyserver.com) als
Reverse-Proxy, der das Let's-Encrypt-Zertifikat automatisch holt. `/etc/caddy/Caddyfile`:

```
wallet.deine-domain.de {
    reverse_proxy 127.0.0.1:8080
}
```

Mit nginx entsprechend `proxy_pass http://127.0.0.1:8080;` und `client_max_body_size 25m;`.

> Gib den Port **nicht ohne HTTPS und Passwort** ins Internet frei. Wer Zugriff auf den Editor hat, kann mit
> deinem Apple-Zertifikat beliebige Pässe signieren.

**Aktualisieren:**

```bash
git pull && docker compose up -d --build
```

**Einstellungen** (Umgebungsvariablen, in `docker-compose.yml` bzw. `.env`):

| Variable | Bedeutung |
|---|---|
| `CREATWALLET_AUTH` | Login für den Editor, `benutzer:passwort` |
| `CREATWALLET_P12` / `CREATWALLET_PASSWORD` | Zertifikat (.p12) und dessen Passwort |
| `CREATWALLET_CERT` / `CREATWALLET_KEY` | alternativ Zertifikat + Schlüssel als PEM |
| `CREATWALLET_WWDR` | Apple WWDR-G4-Zertifikat |
| `CREATWALLET_PORT` | Port im Container (Standard 8080) |

Für Passwörter geht statt der Variable auch `…_FILE` mit einem Dateipfad (z. B. für Docker Secrets):
`CREATWALLET_PASSWORD_FILE=/run/secrets/p12_password`.

Die Kommandozeile funktioniert auch im Container, z. B.:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" creatwallet:latest init posterEventTicket konzert.pass
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" -v "$PWD/certs:/certs:ro" --env-file .env \
    -e CREATWALLET_P12=/certs/pass.p12 -e CREATWALLET_WWDR=/certs/AppleWWDRCAG4.cer \
    creatwallet:latest build konzert.pass -o konzert.pkpass
```

Ohne Docker lässt sich der Editor auch direkt starten:
`creatwallet serve --host 0.0.0.0 --auth admin:passwort --p12 … --wwdr …`.

## 5. Kommandozeile

```bash
creatwallet templates                                   # Vorlagen anzeigen
creatwallet init posterEventTicket konzert.pass \
    --pass-type-id pass.de.meinefirma.ticket --team-id ABCDE12345
#   → konzert.pass/pass.json + Platzhalter-Bilder bearbeiten
creatwallet validate konzert.pass                       # prüfen
creatwallet build konzert.pass -o konzert.pkpass \
    --p12 certs/pass.p12 --password 'geheim' --wwdr certs/AppleWWDRCAG4.cer
creatwallet inspect konzert.pkpass                      # fertige Datei analysieren
```

Zertifikatsangaben können auch als Umgebungsvariablen gesetzt werden:
`CREATWALLET_P12`, `CREATWALLET_PASSWORD`, `CREATWALLET_CERT`, `CREATWALLET_KEY`, `CREATWALLET_WWDR`.

### Personalisieren / viele Pässe erzeugen

Jeder Pass braucht eine eigene `serialNumber`. Werte lassen sich beim Bauen überschreiben –
verschachtelte Schlüssel mit Punkt:

```bash
creatwallet build konzert.pass -o anna.pkpass --new-serial \
    --set semantics.attendeeName="Anna Beispiel" \
    --set barcodes='[{"format":"PKBarcodeFormatQR","message":"T-0001","messageEncoding":"iso-8859-1"}]'

# oder eine JSON-Datei einmischen (Objekte werden zusammengeführt, Listen ersetzt):
creatwallet build konzert.pass -o anna.pkpass --merge anna.json
```

Beispiel für viele Tickets aus einer CSV:

```bash
while IFS=, read -r id name; do
  creatwallet build konzert.pass -o "out/$id.pkpass" -q --set serialNumber="$id" \
      --set semantics.attendeeName="$name" \
      --set barcodes="[{\"format\":\"PKBarcodeFormatQR\",\"message\":\"$id\",\"messageEncoding\":\"iso-8859-1\"}]"
done < gaeste.csv
```

### Als Python-Bibliothek

```python
from creatwallet import load_signer, new_pass, placeholder_images, build_pkpass

signer = load_signer("certs/AppleWWDRCAG4.cer", p12="certs/pass.p12", password="geheim")
p = new_pass("semanticBoardingPass")
p["semantics"]["passengerName"] = {"givenName": "Anna", "familyName": "Beispiel"}
data, issues = build_pkpass(p, placeholder_images("semanticBoardingPass"), signer)
open("bordkarte.pkpass", "wb").write(data)
```

Zum Ausliefern über eine Webseite den Content-Type `application/vnd.apple.pkpass` verwenden.

## 6. Aufbau eines Pass-Projekts

```
konzert.pass/
├── pass.json            # Inhalt & Metadaten
├── icon.png  icon@2x.png  icon@3x.png        # Pflicht
├── logo@2x.png          # klassische Stile
├── primaryLogo@2x.png   # Poster- & semantische Pässe
├── secondaryLogo@2x.png # Poster-Event-Ticket (unten rechts)
├── artwork@2x.png       # Poster-Hintergrund (358×448 pt)
├── strip / thumbnail / background / footer   # je nach Stil
└── de.lproj/            # optional: Übersetzungen
    └── pass.strings
```

| Bild | Größe (pt, ×2/×3 für @2x/@3x) | Stile |
|---|---|---|
| `icon` | 38 × 38 | alle (Pflicht) |
| `logo` | 50–160 × 50 | klassische Stile |
| `primaryLogo` | 30–126 × 30 | semantischer Boarding-Pass, Poster-Event, Poster-Generic |
| `secondaryLogo` | 12–135 × 12 | Poster-Event-Ticket |
| `artwork` | 358 × 448 | Poster-Event-Ticket, Poster-Generic |
| `background` | 343 × 503 | klassisches Event-Ticket |
| `strip` | 375 × 144 | Coupon, Kundenkarte |
| `thumbnail` | 60–90 × 90 | Event-Ticket, Generic |
| `footer` | 268 × 15 | Boarding-Pass |

## 7. Wichtige Hinweise zu den neuen Formaten

- **Poster-Event-Ticket:** braucht `"preferredStyleSchemes": ["posterEventTicket", "eventTicket"]` und die
  semantischen Tags `eventName`, `venueName`, `venueRegionName`, `venueRoom`; bei Sport zusätzlich
  `homeTeamAbbreviation`/`awayTeamAbbreviation`, bei Live-Musik `performerNames`. Fehlt etwas, zeigt Wallet
  ohne Fehlermeldung das klassische Ticket – `creatwallet validate` warnt dann.
- **Semantischer Boarding-Pass:** `"preferredStyleSchemes": ["semanticBoardingPass", "boardingPass"]`,
  `transitType: PKTransitTypeAir` und 12 Pflicht-Tags (Flug, Flughäfen, Zeitzonen, Zeiten, Passagiername).
- **Poster-Generic (iOS 27):** eigener Stil-Schlüssel `posterGeneric`; zusätzlich einen klassischen Stil
  (z. B. `generic`) für ältere Geräte mitliefern.
- **Mehrere Barcodes (iOS 27):** neue Formate wie EAN-13 zuerst, danach z. B. QR als Fallback.
- **Featured Actions (iOS 27)** und **Live-Updates** (Gate-Änderungen, Push) brauchen einen eigenen
  Web-Service (`webServiceURL`) bzw. sind von Apple teils noch nicht als `pass.json`-Schlüssel dokumentiert –
  das Tool erzeugt die Pässe, betreibt aber keinen Update-Server.
- **NFC-Pässe** benötigen eine gesonderte Freigabe von Apple.
- Die Vorschau im Editor ist eine **Annäherung** – das endgültige Aussehen bestimmt Wallet. Zum Testen die
  `.pkpass` in den iOS-Simulator ziehen oder aufs iPhone schicken.

## 8. Tests

```bash
python3 -m unittest discover -s tests -t .
```

Die Tests erzeugen eigene Test-Zertifikate, bauen jede Vorlage und prüfen die Signatur mit `openssl cms -verify`.

## 9. Plattform-API

Im Paket `app/` steckt die mandantenfähige Plattform: Firmen legen Vorlagen mit Platzhaltern an
(`"value": "{{name}}"`) und geben per REST-API Pässe aus, die mit **deinem** Zertifikat signiert werden.
Endkunden bekommen eine Download-Seite mit Button und QR-Code.

**Lokal starten** (Python ≥ 3.11, SQLite):

```bash
pip install -e ".[server]"
export WALLET_SECRET_KEY=$(python -m app generate-secret)   # gut aufbewahren
export WALLET_WWDR=certs/AppleWWDRCAG4.cer
python -m app migrate
python -m app create-tenant --name "Kino Beispiel GmbH"            # gibt die Firmen-ID aus
python -m app add-certificate --p12 certs/pass.p12 --tenant <ID>   # fragt das .p12-Passwort ab
python -m app create-api-key --tenant <ID>                         # Schlüssel wird nur einmal angezeigt
uvicorn app.api:create_app --factory --reload
# API-Doku: http://localhost:8000/docs
```

**Ablauf für eine Firma:**

```bash
KEY=wk_…
# 1. Vorlage aus einer Startvorlage anlegen (oder eigene pass.json + Bilder als base64 senden)
curl -s -X POST localhost:8000/api/v1/templates -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' -d '{"name": "Konzert", "base_template": "posterEventTicket"}'
# 2. Freigabe durch dich
python -m app pending && python -m app approve <VORLAGEN-ID>
# 3. Pass ausgeben - page_url an den Endkunden geben
curl -s -X POST localhost:8000/api/v1/passes -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' -H 'Idempotency-Key: bestellung-4711' \
     -d '{"template_id": "<VORLAGEN-ID>", "data": {}}'
```

| Endpunkt | Zweck |
|---|---|
| `GET /api/v1/account` | eigenes Konto, Zertifikat |
| `GET/POST /api/v1/templates`, `GET/PATCH /api/v1/templates/{id}` | Vorlagen; jede Änderung ist eine neue Version, die freigegeben werden muss |
| `POST /api/v1/templates/{id}/test-pass` | Test-Pass mit Beispielwerten, läuft nach 24 h ab |
| `POST /api/v1/passes` | Pass ausgeben (`Idempotency-Key` verhindert Doppelte) |
| `GET /api/v1/passes`, `GET/PATCH /api/v1/passes/{id}` | auflisten, abrufen, Felder ändern (neue Version) |
| `POST /api/v1/passes/{id}/void` | Pass sperren |
| `GET /api/v1/passes/{id}/pkpass` | Datei fürs eigene Backend (z. B. E-Mail-Anhang) |
| `GET /p/{token}` | öffentliche Seite für Endkunden; `/p/{token}/pass.pkpass` lädt den Pass |
| `GET/POST /api/v1/webhooks`, `DELETE /api/v1/webhooks/{id}`, `POST …/{id}/test` | Webhooks für `pass.installed`, `pass.removed`, `pass.updated`, `pass.voided` |
| `/v1/devices/…`, `/v1/passes/…`, `/v1/log` | Apple-Web-Service: Pfade gibt Apple vor, hier melden sich die iPhones |

**Automatische Updates (Phase 2):** Jeder Pass enthält `webServiceURL` und ein eigenes `authenticationToken`.
Legt jemand den Pass in die Wallet, meldet sich das iPhone an. Bei `PATCH`, `void` oder einer neu
freigegebenen Vorlage legt die API einen Push-Job an; der Worker (`python -m app worker`, in Docker eigener
Dienst) schickt über Apple (APNs) ein Signal, und das iPhone lädt die neue Version. Fehlgeschlagene Pushes
und Webhooks werden bis zu sechsmal mit wachsendem Abstand wiederholt (`python -m app jobs` zeigt den Stand).
Die Warteschlange liegt in der Datenbank: Jobs entstehen in derselben Transaktion wie die Änderung.

**Webhooks prüfen:** Header `Wallet-Signature: t=<unix>,v1=<hex>`; `v1` = HMAC-SHA256 über `"<t>.<body>"` mit dem
`secret` aus der Anlage-Antwort. Webhook-Adressen müssen `https://` sein und dürfen nicht auf interne Adressen zeigen.

**Kunden-Portal und Admin-Bereich:** unter `/portal` gestalten Firmen Vorlagen im Editor (mit Speichern,
Test-Pass und Freigabe-Status), geben Pässe aus, ändern oder sperren sie, verwalten API-Schlüssel, Webhooks,
Team (Rollen Inhaber, Admin, Gestalter, Ausgabe, Nur lesen) und sehen eine Statistik. Unter `/admin`
prüfst du neue Firmen und Vorlagen, ordnest Zertifikate zu, lädst neue `.p12` hoch, siehst Jobs und das
Audit-Log. Der Login läuft über **Authentik** (OpenID Connect): Mitglieder der Authentik-Gruppe
`wallet-admins` sind Plattform-Admins. `deploy/authentik/wallet.yaml` richtet beim Start alles ein –
Anbindung an die Plattform, Admin-Gruppe, Registrierung für neue Firmen und Zwei-Faktor-Pflicht.

Lokal ohne Authentik: `WALLET_DEV_LOGIN=true` aktiviert einen Entwickler-Login unter `/auth/dev-login`
(**nie in Produktion**).

**Auf einem Server:** `deploy/` enthält Dockerfile, `docker-compose.yml` (PostgreSQL, API, Worker,
Authentik, Caddy mit automatischem HTTPS für Plattform- und Login-Domain) und `.env.example`. Ablauf steht oben in `deploy/docker-compose.yml`.

**Einstellungen** (Umgebungsvariablen, auch als `…_FILE`):

| Variable | Bedeutung |
|---|---|
| `WALLET_DATABASE_URL` | z. B. `postgresql+psycopg://user:pw@host/wallet` (Standard: SQLite-Datei) |
| `WALLET_PUBLIC_BASE_URL` | öffentliche Adresse für Download-Links |
| `WALLET_SECRET_KEY` | Fernet-Schlüssel für gespeicherte Geheimnisse |
| `WALLET_WWDR` | Apple-WWDR-Zertifikat |
| `WALLET_CERT_BACKEND` | `file` (verschlüsselt in `WALLET_CERT_DIR`) oder `openbao` (`WALLET_OPENBAO_ADDR`, `…_TOKEN`) |
| `WALLET_REQUIRE_TEMPLATE_APPROVAL` | `true`: Vorlagen erst nach Freigabe nutzbar |
| `WALLET_APPLE_WEB_SERVICE` | `true` (Standard) trägt den Update-Dienst in die Pässe ein |
| `WALLET_APNS_PUSH_TYPE` | optionaler Header `apns-push-type` für APNs (Standard: nicht senden) |
| `WALLET_ALLOW_INSECURE_WEBHOOKS` | nur lokal: Webhooks an `http://` und interne Adressen erlauben |
| `WALLET_OIDC_ISSUER`, `…_CLIENT_ID`, `…_CLIENT_SECRET` | Login über Authentik, z. B. `https://auth.example.de/application/o/wallet/` |
| `WALLET_ADMIN_GROUP` | Authentik-Gruppe der Plattform-Admins (Standard `wallet-admins`) |
| `WALLET_ADMIN_NETWORKS` | Admin-Bereich nur aus diesen Netzen (CIDR, kommagetrennt), z. B. dein VPN |
| `WALLET_ALLOW_SIGNUP` | neue Firmen dürfen sich selbst registrieren (Standard `true`) |
| `WALLET_DEV_LOGIN` | nur lokal: Anmeldung ohne Authentik |

Für den Livebetrieb den Button auf der Download-Seite durch Apples offizielles
„Add to Apple Wallet“-Badge ersetzen (Apple-Richtlinien).

## Quellen

- [Apple: Wallet Passes – Pass (pass.json)](https://developer.apple.com/documentation/walletpasses/pass)
- [Apple: Semantic Tags](https://developer.apple.com/documentation/walletpasses/semantictags)
- [Apple: Creating an event pass using semantic tags](https://developer.apple.com/documentation/walletpasses/creating-an-event-pass-using-semantic-tags)
- [Apple: Creating an airline boarding pass using semantic tags](https://developer.apple.com/documentation/walletpasses/creating-an-airline-boarding-pass-using-semantic-tags)
- [Apple: Creating a poster generic pass](https://developer.apple.com/documentation/walletpasses/creating-a-poster-generic-pass)
- [Apple HIG: Wallet](https://developer.apple.com/design/human-interface-guidelines/wallet)
