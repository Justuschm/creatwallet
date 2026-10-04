#!/bin/sh
# Sicherung der Wallet-Pass-Plattform mit restic (verschlüsselt, mit Versionen).
#
#   RESTIC_REPOSITORY=sftp:u12345@u12345.your-storagebox.de:/wallet \
#   RESTIC_PASSWORD=… ./backup/backup.sh
#
# Gesichert werden alle PostgreSQL-Datenbanken der Compose-Umgebung (Plattform, Authentik,
# GlitchTip - soweit sie laufen) und der Zertifikatsspeicher. Die Datenbank-Dumps werden direkt
# an restic gestreamt: Es liegt nie ein unverschlüsselter Dump auf der Festplatte.
#
# Täglich per cron, z. B.:
#   15 3 * * *  cd /opt/wallet/deploy && RESTIC_REPOSITORY=… RESTIC_PASSWORD_FILE=/root/.restic ./backup/backup.sh
#
# Wiederherstellen: siehe backup/RESTORE.md
set -eu
cd "$(dirname "$0")/.."
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY setzen (z. B. sftp:… oder s3:…)}"
DC="docker compose ${COMPOSE_ARGS:-}"
RESTIC_IMAGE="${RESTIC_IMAGE:-restic/restic:0.18.0}"

# Gemeinsame Optionen für den restic-Container
set -- -e RESTIC_REPOSITORY -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY
[ -n "${RESTIC_PASSWORD_FILE:-}" ] && set -- "$@" -e RESTIC_PASSWORD_FILE=/run/restic-password \
  -v "$RESTIC_PASSWORD_FILE:/run/restic-password:ro"
case "$RESTIC_REPOSITORY" in /*) mkdir -p "$RESTIC_REPOSITORY"; set -- "$@" -v "$RESTIC_REPOSITORY:$RESTIC_REPOSITORY" ;; esac
[ -d "$HOME/.ssh" ] && set -- "$@" -v "$HOME/.ssh:/root/.ssh:ro"

if ! docker run --rm "$@" "$RESTIC_IMAGE" cat config >/dev/null 2>&1; then
  echo "Lege neues restic-Repository an …"
  docker run --rm "$@" "$RESTIC_IMAGE" init
fi

# Optionen für die weiteren restic-Aufrufe (Pfade ohne Leerzeichen vorausgesetzt)
RESTIC_OPTS="$*"
for spec in "db wallet wallet" "authentik-db authentik authentik" "glitchtip-db glitchtip glitchtip"; do
  # shellcheck disable=SC2086
  set -- $spec
  service=$1 user=$2 database=$3
  # shellcheck disable=SC2086
  set -- $RESTIC_OPTS
  if [ -n "$($DC ps -q "$service" 2>/dev/null)" ]; then
    echo "Sichere Datenbank $database …"
    $DC exec -T "$service" pg_dump -U "$user" -Fc "$database" | docker run --rm -i "$@" "$RESTIC_IMAGE" backup \
      --stdin --stdin-filename "$database.dump" --tag wallet --tag "db-$database" --host wallet-platform --quiet
  fi
done

API=$($DC ps -q api)
[ -n "$API" ] || { echo "API-Container läuft nicht - Zertifikatsspeicher kann nicht gesichert werden." >&2; exit 1; }
echo "Sichere Zertifikatsspeicher …"
# shellcheck disable=SC2086
set -- $RESTIC_OPTS
docker run --rm "$@" --volumes-from "$API:ro" "$RESTIC_IMAGE" backup /data/certs --tag wallet --tag certs \
  --host wallet-platform --quiet

echo "Alte Sicherungen aufräumen (14 Tage, 8 Wochen, 12 Monate) …"
docker run --rm "$@" "$RESTIC_IMAGE" forget --tag wallet --group-by host,tags --keep-daily 14 --keep-weekly 8 \
  --keep-monthly 12 --prune --quiet
echo "Sicherung abgeschlossen."
