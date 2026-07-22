"""
RQ worker entrypoint. Run as: python backend/worker.py
(matches the Dockerfile's WORKDIR /app + `COPY backend/ ./backend/` layout,
so this file lives at /app/backend/worker.py inside the container.)

Processes jobs enqueued by the webhook handler via queue_service.py.
If REDIS_URL isn't set, this exits immediately with a clear error --
it has nothing to do without Redis, unlike the main app which degrades
gracefully to BackgroundTasks on its own.
"""
import sys
import structlog

from app.config import get_settings
from app.models.database import init_db

logger = structlog.get_logger()


def configure_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def main():
    configure_logging()
    settings = get_settings()

    if not settings.REDIS_URL:
        logger.error(
            "worker_startup_failed",
            message="REDIS_URL is not set. The worker has nothing to do "
            "without Redis -- either set REDIS_URL, or if you don't need "
            "the queue, don't run this service at all (the main app works "
            "fine on its own via BackgroundTasks).",
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

    logger.info("worker_starting", redis_url=settings.REDIS_URL)
    queue = Queue("devinbox", connection=conn)
    worker = Worker([queue], connection=conn)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
