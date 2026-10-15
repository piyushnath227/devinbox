# DevInbox runbook

Run commands from the project directory on the server. `rq` commands run inside the worker container
(wrapped in `sh -c` so `$REDIS_URL` is read from the container's environment, not your shell). The queue
is named `devinbox`.

## Quick triage

```bash
docker compose ps                                  # anything unhealthy / restarting?
curl -s https://<domain>/ready | python -m json.tool   # which dependency is failing?
docker compose logs --tail=100 app worker          # JSON logs; look at "level":"error"
```

## "Issues are not being processed"
1. `docker compose ps` — is `worker` running and healthy? `docker compose up -d worker` if not.
2. Queue state: `docker compose exec worker sh -c 'rq info -u "$REDIS_URL"'`. Many queued + 0 workers → worker is down.
3. Webhooks arriving? GitHub repo → *Settings → Webhooks → Recent Deliveries*. 401 = secret mismatch
   (re-save the webhook secret in the dashboard and on GitHub); 5xx/timeouts = check `/ready`.
4. Jobs failing? See next section.

## Failed jobs
Jobs retry twice (30 s, then 120 s) and failed ones are kept for 7 days.
```bash
docker compose exec worker sh -c 'rq info -u "$REDIS_URL"'                          # counts incl. failed
docker compose exec worker sh -c 'rq requeue --all -u "$REDIS_URL" --queue devinbox'   # retry all failed jobs
```
Fix the cause first (bad LLM key, rate limit, GitHub permissions) or they will fail again.
The activity log in the dashboard shows the error for each issue.

## LLM errors or rate limits (429 / timeouts)
* Dashboard → **API Keys** → *Test* on the LLM card shows whether the key/model works.
* Lower `LLM_MAX_CONCURRENCY` and `MAX_JOBS_PER_MINUTE`, add `LLM_FALLBACK_MODELS`, then
  `docker compose up -d` to apply.

## `/ready` returns 503
The JSON says which component is unhealthy: `database` (Postgres down or wrong password),
`redis`, or `schema` ("outdated" → migrations did not run: `docker compose up migrate`).

## Migration failed on deploy
`docker compose logs migrate` shows the Alembic error. The app and workers do not start until it
succeeds. Restore the pre-upgrade backup if the database is in a partial state, deploy the previous
version, and report the migration error.

## Locked out of the dashboard
Rate limiting is 5 failed logins per 15 minutes per IP. Wait it out, or clear it:
`docker compose exec redis redis-cli --scan --pattern 'devinbox:rl:login:*' | xargs -r docker compose exec -T redis redis-cli del`

Forgot the admin password: delete the stored admin credential, then set `ADMIN_BOOTSTRAP_PASSWORD`
**before** restarting so nobody else can claim the instance in between:
```bash
# 1) put a new ADMIN_BOOTSTRAP_PASSWORD in .env  2) then:
docker compose exec postgres psql -U devinbox devinbox -c "DELETE FROM encrypted_secrets WHERE name='admin'"
docker compose up -d app
```

## Rotating `SECRET_KEY`
Changing it invalidates every session and makes stored API keys undecryptable. Plan for it:
update `.env`, restart, log in, and re-enter the LLM / GitHub / storage keys under **API Keys**.

## Restore from backup
```bash
./deploy/restore.sh backups/devinbox-<timestamp>.dump
curl -fsS https://<domain>/ready
```
Requires the same `SECRET_KEY` the backup was taken with.

## Disk filling up
Postgres and the audit archive grow with usage. Check `docker system df` and `du -sh` on the
`devinbox_data` and `devinbox_pg_data` volumes; prune old `backups/` and, if using local audit
storage, move `devinbox_data/audit` to an S3-compatible bucket (change `AUDIT_ARCHIVE_BACKEND`).
