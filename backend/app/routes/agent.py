"""Direct agent interaction for testing (no GitHub webhook required),
plus health monitoring."""

import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
import structlog

from ..config import get_settings, get_key_manager
from ..models.database import get_db
from .dashboard import get_current_user
from ..services.llm_service import LLMConfigError, build_llm_service, is_llm_configured
from ..services.github_service import GitHubService

logger = structlog.get_logger()
# Every route here spends LLM quota and/or acts with the owner's GitHub token,
# so the whole router requires the dashboard session.
router = APIRouter(prefix="/api/agent", tags=["agent"], dependencies=[Depends(get_current_user)])


class TestIssueRequest(BaseModel):
    title: str = Field(..., min_length=5, max_length=500)
    body: str = Field(..., min_length=10, max_length=10000)
    repo: str = Field(default="test/test-repo")
    labels: list = Field(default_factory=list)


@router.post("/test")
def test_agent_with_mock_issue(req: TestIssueRequest, db: Session = Depends(get_db)):
    km = get_key_manager()
    settings = get_settings()

    try:
        llm = build_llm_service(km, settings)
    except LLMConfigError as e:
        return JSONResponse({"success": False, "message": f"LLM not configured: {e}"}, status_code=400)

    classification = llm.classify_issue(req.title, req.body, req.labels)
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
        solution = llm.generate_solution(req.title, req.body, parsed_cls["classification"])
        if solution["success"]:
            result["solution"] = json.loads(solution["content"])
            result["solution_tokens"] = solution.get("tokens_used")
            result["solution_latency_ms"] = solution.get("latency_ms")

    return JSONResponse(result)


class RunRealPipelineRequest(BaseModel):
    repo: str = Field(..., pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=200, description="owner/repo of the target issue, e.g. 'titraio/titra'")
    issue_number: int = Field(..., gt=0, description="An existing, real issue number in that repo")


@router.post("/run", status_code=202)
def run_real_pipeline(req: RunRealPipelineRequest, db: Session = Depends(get_db)):
    from ..services.job_service import admit, AdmissionLimited
    try:
        job, created = admit(db, "issue", req.repo, {"issue_number": req.issue_number}, limit=get_settings().MAX_JOBS_PER_MINUTE)
    except AdmissionLimited:
        raise HTTPException(429, "Admission limit reached", headers={"Retry-After": "60"}) from None
    return {"success": True, "status": "queued" if created else "duplicate", "job_id": job.id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, db: Session = Depends(get_db)):
    from ..models.jobs import Job
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str, db: Session = Depends(get_db)):
    from ..models.jobs import Job
    from ..services.job_service import admit, AdmissionLimited
    job = db.get(Job, job_id)
    if not job or job.status != "failed":
        raise HTTPException(409, "Only failed jobs can be replayed")
    try:
        fresh, _ = admit(db, job.kind, job.repository, job.payload, limit=get_settings().MAX_JOBS_PER_MINUTE)
    except AdmissionLimited:
        raise HTTPException(429, "Admission limit reached") from None
    return {"success": True, "job_id": fresh.id}


@router.get("/search-repos")
def search_repos(q: str, max_results: int = 10):
    """Search for repos by name, for the 'pick a repo' flow."""
    km = get_key_manager()
    if not km.has_keys("github"):
        return JSONResponse({"success": False, "message": "GitHub token not configured"}, status_code=400)
    github = GitHubService(km.get_github_token())
    result = github.search_repos(q, max_results=max_results)
    return JSONResponse(result)


@router.get("/repo-issues")
def repo_issues(repo: str, state: str = "open", labels: str = "", max_results: int = 30):
    """List issues for one specific repo the user has already chosen."""
    km = get_key_manager()
    if not km.has_keys("github"):
        return JSONResponse({"success": False, "message": "GitHub token not configured"}, status_code=400)
    github = GitHubService(km.get_github_token())
    label_list = [l.strip() for l in labels.split(",") if l.strip()]
    result = github.list_repo_issues(repo, state=state, labels=label_list or None, max_results=max_results)
    return JSONResponse(result)


@router.get("/discover")
def discover_issues(
    language: str = None,
    labels: str = "good first issue",
    max_results: int = 25,
):
    """
    Browse open, unclaimed issues across public GitHub repos worth
    working on -- e.g. issues labeled "good first issue" or "help
    wanted". `labels` accepts a comma-separated list.

    This does NOT run the agent on anything; it's purely for discovery.
    Pick one from the results and pass its repo + issue_number to
    POST /api/agent/run to actually work on it.
    """
    km = get_key_manager()
    if not km.has_keys("github"):
        return JSONResponse({"success": False, "message": "GitHub token not configured"}, status_code=400)

    github = GitHubService(km.get_github_token())
    label_list = [l.strip() for l in labels.split(",") if l.strip()]
    result = github.discover_issues(language=language or None, labels=label_list, max_results=max_results)
    return JSONResponse(result)


@router.get("/health")
def agent_health_check():
    settings = get_settings()
    km = get_key_manager()
    health = {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat(), "components": {}}

    if is_llm_configured(km, settings):
        try:
            health["components"]["llm"] = build_llm_service(km, settings).health_check()
        except Exception as e:
            health["components"]["llm"] = {"status": "unhealthy", "error": str(e)}
    else:
        health["components"]["llm"] = {"status": "not_configured"}

    if km.has_keys("github"):
        try:
            github = GitHubService(km.get_github_token())
            health["components"]["github"] = github.health_check()
        except Exception as e:
            health["components"]["github"] = {"status": "unhealthy", "error": str(e)}
    else:
        health["components"]["github"] = {"status": "not_configured"}

    return JSONResponse(health)
