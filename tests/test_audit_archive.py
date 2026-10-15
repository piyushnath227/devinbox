"""Tests for the pluggable audit archive (local + S3-compatible backends)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.audit_archive import LocalAuditArchive, S3AuditArchive, build_audit_archive


# ---------- S3 backend (boto3 client injected, no network) ----------
@pytest.fixture
def s3():
    client = MagicMock()
    client.put_object.return_value = {"ETag": '"abc"', "ResponseMetadata": {"HTTPStatusCode": 200}}
    return S3AuditArchive(bucket="devinbox-audit", client=client), client


def test_s3_snapshot_upload_key_layout_and_body(s3):
    archive, client = s3
    result = archive.archive_issue_snapshot("owner/repo", 42, {"status": "pr_created", "pr_number": 7})
    assert result["success"] is True
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "devinbox-audit"
    assert "owner_repo" in kwargs["Key"] and "issue-42" in kwargs["Key"]
    assert kwargs["ContentType"] == "application/json"
    assert json.loads(kwargs["Body"])["pr_number"] == 7


def test_s3_diff_upload(s3):
    archive, client = s3
    assert archive.archive_diff("owner/repo", 42, "src/app.py", "--- a\n+++ b\n")["success"] is True
    assert "diffs/src__app.py.diff" in client.put_object.call_args.kwargs["Key"]


def test_s3_upload_failure_never_raises(s3):
    archive, client = s3
    client.put_object.side_effect = Exception("network error")
    result = archive.archive_issue_snapshot("owner/repo", 42, {})
    assert result["success"] is False and "network error" in result["error"]


def test_s3_health(s3):
    archive, client = s3
    assert archive.health_check()["status"] == "healthy"
    client.head_bucket.side_effect = Exception("NoSuchBucket")
    assert archive.health_check()["status"] == "unhealthy"


def test_s3_requires_bucket():
    with pytest.raises(ValueError):
        S3AuditArchive(bucket="", client=MagicMock())


# ---------- local backend ----------
def test_local_writes_files(tmp_path):
    archive = LocalAuditArchive(str(tmp_path))
    result = archive.archive_diff("owner/repo", 1, "a/b.py", "diff-body")
    assert result["success"] is True
    assert (tmp_path / result["object_key"]).read_text() == "diff-body"
    assert archive.health_check()["status"] == "healthy"


def test_local_blocks_path_traversal(tmp_path):
    archive = LocalAuditArchive(str(tmp_path / "root"))
    result = archive._safe_put("../../escape.txt", b"x", "text/plain", "test")
    assert result["success"] is False
    assert not (tmp_path / "escape.txt").exists()


# ---------- factory ----------
def _settings(**kw):
    base = dict(AUDIT_ARCHIVE_BACKEND="auto", AUDIT_ARCHIVE_LOCAL_DIR="./x", S3_BUCKET="",
                S3_ENDPOINT_URL="", S3_REGION="", S3_ADDRESSING_STYLE="auto")
    base.update(kw)
    return SimpleNamespace(**base)


def test_factory_none_disables():
    km = MagicMock(); km.get_storage_credentials.return_value = None
    assert build_audit_archive(km, _settings(AUDIT_ARCHIVE_BACKEND="none")) is None


def test_factory_auto_without_creds_falls_back_to_free_local_disk(tmp_path):
    km = MagicMock(); km.get_storage_credentials.return_value = None
    archive = build_audit_archive(km, _settings(AUDIT_ARCHIVE_LOCAL_DIR=str(tmp_path)))
    assert isinstance(archive, LocalAuditArchive)


def test_factory_local(tmp_path):
    archive = build_audit_archive(MagicMock(), _settings(AUDIT_ARCHIVE_BACKEND="local", AUDIT_ARCHIVE_LOCAL_DIR=str(tmp_path)))
    assert isinstance(archive, LocalAuditArchive)


def test_factory_auto_uses_saved_s3_creds_including_legacy_shape():
    km = MagicMock()
    km.get_storage_credentials.return_value = {
        "access_key_id": "id", "access_key_secret": "secret",
        "endpoint": "https://oss-ap-southeast-1.aliyuncs.com", "bucket": "b",
    }
    assert isinstance(build_audit_archive(km, _settings()), S3AuditArchive)


def test_factory_swallows_init_errors():
    km = MagicMock(); km.get_storage_credentials.side_effect = RuntimeError("boom")
    assert build_audit_archive(km, _settings(AUDIT_ARCHIVE_BACKEND="s3")) is None
