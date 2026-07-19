"""Direct agent interaction for testing (no GitHub webhook required),
plus health monitoring."""

import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
import structlog

from ..config import get_settings, get_key_manager
from ..models.database import get_db
from ..services.qwen_service import QwenService
from ..services.github_service import GitHubService

logger = structlog.get_logger()
router = APIRouter(prefix="/api/agent", tags=["agent"])


class TestIssueRequest(BaseModel):
    title: str = Field(..., min_length=5, max_length=500)
    body: str = Field(..., min_length=10, max_length=10000)
    repo: str = Field(default="test/test-repo")
    labels: list = Field(default_factory=list)


@router.post("/test")
async def test_agent_with_mock_issue(req: TestIssueRequest, db: Session = Depends(get_db)):
    km = get_key_manager()
    settings = get_settings()

    if not km.has_keys("qwen"):
        return JSONResponse({"success": False, "message": "Qwen Cloud API key not configured"}, status_code=400)

    qwen = QwenService(km.get_qwen_api_key(), km.get_qwen_base_url() or settings.QWEN_BASE_URL, km.get_qwen_model() or settings.QWEN_MODEL)

    classification = qwen.classify_issue(req.title, req.body, req.labels)
    if not classification["success"]:
        return JSONResponse({"success": False, "message": "Classification failed", "error": classification.get("error")}, status_code=500)

    parsed_cls = json.loads(classification["content"])
    result = {
        "success": True,
        "classification": parsed_cls,
        "classification_tokens": classification.get("tokens_used"),
        "classification_latency_ms": classification.get("latency_ms"),
        "solution": None,
    }

    if parsed_cls.get("is_actionable", False):
        solution = qwen.generate_solution(req.title, req.body, parsed_cls["classification"])
        if solution["success"]:
            result["solution"] = json.loads(solution["content"])
            result["solution_tokens"] = solution.get("tokens_used")
            result["solution_latency_ms"] = solution.get("latency_ms")

    return JSONResponse(result)


class RunRealPipelineRequest(BaseModel):
    repo: str = Field(..., description="owner/repo of the target issue, e.g. 'titraio/titra'")
    issue_number: int = Field(..., description="An existing, real issue number in that repo")


@router.post("/run")
async def run_real_pipeline(req: RunRealPipelineRequest, db: Session = Depends(get_db)):
    """
    Manually triggers the FULL agent pipeline (real repo inspection,
    branch creation, commits, and PR) for a specific existing GitHub
    issue -- without requiring a webhook.

    This exists because adding a webhook requires admin access to the
    target repo, which you won't have on external open-source projects.
    This endpoint lets you point DevInbox at any public issue directly;
    if you don't have write access to the repo, the orchestrator
    automatically forks it and opens a cross-repo PR back to upstream.
    """
    from ..services.agent_orchestrator import AgentOrchestrator
    from .webhook import _build_oss_service

    km = get_key_manager()
    settings = get_settings()

    if not km.has_keys("qwen") or not km.has_keys("github"):
        return JSONResponse({"success": False, "message": "Qwen and/or GitHub keys not configured"}, status_code=400)

    github = GitHubService(km.get_github_token())

    try:
        repo = github.get_repository(req.repo)
        gh_issue = repo.get_issue(number=req.issue_number)
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Could not fetch issue: {e}"}, status_code=400)

    qwen = QwenService(km.get_qwen_api_key(), km.get_qwen_base_url() or settings.QWEN_BASE_URL, km.get_qwen_model() or settings.QWEN_MODEL)
    oss = _build_oss_service(km)
    orchestrator = AgentOrchestrator(qwen, github, db, oss_service=oss)

    result = orchestrator.process_issue(
        repo_full_name=req.repo,
        issue_number=req.issue_number,
        title=gh_issue.title,
        body=gh_issue.body or "",
        author=gh_issue.user.login if gh_issue.user else "unknown",
        labels=[l.name for l in gh_issue.labels],
    )
    return JSONResponse(result)


@router.get("/health")
async def agent_health_check():
    settings = get_settings()
    km = get_key_manager()
    health = {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat(), "components": {}}

    if km.has_keys("qwen"):
        try:
            qwen = QwenService(km.get_qwen_api_key(), km.get_qwen_base_url() or settings.QWEN_BASE_URL)
            health["components"]["qwen_cloud"] = qwen.health_check()
        except Exception as e:
            health["components"]["qwen_cloud"] = {"status": "unhealthy", "error": str(e)}
    else:
        health["components"]["qwen_cloud"] = {"status": "not_configured"}

    if km.has_keys("github"):
        try:
            github = GitHubService(km.get_github_token())
            health["components"]["github"] = github.health_check()
        except Exception as e:
            health["components"]["github"] = {"status": "unhealthy", "error": str(e)}
    else:
        health["components"]["github"] = {"status": "not_configured"}

    return JSONResponse(health)
