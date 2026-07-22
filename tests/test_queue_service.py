"""enqueue_issue_job/enqueue_merge_check_job must degrade gracefully when
Redis is unreachable or unconfigured -- webhook.py falls back to
BackgroundTasks in that case, so a None return (not an exception) is
what makes that fallback possible."""

import pytest
from app.services import queue_service


class FakeJob:
    id = "fake-job-id"


class FakeQueue:
    def __init__(self, raise_on_enqueue=False):
        self.raise_on_enqueue = raise_on_enqueue
        self.enqueued_with = None

    def enqueue(self, func_path, *args, **kwargs):
        if self.raise_on_enqueue:
            raise ConnectionError("redis unreachable")
        self.enqueued_with = {"func_path": func_path, "args": args, "kwargs": kwargs}
        return FakeJob()


@pytest.fixture(autouse=True)
def _reset_singleton():
    # _get_queue() memoizes module-level singletons; reset them around
    # each test so mocks from one test can't leak into the next.
    queue_service._queue = None
    queue_service._redis_conn = None
    queue_service._connection_attempted = False
    yield
    queue_service._queue = None
    queue_service._redis_conn = None
    queue_service._connection_attempted = False


def test_enqueue_issue_job_returns_job_id_on_success(monkeypatch):
    fake_queue = FakeQueue()
    monkeypatch.setattr(queue_service, "_get_queue", lambda: fake_queue)

    job_id = queue_service.enqueue_issue_job(
        repo_full_name="owner/repo", issue_number=42, title="t", body="b", author="a", labels=[],
    )

    assert job_id == "fake-job-id"
    assert fake_queue.enqueued_with["func_path"] == "app.worker_jobs.process_issue_job"
    assert fake_queue.enqueued_with["args"] == ("owner/repo", 42, "t", "b", "a", [])


def test_enqueue_issue_job_returns_none_when_queue_unavailable(monkeypatch):
    monkeypatch.setattr(queue_service, "_get_queue", lambda: None)

    job_id = queue_service.enqueue_issue_job(
        repo_full_name="owner/repo", issue_number=42, title="t", body="b", author="a", labels=[],
    )

    assert job_id is None  # caller (webhook.py) falls back to BackgroundTasks on None


def test_enqueue_issue_job_returns_none_when_redis_unreachable(monkeypatch):
    monkeypatch.setattr(queue_service, "_get_queue", lambda: FakeQueue(raise_on_enqueue=True))

    job_id = queue_service.enqueue_issue_job(
        repo_full_name="owner/repo", issue_number=42, title="t", body="b", author="a", labels=[],
    )

    assert job_id is None


def test_enqueue_merge_check_job_returns_job_id_on_success(monkeypatch):
    fake_queue = FakeQueue()
    monkeypatch.setattr(queue_service, "_get_queue", lambda: fake_queue)

    job_id = queue_service.enqueue_merge_check_job("owner/repo")

    assert job_id == "fake-job-id"
    assert fake_queue.enqueued_with["func_path"] == "app.worker_jobs.check_and_merge_job"


def test_is_queue_available_reflects_queue_state(monkeypatch):
    monkeypatch.setattr(queue_service, "_get_queue", lambda: None)
    assert queue_service.is_queue_available() is False

    monkeypatch.setattr(queue_service, "_get_queue", lambda: FakeQueue())
    assert queue_service.is_queue_available() is True


def test_get_queue_returns_none_without_redis_url(monkeypatch):
    class FakeSettings:
        REDIS_URL = ""

    monkeypatch.setattr("app.config.get_settings", lambda: FakeSettings())

    assert queue_service._get_queue() is None
