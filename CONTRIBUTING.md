# Contributing to DevInbox

Thanks for your interest in DevInbox! This project is built to be a genuinely
extensible autopilot agent, not just a hackathon demo — contributions are welcome.

## Getting started

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
pip install -r backend/requirements.txt --break-system-packages  # if using system Python
cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run the test suite before opening a PR:

```bash
pytest tests/ -v
```

CI (`.github/workflows/tests.yml`) runs the same suite on every push and PR.

## Project structure

See the "Project Structure" section in `README.md` and `docs/ARCHITECTURE.md`
for a breakdown of the `config/ / models/ / routes/ / services/` layout.

## How to contribute

1. Fork the repo and create a branch off `main`: `git checkout -b feat/short-description`
2. Make your change, add/update tests under `tests/`
3. Run `pytest tests/ -v` locally
4. Open a PR describing what changed and why

## Reporting issues

Use GitHub Issues. Please include:
- What you expected to happen vs. what actually happened
- Steps to reproduce
- Relevant logs (DevInbox uses `structlog` — logs are structured JSON)

## Ideas for contributions

- Additional Qwen tool-use functions (e.g. `run_tests`, `grep_repo`)
- Support for GitLab/Bitbucket webhooks alongside GitHub
- Alternative audit-trail backends (S3-compatible, Alibaba Cloud SLS)
- Rate-limiting / backpressure handling for high-volume repos

## Code style

- Python 3.12, type hints where practical
- Keep services (`app/services/`) free of FastAPI/route concerns — they should
  be usable standalone or from `tests/`
- Never log API keys or secrets (see `KeyManager` — keys are never logged)

## License

By contributing, you agree your contributions will be licensed under the
project's MIT License (see `LICENSE`).
