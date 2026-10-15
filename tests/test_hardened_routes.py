import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.jobs import Job
from app.routes import webhook, agent, dashboard
from app.config.key_manager import SecretReadError


@pytest.fixture
def routes(monkeypatch):
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    settings = SimpleNamespace(REQUIRE_WEBHOOK_SIGNATURE=True, WEBHOOK_SECRET='secret', MAX_JOBS_PER_MINUTE=20,
                               MAX_WEBHOOK_BYTES=4096, AGENT_REQUIRED_LABEL='devinbox', DEBUG=False, TRUSTED_PROXY_IPS='')
    km = MagicMock(); km.get_webhook_secret.return_value = None
    for module in (webhook, agent, dashboard):
        monkeypatch.setattr(module, 'get_settings', lambda: settings)
        monkeypatch.setattr(module, 'get_key_manager', lambda: km)
    app = FastAPI(); app.include_router(webhook.router); app.include_router(agent.router); app.include_router(dashboard.router)
    app.dependency_overrides[webhook.get_db] = lambda: db
    app.dependency_overrides[dashboard.get_current_user] = lambda: {'user': 'admin'}
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, db, km
    db.close(); engine.dispose()


def send(client, payload, delivery='d1'):
    body = json.dumps(payload).encode()
    signature = hmac.new(b'secret', body, hashlib.sha256).hexdigest()
    return client.post('/webhook/github', content=body, headers={
        'X-Hub-Signature-256': 'sha256=' + signature, 'X-GitHub-Event': 'issues', 'X-GitHub-Delivery': delivery})


def payload(label=True):
    return {'repository': {'full_name': 'o/r'}, 'action': 'opened',
            'issue': {'number': 1, 'labels': [{'name': 'devinbox'}] if label else []}}


def test_signed_delivery_is_durable_without_redis(routes):
    c, db, _ = routes
    a = send(c, payload()); b = send(c, payload())
    assert a.status_code == b.status_code == 202
    assert a.json()['job_id'] == b.json()['job_id']
    assert db.query(Job).count() == 1


def test_opt_in_and_payload_limits(routes):
    c, db, _ = routes
    assert send(c, payload(False)).json()['status'] == 'ignored'
    assert send(c, payload(), delivery='').status_code == 400
    assert c.post('/webhook/github', content=b'x' * 4097).status_code == 413
    assert db.query(Job).count() == 0


def test_manual_run_returns_queued_status(routes):
    c, db, _ = routes
    response = c.post('/api/agent/run', json={'repo': 'o/r', 'issue_number': 1})
    assert response.status_code == 202
    job_id = response.json()['job_id']
    assert c.get('/api/agent/jobs/' + job_id).json()['status'] == 'pending'
    assert db.query(Job).count() == 1


def test_production_missing_admin_never_sets_cookie(routes, monkeypatch):
    c, _, km = routes
    monkeypatch.setattr(dashboard, '_login_rate_limited', lambda _: False)
    monkeypatch.setattr(dashboard, 'get_crypto_service', MagicMock())
    km.get_admin_password_hash.return_value = None
    response = c.post('/dashboard/api/auth/login', data={'password': 'attacker-password'})
    assert response.status_code == 503 and 'set-cookie' not in response.headers
    km.save_admin_password_hash.assert_not_called()


def test_unreadable_admin_secret_never_sets_cookie(routes, monkeypatch):
    c, _, km = routes
    monkeypatch.setattr(dashboard, '_login_rate_limited', lambda _: False)
    monkeypatch.setattr(dashboard, 'get_crypto_service', MagicMock())
    km.get_admin_password_hash.side_effect = SecretReadError('unreadable')
    response = c.post('/dashboard/api/auth/login', data={'password': 'attacker-password'})
    assert response.status_code >= 500 and 'set-cookie' not in response.headers
    km.save_admin_password_hash.assert_not_called()
