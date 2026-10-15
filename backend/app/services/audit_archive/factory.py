"""Single construction point for the audit archive.

Replaces the three copy-pasted `_build_oss_service` helpers that previously
lived in routes/webhook.py, worker_jobs.py and the dashboard test handler.
"""

from typing import Any, Optional

import structlog

from .base import AuditArchive
from .local_archive import LocalAuditArchive
from .s3_archive import S3AuditArchive

logger = structlog.get_logger()


def build_audit_archive(km: Any, settings: Any) -> Optional[AuditArchive]:
    """Return the configured archive, or None. Never raises (archival is optional).

    AUDIT_ARCHIVE_BACKEND:
      auto  (default) -> S3-compatible store if credentials are saved in the dashboard,
                         otherwise local disk (free, no account needed)
      none            -> disabled
      local           -> write JSON/diffs under AUDIT_ARCHIVE_LOCAL_DIR
      s3              -> any S3-compatible store (AWS, R2, MinIO, OSS, ...)
    """
    backend = (settings.AUDIT_ARCHIVE_BACKEND or "auto").lower()
    try:
        if backend == "none":
            return None
        if backend == "local":
            return LocalAuditArchive(settings.AUDIT_ARCHIVE_LOCAL_DIR)

        creds = km.get_storage_credentials() or {}
        if backend == "auto" and not creds:
            return LocalAuditArchive(settings.AUDIT_ARCHIVE_LOCAL_DIR)
        if backend in ("auto", "s3"):
            bucket = creds.get("bucket") or settings.S3_BUCKET
            if not bucket:
                return None
            return S3AuditArchive(
                bucket=bucket,
                access_key_id=creds.get("access_key_id"),
                secret_access_key=creds.get("access_key_secret"),
                endpoint_url=creds.get("endpoint") or settings.S3_ENDPOINT_URL,
                region=creds.get("region") or settings.S3_REGION,
                addressing_style=settings.S3_ADDRESSING_STYLE,
            )
        logger.warning("audit_archive_unknown_backend", backend=backend)
    except Exception as e:
        logger.warning("audit_archive_init_failed", backend=backend, error=str(e))
    return None
