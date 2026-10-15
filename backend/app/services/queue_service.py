"""Redis/RQ transport. SQL jobs are the durable source of truth.

The dispatcher retries unavailable connections; request handlers never fall back
 to in-process execution. Legacy enqueue helpers remain for compatibility only.
"""
import time
from typing import Optional
import structlog

logger = structlog.get_logger()

_redis_conn = None
_queue = None


def _job_policy() -> dict:
    """Transient failures (GitHub 5xx, LLM 429/timeouts, dropped DB connection)
    are retried with backoff; failed jobs are kept 7 days so they can be
    inspected / requeued (`rq info`, rq-dashboard) instead of vanishing."""
    try:
        from rq import Retry
        return {
            "retry": Retry(max=2, interval=[30, 120]),
            "failure_ttl": 7 * 24 * 3600,
            "result_ttl": 3600,
        }
    except Exception:  # pragma: no cover
        return {}


_JOB_POLICY = _job_policy()
_connection_attempted = False
_next_connect_at = 0.0


def _get_queue():
    """Lazily connect to Redis and build the RQ Queue. Returns None if
    REDIS_URL isn't configured or the connection fails -- never raises."""
    global _redis_conn, _queue, _connection_attempted, _next_connect_at

    if _queue is not None:
        return _queue
    if time.monotonic() < _next_connect_at:
        # Bound reconnect attempts during an outage.
        return None

    _connection_attempted = True
    _next_connect_at = time.monotonic() + 5

    from ..config import get_settings
    settings = get_settings()

    if not settings.REDIS_URL:
        return None

    try:
        import redis
        from rq import Queue

        _redis_conn = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        _redis_conn.ping()
        _queue = Queue("devinbox", connection=_redis_conn)
        logger.info("queue_service_connected")
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
            **_JOB_POLICY,
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
            **_JOB_POLICY,
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
