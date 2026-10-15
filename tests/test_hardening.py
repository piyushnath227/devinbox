"""Security/correctness regressions for the hardened release."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
import difflib

import pytest
from sqlalchemy.exc import IntegrityError

from app.config.key_manager import KeyManager, SecretReadError
from app.models.jobs import Job, Delivery
from app.models.issues import IssueRecord
from app.services.patch_service import apply_patch, PatchError, Solution
from app.services.job_service import admit, dispatch_batch, claim, finish, now, AdmissionLimited
from app.services.github_service import GitHubService


def patch(old, new, path='app.py'):
    return ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile='a/' + path, tofile='b/' + path))


def test_patch_preserves_lines_outside_hunk_and_trailing_newline():
    original = ''.join(f'x{i} = {i}\n' for i in range(30))
    wanted = original.replace('x15 = 15', 'x15 = 42')
    assert apply_patch(original, patch(original, wanted), 'app.py') == wanted


def test_new_file_patch():
    diff = '--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+answer = 42\n'
    assert apply_patch(None, diff, 'new.py') == 'answer = 42\n'


@pytest.mark.parametrize('path', ['../escape', '/tmp/escape', '.git/config', '.github/workflows/run.yml', '.env', 'x/../../escape'])
def test_reject_unsafe_paths(path):
    with pytest.raises(PatchError):
        apply_patch('a\n', 'anything', path)


def test_reject_mismatching_source_and_invalid_python():
    with pytest.raises(PatchError):
        apply_patch('x = 5\n', patch('x = 1\n', 'x = 2\n'), 'app.py')
    with pytest.raises(PatchError):
        apply_patch('x = 1\n', patch('x = 1\n', 'def (\n'), 'app.py')


def test_reject_extra_target_and_no_change():
    diff = patch('x=1\n', 'x=2\n') + patch('y=1\n', 'y=2\n', 'other.py')
    with pytest.raises(PatchError):
        apply_patch('x=1\n', diff, 'app.py')
    with pytest.raises(PatchError):
        apply_patch('x=1\n', '', 'app.py')


def test_solution_rejects_empty_changes_and_unbounded_confidence():
    with pytest.raises(ValueError):
        Solution.model_validate({'changes': [], 'confidence': 1.5})


def test_corrupt_admin_secret_is_not_missing(tmp_path):
    km = KeyManager(str(tmp_path), 'a-valid-secret')
    km._store.put('admin', b'not-fernet-data')
    with pytest.raises(SecretReadError):
        km.get_admin_password_hash()


def test_wrong_encryption_key_fails_closed(tmp_path):
    km = KeyManager(str(tmp_path), 'original-key')
    assert km.save_admin_password_hash('hash')
    with pytest.raises(SecretReadError):
        KeyManager(str(tmp_path), 'different-key').get_admin_password_hash()


def test_delivery_deduplication_survives_completion(test_db):
    a, created = admit(test_db, 'issue', 'o/r', {'issue_number': 1}, 'delivery-a')
    assert created
    a.status, a.active_key = 'succeeded', None
    test_db.commit()
    b, created = admit(test_db, 'issue', 'o/r', {'issue_number': 1}, 'delivery-a')
    assert not created and b.id == a.id
    assert test_db.query(Job).count() == 1


def test_active_issue_coalesces_distinct_deliveries(test_db):
    a, _ = admit(test_db, 'issue', 'o/r', {'issue_number': 1}, 'a')
    b, created = admit(test_db, 'issue', 'o/r', {'issue_number': 1}, 'b')
    assert a.id == b.id and not created
    assert test_db.query(Delivery).count() == 2


def test_admission_limit_does_not_lose_delivery(test_db):
    admit(test_db, 'issue', 'o/r', {'issue_number': 1}, 'a', limit=1)
    with pytest.raises(AdmissionLimited):
        admit(test_db, 'issue', 'o/r', {'issue_number': 2}, 'b', limit=1)
    assert test_db.get(Delivery, 'b') is None


def test_queue_failure_leaves_durable_pending_job(test_db):
    job, _ = admit(test_db, 'issue', 'o/r', {'issue_number': 1}, 'a')
    queue = MagicMock(); queue.enqueue.side_effect = ConnectionError('offline')
    assert dispatch_batch(test_db, queue) == 0
    test_db.refresh(job)
    assert job.status == 'pending' and job.dispatch_token is None


def test_only_one_worker_can_claim_and_stale_notification_cannot_run(test_db):
    job, _ = admit(test_db, 'issue', 'o/r', {'issue_number': 1})
    dispatch_batch(test_db, MagicMock())
    test_db.refresh(job)
    assert claim(test_db, job.id, 'wrong-token') is None
    token = job.dispatch_token
    assert claim(test_db, job.id, token) is not None
    assert claim(test_db, job.id, token) is None
    finish(test_db, job.id, token, result={'status': 'success'})
    test_db.refresh(job)
    assert job.status == 'succeeded' and job.active_key is None


def test_retry_backoff_and_permanent_failures(test_db):
    job, _ = admit(test_db, 'issue', 'o/r', {'issue_number': 1})
    dispatch_batch(test_db, MagicMock()); test_db.refresh(job)
    token = job.dispatch_token
    claim(test_db, job.id, token)
    finish(test_db, job.id, token, error='provider timeout')
    test_db.refresh(job)
    assert job.status == 'pending' and job.active_key is not None
    job.available_at = now() - timedelta(seconds=1); test_db.commit()
    dispatch_batch(test_db, MagicMock()); test_db.refresh(job)
    token = job.dispatch_token
    claim(test_db, job.id, token)
    finish(test_db, job.id, token, error='invalid patch', retryable=False)
    test_db.refresh(job)
    assert job.status == 'failed' and job.active_key is None


def test_expired_job_is_recovered(test_db):
    job, _ = admit(test_db, 'issue', 'o/r', {'issue_number': 1})
    job.status, job.lease_until = 'running', now() - timedelta(seconds=1)
    test_db.commit()
    dispatch_batch(test_db, MagicMock()); test_db.refresh(job)
    assert job.status == 'dispatched'


def test_issue_uniqueness_is_enforced(test_db):
    test_db.add_all([IssueRecord(repository='o/r', issue_number=1, title='x'), IssueRecord(repository='o/r', issue_number=1, title='y')])
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def github_with_pr(**kwargs):
    svc = GitHubService.__new__(GitHubService)
    repo = MagicMock()
    pr = SimpleNamespace(head=SimpleNamespace(sha='a' * 40), draft=False, mergeable=True, **kwargs)
    repo.get_pull.return_value = pr
    svc.get_repository = lambda _: repo
    return svc, pr


def test_merge_rejects_stale_approval_and_draft():
    svc, pr = github_with_pr()
    assert not svc.merge_pull_request('o/r', 1, expected_sha='b' * 40)['success']
    pr.draft = True
    assert 'draft' in svc.merge_pull_request('o/r', 1, expected_sha='a' * 40)['error']


def test_unknown_mergeability_is_not_reported_as_conflict():
    svc, pr = github_with_pr()
    pr.mergeable = None
    result = svc.merge_pull_request('o/r', 1, expected_sha='a' * 40)
    assert 'pending' in result['error'] and 'conflict' not in result['error']


def test_concurrent_admission_creates_one_active_job(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    engine = create_engine(f'sqlite:///{tmp_path}/concurrent.db', connect_args={'timeout': 20})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def submit(i):
        with factory() as db:
            job, _ = admit(db, 'issue', 'o/r', {'issue_number': 1}, f'delivery-{i}')
            return job.id
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(submit, range(12)))
    assert len(set(ids)) == 1
    with factory() as db:
        assert db.query(Job).count() == 1 and db.query(Delivery).count() == 12
    engine.dispose()


def test_resume_checkpoint_does_not_regenerate_code(test_db):
    from app.services.agent_orchestrator import AgentOrchestrator
    from app.models.issues import IssueStatus
    issue = IssueRecord(repository='o/r', issue_number=1, title='original', status=IssueStatus.FAILED,
                        checkpoint={'base_sha': 'a' * 40, 'base_branch': 'develop', 'title': 'original',
                                    'solution': {'changes': [], 'confidence': 0.9},
                                    'prepared': [{'path': 'app.py', 'content': 'x = 2\n', 'mode': '100644'}]})
    test_db.add(issue); test_db.commit()
    gh = MagicMock()
    gh.find_existing_fix_pr.return_value = None
    gh.has_write_access.return_value = True
    gh.create_pull_request.return_value = {'success': True, 'pr_number': 9, 'pr_url': 'https://github.com/o/r/pull/9'}
    llm = MagicMock()
    result = AgentOrchestrator(llm, gh, test_db).process_issue('o/r', 1, 'new title', 'body', 'author', [])
    assert result['status'] == 'success'
    llm.classify_issue.assert_not_called()
    assert gh.commit_patch_set.call_args.args[2] == 'a' * 40
    assert gh.create_pull_request.call_args.kwargs['base_branch'] == 'develop'


def test_atomic_commit_failure_never_creates_pr(test_db):
    from app.services.agent_orchestrator import AgentOrchestrator
    from app.models.issues import IssueStatus
    issue = IssueRecord(repository='o/r', issue_number=1, title='x', status=IssueStatus.FAILED,
                        checkpoint={'base_sha': 'a' * 40, 'base_branch': 'main', 'title': 'x',
                                    'solution': {'changes': [], 'confidence': 0.9}, 'prepared': []})
    test_db.add(issue); test_db.commit()
    gh = MagicMock(); gh.find_existing_fix_pr.return_value = None; gh.has_write_access.return_value = True
    gh.commit_patch_set.side_effect = RuntimeError('GitHub unavailable')
    result = AgentOrchestrator(MagicMock(), gh, test_db).process_issue('o/r', 1, 'x', 'body', 'author', [])
    assert result['status'] == 'failed'
    gh.create_pull_request.assert_not_called()


def test_merge_requires_successful_ci_and_pins_sha():
    svc = GitHubService.__new__(GitHubService)
    repo, pr, commit = MagicMock(), MagicMock(), MagicMock()
    svc.get_repository = lambda _: repo
    repo.get_pull.return_value = pr
    repo.get_commit.return_value = commit
    pr.head.sha, pr.draft, pr.mergeable = 'a' * 40, False, True
    commit.get_combined_status.return_value.statuses = []
    commit.get_check_runs.return_value = []
    assert not svc.merge_pull_request('o/r', 1, expected_sha='a' * 40)['success']
    pr.merge.assert_not_called()
    commit.get_check_runs.return_value = [SimpleNamespace(status='completed', conclusion='success')]
    pr.merge.return_value.merged = True
    assert svc.merge_pull_request('o/r', 1, expected_sha='a' * 40)['success']
    pr.merge.assert_called_once_with(merge_method='squash', sha='a' * 40)


def test_stale_approval_does_not_count():
    svc = GitHubService.__new__(GitHubService)
    svc.authenticated_user = 'bot'
    repo, pr = MagicMock(), MagicMock()
    svc.get_repository = lambda _: repo
    repo.get_pull.return_value = pr
    pr.head.sha = 'b' * 40
    pr.get_issue_comments.return_value = [SimpleNamespace(body='/approve ' + 'a' * 40, user=SimpleNamespace(login='owner'))]
    repo.get_collaborator_permission.return_value = 'admin'
    assert svc.check_for_approval('o/r', 1) == {'approved': False}


def test_worker_claim_renews_lease_after_waiting_in_queue(test_db):
    job, _ = admit(test_db, 'issue', 'o/r', {'issue_number': 91})
    dispatch_batch(test_db, MagicMock())
    test_db.refresh(job)
    job.lease_until = now() + timedelta(seconds=1)
    test_db.commit()
    claim(test_db, job.id, job.dispatch_token)
    test_db.refresh(job)
    stamp = job.lease_until
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=now().tzinfo)
    assert stamp > now() + timedelta(seconds=850)


def test_recovered_commit_with_unexpected_content_is_rejected():
    svc = GitHubService.__new__(GitHubService)
    repo = MagicMock()
    svc.get_repository = lambda _: repo
    repo.get_git_ref.return_value.object.sha = 'b' * 40
    commit = SimpleNamespace(message='expected', parents=[SimpleNamespace(sha='a' * 40)],
                             tree=SimpleNamespace(sha='tree'))
    repo.get_git_commit.return_value = commit
    repo.get_git_tree.return_value = SimpleNamespace(truncated=False, tree=[])
    with pytest.raises(PatchError, match='does not match'):
        svc.commit_patch_set('o/r', 'branch', 'a' * 40,
                             [{'path': 'x.py', 'content': 'x=1\n', 'mode': '100644'}], 'expected')
    repo.get_git_ref.return_value.edit.assert_not_called()
