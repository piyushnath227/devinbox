"""Web UI + API for managing DevInbox: login, API key management,
overview stats, activity log, and the test-agent page. API keys are
entered here and encrypted at rest — no code editing required."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
import structlog

from ..config import Settings, get_settings, get_key_manager
from ..models.database import get_db
from ..models.api_keys import APIKeyConfig
from ..models.issues import IssueRecord, IssueStatus
from ..models.activity_log import ActivityLog
from ..services.llm_service import LLMConfigError, PROVIDER_PRESETS, build_llm_service, is_llm_configured
from ..services.github_service import GitHubService
from ..services.crypto_service import get_crypto_service
from ..services.audit_archive import build_audit_archive
from ..services.rate_limiter import FailureRateLimiter, redis_client_from_settings

logger = structlog.get_logger()
router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def get_current_user(request: Request) -> dict:
    token = request.cookies.get("devinbox_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = get_crypto_service().verify_token(token)
    if payload is None or payload.get("user") != "admin" or payload.get("type") != "access_token":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


def _get_client_ip(request: Request, settings: Settings) -> str:
    """FIX #2: Get client IP with proper proxy header handling.
    
    Checks X-Forwarded-For header first (for proxies), then falls back to direct connection.
    Only trusts X-Forwarded-For if the proxy IP is in TRUSTED_PROXY_IPS.
    """
    # Check if there's a direct connection IP
    if request.client:
        client_ip = request.client.host
        # If we have a direct IP and there's an X-Forwarded-For header,
        # only trust it if the direct IP is in the trusted proxy list
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for:
            trusted_proxies = [ip.strip() for ip in settings.TRUSTED_PROXY_IPS.split(",") if ip.strip()]
            if trusted_proxies and client_ip in trusted_proxies:
                # Use the first IP from X-Forwarded-For (client IP)
                forwarded_ip = x_forwarded_for.split(",")[0].strip()
                if forwarded_ip:
                    return forwarded_ip
        return client_ip
    return "unknown"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    settings = get_settings()
    km = get_key_manager()
    return templates.TemplateResponse(request, "dashboard/login.html", {
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

    return templates.TemplateResponse(request, "dashboard/overview.html", {
        "request": request, "app_name": settings.APP_NAME, "app_version": settings.APP_VERSION,
        "stats": {"total_issues": total, "pr_created": pr_created, "merged": merged, "failed": failed},
        "llm_configured": is_llm_configured(km, settings), "github_configured": km.has_keys("github"),
        "recent_activity": [log.to_dict() for log in recent], "current_page": "overview",
        "trend_labels": trend_labels, "trend_counts": trend_counts,
    })


@router.get("/keys", response_class=HTMLResponse)
async def api_keys_page(request: Request, user: dict = Depends(get_current_user)):
    settings = get_settings()
    km = get_key_manager()
    return templates.TemplateResponse(request, "dashboard/api_keys.html", {
        "request": request, "app_name": settings.APP_NAME,
        "llm_configured": is_llm_configured(km, settings), "github_configured": km.has_keys("github"),
        "storage_configured": km.get_storage_credentials() is not None,
        "llm_provider": (km.get_llm_credentials() or {}).get("provider") or settings.LLM_PROVIDER or "qwen",
        "llm_model": (km.get_llm_credentials() or {}).get("model") or settings.LLM_MODEL,
        "llm_base_url": (km.get_llm_credentials() or {}).get("base_url") or settings.LLM_BASE_URL,
        "llm_fallbacks": (km.get_llm_credentials() or {}).get("fallback_models") or settings.LLM_FALLBACK_MODELS,
        "llm_presets": {k: v["base_url"] for k, v in PROVIDER_PRESETS.items()},
        "storage_endpoint": settings.S3_ENDPOINT_URL,
        "current_page": "keys",
    })


@router.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    logs = db.query(ActivityLog).order_by(desc(ActivityLog.timestamp)).limit(100).all()
    return templates.TemplateResponse(request, "dashboard/activity.html", {
        "request": request, "app_name": settings.APP_NAME,
        "activity_logs": [log.to_dict() for log in logs], "current_page": "activity",
    })


@router.get("/test", response_class=HTMLResponse)
async def test_agent_page(request: Request, user: dict = Depends(get_current_user)):
    settings = get_settings()
    return templates.TemplateResponse(request, "dashboard/test_agent.html", {
        "request": request, "app_name": settings.APP_NAME, "current_page": "test",
    })


@router.get("/discover", response_class=HTMLResponse)
async def discover_page(request: Request, user: dict = Depends(get_current_user)):
    settings = get_settings()
    km = get_key_manager()
    return templates.TemplateResponse(request, "dashboard/discover.html", {
        "request": request, "app_name": settings.APP_NAME, "current_page": "discover",
        "github_configured": km.has_keys("github"),
    })


# Login throttle: 5 failures / 15 min per client IP. Redis-backed when REDIS_URL
# is set, so the limit holds across uvicorn workers and app replicas.
_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_WINDOW_SECONDS = 900
_login_limiter: Optional[FailureRateLimiter] = None


def _get_login_limiter() -> FailureRateLimiter:
    global _login_limiter
    if _login_limiter is None:
        _login_limiter = FailureRateLimiter(
            "login", _LOGIN_RATE_LIMIT, _LOGIN_RATE_WINDOW_SECONDS,
            redis_client=redis_client_from_settings(get_settings()),
        )
    return _login_limiter


def _login_rate_limited(client_ip: str) -> bool:
    return _get_login_limiter().is_limited(client_ip)


def _record_login_failure(client_ip: str) -> None:
    _get_login_limiter().record_failure(client_ip)


@router.post("/api/auth/login")
async def api_login(request: Request):
    settings = get_settings()
    # FIX #2: Improved client IP extraction with proxy support
    client_ip = _get_client_ip(request, settings)
    if _login_rate_limited(client_ip):
        logger.warning("login_rate_limited", client_ip=client_ip)
        return JSONResponse(
            {"success": False, "message": "Too many login attempts. Please try again later."},
            status_code=429,
        )

    # FIX #4: Proper error handling for form data
    try:
        form_data = await request.form()
        password = form_data.get("password")
        if not password:
            return JSONResponse({"success": False, "message": "Password required"}, status_code=400)
    except Exception as e:
        logger.error("login_form_parse_error", error=str(e))
        return JSONResponse({"success": False, "message": "Invalid request format"}, status_code=400)

    km = get_key_manager()
    crypto = get_crypto_service()
    stored_hash = km.get_admin_password_hash()

    if stored_hash is None:
        if not settings.DEBUG:
            return JSONResponse({"success": False, "message": "Administrator must be provisioned by the operator"}, status_code=503)
        if len(password) < 12:
            return JSONResponse({"success": False, "message": "Use at least 12 characters"}, status_code=400)
        stored_hash = crypto.hash_password(password)
        if not km.save_admin_password_hash(stored_hash):
            return JSONResponse({"success": False, "message": "Could not save administrator"}, status_code=503)
        token = crypto.create_access_token({"user": "admin"})
        resp = JSONResponse({"success": True, "is_first_run": True})
        resp.set_cookie("devinbox_token", token, httponly=True, secure=not settings.DEBUG, samesite="strict", max_age=86400)
        logger.info("login_first_run", client_ip=client_ip)
        return resp

    if crypto.verify_password(password, stored_hash):
        token = crypto.create_access_token({"user": "admin"})
        resp = JSONResponse({"success": True})
        resp.set_cookie("devinbox_token", token, httponly=True, secure=not settings.DEBUG, samesite="strict", max_age=86400)
        logger.info("login_success", client_ip=client_ip)
        return resp

    _record_login_failure(client_ip)
    logger.warning("login_failed", client_ip=client_ip)
    return JSONResponse({"success": False, "message": "Invalid password"}, status_code=401)


@router.post("/api/keys/save")
async def save_api_keys(request: Request, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """FIX #4, #5, #6: Improved error handling, input validation, and sensitive data protection."""
    try:
        # FIX #4: Better JSON error handling
        try:
            data = await request.json()
        except json.JSONDecodeError as e:
            logger.warning("api_keys_json_parse_error", error=str(e))
            return JSONResponse(
                {"success": False, "message": "Invalid JSON format"},
                status_code=400
            )

        service = data.get("service")
        keys = data.get("keys", {})

        # FIX #5: Validate service parameter
        service = {"alibaba": "storage", "qwen": "llm"}.get(service, service)  # legacy client names
        if service not in ("llm", "github", "storage"):
            logger.warning("api_keys_invalid_service", service=service)
            return JSONResponse(
                {"success": False, "message": "Invalid service"},
                status_code=400
            )

        # FIX #5: Validate keys is a dictionary
        if not isinstance(keys, dict):
            logger.warning("api_keys_invalid_format", service=service)
            return JSONResponse(
                {"success": False, "message": "Keys must be a dictionary"},
                status_code=400
            )

        # FIX #5: Reasonable limit on number of keys
        if len(keys) > 10:
            logger.warning("api_keys_too_many", service=service, count=len(keys))
            return JSONResponse(
                {"success": False, "message": "Too many keys (max 10)"},
                status_code=400
            )

        saved = get_key_manager().save_keys(service, keys)
        if saved:
            config = db.query(APIKeyConfig).filter(APIKeyConfig.service == service).first()
            if not config:
                config = APIKeyConfig(service=service)
                db.add(config)
            config.is_configured = True
            config.updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("api_keys_saved", service=service)
            return JSONResponse({"success": True, "message": f"{service.title()} keys saved"})
        
        logger.error("api_keys_save_failed", service=service)
        return JSONResponse(
            {"success": False, "message": "Failed to save keys"},
            status_code=500
        )
    except Exception as e:
        # FIX #6: Don't expose sensitive error details to client
        logger.error("api_keys_save_error", error=str(e), exc_info=True)
        return JSONResponse(
            {"success": False, "message": "An error occurred. Check server logs for details."},
            status_code=500
        )


@router.get("/api/keys/test/{service}")
async def test_api_keys(service: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    km = get_key_manager()
    settings = get_settings()

    if service in ("llm", "qwen"):
        try:
            llm = build_llm_service(km, settings)
        except LLMConfigError as e:
            return JSONResponse({"success": False, "message": f"Not configured: {e}"}, status_code=400)
        try:
            health = llm.health_check()
            return JSONResponse({"success": health["status"] == "healthy", **health})
        except Exception as e:
            logger.error("llm_health_check_failed", error=str(e))
            return JSONResponse({"success": False, "error": "Health check failed"}, status_code=500)

    if service == "github":
        token = km.get_github_token()
        if not token:
            return JSONResponse({"success": False, "message": "Not configured"}, status_code=400)
        try:
            github = GitHubService(token)
            health = github.health_check()
            return JSONResponse({"success": health["status"] == "healthy", **health})
        except ValueError as e:
            logger.error("github_auth_failed", error=str(e))
            return JSONResponse({"success": False, "error": "Authentication failed"}, status_code=400)
        except Exception as e:
            logger.error("github_health_check_failed", error=str(e))
            return JSONResponse({"success": False, "error": "Health check failed"}, status_code=500)

    if service in ("storage", "alibaba"):
        archive = build_audit_archive(km, settings)
        if archive is None:
            return JSONResponse({"success": False, "message": "Not configured"}, status_code=400)
        try:
            health = archive.health_check()
            return JSONResponse({"success": health["status"] == "healthy", **health})
        except Exception as e:
            logger.error("audit_archive_health_check_failed", error=str(e))
            return JSONResponse({"success": False, "error": "Health check failed"}, status_code=500)

    return JSONResponse({"success": False, "message": "Invalid service"}, status_code=400)


@router.get("/api/activity")
async def get_activity_logs(limit: int = 50, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    # FIX #5: Validate limit parameter
    if limit <= 0 or limit > 500:
        limit = 50
    logs = db.query(ActivityLog).order_by(desc(ActivityLog.timestamp)).limit(limit).all()
    return JSONResponse({"success": True, "logs": [log.to_dict() for log in logs]})


@router.get("/api/logout")
async def api_logout():
    resp = RedirectResponse(url="/dashboard/login", status_code=302)
    resp.delete_cookie("devinbox_token")
    return resp
