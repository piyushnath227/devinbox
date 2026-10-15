#!/bin/sh
# Nightly Postgres backup for the docker-compose.yml stack.
#   crontab:  0 3 * * *  cd /opt/devinbox && ./deploy/backup.sh /var/backups/devinbox
# Keeps the 14 newest dumps. Copy them OFF this machine (rclone/rsync/S3) too.
#
# IMPORTANT: stored API keys are encrypted with SECRET_KEY. A database backup is
# useless for those keys without it -- back SECRET_KEY up separately (password manager).
set -eu
DIR="${1:-./backups}"
mkdir -p "$DIR"
OUT="$DIR/devinbox-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T postgres pg_dump -U devinbox -Fc devinbox > "$OUT"
[ -s "$OUT" ] || { echo "backup is empty, aborting" >&2; rm -f "$OUT"; exit 1; }
ls -1t "$DIR"/devinbox-*.dump | tail -n +15 | xargs -r rm --
echo "backup written: $OUT ($(du -h "$OUT" | cut -f1))"
