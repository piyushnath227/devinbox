"""GitHub webhook receiver. Validates the HMAC-SHA256 signature, then
persists a durable job before acknowledging delivery."""

import hmac
import hashlib
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import structlog

from ..config import get_settings, get_key_manager
from ..models.database import get_db

logger = structlog.get_logger()
router = APIRouter(tags=["webhooks"])


def verify_github_signature(request_body: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header or not secret:
        return False
    try:
        algorithm, signature = signature_header.split("=", 1)
    except ValueError:
        return False
    if algorithm != "sha256":
        return False
    mac = hmac.new(secret.encode(), msg=request_body, digestmod=hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), signature)


def resolve_webhook_secret(km, settings) -> str:
    """Dashboard-saved secret wins; falls back to the WEBHOOK_SECRET env var."""
    return (km.get_webhook_secret() or settings.WEBHOOK_SECRET or "").strip()


@router.post("/webhook/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    from starlette.concurrency import run_in_threadpool
    from ..services.job_service import admit, AdmissionLimited
    import re
    settings = get_settings()
    raw_body = bytearray()
    async for chunk in request.stream():
        raw_body.extend(chunk)
        if len(raw_body) > getattr(settings, "MAX_WEBHOOK_BYTES", 1_000_000):
            return JSONResponse({"error": "Payload too large"}, status_code=413)
    if settings.REQUIRE_WEBHOOK_SIGNATURE:
        secret = await run_in_threadpool(resolve_webhook_secret, get_key_manager(), settings)
        if not secret:
            return JSONResponse({"error": "Webhook secret not configured"}, status_code=503)
        if not verify_github_signature(bytes(raw_body), request.headers.get("X-Hub-Signature-256", ""), secret):
            return JSONResponse({"error": "Invalid signature"}, status_code=401)
    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "Invalid JSON object"}, status_code=400)
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return JSONResponse({"status": "ok"})
    try:
        repo = payload.get("repository", {}).get("full_name", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or len(repo) > 200:
            raise ValueError()
        issue = payload.get("issue", {})
        number = issue.get("number")
        if type(number) is not int or number < 1:
            raise ValueError()
        data = {"issue_number": number}
        if event == "issues" and payload.get("action") in ("opened", "reopened", "labeled"):
            label = getattr(settings, "AGENT_REQUIRED_LABEL", "devinbox")
            labels = [item["name"] for item in issue.get("labels", [])]
            if label and label not in labels:
                return JSONResponse({"status": "ignored", "reason": "Opt-in label required"})
            kind = "issue"
        elif event == "issue_comment" and payload.get("action") == "created" and issue.get("pull_request"):
            if not re.search(r"^/approve [0-9a-f]{40}$", payload.get("comment", {}).get("body", ""), re.MULTILINE):
                return JSONResponse({"status": "ignored"})
            kind, data = "merge", {"pr_number": number}
        else:
            return JSONResponse({"status": "ignored"})
    except (ValueError, TypeError, AttributeError, KeyError):
        return JSONResponse({"error": "Invalid event payload"}, status_code=400)
    delivery = request.headers.get("X-GitHub-Delivery", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", delivery):
        return JSONResponse({"error": "Missing or invalid delivery ID"}, status_code=400)
    try:
        job, created = await run_in_threadpool(admit, db, kind, repo, data, delivery, getattr(settings, "MAX_JOBS_PER_MINUTE", 20))
    except AdmissionLimited:
        return JSONResponse({"error": "Admission limit reached"}, status_code=429, headers={"Retry-After": "60"})
    return JSONResponse({"status": "queued" if created else "duplicate", "job_id": job.id}, status_code=202)
