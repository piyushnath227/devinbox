"""Wrapper around the GitHub API (via PyGithub) for branch/commit/PR
automation and comment-based approval detection."""

import datetime
from typing import Optional, Dict, Any
from github import Github, GithubException
import structlog

logger = structlog.get_logger()


class GitHubService:
    APPROVAL_COMMAND = "/approve"
    BRANCH_PREFIX = "devinbox"

    def __init__(self, access_token: str):
        self.token = access_token
        self.client = Github(access_token)
        try:
            self.authenticated_user = self.client.get_user().login
            logger.info("github_service_initialized", authenticated_as=self.authenticated_user)
        except GithubException as e:
            logger.error("github_auth_failed", error=str(e))
            raise ValueError(f"Invalid GitHub token: {e}")

    def get_repository(self, repo_full_name: str):
        return self.client.get_repo(repo_full_name)

    def read_file(self, repo_full_name: str, path: str, ref: Optional[str] = None) -> Dict[str, Any]:
        """Read a file's contents. Used as a tool call so the agent can inspect
        real code before proposing a fix."""
        try:
            repo = self.get_repository(repo_full_name)
            kwargs = {"ref": ref} if ref else {}
            file_content = repo.get_contents(path, **kwargs)
            if isinstance(file_content, list):
                return {"success": False, "error": f"'{path}' is a directory, not a file"}
            content = file_content.decoded_content.decode("utf-8", errors="replace")
            MAX_CHARS = 8000
            truncated = len(content) > MAX_CHARS
            return {
                "success": True, "path": path, "content": content[:MAX_CHARS],
                "truncated": truncated, "size": file_content.size,
            }
        except GithubException as e:
            logger.warning("read_file_failed", path=path, error=str(e))
            return {"success": False, "error": f"Could not read '{path}': {e.data.get('message', str(e)) if e.data else str(e)}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def search_code(self, repo_full_name: str, query: str, max_results: int = 10) -> Dict[str, Any]:
        """Search the repo's code via GitHub's search API. Used as a tool call
        to locate relevant files before deciding what to modify."""
        try:
            full_query = f"{query} repo:{repo_full_name}"
            results = self.client.search_code(full_query)
            matches = []
            for i, item in enumerate(results):
                if i >= max_results:
                    break
                matches.append({"path": item.path, "name": item.name})
            return {"success": True, "query": query, "matches": matches}
        except GithubException as e:
            logger.warning("search_code_failed", query=query, error=str(e))
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_repository_structure(self, repo_full_name: str) -> Dict[str, Any]:
        try:
            repo = self.get_repository(repo_full_name)
            contents = repo.get_contents("")
            files = []
            items = contents if isinstance(contents, list) else [contents]
            for item in items[:50]:
                files.append({"name": item.name, "path": item.path, "type": item.type})
            return {
                "repository": repo_full_name,
                "default_branch": repo.default_branch,
                "language": repo.language,
                "files": files,
            }
        except Exception as e:
            logger.warning("structure_fetch_failed", error=str(e))
            return {"repository": repo_full_name, "error": str(e), "files": []}

    def create_fix_branch(self, repo_full_name: str, issue_number: int, base_branch: str = "main") -> Dict[str, Any]:
        try:
            repo = self.get_repository(repo_full_name)
            base_ref = repo.get_git_ref(f"heads/{base_branch}")
            base_sha = base_ref.object.sha
            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
            branch_name = f"{self.BRANCH_PREFIX}/issue-{issue_number}-{timestamp}"
            repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_sha)
            logger.info("branch_created", repo=repo_full_name, branch=branch_name)
            return {"success": True, "branch_name": branch_name}
        except GithubException as e:
            logger.error("branch_creation_failed", error=str(e))
            return {"success": False, "error": str(e)}

    def commit_changes(self, repo_full_name: str, branch_name: str, file_path: str, content: str, commit_message: str) -> Dict[str, Any]:
        try:
            repo = self.get_repository(repo_full_name)
            try:
                existing = repo.get_contents(file_path, ref=branch_name)
                repo.update_file(file_path, commit_message, content, existing.sha, branch=branch_name)
                return {"success": True, "action": "updated"}
            except GithubException as e:
                if e.status == 404:
                    repo.create_file(file_path, commit_message, content, branch=branch_name)
                    return {"success": True, "action": "created"}
                raise
        except GithubException as e:
            logger.error("commit_failed", file=file_path, error=str(e))
            return {"success": False, "error": str(e)}

    def create_pull_request(self, repo_full_name: str, branch_name: str, title: str, body: str, base_branch: str = "main", draft: bool = True) -> Dict[str, Any]:
        try:
            repo = self.get_repository(repo_full_name)
            pr = repo.create_pull(title=title, body=body, head=branch_name, base=base_branch, draft=draft)
            logger.info("pr_created", repo=repo_full_name, pr_number=pr.number)
            return {"success": True, "pr_number": pr.number, "pr_url": pr.html_url}
        except GithubException as e:
            logger.error("pr_creation_failed", error=str(e))
            return {"success": False, "error": str(e)}

    def post_comment(self, repo_full_name: str, issue_number: int, comment: str) -> Dict[str, Any]:
        try:
            repo = self.get_repository(repo_full_name)
            issue = repo.get_issue(number=issue_number)
            posted = issue.create_comment(comment)
            return {"success": True, "comment_url": posted.html_url}
        except GithubException as e:
            logger.error("comment_failed", error=str(e))
            return {"success": False, "error": str(e)}

    def check_for_approval(self, repo_full_name: str, pr_number: int) -> Dict[str, Any]:
        try:
            repo = self.get_repository(repo_full_name)
            pr = repo.get_pull(pr_number)
            for comment in pr.get_issue_comments():
                if self.APPROVAL_COMMAND in comment.body and comment.user.login != self.authenticated_user:
                    return {"approved": True, "approved_by": comment.user.login}
            return {"approved": False}
        except GithubException as e:
            logger.error("approval_check_failed", error=str(e))
            return {"approved": False, "error": str(e)}

    def merge_pull_request(self, repo_full_name: str, pr_number: int, merge_method: str = "squash") -> Dict[str, Any]:
        try:
            repo = self.get_repository(repo_full_name)
            pr = repo.get_pull(pr_number)
            if not pr.mergeable:
                return {"success": False, "error": "PR has conflicts"}
            result = pr.merge(merge_method=merge_method)
            return {"success": result.merged}
        except GithubException as e:
            logger.error("pr_merge_failed", error=str(e))
            return {"success": False, "error": str(e)}

    def get_issue(self, repo_full_name: str, issue_number: int) -> Dict[str, Any]:
        try:
            repo = self.get_repository(repo_full_name)
            issue = repo.get_issue(number=issue_number)
            return {
                "success": True,
                "number": issue.number,
                "title": issue.title,
                "body": issue.body or "",
                "author": issue.user.login if issue.user else "unknown",
                "labels": [label.name for label in issue.labels],
            }
        except GithubException as e:
            return {"success": False, "error": str(e)}

    def health_check(self) -> Dict[str, Any]:
        try:
            user = self.client.get_user()
            rate = self.client.get_rate_limit()
            return {
                "status": "healthy",
                "authenticated_as": user.login,
                "rate_limit_remaining": rate.core.remaining,
            }
        except GithubException as e:
            logger.error("github_health_check_failed", error=str(e))
            return {"status": "unhealthy", "error": str(e)}
