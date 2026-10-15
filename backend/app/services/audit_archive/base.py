"""Backend-agnostic audit-trail archival.

DevInbox archives every issue snapshot and generated diff as an object so
maintainers have a durable, off-box record of what the agent did. Backends
only implement `_put` and `health_check`; the object-key layout, JSON
serialisation and never-raise error handling live here once, so every
backend (local disk, S3, R2, MinIO, GCS-interop, Alibaba OSS, ...) behaves
identically.
"""

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()


class AuditArchive(ABC):
    """Contract the orchestrator depends on. Archival is best-effort: public
    methods return {"success": bool, ...} and never raise."""

    name: str = "base"

    # -- shared key layout (identical across backends) ------------------
    @staticmethod
    def object_key(repo_full_name: str, issue_number: int, suffix: str) -> str:
        repo_slug = repo_full_name.replace("/", "_")
        date_prefix = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        return f"devinbox-audit/{date_prefix}/{repo_slug}/issue-{issue_number}/{suffix}"

    # -- backend hooks --------------------------------------------------
    @abstractmethod
    def _put(self, key: str, body: bytes, content_type: str) -> Dict[str, Any]:
        """Persist one object. May raise; callers wrap it."""

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Return {"status": "healthy" | "unhealthy", ...}."""

    def get_signed_url(self, object_key: str, expires_in: int = 3600) -> Optional[str]:
        """Temporary read URL, for backends that support it (default: none)."""
        return None

    # -- public API used by AgentOrchestrator ---------------------------
    def archive_issue_snapshot(self, repo_full_name: str, issue_number: int, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        key = self.object_key(repo_full_name, issue_number, f"snapshot-{int(time.time())}.json")
        body = json.dumps(snapshot, default=str, indent=2).encode("utf-8")
        return self._safe_put(key, body, "application/json", "snapshot")

    def archive_diff(self, repo_full_name: str, issue_number: int, file_path: str, diff_text: str) -> Dict[str, Any]:
        safe_file = file_path.replace("/", "__")
        key = self.object_key(repo_full_name, issue_number, f"diffs/{safe_file}.diff")
        return self._safe_put(key, diff_text.encode("utf-8"), "text/plain", "diff")

    def _safe_put(self, key: str, body: bytes, content_type: str, kind: str) -> Dict[str, Any]:
        try:
            result = self._put(key, body, content_type)
            logger.info("audit_archived", backend=self.name, kind=kind, key=key)
            return {"success": True, "object_key": key, **result}
        except Exception as e:  # archival must never break the pipeline
            logger.error("audit_archive_failed", backend=self.name, kind=kind, key=key, error=str(e))
            return {"success": False, "error": str(e)}
