"""Webhook idempotency: GitHub retries delivery on timeout/5xx, so
re-processing an issue that already has a PR must be a no-op."""

from app.services.agent_orchestrator import AgentOrchestrator
from app.models.issues import IssueRecord, IssueStatus


class FakeQwenService:
    """Tracks call count so tests can assert the pipeline short-circuited
    before reaching classification."""

    def __init__(self):
        self.classify_calls = 0

    def classify_issue(self, *args, **kwargs):
        self.classify_calls += 1
        return {"success": True, "content": '{"classification": "bug", "confidence": 0.9, "is_actionable": true}'}


class FakeGitHubService:
    def post_comment(self, *args, **kwargs):
        return {"success": True}


def _make_issue(db, status, repository="test-owner/test-repo", issue_number=42, **kwargs):
    issue = IssueRecord(
        repository=repository, issue_number=issue_number, title="Bug: something broke",
        body="details", author="tester", labels=[], status=status,
    )
    for key, value in kwargs.items():
        setattr(issue, key, value)
    db.add(issue)
    db.commit()
    db.refresh(issue)
    return issue


def test_process_issue_skips_when_already_pr_created(test_db, sample_issue_data):
    _make_issue(test_db, IssueStatus.PR_CREATED, pr_number=7, pr_url="https://github.com/x/y/pull/7")
    qwen = FakeQwenService()
    orchestrator = AgentOrchestrator(qwen, FakeGitHubService(), test_db)

    result = orchestrator.process_issue(
        repo_full_name="test-owner/test-repo", issue_number=42, title="Bug: something broke",
        body="details", author="tester", labels=[],
    )

    assert result["status"] == "skipped_duplicate"
    assert qwen.classify_calls == 0  # never started re-processing


def test_process_issue_skips_when_already_merged(test_db):
    _make_issue(test_db, IssueStatus.MERGED, pr_number=7)
    qwen = FakeQwenService()
    orchestrator = AgentOrchestrator(qwen, FakeGitHubService(), test_db)

    result = orchestrator.process_issue(
        repo_full_name="test-owner/test-repo", issue_number=42, title="Bug: something broke",
        body="details", author="tester", labels=[],
    )

    assert result["status"] == "skipped_duplicate"
    assert qwen.classify_calls == 0


def test_process_issue_proceeds_when_status_is_received(test_db):
    _make_issue(test_db, IssueStatus.RECEIVED)
    qwen = FakeQwenService()
    orchestrator = AgentOrchestrator(qwen, FakeGitHubService(), test_db)

    orchestrator.process_issue(
        repo_full_name="test-owner/test-repo", issue_number=42, title="Bug: something broke",
        body="details", author="tester", labels=[],
    )

    assert qwen.classify_calls == 1  # normal processing was attempted
