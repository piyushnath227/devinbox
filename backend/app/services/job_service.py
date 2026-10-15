"""Database admission + outbox dispatch with atomic claims and bounded recovery."""
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from ..models.jobs import Job, Delivery, AdmissionWindow

MAX_ATTEMPTS = 3
LEASE_SECONDS = 900  # longer than RQ's enforced 600s execution timeout


class AdmissionLimited(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc)


def _insert_if_absent(db, model, values, key='id'):
    dialect = db.bind.dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=[key]))


def admit(db, kind, repository, payload, delivery_id=None, limit=20):
    """Commit job and delivery together before acknowledging the request."""
    active_key = f'{kind}:{repository}:{payload.get("issue_number", payload.get("pr_number", "all"))}'
    for _ in range(3):
        try:
            if delivery_id:
                delivery = db.get(Delivery, delivery_id)
                if delivery:
                    return db.get(Job, delivery.job_id), False
            existing = db.query(Job).filter_by(active_key=active_key).first()
            if existing:
                if delivery_id:
                    db.add(Delivery(id=delivery_id, job_id=existing.id))
                    db.commit()
                return existing, False
            stamp = now()
            window = stamp.strftime('%Y%m%d%H%M')
            _insert_if_absent(db, AdmissionWindow, {'id': window, 'count': 0})
            changed = db.execute(update(AdmissionWindow).where(
                AdmissionWindow.id == window, AdmissionWindow.count < limit,
            ).values(count=AdmissionWindow.count + 1)).rowcount
            if not changed:
                db.rollback()
                raise AdmissionLimited('Job admission limit reached; retry in one minute')
            job = Job(id=str(uuid.uuid4()), kind=kind, repository=repository, payload=payload,
                      active_key=active_key, status='pending', available_at=stamp, updated_at=stamp)
            db.add(job)
            db.flush()
            if delivery_id:
                db.add(Delivery(id=delivery_id, job_id=job.id))
            db.commit()
            return job, True
        except IntegrityError:
            db.rollback()  # a concurrent request won the unique resource/delivery claim
    raise RuntimeError('Could not admit job after concurrent writes')


def dispatch_batch(db, queue, batch_size=25):
    stamp = now()
    # Expired running jobs were killed/timed out. The lease exceeds the hard RQ timeout.
    for exhausted in (False, True):
        condition = Job.attempts >= MAX_ATTEMPTS if exhausted else Job.attempts < MAX_ATTEMPTS
        values = {"status": "failed" if exhausted else "pending", "error": "Worker lease expired", "available_at": stamp}
        if exhausted:
            values["active_key"] = None
        db.execute(update(Job).where(Job.status.in_(['dispatched', 'running']), Job.lease_until < stamp, condition).values(**values))
    db.commit()
    ids = [x[0] for x in db.query(Job.id).filter(Job.status == 'pending', Job.available_at <= stamp)
           .order_by(Job.available_at).limit(batch_size).all()]
    sent = 0
    for job_id in ids:
        token = str(uuid.uuid4())
        claimed = db.execute(update(Job).where(Job.id == job_id, Job.status == 'pending').values(
            status='dispatched', dispatch_token=token, lease_until=stamp + timedelta(seconds=LEASE_SECONDS), updated_at=stamp,
        )).rowcount
        db.commit()
        if not claimed:
            continue
        try:
            queue.enqueue('app.worker_jobs.execute_job', job_id, token, job_id=token,
                          job_timeout=600, result_ttl=3600, failure_ttl=604800)
            sent += 1
        except Exception:
            # Even an ambiguous enqueue is safe: the old notification cannot claim the reset job.
            db.execute(update(Job).where(Job.id == job_id, Job.status == 'dispatched', Job.dispatch_token == token)
                       .values(status='pending', available_at=now() + timedelta(seconds=5), dispatch_token=None))
            db.commit()
    db.query(AdmissionWindow).filter(AdmissionWindow.id < (stamp - timedelta(days=1)).strftime('%Y%m%d%H%M')).delete()
    db.commit()
    return sent


def claim(db, job_id, token):
    changed = db.execute(update(Job).where(Job.id == job_id, Job.status == 'dispatched', Job.dispatch_token == token)
                         .values(status='running', attempts=Job.attempts + 1, updated_at=now(),
                                 lease_until=now() + timedelta(seconds=LEASE_SECONDS))).rowcount
    db.commit()
    return db.get(Job, job_id) if changed else None


def finish(db, job_id, token, result=None, error=None, retryable=True):
    db.expire_all()
    job = db.get(Job, job_id)
    if not job or job.dispatch_token != token or job.status != 'running':
        return
    job.result = result
    job.error = error
    job.updated_at = now()
    if error and retryable and job.attempts < MAX_ATTEMPTS:
        job.status = 'pending'
        job.available_at = now() + timedelta(seconds=30 if job.attempts == 1 else 120)
    else:
        job.status = 'failed' if error else 'succeeded'
        job.active_key = None
    db.commit()
