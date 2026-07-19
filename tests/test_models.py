"""Tests for database models and basic issue lifecycle."""

from app.models.issues import IssueRecord, IssueStatus


def test_issue_record_creation(test_db, sample_issue_data):
    issue = IssueRecord(
        repository=sample_issue_data["repo_full_name"],
        issue_number=sample_issue_data["issue_number"],
        title=sample_issue_data["title"],
        body=sample_issue_data["body"],
        author=sample_issue_data["author"],
        labels=sample_issue_data["labels"],
        status=IssueStatus.RECEIVED,
    )
    test_db.add(issue)
    test_db.commit()

    saved = test_db.query(IssueRecord).filter(IssueRecord.issue_number == 42).first()
    assert saved is not None
    assert saved.status == IssueStatus.RECEIVED
    assert saved.repository == "test-owner/test-repo"


def test_issue_status_transitions(test_db, sample_issue_data):
    issue = IssueRecord(
        repository=sample_issue_data["repo_full_name"],
        issue_number=sample_issue_data["issue_number"],
        title=sample_issue_data["title"],
        body=sample_issue_data["body"],
        author=sample_issue_data["author"],
        labels=sample_issue_data["labels"],
        status=IssueStatus.RECEIVED,
    )
    test_db.add(issue)
    test_db.commit()

    for status in [IssueStatus.ANALYZING, IssueStatus.CLASSIFIED, IssueStatus.PR_CREATED, IssueStatus.MERGED]:
        issue.status = status
        test_db.commit()
        refreshed = test_db.query(IssueRecord).filter(IssueRecord.id == issue.id).first()
        assert refreshed.status == status
