"""Webhook authentication + /approve authorization tests."""

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import webhook as webhook_module
from app.services.github_service import GitHubService

SECRET = "s3cret"


def _sig(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(monkeypatch, test_db):
    app = FastAPI()
    app.include_router(webhook_module.router)
    app.dependency_overrides[webhook_module.get_db] = lambda: test_db
    settings = SimpleNamespace(REQUIRE_WEBHOOK_SIGNATURE=True, WEBHOOK_SECRET=SECRET)
    km = MagicMock(); km.get_webhook_secret.return_value = None
    monkeypatch.setattr(webhook_module, "get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "get_key_manager", lambda: km)
    return TestClient(app), settings, km


def test_unsigned_request_rejected(client):
    c, _, _ = client
    r = c.post("/webhook/github", content=b"{}", headers={"X-GitHub-Event": "ping"})
    assert r.status_code == 401


def test_wrong_signature_rejected(client):
    c, _, _ = client
    body = b"{}"
    r = c.post("/webhook/github", content=body, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sig(body, "other")})
    assert r.status_code == 401


def test_valid_signature_accepted(client):
    c, _, _ = client
    body = json.dumps({"zen": "hi"}).encode()
    r = c.post("/webhook/github", content=body, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sig(body)})
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_server_without_secret_fails_closed(client):
    c, settings, _ = client
    settings.WEBHOOK_SECRET = ""
    body = b"{}"
    r = c.post("/webhook/github", content=body, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sig(body)})
    assert r.status_code == 503


def test_dashboard_secret_overrides_env(client):
    c, _, km = client
    km.get_webhook_secret.return_value = "from-dashboard"
    body = b"{}"
    ok = c.post("/webhook/github", content=body, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sig(body, "from-dashboard")})
    assert ok.status_code == 200


def test_invalid_json_with_valid_signature_is_400(client):
    c, _, _ = client
    body = b"not json"
    r = c.post("/webhook/github", content=body, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": _sig(body)})
    assert r.status_code == 400


# ---------- /approve authorization ----------
def _gh(comments, permissions):
    svc = GitHubService.__new__(GitHubService)  # skip network auth in __init__
    svc.authenticated_user = "devinbox-bot"
    repo = MagicMock()
    pr = MagicMock()
    pr.head.sha = "a" * 40
    pr.get_issue_comments.return_value = [
        SimpleNamespace(body=b, user=SimpleNamespace(login=u)) for u, b in comments
    ]
    repo.get_pull.return_value = pr
    repo.get_collaborator_permission.side_effect = lambda login: permissions.get(login, "none")
    svc.get_repository = lambda name: repo
    return svc


def test_approve_from_stranger_is_ignored():
    svc = _gh([("random-user", "/approve")], {"random-user": "read"})
    assert svc.check_for_approval("o/r", 1)["approved"] is False


def test_approve_from_maintainer_counts():
    svc = _gh([("random-user", "/approve"), ("maint", "LGTM\n/approve " + "a" * 40)], {"maint": "write"})
    assert svc.check_for_approval("o/r", 1) == {"approved": True, "approved_by": "maint", "head_sha": "a" * 40}


def test_quoted_approve_in_prose_does_not_count():
    svc = _gh([("maint", "please don't /approve this yet")], {"maint": "admin"})
    assert svc.check_for_approval("o/r", 1)["approved"] is False


def test_bot_cannot_approve_itself():
    svc = _gh([("devinbox-bot", "/approve")], {"devinbox-bot": "admin"})
    assert svc.check_for_approval("o/r", 1)["approved"] is False
