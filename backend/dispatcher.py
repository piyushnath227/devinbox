"""Recover and dispatch SQL outbox jobs; safe to run more than one dispatcher."""
import time
import signal
from pathlib import Path
import structlog
from app.config import get_settings
from app.models.database import init_db, create_session
from app.services.queue_service import _get_queue
from app.services.job_service import dispatch_batch

running = True


def stop(*args):
    global running
    running = False


def main():
    settings = get_settings()
    init_db(settings.DATABASE_URL)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        try:
            queue = _get_queue()
            if queue is not None:
                with create_session() as db:
                    dispatch_batch(db, queue)
                Path('/tmp/devinbox-dispatcher-heartbeat').touch()
        except Exception:
            structlog.get_logger().exception('dispatcher_failed')
        time.sleep(2)


if __name__ == '__main__':
    main()
