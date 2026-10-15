"""Per-container RQ heartbeat probe."""
import os
import socket
from datetime import datetime, timezone
import redis
from rq import Worker

conn = redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=2, socket_timeout=2)
worker = Worker.find_by_key(f'rq:worker:{socket.gethostname()}', connection=conn)
if worker is None or worker.last_heartbeat is None:
    raise SystemExit(1)
stamp = worker.last_heartbeat
if stamp.tzinfo is None:
    stamp = stamp.replace(tzinfo=timezone.utc)
raise SystemExit(0 if (datetime.now(timezone.utc) - stamp).total_seconds() < 120 else 1)
