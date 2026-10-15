#!/bin/sh
# Restore a dump made by backup.sh into the running compose Postgres.
#   ./deploy/restore.sh backups/devinbox-20261009T030000Z.dump
# Stops app+worker first so nothing writes during the restore.
set -eu
[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: $0 <dump-file>" >&2; exit 2; }
docker compose stop app worker dispatcher
docker compose exec -T postgres pg_restore -U devinbox -d devinbox --clean --if-exists --no-owner --exit-on-error --single-transaction < "$1"
docker compose up -d app worker dispatcher
echo "restored from $1"
