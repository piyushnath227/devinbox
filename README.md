# 🤖 DevInbox — AI-Powered Issue-to-PR Autopilot

> **Hardened release:** read [docs/HARDENING.md](docs/HARDENING.md) first. Jobs now require Redis, a worker, and a dispatcher. Approvals require the full reviewed head SHA.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://github.com/piyushnath227/devinbox/actions/workflows/tests.yml/badge.svg)](https://github.com/piyushnath227/devinbox/actions/workflows/tests.yml)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.143+-teal.svg)](https://fastapi.tiangolo.com)
[![CI](https://img.shields.io/badge/CI-lint%20%7C%20tests%20%7C%20postgres%20%7C%20audit-blue.svg)](.github/workflows/tests.yml)

DevInbox is an open-source, cloud-agnostic AI agent that monitors GitHub repositories, analyzes incoming issues with any OpenAI-compatible LLM (Qwen, Gemini, Groq, OpenRouter, OpenAI, or a local Ollama model), generates code fixes, and opens pull requests — all with human-in-the-loop approval.

**Deploying for real?** Read [docs/PRODUCTION.md](docs/PRODUCTION.md). On-call? [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Features
- 🔍 Intelligent Issue Classification (bug, feature, spam, etc.)
- 🛠️ Real Qwen tool-use — the agent calls `search_repo` / `read_file` mid-reasoning to inspect actual code before proposing a fix, instead of guessing from the issue text alone
- ⚡ Automated Code Generation with unified diffs
- 🔀 Automatic Draft PR Creation
- 👤 Human-in-the-Loop (`/approve` command)
- 🔐 Encrypted API Key Management (Fernet AES-256)
- 🌐 Web Dashboard for zero-code configuration
- 📊 Full Audit Trail of every agent decision — optionally archived to any **S3-compatible bucket** or local disk (see below)
- ♻️ Webhook idempotency guard — safe against GitHub's automatic webhook redelivery

## Quick Start

### Local Development
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The dashboard's Tailwind CSS is pre-compiled to `backend/app/static/css/tailwind.css`, so no Node.js is required to run the app. If you change class names in `backend/app/templates/`, regenerate the stylesheet with:
```bash
cd backend
npm install
npm run build:css
```

### Docker (contributors): SQLite + Redis, no proxy
```bash
docker compose -f docker-compose.dev.yml up --build   # dashboard on http://localhost:8000/dashboard/
```

### Docker (production): Postgres + Redis + workers + automatic HTTPS
```bash
cp .env.example .env        # set SECRET_KEY, POSTGRES_PASSWORD, DOMAIN, ADMIN_BOOTSTRAP_PASSWORD
docker compose up -d --build
docker compose up -d --scale worker=3     # more concurrency = more workers
```
This starts Caddy (HTTPS), the app, Postgres, Redis, a one-shot `migrate` job that applies
Alembic migrations before anything else starts, and the queue workers. Full guide:
[docs/PRODUCTION.md](docs/PRODUCTION.md).

### Local Development (without Redis)
If you run the app directly with `uvicorn` (not Docker) and don't set
`REDIS_URL`, DevInbox falls back to a simple in-process job queue -- no
extra services needed, but queued jobs are lost if the process restarts.
Fine for local development; set `REDIS_URL` for anything closer to production.

### Any Linux server (AWS EC2, GCP, Hetzner, DigitalOcean, a home server, ...)
```bash
chmod +x deploy/docker-host-setup.sh
sudo ./deploy/docker-host-setup.sh      # installs Docker; then follow the production steps above
```

## Usage
1. Open the dashboard at `http://localhost:8000/dashboard/`
2. Log in (with `ADMIN_BOOTSTRAP_PASSWORD` if you set it; otherwise the first visitor creates the admin password)
3. Go to **API Keys**, pick an LLM provider and paste its key, then add your GitHub token
4. Add the webhook URL (`/webhook/github`) to a GitHub repo's webhook settings
5. Create an issue — watch DevInbox classify it, generate a fix, and open a draft PR
6. Comment `/approve` on the PR to merge it

## Audit Archive (cloud-agnostic)

Every classification, generated diff, and PR decision can be archived as JSON/text
for a durable, off-box audit trail. Pick a backend with `AUDIT_ARCHIVE_BACKEND`:

| Value | Behaviour |
|-------|-----------|
| `auto` (default) | Use an S3-compatible bucket if credentials were saved in the dashboard, otherwise local disk (free, no account) |
| `none` | Disabled |
| `local` | Write under `AUDIT_ARCHIVE_LOCAL_DIR` (default `./data/audit`) -- no extra services |
| `s3` | Any S3-compatible store via `boto3`: AWS S3, Cloudflare R2, MinIO, Backblaze B2, Supabase Storage, Alibaba OSS (S3 API), ... |

For S3-compatible stores set `S3_BUCKET`, and `S3_ENDPOINT_URL` for non-AWS providers
(or enter them under dashboard -> **API Keys** -> **S3-Compatible Audit Storage**).
On AWS, leave keys blank and attach an IAM role: boto3's default credential chain is used.
See `backend/app/services/audit_archive/` to add another backend (implement `_put` + `health_check`).

## Known Limitations
- The Redis-backed queue caps concurrency and requests-per-minute globally,
  but merge-conflict resolution still isn't automated — a maintainer must
  fix conflicts manually and comment `/approve` again to retry.
- The `search_repo` tool can now search non-default branches, but only by
  filename (not file contents), since GitHub's content search API only
  indexes the default branch.
- SQLite is for local dev only. Production uses Postgres (the compose stack does this for you);
  schema changes ship as Alembic migrations in `backend/migrations/`.
- Authentication is a single shared admin login. There is no per-user accounts / RBAC yet.
- Authentication to GitHub uses a personal access token; a GitHub App (per-repo installs,
  short-lived tokens) is the planned next step for multi-tenant use.

## Architecture
See `docs/ARCHITECTURE.md` for the full system diagram and component breakdown.

## Project Structure
```
devinbox/
├── backend/
│   ├── app/
│   │   ├── config/        # Settings & encrypted key management
│   │   ├── models/        # SQLAlchemy ORM models
│   │   ├── routes/        # FastAPI route handlers
│   │   ├── services/      # LLM, GitHub, orchestrator, crypto, audit_archive/
│   │   ├── templates/     # Jinja2 dashboard templates
│   │   └── static/        # CSS/JS
│   └── requirements.txt
├── docker/                # Caddyfile (default HTTPS proxy), entrypoint, optional nginx.conf
├── deploy/                # Docker-host setup, backup.sh / restore.sh
├── tests/                 # Test suite
├── docs/                  # Architecture & video script
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## License
MIT — see [LICENSE](LICENSE).

---
