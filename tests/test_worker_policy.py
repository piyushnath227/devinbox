"""Guards the contract between enqueue-time retry policy and the worker process."""

from pathlib import Path

from app.services import queue_service

WORKER = Path(__file__).resolve().parent.parent / "backend" / "worker.py"


def test_worker_runs_the_rq_scheduler():
    """Retry(interval=...) parks a failed job in RQ's scheduled registry; only a worker started
    with the scheduler moves it back to the queue. Without this, retried jobs are stranded forever."""
    assert "with_scheduler=True" in WORKER.read_text()
    assert "with_scheduler=False" not in WORKER.read_text()


def test_retry_policy_uses_backoff_intervals():
    assert queue_service._JOB_POLICY["retry"].intervals == [30, 120]
