"""Every /api/agent/* route spends LLM quota or acts with the owner's GitHub
token, so they must all require a dashboard session."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import agent_router


def test_all_agent_routes_require_authentication():
    app = FastAPI()
    app.include_router(agent_router)
    client = TestClient(app)
    probes = [
        ("post", "/api/agent/test", {"json": {"title": "hello world", "body": "some body text here"}}),
        ("post", "/api/agent/run", {"json": {"repo": "o/r", "issue_number": 1}}),
        ("get", "/api/agent/search-repos", {}),
        ("get", "/api/agent/repo-issues", {}),
        ("get", "/api/agent/discover", {}),
        ("get", "/api/agent/health", {}),
    ]
    for method, path, kwargs in probes:
        r = getattr(client, method)(path, **kwargs)
        assert r.status_code == 401, f"{method.upper()} {path} is reachable without auth ({r.status_code})"
