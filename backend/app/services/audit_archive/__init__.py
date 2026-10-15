"""Pluggable, cloud-agnostic audit-trail archival."""

from .base import AuditArchive
from .factory import build_audit_archive
from .local_archive import LocalAuditArchive
from .s3_archive import S3AuditArchive

__all__ = ["AuditArchive", "build_audit_archive", "LocalAuditArchive", "S3AuditArchive"]
