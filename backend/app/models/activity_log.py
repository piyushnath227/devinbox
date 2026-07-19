"""Activity Log Model - audit trail of every agent decision and action."""

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean, func
)
from sqlalchemy.orm import relationship
from .database import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, ForeignKey("issue_records.id", ondelete="CASCADE"), nullable=True)
    issue = relationship("IssueRecord", back_populates="activity_logs")

    action_type = Column(String(50), nullable=False, comment=(
        "webhook_received | classification | code_generation | pr_created | "
        "approval_granted | error | clarification_requested | issue_closed"
    ))
    description = Column(Text, nullable=False)
    log_metadata = Column(JSON, nullable=True)

    is_success = Column(Boolean, default=True)
    level = Column(String(20), default="info")  # info | warning | error | critical

    latency_ms = Column(Integer, nullable=True)
    tokens_used = Column(Integer, nullable=True)

    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "issue_id": self.issue_id,
            "action_type": self.action_type,
            "description": self.description,
            "metadata": self.log_metadata,
            "is_success": self.is_success,
            "level": self.level,
            "latency_ms": self.latency_ms,
            "tokens_used": self.tokens_used,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }
