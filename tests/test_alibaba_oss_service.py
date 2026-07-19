"""Tests for AlibabaOSSService. Mocks oss2.Bucket rather than hitting the
real network — these verify DevInbox's own logic (object key layout,
payload shape, error handling)."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.alibaba_oss_service import AlibabaOSSService


@pytest.fixture
def oss_service():
    with patch("app.services.alibaba_oss_service.oss2") as mock_oss2:
        mock_bucket = MagicMock()
        mock_oss2.Bucket.return_value = mock_bucket
        mock_oss2.Auth.return_value = MagicMock()
        service = AlibabaOSSService(
            access_key_id="fake-id", access_key_secret="fake-secret",
            endpoint="https://oss-ap-southeast-1.aliyuncs.com", bucket_name="devinbox-audit-trail",
        )
        service.bucket = mock_bucket
        yield service, mock_bucket


def test_archive_issue_snapshot_uploads_json(oss_service):
    service, mock_bucket = oss_service
    mock_bucket.put_object.return_value = MagicMock(status=200, etag="abc123")

    result = service.archive_issue_snapshot("owner/repo", 42, {"status": "pr_created", "pr_number": 7})

    assert result["success"] is True
    assert mock_bucket.put_object.called
    key_arg = mock_bucket.put_object.call_args[0][0]
    assert "owner_repo" in key_arg
    assert "issue-42" in key_arg


def test_archive_diff_uploads_text(oss_service):
    service, mock_bucket = oss_service
    mock_bucket.put_object.return_value = MagicMock(status=200, etag="def456")

    result = service.archive_diff("owner/repo", 42, "src/app.py", "--- a/src/app.py\n+++ b/src/app.py\n")

    assert result["success"] is True
    key_arg = mock_bucket.put_object.call_args[0][0]
    assert "diffs/" in key_arg


def test_archive_snapshot_handles_upload_failure_gracefully(oss_service):
    service, mock_bucket = oss_service
    mock_bucket.put_object.side_effect = Exception("network error")

    result = service.archive_issue_snapshot("owner/repo", 42, {"status": "failed"})

    assert result["success"] is False
    assert "network error" in result["error"]


def test_health_check_reports_healthy(oss_service):
    service, mock_bucket = oss_service
    mock_bucket.get_bucket_info.return_value = MagicMock()

    health = service.health_check()

    assert health["status"] == "healthy"


def test_health_check_reports_unhealthy_on_error(oss_service):
    service, mock_bucket = oss_service
    mock_bucket.get_bucket_info.side_effect = Exception("bucket not found")

    health = service.health_check()

    assert health["status"] == "unhealthy"
