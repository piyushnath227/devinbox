# Hardened single-installation release — 2026-10-14

This release addresses the code review's correctness, security and durable-processing
findings. It is not a certification of production readiness or a multi-tenant product.

## Behavior changes

- Patches apply against a pinned upstream commit using `git apply --check`, then
  `git apply`. Unchanged lines are preserved. Bounds, strict schemas, safe paths,
  regular-file modes, duplicate targets and Python/JSON syntax are checked.
  Deletes, renames, binaries, workflows and secret-file edits are rejected.
- GitHub receives one tree/commit for the full change set. An unexpected branch
  head stops publication. Retry recovery verifies recovered tree contents, not
  just the commit message. The repository's actual default branch is used.
- A saved publication checkpoint prevents regenerating code on publication retry.
  Deterministic issue branches and PR lookup recover ambiguous GitHub responses.
- Corrupt/unreadable secrets fail closed. Production needs explicit administrator
  bootstrap. Startup salt creation uses atomic storage operations.
- GitHub text is escaped in dashboard cards, links are restricted to HTTPS GitHub,
  and user-supplied repository values no longer become inline event handlers.
- Signed webhook deliveries are persisted with jobs, with unique delivery IDs,
  one active job per issue and database issue uniqueness. Webhooks require the
  `devinbox` label by default (`AGENT_REQUIRED_LABEL`). Manual admin runs are an
  explicit opt-in and bypass the label requirement.
- Manual runs return HTTP 202 with `job_id`; the dashboard polls job status.
  Redis outages leave work in SQL; there is no in-process execution fallback.
- SQL controls three total attempts with 30/120-second retry backoff. RQ enforces
  a 600-second timeout. Dispatch and execution leases last 900 seconds; execution
  renews the lease when claimed, so time spent queued does not consume it.
- Admission is globally bounded by `MAX_JOBS_PER_MINUTE` using SQL counters.
- Drafts must be marked ready in GitHub. Maintainers approve with
  `/approve <full-40-character-head-sha>` on its own line. New commits invalidate
  old approvals. Merge pins that SHA and requires successful checks; branch
  protection must define the checks and reviews required by your repository.
- Merge failures are recorded as failures and retried within the attempt budget.
  After exhaustion, resolve the cause and replay the failed job or post a new
  approval comment. Do not expect CI-completion webhooks to trigger a merge.
- Worker health is checked per container; the dispatcher has its own heartbeat.

## Deploy or upgrade

1. Back up the database and keep the existing encryption `SECRET_KEY` separately.
2. Set `SECRET_KEY`, `POSTGRES_PASSWORD`, `DOMAIN`, and a strong
   `ADMIN_BOOTSTRAP_PASSWORD` in `.env`; configure GitHub and LLM credentials.
3. Run `docker compose up -d --build`. Services now include **dispatcher**, **worker**,
   **app**, **migrate**, **postgres**, **redis** and **caddy**. Do not omit dispatcher.
4. Migration `0002` adds jobs, webhook deliveries, admission counters, checkpoints
   and the issue uniqueness constraint. Existing duplicate issue records deliberately
   block migration. Reconcile their PRs and activity-log ownership before deleting
   redundant records; no automatic deletion is attempted.
5. Verify `/ready`, dispatcher/worker health, then use a private staging repository
   with the `devinbox` label and test a full issue → draft → review → CI → approval flow.

For a fresh local database use `docker compose -f docker-compose.dev.yml up --build`.
Outside Docker run Redis, `uvicorn app.main:app` from `backend`, and separately run
`python backend/worker.py` and `python backend/dispatcher.py` from the project root,
all with the same DATABASE_URL, REDIS_URL and SECRET_KEY. Install Git on the host.
Existing SQLite databases are not upgraded by `create_all`: back up first, use
Alembic to upgrade a migration-managed database. An unversioned database must be
verified to match revision 0001 before `alembic stamp 0001` and `alembic upgrade head`.
Never stamp a database whose schema has not been checked.

## Diagnose and replay

Authenticated endpoints:

- `GET /api/agent/jobs/{job_id}` returns status, attempts, result and generic error.
- `POST /api/agent/jobs/{job_id}/retry` replays a failed job as a new job.

Inspect activity logs for stage details and server logs for exceptions. SQL is the
source of truth: an RQ notification may be marked failed while SQL has scheduled
its next attempt. Do not rely on the RQ failed registry alone. Pending jobs after
an outage resume when Redis, dispatcher and a worker are available. A lost worker
is recovered after lease expiry, up to the execution attempt budget.

## Scope and remaining work

- Generated code is **not executed in an isolated sandbox** before PR creation.
  Static checks only cover Python/JSON; other languages need repository CI. Model
  confidence and model-written testing notes are not evidence of passed tests.
- No multi-tenant isolation, memberships, tenant credentials, billing, per-repository
  budgets, distributed LLM semaphore, cancellation UI or scheduled retention cleanup.
  `LLM_MAX_CONCURRENCY` is process-local; worker count bounds fleet concurrency.
- Delivery/job/checkpoint tables need a retention policy as volume grows. Keep
  delivery IDs long enough for your redelivery window. Audit archival is best-effort.
- `/ready` checks dependencies/schema, not end-to-end execution; monitor queue age,
  dispatcher heartbeat, individual worker heartbeats and failed job counts separately.
- PostgreSQL/Redis integration, Docker startup, live provider calls and a live GitHub
  round trip still need staging validation. Local automated tests use SQLite and
  mocked external services; they do not establish production fault tolerance.
- Legacy docs describe older optional queues and plain `/approve`; this document
  takes precedence for the hardened release.

## Local verification results

- `python -m pytest -q tests`: **123 passed**, one upstream Starlette/httpx
  deprecation warning.
- `ruff check .`: passed.
- Tests include patch preservation/rejection, corrupt secrets, duplicate and
  concurrent admission, lease renewal/recovery, atomic publication failure,
  checkpoint recovery, changed recovered commits, SHA-bound approvals and CI gates.
- Alembic upgrade/downgrade and model/schema comparison passed against SQLite.
- Production Compose YAML passes duplicate-key and service-dependency validation.
  Docker itself was not run.
