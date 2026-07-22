"""
Redis/RQ job queue for webhook-triggered processing.

This is an optional upgrade over FastAPI BackgroundTasks: jobs survive a
server restart (they stay in Redis until a worker picks them up), and
you can scale horizontally with `docker compose up -d --scale worker=3`.

Design principle: every function here fails soft. If REDIS_URL isn't
set, or Redis is unreachable, enqueue_job() returns None and the caller
(webhook.py) falls back to BackgroundTasks -- so local dev and any
deployment without Redis still works with zero extra services.
"""
from typing import Optional
import structlog

logger = structlog.get_logger()

_redis_conn = None
_queue = None
_connection_attempted = False


def _get_queue():
    """Lazily connect to Redis and build the RQ Queue. Returns None if
    REDIS_URL isn't configured or the connection fails -- never raises."""
    global _redis_conn, _queue, _connection_attempted

    if _queue is not None:
        return _queue
    if _connection_attempted:
        # Already tried and failed this process lifetime; don't retry
        # on every single webhook call.
        return None

    _connection_attempted = True

    from ..config import get_settings
    settings = get_settings()

    if not settings.REDIS_URL:
        return None

    try:
        import redis
        from rq import Queue

        _redis_conn = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        _redis_conn.ping()
        _queue = Queue("devinbox", connection=_redis_conn)
        logger.info("queue_service_connected", redis_url=settings.REDIS_URL)
        return _queue
    except Exception as e:
        logger.warning("queue_service_unavailable", error=str(e))
        return None


def enqueue_issue_job(repo_full_name: str, issue_number: int, title: str,
                       body: str, author: str, labels: list) -> Optional[str]:
    """Enqueue issue processing. Returns the RQ job ID on success, or
    None if the queue isn't available (caller should fall back)."""
    queue = _get_queue()
    if queue is None:
        return None
    try:
        job = queue.enqueue(
            "app.worker_jobs.process_issue_job",
            repo_full_name, issue_number, title, body, author, labels,
            job_timeout="10m",
        )
        logger.info("job_enqueued", job_id=job.id, repo=repo_full_name, issue=issue_number)
        return job.id
    except Exception as e:
        logger.warning("job_enqueue_failed", error=str(e))
        return None


def enqueue_merge_check_job(repo_full_name: str) -> Optional[str]:
    """Enqueue a merge-check job. Returns the RQ job ID, or None if the
    queue isn't available."""
    queue = _get_queue()
    if queue is None:
        return None
    try:
        job = queue.enqueue(
            "app.worker_jobs.check_and_merge_job",
            repo_full_name,
            job_timeout="5m",
        )
        logger.info("merge_check_job_enqueued", job_id=job.id, repo=repo_full_name)
        return job.id
    except Exception as e:
        logger.warning("merge_check_job_enqueue_failed", error=str(e))
        return None


def is_queue_available() -> bool:
    """Cheap check for whether the queue is usable right now, e.g. for
    a dashboard status indicator."""
    return _get_queue() is not None
