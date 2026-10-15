# Running DevInbox in production

## What you are deploying

```
GitHub ──webhook──▶ Caddy (HTTPS) ──▶ app (FastAPI, N uvicorn workers)
                                         │  enqueue            ▲ /ready
                                         ▼                     │
                                       Redis ◀── RQ workers (scale with --scale worker=N)
                                         │
                       Postgres  ◀───────┴──── issues, activity log, encrypted API keys
                       Audit archive (local disk | S3-compatible bucket)
```

* **Postgres** is the system of record, including your encrypted API keys, so every
  container sees the same state and one `pg_dump` backs up everything.
* **Redis** carries execution notifications (SQL owns durable jobs and retry state) and
  the shared login rate-limit counters.
* **Schema** is owned by Alembic. A one-shot `migrate` container runs `alembic upgrade head`
  before the app and workers start; the app refuses to boot against an unmigrated database.

## First deploy (any Linux VPS with Docker)

1. Point a DNS **A record** for your domain at the server; open ports 80 and 443.
2. `cp .env.example .env` and set:
   * `SECRET_KEY` — `python -c "import secrets; print(secrets.token_hex(32))"`
   * `POSTGRES_PASSWORD` — letters/digits only, e.g. `openssl rand -hex 24`
   * `DOMAIN` — e.g. `devinbox.example.com`
   * `ADMIN_BOOTSTRAP_PASSWORD` — 12+ characters. **Do not skip this on a public server:** without
     it, production startup refuses to initialize the administrator.
3. `docker compose up -d --build`
4. Check `https://<domain>/ready` returns `{"status":"ready", ...}`.
5. Log in → **API Keys** → choose an LLM provider and paste its key; add your GitHub token and
   webhook secret.
6. In each GitHub repo: *Settings → Webhooks → Add webhook* → Payload URL
   `https://<domain>/webhook/github`, content type `application/json`, the same secret, event
   **Issues** and **Issue comments**. Add the `devinbox` label to opt an issue in. Deliveries with a bad signature are rejected.

## Free / low-cost choices

| Component | Zero-cost option | Notes |
|---|---|---|
| LLM | Gemini or Groq free tiers, or a local **Ollama** model | Free tiers are rate limited; add `LLM_FALLBACK_MODELS` and keep `LLM_MAX_CONCURRENCY` low. Check each provider's current terms. |
| Database | The bundled Postgres container, or a managed free-tier Postgres via `DATABASE_URL` | Supabase's connection string works as-is (`postgresql://…` is normalised for you). |
| Audit archive | Local disk (default) | Add Supabase Storage / R2 / MinIO later by changing env vars only. |
| Server | Any small VPS | Need ~1 GB RAM for the full stack. Verify current free-tier terms with your host. |

## Configuration reference

All settings are in `.env.example`, grouped and commented. The ones that matter most in production:
`SECRET_KEY`, `POSTGRES_PASSWORD`, `DOMAIN`, `ADMIN_BOOTSTRAP_PASSWORD`, `LLM_*` (or the dashboard),
`MAX_JOBS_PER_MINUTE`, `LLM_MAX_CONCURRENCY`, `AUDIT_ARCHIVE_BACKEND`.

## Scaling

| Symptom | Lever |
|---|---|
| Queue depth grows | `docker compose up -d --scale worker=N` (each worker handles one job at a time) |
| LLM 429s / timeouts | Reduce worker count, set `LLM_FALLBACK_MODELS`, lower global `MAX_JOBS_PER_MINUTE`; LLM concurrency is per process |
| Web latency | Raise `WEB_CONCURRENCY` (uvicorn workers per app container) or run more app replicas |
| Multiple hosts | Workers on other machines need only `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`: keys are in Postgres, so no shared disk |

Run `migrate` from a single place: with several app replicas, keep the one-shot `migrate` service
(or run `alembic upgrade head` once in CI/CD) rather than migrating from every container.

## Upgrades

```bash
git pull
./deploy/backup.sh                       # always back up first
docker compose up -d --build             # `migrate` applies new migrations, then app/workers restart
curl -fsS https://<domain>/ready
```
Workers get a 660 s grace period to finish the current job. Roll back by checking out the previous
tag and, if a migration must be reversed, `docker compose run --rm -w /app/backend -e RUN_MIGRATIONS=0 migrate alembic downgrade -1`
**before** deploying the older code.

## Backups

`deploy/backup.sh` writes a compressed `pg_dump` and keeps the 14 newest; schedule it with cron
and copy the files off the server. `deploy/restore.sh <file>` restores into the running stack.
**Also store `SECRET_KEY` separately** (password manager): stored API keys are encrypted with it,
so a database restore without the original key means re-entering your keys. Test a restore
before you need one.

## Security checklist

- [ ] `ADMIN_BOOTSTRAP_PASSWORD` set; `DEBUG=false`
- [ ] Strong, unique `SECRET_KEY` and `POSTGRES_PASSWORD`; `.env` is not committed
- [ ] Only ports 80/443 are public (Postgres, Redis and the app are not published by the compose file)
- [ ] GitHub webhook secret configured on both sides
- [ ] GitHub token has the minimum scopes (repo access only for repos you want covered)
- [ ] Weekly dependency audit (`CI → dependency-audit`) is green; Dependabot PRs are reviewed
- [ ] Backups run and one restore has been tested
- [ ] Merges still require a human `/approve <full-head-sha>`

## Observability

* Logs are JSON on stdout (`docker compose logs -f app worker`) — ship them to any log tool.
* `/health` — process is up (container healthcheck). `/ready` — database, schema version and Redis
  are all fine (use this for load balancers and uptime monitors; returns 503 otherwise).
* Failed jobs: see [RUNBOOK.md](RUNBOOK.md).

## Hardened release

Read [HARDENING.md](HARDENING.md) before upgrading: it documents migration 0002,
the required dispatcher, retry recovery, new approvals, and remaining limitations.
