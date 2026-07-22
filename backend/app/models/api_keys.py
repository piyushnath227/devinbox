"""API Key Configuration Model - tracks key config status (not the keys themselves)."""

from sqlalchemy import Column, String, Boolean, DateTime, func
from .database import Base


class APIKeyConfig(Base):
    __tablename__ = "api_key_configs"

    service = Column(String(50), primary_key=True, comment="'qwen' or 'github'")
    is_configured = Column(Boolean, default=False)
    last_validated = Column(DateTime(timezone=True), nullable=True)
    is_valid = Column(Boolean, default=False)
    validation_error = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "service": self.service,
            "is_configured": self.is_configured,
            "last_validated": self.last_validated.isoformat() if self.last_validated else None,
            "is_valid": self.is_valid,
            "validation_error": self.validation_error,
        }
