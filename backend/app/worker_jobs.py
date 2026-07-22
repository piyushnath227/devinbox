"""
Job functions executed by the RQ worker process (backend/worker.py).

Each job creates and closes its own DB session -- these run in a
separate process from the FastAPI app, so there's no request-scoped
session to borrow. This mirrors the logic in routes/webhook.py's
BackgroundTasks functions; the two paths (queue vs. background task)
do the same work, just with different session lifecycles.
"""
from typing import Optional
import structlog

from .config import get_settings, get_key_manager
from .models.database import create_session
from .services.qwen_service import QwenService
from .services.github_service import GitHubService
from .services.agent_orchestrator import AgentOrchestrator
from .services.alibaba_oss_service import AlibabaOSSService

logger = structlog.get_logger()


def _build_oss_service(km) -> Optional[AlibabaOSSService]:
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


def process_issue_job(repo_full_name: str, issue_number: int, title: str,
                       body: str, author: str, labels: list):
    """RQ job: run the full issue-to-PR pipeline. Own DB session,
    own error handling -- a failed job here doesn't take down the
    worker process, it just gets logged and RQ marks the job failed."""
    db = create_session()
    try:
        km = get_key_manager()
        settings = get_settings()
        qwen_key = km.get_qwen_api_key()
        github_token = km.get_github_token()
        if not qwen_key or not github_token:
            logger.error("worker_job_missing_keys", repo=repo_full_name, issue=issue_number)
            return

        qwen = QwenService(qwen_key, km.get_qwen_base_url() or settings.QWEN_BASE_URL, km.get_qwen_model() or settings.QWEN_MODEL)
        github = GitHubService(github_token)
        oss = _build_oss_service(km)

        orchestrator = AgentOrchestrator(qwen, github, db, oss_service=oss)
        orchestrator.process_issue(repo_full_name, issue_number, title, body, author, labels)
        logger.info("worker_job_completed", repo=repo_full_name, issue=issue_number)
    except Exception as e:
        logger.error("worker_job_failed", repo=repo_full_name, issue=issue_number, error=str(e), exc_info=True)
        raise
    finally:
        db.close()


def check_and_merge_job(repo_full_name: str):
    """RQ job: check for /approve comments and merge eligible PRs."""
    db = create_session()
    try:
        km = get_key_manager()
        settings = get_settings()
        github_token = km.get_github_token()
        if not github_token:
            logger.error("worker_merge_job_missing_token", repo=repo_full_name)
            return

        github = GitHubService(github_token)
        qwen = QwenService(km.get_qwen_api_key() or "", km.get_qwen_base_url() or settings.QWEN_BASE_URL)
        oss = _build_oss_service(km)

        orchestrator = AgentOrchestrator(qwen, github, db, oss_service=oss)
        orchestrator.check_and_merge_approved_prs(repo_full_name)
        logger.info("worker_merge_job_completed", repo=repo_full_name)
    except Exception as e:
        logger.error("worker_merge_job_failed", repo=repo_full_name, error=str(e), exc_info=True)
        raise
    finally:
        db.close()
