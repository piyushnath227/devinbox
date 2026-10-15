"""
Job functions executed by the RQ worker process (backend/worker.py).

Each job creates and closes its own DB session -- these run in a
separate process from the FastAPI app, so there's no request-scoped
session to borrow. This mirrors the logic in routes/webhook.py's
BackgroundTasks functions; the two paths (queue vs. background task)
do the same work, just with different session lifecycles.
"""
import structlog

from .config import get_settings, get_key_manager
from .models.database import create_session
from .services.llm_service import build_llm_service, LLMConfigError
from .services.github_service import GitHubService
from .services.agent_orchestrator import AgentOrchestrator
from .services.audit_archive import build_audit_archive

logger = structlog.get_logger()


def process_issue_job(repo_full_name: str, issue_number: int, title: str,
                       body: str, author: str, labels: list):
    """RQ job: run the full issue-to-PR pipeline. Own DB session,
    own error handling -- a failed job here doesn't take down the
    worker process, it just gets logged and RQ marks the job failed."""
    db = create_session()
    try:
        km = get_key_manager()
        settings = get_settings()
        github_token = km.get_github_token()
        if not github_token:
            logger.error("worker_job_missing_keys", repo=repo_full_name, issue=issue_number)
            raise LLMConfigError("GitHub token missing")

        llm = build_llm_service(km, settings)  # raises LLMConfigError -> RQ marks job failed (visible, retryable)
        github = GitHubService(github_token)
        archive = build_audit_archive(km, settings)

        orchestrator = AgentOrchestrator(llm, github, db, audit_archive=archive)
        result = orchestrator.process_issue(repo_full_name, issue_number, title, body, author, labels)
        if result.get("status") == "failed":
            raise RuntimeError("Issue pipeline failed")
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
            raise LLMConfigError("GitHub token missing")

        github = GitHubService(github_token)
        llm = None  # merge checks do not need an LLM
        archive = build_audit_archive(km, settings)

        orchestrator = AgentOrchestrator(llm, github, db, audit_archive=archive)
        orchestrator.check_and_merge_approved_prs(repo_full_name)
        logger.info("worker_merge_job_completed", repo=repo_full_name)
    except Exception as e:
        logger.error("worker_merge_job_failed", repo=repo_full_name, error=str(e), exc_info=True)
        raise
    finally:
        db.close()


def execute_job(job_id: str, dispatch_token: str):
    """RQ notification handler. SQL owns state/retries; stale notifications are no-ops."""
    from .models.database import init_db
    from .services.job_service import claim, finish
    from .services.patch_service import PatchError
    from .services.llm_service import LLMConfigError
    settings = get_settings()
    # RQ forks: never reuse a parent's SQL connections in the child.
    init_db(settings.DATABASE_URL)
    with create_session() as db:
        job = claim(db, job_id, dispatch_token)
        if job is None:
            return {'status': 'stale_notification'}
        try:
            km = get_key_manager()
            token = km.get_github_token()
            if not token:
                raise LLMConfigError('GitHub credentials are not configured')
            github = GitHubService(token)
            llm = build_llm_service(km, settings) if job.kind == 'issue' else None
            orchestrator = AgentOrchestrator(llm, github, db, audit_archive=build_audit_archive(km, settings))
            if job.kind == 'issue':
                issue = github.get_repository(job.repository).get_issue(number=job.payload['issue_number'])
                if issue.pull_request is not None or issue.state != 'open':
                    raise PatchError('Target must be an open issue, not a pull request')
                result = orchestrator.process_issue(job.repository, issue.number, issue.title, issue.body or '',
                                                    issue.user.login if issue.user else 'unknown', [x.name for x in issue.labels])
            else:
                result = orchestrator.check_and_merge_approved_prs(job.repository, job.payload.get('pr_number'))
            if result.get('status') == 'failed':
                finish(db, job_id, dispatch_token, result=result, error='Pipeline failed; see activity log', retryable=result.get('retryable', True))
                raise RuntimeError('Pipeline failed; durable retry policy recorded')
            finish(db, job_id, dispatch_token, result=result)
            return result
        except Exception as exc:
            db.rollback()
            finish(db, job_id, dispatch_token, error='Execution failed; inspect server logs',
                   retryable=not isinstance(exc, (PatchError, LLMConfigError, ValueError)))
            logger.exception('execution_failed', job_id=job_id)
            raise
