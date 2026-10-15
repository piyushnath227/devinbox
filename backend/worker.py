"""
RQ worker entrypoint. Run as: python backend/worker.py
(matches the Dockerfile's WORKDIR /app + `COPY backend/ ./backend/` layout,
so this file lives at /app/backend/worker.py inside the container.)

Processes notifications from the SQL outbox dispatcher. Redis, the dispatcher,
and at least one worker are required for execution.
"""
import sys
import socket
import structlog

from app.config import get_settings
from app.models.database import init_db

logger = structlog.get_logger()


def configure_logging(json_logs: bool = False):
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def main():
    settings = get_settings()
    configure_logging(json_logs=not settings.DEBUG)

    if not settings.REDIS_URL:
        logger.error(
            "worker_startup_failed",
            message="REDIS_URL is not set. The worker has nothing to do "
            "without Redis. Set REDIS_URL and run the dispatcher too.",
        )
        sys.exit(1)

    try:
        import redis
        from rq import Worker, Queue
    except ImportError:
        logger.error(
            "worker_startup_failed",
            message="redis/rq packages not installed. Add them to requirements.txt.",
        )
        sys.exit(1)

    init_db(settings.DATABASE_URL)

    try:
        conn = redis.from_url(settings.REDIS_URL, socket_connect_timeout=5)
        conn.ping()
    except Exception as e:
        logger.error("worker_startup_failed", message=f"Could not connect to Redis at {settings.REDIS_URL}: {e}")
        sys.exit(1)

    logger.info("worker_starting", redis_configured=True)
    queue = Queue("devinbox", connection=conn)
    worker = Worker([queue], connection=conn, name=socket.gethostname())
    worker.work(with_scheduler=True)  # required: Retry(interval=...) parks jobs in the scheduled registry


if __name__ == "__main__":
    main()
