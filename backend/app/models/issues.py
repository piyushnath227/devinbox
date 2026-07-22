"""Tracks a GitHub issue through the agent pipeline:
RECEIVED -> ANALYZING -> CLASSIFIED -> GENERATING -> PR_CREATED -> MERGED
                                   \\-> CLOSED (spam/out_of_scope)
"""

import enum
from sqlalchemy import (
    Column, String, Integer, Text, DateTime, Boolean, Float, Enum, func, JSON
)
from sqlalchemy.orm import relationship
from .database import Base


class IssueStatus(str, enum.Enum):
    RECEIVED = "received"
    ANALYZING = "analyzing"
    CLASSIFIED = "classified"
    NEEDS_CLARIFICATION = "needs_clarification"
    GENERATING = "generating"
    LOW_CONFIDENCE = "low_confidence"
    PR_CREATED = "pr_created"
    MERGE_CONFLICT = "merge_conflict"
    APPROVED = "approved"
    MERGED = "merged"
    CLOSED = "closed"
    FAILED = "failed"
    OUT_OF_SCOPE = "out_of_scope"


class IssueRecord(Base):
    __tablename__ = "issue_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_number = Column(Integer, nullable=False)
    repository = Column(String(200), nullable=False)

    title = Column(String(500), nullable=False)
    body = Column(Text, nullable=True)
    author = Column(String(100), nullable=True)
    labels = Column(JSON, nullable=True)

    status = Column(Enum(IssueStatus), default=IssueStatus.RECEIVED)
    classification = Column(String(50), nullable=True)
    classification_confidence = Column(Float, nullable=True)
    summary = Column(Text, nullable=True)
    solution_plan = Column(Text, nullable=True)

    generated_diff = Column(Text, nullable=True)
    language = Column(String(50), nullable=True)
    modified_files = Column(JSON, nullable=True)

    pr_number = Column(Integer, nullable=True)
    pr_url = Column(String(500), nullable=True)
    branch_name = Column(String(200), nullable=True)

    approved_by = Column(String(100), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)

    activity_logs = relationship("ActivityLog", back_populates="issue", order_by="ActivityLog.timestamp")

    def to_dict(self):
        return {
            "id": self.id,
            "issue_number": self.issue_number,
            "repository": self.repository,
            "title": self.title,
            "status": self.status.value if self.status else None,
            "classification": self.classification,
            "classification_confidence": self.classification_confidence,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "branch_name": self.branch_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
