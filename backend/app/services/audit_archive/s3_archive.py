"""S3-compatible backend (boto3).

One implementation covers AWS S3, Cloudflare R2, Backblaze B2, MinIO,
Supabase Storage, DigitalOcean Spaces, GCS (interop mode) and Alibaba OSS's
S3-compatible endpoint -- switch providers by changing `endpoint_url`.

If no static keys are given, boto3's default credential chain is used
(IAM role / IRSA / env vars), which is what you want in production on AWS.
"""

import time
from typing import Any, Dict, Optional

from .base import AuditArchive


class S3AuditArchive(AuditArchive):
    name = "s3"

    def __init__(
        self,
        bucket: str,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        region: Optional[str] = None,
        addressing_style: str = "auto",  # "path" for MinIO, "virtual" for some providers
        client: Any = None,  # injectable for tests
    ):
        if not bucket:
            raise ValueError("S3 audit archive requires a bucket name")
        self.bucket = bucket
        if client is not None:
            self._client = client
            return
        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("boto3 is required for the S3 audit backend: pip install boto3") from e
        self._client = boto3.client(
            "s3",
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
            endpoint_url=endpoint_url or None,
            region_name=region or None,
            config=Config(
                s3={"addressing_style": addressing_style},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=15,
            ),
        )

    def _put(self, key: str, body: bytes, content_type: str) -> Dict[str, Any]:
        resp = self._client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        return {"etag": resp.get("ETag"), "status": resp.get("ResponseMetadata", {}).get("HTTPStatusCode")}

    def get_signed_url(self, object_key: str, expires_in: int = 3600) -> Optional[str]:
        try:
            return self._client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": object_key}, ExpiresIn=expires_in
            )
        except Exception:
            return None

    def health_check(self) -> Dict[str, Any]:
        try:
            start = time.time()
            self._client.head_bucket(Bucket=self.bucket)
            return {"status": "healthy", "backend": self.name, "bucket": self.bucket,
                    "latency_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            return {"status": "unhealthy", "backend": self.name, "bucket": self.bucket, "error": str(e)}
