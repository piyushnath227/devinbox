"""Merge-conflict handling: a PR that can't be auto-merged because it
conflicts with the base branch should be flagged (status + PR comment)
instead of failing silently, and should still be retried once a
maintainer fixes the branch and comments /approve again."""

from app.services.agent_orchestrator import AgentOrchestrator
from app.models.issues import IssueRecord, IssueStatus


class FakeQwenService:
    def classify_issue(self, *args, **kwargs):
        return {"success": True, "content": '{"classification": "bug", "confidence": 0.9, "is_actionable": true}'}


class FakeGitHubService:
    """Configurable fake: approval is always granted, and merge either
    succeeds or fails with a caller-supplied error message."""

    def __init__(self, merge_error="Pull Request is not mergeable (conflict)", fail_times=1):
        self.merge_error = merge_error
        self.fail_times = fail_times
        self.merge_calls = 0
        self.comments = []

    def check_for_approval(self, *args, **kwargs):
        return {"approved": True, "approved_by": "maintainer"}

    def merge_pull_request(self, *args, **kwargs):
        self.merge_calls += 1
        if self.merge_calls <= self.fail_times:
            return {"success": False, "error": self.merge_error}
        return {"success": True}

    def post_comment(self, repo_full_name, issue_number, comment):
        self.comments.append(comment)
        return {"success": True}


def _make_issue(db, status, **kwargs):
    issue = IssueRecord(
        repository="test-owner/test-repo", issue_number=42, title="Bug: something broke",
        body="details", author="tester", labels=[], status=status, pr_number=7,
        branch_name="devinbox/issue-42",
    )
    for key, value in kwargs.items():
        setattr(issue, key, value)
    db.add(issue)
    db.commit()
    db.refresh(issue)
    return issue


def test_conflicting_pr_is_flagged_not_dropped(test_db):
    _make_issue(test_db, IssueStatus.PR_CREATED)
    github = FakeGitHubService(fail_times=99)  # always conflicts
    orchestrator = AgentOrchestrator(FakeQwenService(), github, test_db)

    result = orchestrator.check_and_merge_approved_prs("test-owner/test-repo")

    issue = test_db.query(IssueRecord).filter_by(issue_number=42).first()
    assert issue.status == IssueStatus.MERGE_CONFLICT
    assert result["conflicts"] == 1
    assert result["merged"] == 0
    assert any("conflict" in c.lower() for c in github.comments)


def test_non_conflict_merge_failure_does_not_flag_conflict(test_db):
    _make_issue(test_db, IssueStatus.PR_CREATED)
    github = FakeGitHubService(merge_error="required status check has not passed", fail_times=99)
    orchestrator = AgentOrchestrator(FakeQwenService(), github, test_db)

    result = orchestrator.check_and_merge_approved_prs("test-owner/test-repo")

    issue = test_db.query(IssueRecord).filter_by(issue_number=42).first()
    assert issue.status == IssueStatus.PR_CREATED  # unchanged, not marked as conflict
    assert result["conflicts"] == 0
    assert result["merged"] == 0


def test_resolved_conflict_is_retried_and_merges(test_db):
    _make_issue(test_db, IssueStatus.MERGE_CONFLICT)
    # First check_and_merge call the maintainer fixed it, so the merge now succeeds.
    github = FakeGitHubService(fail_times=0)
    orchestrator = AgentOrchestrator(FakeQwenService(), github, test_db)

    result = orchestrator.check_and_merge_approved_prs("test-owner/test-repo")

    issue = test_db.query(IssueRecord).filter_by(issue_number=42).first()
    assert issue.status == IssueStatus.MERGED
    assert result["merged"] == 1
    assert github.merge_calls == 1  # a MERGE_CONFLICT issue is included in the retry pass
