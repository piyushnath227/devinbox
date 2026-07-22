# 🤖 DevInbox — AI-Powered Issue-to-PR Autopilot

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://github.com/piyushnath227/devinbox/actions/workflows/tests.yml/badge.svg)](https://github.com/piyushnath227/devinbox/actions/workflows/tests.yml)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal.svg)](https://fastapi.tiangolo.com)
[![Qwen Cloud](https://img.shields.io/badge/Qwen%20Cloud-API-purple.svg)](https://qwencloud.com)

**Track 4: Autopilot Agent — Global AI Hackathon with Qwen Cloud**

DevInbox is an autonomous AI agent that monitors GitHub repositories, analyzes incoming issues using Qwen Cloud's advanced reasoning, generates code fixes, and creates pull requests — all with human-in-the-loop approval.

## Features
- 🔍 Intelligent Issue Classification (bug, feature, spam, etc.)
- 🛠️ Real Qwen tool-use — the agent calls `search_repo` / `read_file` mid-reasoning to inspect actual code before proposing a fix, instead of guessing from the issue text alone
- ⚡ Automated Code Generation with unified diffs
- 🔀 Automatic Draft PR Creation
- 👤 Human-in-the-Loop (`/approve` command)
- 🔐 Encrypted API Key Management (Fernet AES-256)
- 🌐 Web Dashboard for zero-code configuration
- 📊 Full Audit Trail of every agent decision — optionally archived to **Alibaba Cloud OSS** (see below)
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

### Docker Deployment
```bash
docker compose up -d --build
```
This starts three containers: the web app, a Redis instance, and one worker
process that consumes the issue-processing queue. To handle more concurrent
issues, scale the worker instead of raising a single worker's concurrency:
```bash
docker compose up -d --scale worker=3
```

### Local Development (without Redis)
If you run the app directly with `uvicorn` (not Docker) and don't set
`REDIS_URL`, DevInbox falls back to a simple in-process job queue -- no
extra services needed, but queued jobs are lost if the process restarts.
Fine for local development; set `REDIS_URL` for anything closer to production.

### Alibaba Cloud ECS Deployment
```bash
chmod +x deploy/alibaba-cloud-setup.sh
sudo ./deploy/alibaba-cloud-setup.sh
```

## Usage
1. Open the dashboard at `http://localhost:8000/dashboard/`
2. Create your admin password on first run
3. Go to **API Keys** and paste your Qwen Cloud key + GitHub token
4. Add the webhook URL (`/webhook/github`) to a GitHub repo's webhook settings
5. Create an issue — watch DevInbox classify it, generate a fix, and open a draft PR
6. Comment `/approve` on the PR to merge it

## Alibaba Cloud Integration
DevInbox archives its full audit trail — issue classifications, generated diffs,
and PR decisions — to **Alibaba Cloud OSS (Object Storage Service)** using the
official `oss2` SDK. This is real, functional use of an Alibaba Cloud service
from application code (see `backend/app/services/alibaba_oss_service.py`),
not just container hosting on ECS.

To enable it:
1. Create an OSS bucket in the Alibaba Cloud console (e.g. `ap-southeast-1`)
2. Create a RAM user with `AliyunOSSFullAccess` and generate an AccessKey pair
3. Open the dashboard → **API Keys** → **Alibaba Cloud OSS**, and enter the AccessKey ID/Secret, endpoint, and bucket name

OSS archival is optional — DevInbox runs fine without it, using SQLite as the
primary audit-trail store. When configured, every classification and diff is
also written to OSS as JSON for durable, off-box compliance/audit records.

## Known Limitations
- The Redis-backed queue caps concurrency and requests-per-minute globally,
  but merge-conflict resolution still isn't automated — a maintainer must
  fix conflicts manually and comment `/approve` again to retry.
- The `search_repo` tool can now search non-default branches, but only by
  filename (not file contents), since GitHub's content search API only
  indexes the default branch.
- The SQLite default (`DATABASE_URL`) is fine for a single-server/demo
  setup; for real production use with multiple app/worker instances,
  switch to PostgreSQL.

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
│   │   ├── services/      # Qwen, GitHub, orchestrator, crypto
│   │   ├── templates/     # Jinja2 dashboard templates
│   │   └── static/        # CSS/JS
│   └── requirements.txt
├── docker/                # Nginx config
├── deploy/                # Alibaba Cloud deployment script
├── tests/                 # Test suite
├── docs/                  # Architecture & video script
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## License
MIT — see [LICENSE](LICENSE).

---
Built for the Global AI Hackathon with Qwen Cloud — Track 4: Autopilot Agent.
