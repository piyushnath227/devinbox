"""Web UI + API for managing DevInbox: login, API key management,
overview stats, activity log, and the test-agent page. API keys are
entered here and encrypted at rest — no code editing required."""

import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
import structlog

from ..config import get_settings, get_key_manager
from ..models.database import get_db
from ..models.api_keys import APIKeyConfig
from ..models.issues import IssueRecord, IssueStatus
from ..models.activity_log import ActivityLog
from ..services.qwen_service import QwenService
from ..services.github_service import GitHubService
from ..services.crypto_service import get_crypto_service
from ..services.alibaba_oss_service import AlibabaOSSService

logger = structlog.get_logger()
router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def get_current_user(request: Request) -> dict:
    token = request.cookies.get("devinbox_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = get_crypto_service().verify_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    settings = get_settings()
    km = get_key_manager()
    return templates.TemplateResponse("dashboard/login.html", {
        "request": request, "app_name": settings.APP_NAME,
        "is_first_run": km.get_admin_password_hash() is None,
    })


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    km = get_key_manager()

    total = db.query(func.count(IssueRecord.id)).scalar() or 0
    pr_created = db.query(func.count(IssueRecord.id)).filter(IssueRecord.status == IssueStatus.PR_CREATED).scalar() or 0
    merged = db.query(func.count(IssueRecord.id)).filter(IssueRecord.status == IssueStatus.MERGED).scalar() or 0
    failed = db.query(func.count(IssueRecord.id)).filter(IssueRecord.status == IssueStatus.FAILED).scalar() or 0
    recent = db.query(ActivityLog).order_by(desc(ActivityLog.timestamp)).limit(10).all()

    window_start = datetime.now(timezone.utc) - timedelta(days=13)
    created_at_rows = db.query(IssueRecord.created_at).filter(IssueRecord.created_at >= window_start).all()
    trend_counts = [0] * 14
    for (created_at,) in created_at_rows:
        if created_at:
            day_index = (created_at.date() - window_start.date()).days
            if 0 <= day_index < 14:
                trend_counts[day_index] += 1
    trend_labels = [(window_start + timedelta(days=i)).strftime("%b %d") for i in range(14)]

    return templates.TemplateResponse("dashboard/overview.html", {
        "request": request, "app_name": settings.APP_NAME, "app_version": settings.APP_VERSION,
        "stats": {"total_issues": total, "pr_created": pr_created, "merged": merged, "failed": failed},
        "qwen_configured": km.has_keys("qwen"), "github_configured": km.has_keys("github"),
        "recent_activity": [log.to_dict() for log in recent], "current_page": "overview",
        "trend_labels": trend_labels, "trend_counts": trend_counts,
    })


@router.get("/keys", response_class=HTMLResponse)
async def api_keys_page(request: Request, user: dict = Depends(get_current_user)):
    settings = get_settings()
    km = get_key_manager()
    return templates.TemplateResponse("dashboard/api_keys.html", {
        "request": request, "app_name": settings.APP_NAME,
        "qwen_configured": km.has_keys("qwen"), "github_configured": km.has_keys("github"),
        "alibaba_configured": km.has_keys("alibaba"),
        "qwen_model": settings.QWEN_MODEL, "qwen_base_url": settings.QWEN_BASE_URL,
        "alibaba_endpoint": settings.ALIBABA_OSS_ENDPOINT,
        "current_page": "keys",
    })


@router.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    logs = db.query(ActivityLog).order_by(desc(ActivityLog.timestamp)).limit(100).all()
    return templates.TemplateResponse("dashboard/activity.html", {
        "request": request, "app_name": settings.APP_NAME,
        "activity_logs": [log.to_dict() for log in logs], "current_page": "activity",
    })


@router.get("/test", response_class=HTMLResponse)
async def test_agent_page(request: Request, user: dict = Depends(get_current_user)):
    settings = get_settings()
    return templates.TemplateResponse("dashboard/test_agent.html", {
        "request": request, "app_name": settings.APP_NAME, "current_page": "test",
    })


# Simple in-memory rate limiter for login attempts, keyed by client IP.
# This resets on restart and only applies within a single process — fine for
# DevInbox's single-instance deployment, but wouldn't hold up across a
# multi-worker/multi-replica setup without a shared store (e.g. Redis).
_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_WINDOW_SECONDS = 900
_login_attempts: Dict[str, List[float]] = {}


def _login_rate_limited(client_ip: str) -> bool:
    now = time.time()
    attempts = _login_attempts.setdefault(client_ip, [])
    attempts[:] = [t for t in attempts if now - t < _LOGIN_RATE_WINDOW_SECONDS]
    return len(attempts) >= _LOGIN_RATE_LIMIT


def _record_login_failure(client_ip: str) -> None:
    _login_attempts.setdefault(client_ip, []).append(time.time())


@router.post("/api/auth/login")
async def api_login(request: Request, password: str = Form(...)):
    client_ip = request.client.host if request.client else "unknown"
    if _login_rate_limited(client_ip):
        return JSONResponse(
            {"success": False, "message": "Too many login attempts. Please try again later."},
            status_code=429,
        )

    km = get_key_manager()
    crypto = get_crypto_service()
    stored_hash = km.get_admin_password_hash()

    if stored_hash is None:
        stored_hash = crypto.hash_password(password)
        km.save_admin_password_hash(stored_hash)
        token = crypto.create_access_token({"user": "admin"})
        resp = JSONResponse({"success": True, "is_first_run": True})
        resp.set_cookie("devinbox_token", token, httponly=True, samesite="lax", max_age=86400)
        return resp

    if crypto.verify_password(password, stored_hash):
        token = crypto.create_access_token({"user": "admin"})
        resp = JSONResponse({"success": True})
        resp.set_cookie("devinbox_token", token, httponly=True, samesite="lax", max_age=86400)
        return resp

    _record_login_failure(client_ip)
    return JSONResponse({"success": False, "message": "Invalid password"}, status_code=401)


@router.post("/api/keys/save")
async def save_api_keys(request: Request, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        data = await request.json()
        service = data.get("service")
        keys = data.get("keys", {})
        if service not in ("qwen", "github", "alibaba"):
            return JSONResponse({"success": False, "message": "Invalid service"}, status_code=400)

        saved = get_key_manager().save_keys(service, keys)
        if saved:
            config = db.query(APIKeyConfig).filter(APIKeyConfig.service == service).first()
            if not config:
                config = APIKeyConfig(service=service)
                db.add(config)
            config.is_configured = True
            config.updated_at = datetime.now(timezone.utc)
            db.commit()
            return JSONResponse({"success": True, "message": f"{service.title()} keys saved"})
        return JSONResponse({"success": False, "message": "Failed to save keys"}, status_code=500)
    except Exception as e:
        logger.error("api_keys_save_error", error=str(e))
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@router.get("/api/keys/test/{service}")
async def test_api_keys(service: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    km = get_key_manager()
    settings = get_settings()

    if service == "qwen":
        api_key = km.get_qwen_api_key()
        if not api_key:
            return JSONResponse({"success": False, "message": "Not configured"}, status_code=400)
        qwen = QwenService(api_key, km.get_qwen_base_url() or settings.QWEN_BASE_URL, km.get_qwen_model() or settings.QWEN_MODEL)
        health = qwen.health_check()
        return JSONResponse({"success": health["status"] == "healthy", **health})

    if service == "github":
        token = km.get_github_token()
        if not token:
            return JSONResponse({"success": False, "message": "Not configured"}, status_code=400)
        try:
            github = GitHubService(token)
            health = github.health_check()
            return JSONResponse({"success": health["status"] == "healthy", **health})
        except ValueError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    if service == "alibaba":
        creds = km.get_alibaba_credentials()
        if not creds or not creds.get("access_key_id") or not creds.get("bucket"):
            return JSONResponse({"success": False, "message": "Not configured"}, status_code=400)
        try:
            oss = AlibabaOSSService(
                access_key_id=creds["access_key_id"],
                access_key_secret=creds["access_key_secret"],
                endpoint=creds.get("endpoint") or settings.ALIBABA_OSS_ENDPOINT,
                bucket_name=creds["bucket"],
            )
            health = oss.health_check()
            return JSONResponse({"success": health["status"] == "healthy", **health})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    return JSONResponse({"success": False, "message": "Invalid service"}, status_code=400)


@router.get("/api/activity")
async def get_activity_logs(limit: int = 50, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    logs = db.query(ActivityLog).order_by(desc(ActivityLog.timestamp)).limit(limit).all()
    return JSONResponse({"success": True, "logs": [log.to_dict() for log in logs]})


@router.get("/api/logout")
async def api_logout():
    resp = RedirectResponse(url="/dashboard/login", status_code=302)
    resp.delete_cookie("devinbox_token")
    return resp
