# Wiederherstellen aus der Sicherung

Voraussetzung: dieselben Werte für `RESTIC_REPOSITORY` und `RESTIC_PASSWORD` wie beim Sichern
und **`WALLET_SECRET_KEY` aus der alten `.env`** – ohne ihn sind die gespeicherten Zertifikate unbrauchbar.
Jede Datenbank ist eine eigene Sicherung (Tag `db-<name>`), der Zertifikatsspeicher hat den Tag `certs`.

```sh
R="docker run --rm -i -e RESTIC_REPOSITORY -e RESTIC_PASSWORD -v $HOME/.ssh:/root/.ssh:ro restic/restic:0.18.0"
$R snapshots                        # was ist vorhanden?

# 1. Datenbanken starten (leer) und einspielen - direkt aus der Sicherung, ohne Zwischendatei
docker compose up -d db authentik-db
$R dump latest --tag db-wallet wallet.dump | docker compose exec -T db pg_restore -U wallet -d wallet --clean --if-exists
$R dump latest --tag db-authentik authentik.dump | docker compose exec -T authentik-db pg_restore -U authentik -d authentik --clean --if-exists

# 2. Zertifikatsspeicher zurückspielen
docker compose up -d api
docker run --rm -e RESTIC_REPOSITORY -e RESTIC_PASSWORD --volumes-from "$(docker compose ps -q api)" \
  restic/restic:0.18.0 restore latest --tag certs --target /

# 3. Alles starten
docker compose up -d
```

Die Wiederherstellung einmal im Quartal auf einem Testserver üben.
