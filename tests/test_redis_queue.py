"""enqueue_job must degrade gracefully when Redis is unreachable or
unconfigured -- the webhook route falls back to BackgroundTasks in that
case, so a None return (not an exception) is what makes that fallback
possible."""

import pytest
from app.services import redis_queue


class FakeJob:
    id = "fake-job-id"


class FakeQueue:
    def __init__(self, raise_on_enqueue=False):
        self.raise_on_enqueue = raise_on_enqueue
        self.enqueued_with = None

    def enqueue(self, func, **kwargs):
        if self.raise_on_enqueue:
            raise ConnectionError("redis unreachable")
        self.enqueued_with = kwargs
        return FakeJob()


@pytest.fixture(autouse=True)
def _reset_singleton():
    # get_rq_queue() memoizes a module-level singleton; reset it around
    # each test so mocks from one test can't leak into the next.
    redis_queue._rq_queue = None
    redis_queue._redis_conn = None
    yield
    redis_queue._rq_queue = None
    redis_queue._redis_conn = None


def _noop_job(**kwargs):
    pass


def test_enqueue_job_returns_job_id_on_success(monkeypatch):
    fake_queue = FakeQueue()
    monkeypatch.setattr(redis_queue, "get_rq_queue", lambda: fake_queue)

    job_id = redis_queue.enqueue_job(_noop_job, issue_number=42)

    assert job_id == "fake-job-id"
    assert fake_queue.enqueued_with["kwargs"] == {"issue_number": 42}


def test_enqueue_job_returns_none_when_redis_unreachable(monkeypatch):
    monkeypatch.setattr(redis_queue, "get_rq_queue", lambda: FakeQueue(raise_on_enqueue=True))

    job_id = redis_queue.enqueue_job(_noop_job, issue_number=42)

    assert job_id is None  # caller (webhook.py) falls back to BackgroundTasks on None
