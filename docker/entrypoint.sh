#!/bin/sh
# Container entrypoint. Runs DB migrations only when asked (the one-shot
# `migrate` compose service sets RUN_MIGRATIONS=1) and never for SQLite, where
# the app creates its own tables for zero-config local use.
set -e
if [ "$RUN_MIGRATIONS" = "1" ]; then
  case "$DATABASE_URL" in
    sqlite*) echo "SQLite database: schema is created by the app, skipping Alembic." ;;
    *) echo "Applying database migrations..."; (cd /app/backend && alembic upgrade head) ;;
  esac
fi
exec "$@"
