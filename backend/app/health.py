"""Liveness vs readiness.

/health  -> process is up (cheap; used by container HEALTHCHECK)
/ready   -> dependencies work (DB reachable + schema current, Redis if configured).
            Load balancers and `depends_on: condition: service_healthy` should use this.
"""

from pathlib import Path
from typing import Any, Dict, Tuple

import structlog
from sqlalchemy import text

logger = structlog.get_logger()

BACKEND_DIR = Path(__file__).resolve().parent.parent


def check_database() -> Dict[str, Any]:
    from .models.database import get_engine
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)[:200]}


def check_redis(redis_url: str) -> Dict[str, Any]:
    if not redis_url:
        return {"status": "not_configured"}
    try:
        import redis
        redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)[:200]}


def schema_status() -> Dict[str, Any]:
    """Compare the DB's Alembic revision with the code's head revision."""
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from .models.database import get_engine

        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
        with get_engine().connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        return {"status": "healthy" if current == head else "outdated", "current": current, "head": head}
    except Exception as e:
        return {"status": "unknown", "error": str(e)[:200]}


def verify_schema_current_or_raise() -> None:
    """Fail fast at startup if Postgres has not been migrated -- a clear error
    now beats a 500 on the first webhook."""
    status = schema_status()
    if status["status"] != "healthy":
        raise RuntimeError(
            f"Database schema is at {status.get('current')!r} but the code expects {status.get('head')!r}. "
            "Run `alembic upgrade head` (the Docker image does this on start)."
        )


def readiness(redis_url: str, check_schema: bool = False) -> Tuple[bool, Dict[str, Any]]:
    components: Dict[str, Any] = {"database": check_database(), "redis": check_redis(redis_url)}
    if check_schema:
        components["schema"] = schema_status()
    ok = all(c["status"] in ("healthy", "not_configured") for c in components.values())
    return ok, components
