"""Durable inbox/outbox. Redis carries execution notifications, never the only job copy."""
from sqlalchemy import Column, String, Integer, JSON, DateTime, ForeignKey, Index, func
from .database import Base


class Job(Base):
    __tablename__ = 'jobs'
    id = Column(String(36), primary_key=True)
    kind = Column(String(20), nullable=False)
    repository = Column(String(200), nullable=False)
    payload = Column(JSON, nullable=False)
    # Unique while admitted/in flight; NULL releases the resource after completion.
    active_key = Column(String(240), unique=True, nullable=True)
    status = Column(String(20), nullable=False, default='pending')
    attempts = Column(Integer, nullable=False, default=0)
    dispatch_token = Column(String(36), nullable=True)
    available_at = Column(DateTime(timezone=True), nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index('ix_jobs_dispatch', 'status', 'available_at'),)

    def to_dict(self):
        return {k: getattr(self, k) for k in ('id', 'kind', 'repository', 'status', 'attempts', 'result', 'error')}


class Delivery(Base):
    __tablename__ = 'webhook_deliveries'
    id = Column(String(128), primary_key=True)
    job_id = Column(String(36), ForeignKey('jobs.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AdmissionWindow(Base):
    __tablename__ = 'admission_windows'
    id = Column(String(100), primary_key=True)
    count = Column(Integer, nullable=False, default=0)
