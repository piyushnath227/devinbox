"""Encrypted secret blobs stored in the database (multi-host safe).

Values are already Fernet-encrypted by KeyManager before they reach this
table, so a database leak alone does not expose API keys.
"""

from sqlalchemy import Column, String, Text, DateTime, func
from .database import Base


class EncryptedSecret(Base):
    __tablename__ = "encrypted_secrets"

    name = Column(String(100), primary_key=True)  # e.g. "llm", "github", "admin", ".salt"
    value = Column(Text, nullable=False)  # base64 text of the encrypted (or salt) bytes
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
