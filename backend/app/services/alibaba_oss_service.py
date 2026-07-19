"""Archives DevInbox's audit trail (activity logs and generated diffs) to
Alibaba Cloud OSS using the official oss2 SDK.

Docs: https://www.alibabacloud.com/help/en/oss/developer-reference/python-2
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog

try:
    import oss2
except ImportError:  # pragma: no cover - optional dependency
    oss2 = None

logger = structlog.get_logger()


class AlibabaOSSService:
    """Thin wrapper around Alibaba Cloud OSS for archiving audit-trail objects."""

    def __init__(self, access_key_id: str, access_key_secret: str, endpoint: str, bucket_name: str):
        if oss2 is None:
            raise RuntimeError(
                "The 'oss2' package is required for Alibaba Cloud OSS integration. "
                "Install it with `pip install oss2`."
            )
        self.bucket_name = bucket_name
        self.endpoint = endpoint
        auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(auth, endpoint, bucket_name)
        logger.info("alibaba_oss_service_initialized", endpoint=endpoint, bucket=bucket_name)

    def _object_key(self, repo_full_name: str, issue_number: int, suffix: str) -> str:
        repo_slug = repo_full_name.replace("/", "_")
        date_prefix = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        return f"devinbox-audit/{date_prefix}/{repo_slug}/issue-{issue_number}/{suffix}"

    def archive_issue_snapshot(self, repo_full_name: str, issue_number: int, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Upload a JSON snapshot of an issue's pipeline state to OSS."""
        try:
            key = self._object_key(repo_full_name, issue_number, f"snapshot-{int(time.time())}.json")
            body = json.dumps(snapshot, default=str, indent=2).encode("utf-8")
            result = self.bucket.put_object(key, body, headers={"Content-Type": "application/json"})
            logger.info("oss_snapshot_archived", key=key, status=result.status)
            return {"success": True, "object_key": key, "etag": result.etag, "status": result.status}
        except Exception as e:
            logger.error("oss_snapshot_archive_failed", error=str(e))
            return {"success": False, "error": str(e)}

    def archive_diff(self, repo_full_name: str, issue_number: int, file_path: str, diff_text: str) -> Dict[str, Any]:
        """Upload a generated unified diff to OSS, keyed by repo/issue/file."""
        try:
            safe_file = file_path.replace("/", "__")
            key = self._object_key(repo_full_name, issue_number, f"diffs/{safe_file}.diff")
            result = self.bucket.put_object(key, diff_text.encode("utf-8"), headers={"Content-Type": "text/plain"})
            logger.info("oss_diff_archived", key=key, status=result.status)
            return {"success": True, "object_key": key, "etag": result.etag, "status": result.status}
        except Exception as e:
            logger.error("oss_diff_archive_failed", error=str(e))
            return {"success": False, "error": str(e)}

    def get_signed_url(self, object_key: str, expires_in: int = 3600) -> Optional[str]:
        """Generate a temporary signed URL so a maintainer can view an archived object."""
        try:
            return self.bucket.sign_url("GET", object_key, expires_in)
        except Exception as e:
            logger.error("oss_sign_url_failed", error=str(e))
            return None

    def health_check(self) -> Dict[str, Any]:
        try:
            start = time.time()
            self.bucket.get_bucket_info()
            return {"status": "healthy", "bucket": self.bucket_name, "latency_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            logger.error("oss_health_check_failed", error=str(e))
            return {"status": "unhealthy", "bucket": self.bucket_name, "error": str(e)}
