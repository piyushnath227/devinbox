"""GitHub webhook receiver. Validates the HMAC-SHA256 signature, then
triggers the agent pipeline as a background task so we respond to
GitHub within its timeout window."""

import hmac
import hashlib
import json
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import structlog

from ..config import get_settings, get_key_manager
from ..models.database import get_db
from ..services.qwen_service import QwenService
from ..services.github_service import GitHubService
from ..services.agent_orchestrator import AgentOrchestrator
from ..services.alibaba_oss_service import AlibabaOSSService

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


@router.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    raw_body = await request.body()
    payload = json.loads(raw_body)
    event_type = request.headers.get("X-GitHub-Event", "")

    logger.info("webhook_received", event_type=event_type)

    if event_type == "ping":
        return JSONResponse({"status": "ok", "message": "Webhook configured successfully"})

    if event_type == "issues":
        action = payload.get("action", "")
        if action in ("opened", "reopened", "labeled"):
            issue_data = payload.get("issue", {})
            repo_data = payload.get("repository", {})
            repo_full_name = repo_data.get("full_name", "")
            issue_number = issue_data.get("number")

            if not repo_full_name or not issue_number:
                return JSONResponse({"status": "error", "message": "Missing data"}, status_code=400)

            background_tasks.add_task(
                process_issue_background,
                repo_full_name=repo_full_name,
                issue_number=issue_number,
                title=issue_data.get("title", ""),
                body=issue_data.get("body", "") or "",
                author=issue_data.get("user", {}).get("login", "unknown"),
                labels=[l["name"] for l in issue_data.get("labels", [])],
                db=db,
            )
            return JSONResponse({"status": "received", "repo": repo_full_name})

    elif event_type == "issue_comment":
        if payload.get("action") == "created":
            comment_body = payload.get("comment", {}).get("body", "")
            if "/approve" in comment_body:
                repo_full_name = payload.get("repository", {}).get("full_name", "")
                background_tasks.add_task(check_and_merge_background, repo_full_name=repo_full_name, db=db)
            return JSONResponse({"status": "received"})

    return JSONResponse({"status": "ignored", "event": event_type})


def _build_oss_service(km) -> Optional[AlibabaOSSService]:
    """Build the OSS archival service, or return None if not configured.

    OSS archival is an enhancement to the audit trail, not a hard
    requirement, so this never raises.
    """
    creds = km.get_alibaba_credentials()
    if not creds or not creds.get("access_key_id") or not creds.get("access_key_secret") or not creds.get("bucket"):
        return None
    try:
        return AlibabaOSSService(
            access_key_id=creds["access_key_id"],
            access_key_secret=creds["access_key_secret"],
            endpoint=creds.get("endpoint") or "https://oss-ap-southeast-1.aliyuncs.com",
            bucket_name=creds["bucket"],
        )
    except Exception as e:
        logger.warning("oss_service_init_failed", error=str(e))
        return None


def process_issue_background(repo_full_name, issue_number, title, body, author, labels, db: Session):
    try:
        km = get_key_manager()
        settings = get_settings()
        qwen_key = km.get_qwen_api_key()
        github_token = km.get_github_token()
        if not qwen_key or not github_token:
            logger.error("background_processing_missing_keys")
            return

        qwen = QwenService(qwen_key, km.get_qwen_base_url() or settings.QWEN_BASE_URL, km.get_qwen_model() or settings.QWEN_MODEL)
        github = GitHubService(github_token)
        oss = _build_oss_service(km)
        orchestrator = AgentOrchestrator(qwen, github, db, oss_service=oss)
        orchestrator.process_issue(repo_full_name, issue_number, title, body, author, labels)
    except Exception as e:
        logger.error("background_processing_error", error=str(e))


def check_and_merge_background(repo_full_name, db: Session):
    try:
        km = get_key_manager()
        settings = get_settings()
        github_token = km.get_github_token()
        if not github_token:
            return
        github = GitHubService(github_token)
        qwen = QwenService(km.get_qwen_api_key() or "", km.get_qwen_base_url() or settings.QWEN_BASE_URL)
        oss = _build_oss_service(km)
        orchestrator = AgentOrchestrator(qwen, github, db, oss_service=oss)
        orchestrator.check_and_merge_approved_prs(repo_full_name)
    except Exception as e:
        logger.error("background_merge_error", error=str(e))
