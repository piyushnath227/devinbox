"""Production-readiness building blocks: DB URL handling, DB-backed secrets,
Alembic migrations, readiness checks, shared rate limiting, queue retry policy."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.config.key_manager import KeyManager
from app.config.secret_store import DbSecretStore, FileSecretStore, build_secret_store
from app.health import check_redis, readiness
from app.models import Base
from app.models import database as dbmod
from app.models.database import init_db, is_sqlite, normalize_database_url
from app.services.rate_limiter import FailureRateLimiter

BACKEND = Path(__file__).resolve().parent.parent / "backend"


@pytest.fixture
def sqlite_app_db(tmp_path):
    """Initialise the module-level engine on a temp SQLite file, then restore."""
    saved = (dbmod._engine, dbmod._SessionLocal)
    init_db(f"sqlite:///{tmp_path}/app.db")
    yield
    dbmod._engine, dbmod._SessionLocal = saved


# ---------- database URL handling ----------
@pytest.mark.parametrize("raw,expected", [
    ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ("postgresql://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ("sqlite:///./x.db", "sqlite:///./x.db"),
])
def test_normalize_database_url(raw, expected):
    assert normalize_database_url(raw) == expected


def test_is_sqlite():
    assert is_sqlite("sqlite:///x.db") and not is_sqlite("postgresql://h/d")


# ---------- secret stores ----------
def test_file_store_roundtrip_and_delete(tmp_path):
    s = FileSecretStore(str(tmp_path))
    assert s.get("llm") is None
    s.put("llm", b"blob")
    assert s.get("llm") == b"blob" and (tmp_path / "llm.enc").exists()
    s.put(".salt", b"salt")
    assert (tmp_path / ".salt").exists()  # historical salt filename preserved
    s.delete("llm")
    assert s.get("llm") is None


def test_db_store_roundtrip_overwrite_delete(sqlite_app_db):
    s = DbSecretStore()
    assert s.get("github") is None
    s.put("github", b"v1")
    s.put("github", b"v2")
    assert s.get("github") == b"v2"
    s.delete("github")
    assert s.get("github") is None


def test_db_store_imports_legacy_files_once_and_delete_does_not_resurrect(sqlite_app_db, tmp_path):
    legacy = FileSecretStore(str(tmp_path))
    legacy.put("github", b"legacy-blob")
    s = DbSecretStore(fallback=legacy)
    assert s.get("github") == b"legacy-blob"
    assert DbSecretStore().get("github") == b"legacy-blob"  # now lives in the DB
    s.delete("github")
    assert s.get("github") is None and legacy.get("github") is None


def test_two_hosts_share_secrets_through_db(sqlite_app_db, tmp_path):
    a = KeyManager(str(tmp_path / "hostA"), "secret", store=DbSecretStore())
    b = KeyManager(str(tmp_path / "hostB"), "secret", store=DbSecretStore())  # no key files on this host
    a.save_keys("github", {"token": "ghp_x", "webhook_secret": "w"})
    assert b.get_github_token() == "ghp_x" and b.get_webhook_secret() == "w"


def test_build_secret_store_selection(tmp_path):
    mk = lambda **kw: SimpleNamespace(**{"SECRETS_BACKEND": "auto", "KEYS_DIR": str(tmp_path), **kw})
    assert isinstance(build_secret_store(mk(DATABASE_URL="sqlite:///x.db")), FileSecretStore)
    assert isinstance(build_secret_store(mk(DATABASE_URL="postgresql://h/d")), DbSecretStore)
    assert isinstance(build_secret_store(mk(DATABASE_URL="sqlite:///x.db", SECRETS_BACKEND="db")), DbSecretStore)
    assert isinstance(build_secret_store(mk(DATABASE_URL="postgresql://h/d", SECRETS_BACKEND="files")), FileSecretStore)


# ---------- migrations ----------
def _alembic(db_url, *args):
    env = {**os.environ, "DATABASE_URL": db_url, "SECRET_KEY": "x"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                          capture_output=True, text=True)


def test_migrations_upgrade_downgrade_and_match_models(tmp_path):
    url = f"sqlite:///{tmp_path}/mig.db"
    assert _alembic(url, "upgrade", "head").returncode == 0
    engine = create_engine(url)
    assert {"issue_records", "activity_logs", "api_key_configs", "encrypted_secrets"} <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models drifted from migrations -- run `alembic revision --autogenerate`: {diff}"
    assert _alembic(url, "downgrade", "base").returncode == 0
    assert _alembic(url, "upgrade", "head").returncode == 0


# ---------- readiness ----------
def test_readiness_without_redis(sqlite_app_db):
    ok, c = readiness("")
    assert ok and c["database"]["status"] == "healthy" and c["redis"]["status"] == "not_configured"


def test_readiness_fails_when_redis_unreachable(sqlite_app_db):
    ok, c = readiness("redis://127.0.0.1:1/0")  # nothing listens on port 1
    assert not ok and c["redis"]["status"] == "unhealthy"
    assert check_redis("")["status"] == "not_configured"


# ---------- rate limiter ----------
class FakeRedis:
    def __init__(self): self.d, self.ttl = {}, {}
    def get(self, k): return self.d.get(k)
    def incr(self, k): self.d[k] = int(self.d.get(k, 0)) + 1; return self.d[k]
    def expire(self, k, s): self.ttl[k] = s
    def delete(self, k): self.d.pop(k, None)


class BrokenRedis:
    def get(self, k): raise ConnectionError("down")
    incr = expire = delete = get


def test_limiter_blocks_at_limit_and_is_shared_between_instances():
    r = FakeRedis()
    a, b = FailureRateLimiter("login", 3, 900, r), FailureRateLimiter("login", 3, 900, r)  # two "workers"
    for _ in range(2): a.record_failure("ip")
    assert not b.is_limited("ip")
    b.record_failure("ip")
    assert a.is_limited("ip") and not a.is_limited("other-ip")
    assert r.ttl["devinbox:rl:login:ip"] == 900  # window set on first failure
    a.reset("ip")
    assert not b.is_limited("ip")


def test_limiter_degrades_to_memory_when_redis_down():
    lim = FailureRateLimiter("login", 2, 900, BrokenRedis())
    lim.record_failure("ip"); lim.record_failure("ip")
    assert lim.is_limited("ip")  # no crash, still limits (per-process)


def test_limiter_memory_only_mode():
    lim = FailureRateLimiter("x", 1, 900)
    assert not lim.is_limited("k"); lim.record_failure("k"); assert lim.is_limited("k")


# ---------- queue policy ----------
def test_queue_jobs_retry_with_backoff_and_keep_failures():
    from app.services import queue_service
    p = queue_service._JOB_POLICY
    assert p["retry"].max == 2 and p["retry"].intervals == [30, 120]
    assert p["failure_ttl"] == 7 * 24 * 3600
