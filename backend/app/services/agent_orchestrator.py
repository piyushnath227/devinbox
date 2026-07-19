"""Coordinates the full issue-to-PR pipeline:

RECEIVED -> ANALYZING -> CLASSIFIED -> GENERATING -> PR_CREATED -> MERGED
                                   \\-> CLOSED (spam / out_of_scope)

All generated code lands in a draft PR; merging requires a maintainer to
comment "/approve".
"""

import json
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
import structlog

from .qwen_service import QwenService
from .github_service import GitHubService
from ..models.issues import IssueRecord, IssueStatus
from ..models.activity_log import ActivityLog

logger = structlog.get_logger()

# Statuses meaning a PR already exists — guards against duplicate webhook retries.
_ALREADY_PROCESSED_STATUSES = (IssueStatus.PR_CREATED, IssueStatus.APPROVED, IssueStatus.MERGED)


class AgentOrchestrator:
    def __init__(
        self,
        qwen_service: QwenService,
        github_service: GitHubService,
        db_session: Session,
        oss_service: Optional[Any] = None,
    ):
        self.qwen = qwen_service
        self.github = github_service
        self.db = db_session
        self.oss = oss_service  # optional, for audit-trail archival

    def process_issue(
        self, repo_full_name: str, issue_number: int, title: str, body: str, author: str, labels: list
    ) -> Dict[str, Any]:
        start_time = time.time()
        issue = self._get_or_create_issue_record(repo_full_name, issue_number, title, body, author, labels)

        if issue.status in _ALREADY_PROCESSED_STATUSES:
            logger.info("skipping_already_processed_issue", issue_number=issue_number, status=issue.status.value)
            self._log(
                issue, "duplicate_webhook_skipped",
                f"Skipped duplicate webhook delivery — issue already at status '{issue.status.value}'",
            )
            return self._result(issue, "skipped_duplicate", start_time)

        self._log(issue, "processing_started", f"Started processing issue #{issue_number}: {title[:100]}")

        try:
            # Classify the issue
            self._set_status(issue, IssueStatus.ANALYZING)
            cls_result = self.qwen.classify_issue(title, body, labels)
            if not cls_result["success"]:
                self._fail(issue, f"Classification failed: {cls_result.get('error')}")
                return self._result(issue, "failed", start_time)

            classification = json.loads(cls_result["content"])
            issue.classification = classification.get("classification")
            issue.classification_confidence = classification.get("confidence", 0)
            issue.summary = classification.get("reasoning", "")
            self._set_status(issue, IssueStatus.CLASSIFIED)
            self._log(
                issue, "classification",
                f"Classified as '{issue.classification}' with {issue.classification_confidence:.0%} confidence",
                metadata=classification, tokens_used=cls_result.get("tokens_used"), latency_ms=cls_result.get("latency_ms"),
            )

            # Route non-actionable issues (spam / out of scope) instead of generating code
            if classification.get("classification") in ("spam", "out_of_scope") or not classification.get("is_actionable", False):
                return self._handle_non_actionable(issue, classification, repo_full_name, issue_number, start_time)

            # Generate a solution, letting Qwen inspect the repo via tools
            self._set_status(issue, IssueStatus.GENERATING)
            repo_context = self._get_repository_context(repo_full_name)
            tool_executor = self._make_tool_executor(repo_full_name)
            sol_result = self.qwen.generate_solution_with_tools(
                title, body, issue.classification, tool_executor, repo_context
            )
            if not sol_result["success"]:
                self._fail(issue, f"Solution generation failed: {sol_result.get('error')}")
                return self._result(issue, "failed", start_time)

            solution = json.loads(sol_result["content"])
            issue.solution_plan = solution.get("solution_approach", "")
            issue.language = solution.get("primary_language", "")
            issue.modified_files = solution.get("files_to_modify", [])
            tool_calls = sol_result.get("tool_calls", [])
            self._log(
                issue, "code_generation",
                f"Generated solution modifying {len(solution.get('files_to_modify', []))} file(s) "
                f"using {len(tool_calls)} repo tool call(s)",
                metadata=solution, tokens_used=sol_result.get("tokens_used"), latency_ms=sol_result.get("latency_ms"),
            )

            # Create the fix branch and commit changes
            branch_result = self.github.create_fix_branch(repo_full_name, issue_number)
            if not branch_result["success"]:
                self._fail(issue, f"Branch creation failed: {branch_result.get('error')}")
                self.github.post_comment(repo_full_name, issue_number, f"❌ Failed to create branch: {branch_result.get('error')}")
                return self._result(issue, "failed", start_time)

            issue.branch_name = branch_result["branch_name"]
            for change in solution.get("changes", []):
                self.github.commit_changes(
                    repo_full_name, issue.branch_name, change.get("file", ""),
                    self._extract_content_from_diff(change.get("code_diff", "")),
                    f"fix: {title[:72]} (Issue #{issue_number})",
                )
                self._archive_diff_to_oss(repo_full_name, issue_number, change)

            # Open the draft PR for human review
            pr_body = self._build_pr_description(issue, solution)
            pr_title = f"🤖 [DevInbox] Fix: {title[:60]} (Closes #{issue_number})"
            pr_result = self.github.create_pull_request(
                repo_full_name, issue.branch_name, pr_title, pr_body, draft=True
            )
            if not pr_result["success"]:
                self._fail(issue, f"PR creation failed: {pr_result.get('error')}")
                return self._result(issue, "failed", start_time)

            issue.pr_number = pr_result["pr_number"]
            issue.pr_url = pr_result["pr_url"]
            self._set_status(issue, IssueStatus.PR_CREATED)
            self._log(issue, "pr_created", f"Created draft PR #{pr_result['pr_number']}: {pr_result['pr_url']}")

            comment = (
                f"🤖 **DevInbox has created a fix for this issue!**\n\n"
                f"- **Classification:** {issue.classification}\n"
                f"- **Files modified:** {', '.join(issue.modified_files or ['unknown'])}\n\n"
                f"### 📝 Pull Request: #{pr_result['pr_number']}\n{pr_result['pr_url']}\n\n"
                f"**Review Required:** A human maintainer must review and comment `/approve` to merge.\n\n"
                f"---\n*Automated response by DevInbox AI Agent*"
            )
            self.github.post_comment(repo_full_name, issue_number, comment)

            issue.processed_at = datetime.now(timezone.utc)
            self.db.commit()
            self._archive_snapshot_to_oss(issue)
            return self._result(issue, "success", start_time)

        except Exception as e:
            logger.error("processing_unexpected_error", issue_number=issue_number, error=str(e))
            self._fail(issue, f"Unexpected error: {e}")
            return self._result(issue, "failed", start_time)

    def _handle_non_actionable(self, issue, classification, repo_full_name, issue_number, start_time):
        reason = classification.get("suggested_action", classification.get("classification", ""))
        self._set_status(issue, IssueStatus.CLOSED)
        issue.processed_at = datetime.now(timezone.utc)
        self._log(issue, "issue_closed", f"Issue closed: {reason}")
        comment = (
            f"🤖 **DevInbox Analysis Complete**\n\n"
            f"This issue was classified as **{classification.get('classification')}**.\n\n"
            f"**Reason:** {reason}\n\n"
            f"This requires human attention and cannot be automatically resolved.\n\n"
            f"---\n*Automated response by DevInbox AI Agent*"
        )
        self.github.post_comment(repo_full_name, issue_number, comment)
        self.db.commit()
        self._archive_snapshot_to_oss(issue)
        return {"status": "closed", "issue_id": issue.id}

    def check_and_merge_approved_prs(self, repo_full_name: str) -> Dict[str, Any]:
        open_issues = (
            self.db.query(IssueRecord)
            .filter(IssueRecord.repository == repo_full_name, IssueRecord.status == IssueStatus.PR_CREATED)
            .all()
        )
        merged = []
        for issue in open_issues:
            approval = self.github.check_for_approval(repo_full_name, issue.pr_number)
            if approval.get("approved"):
                merge_result = self.github.merge_pull_request(repo_full_name, issue.pr_number)
                if merge_result.get("success"):
                    issue.status = IssueStatus.MERGED
                    issue.approved_by = approval["approved_by"]
                    issue.approved_at = datetime.now(timezone.utc)
                    self._log(issue, "approval_granted", f"PR #{issue.pr_number} merged by {approval['approved_by']}")
                    self.github.post_comment(
                        repo_full_name, issue.issue_number,
                        f"✅ Merged! Approved by @{approval['approved_by']}. Thanks for using DevInbox! 🎉",
                    )
                    merged.append(issue.issue_number)
        self.db.commit()
        return {"checked": len(open_issues), "merged": len(merged)}

    # Helpers

    def _get_or_create_issue_record(self, repo, num, title, body, author, labels) -> IssueRecord:
        existing = self.db.query(IssueRecord).filter_by(repository=repo, issue_number=num).first()
        if existing:
            existing.title, existing.body, existing.author, existing.labels = title, body, author, labels
            self.db.commit()
            return existing
        record = IssueRecord(
            repository=repo, issue_number=num, title=title, body=body,
            author=author, labels=labels, status=IssueStatus.RECEIVED,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def _set_status(self, issue: IssueRecord, status: IssueStatus):
        issue.status = status
        issue.updated_at = datetime.now(timezone.utc)
        self.db.commit()

    def _fail(self, issue: IssueRecord, description: str):
        self._set_status(issue, IssueStatus.FAILED)
        self._log(issue, "error", description, level="error", is_success=False)

    def _log(self, issue, action_type, description, metadata=None, level="info", is_success=True, tokens_used=None, latency_ms=None):
        entry = ActivityLog(
            issue_id=issue.id, action_type=action_type, description=description,
            log_metadata=metadata or {}, level=level, is_success=is_success,
            tokens_used=tokens_used, latency_ms=latency_ms,
        )
        self.db.add(entry)
        self.db.commit()

    def _get_repository_context(self, repo_full_name: str) -> Optional[str]:
        try:
            structure = self.github.get_repository_structure(repo_full_name)
            if structure.get("error"):
                return None
            lines = [f"Repository: {structure['repository']}", f"Language: {structure.get('language')}"]
            for f in structure.get("files", [])[:30]:
                lines.append(f"  - {f['path']} ({f['type']})")
            return "\n".join(lines)
        except Exception:
            return None

    def _make_tool_executor(self, repo_full_name: str):
        """Build a tool_executor(name, args) -> str closure bound to this repo."""
        def executor(tool_name: str, args: dict) -> str:
            try:
                if tool_name == "search_repo":
                    result = self.github.search_code(repo_full_name, args.get("query", ""))
                elif tool_name == "read_file":
                    result = self.github.read_file(repo_full_name, args.get("path", ""))
                else:
                    result = {"success": False, "error": f"Unknown tool '{tool_name}'"}
            except Exception as e:
                result = {"success": False, "error": str(e)}
            return json.dumps(result)
        return executor

    def _archive_diff_to_oss(self, repo_full_name: str, issue_number: int, change: dict) -> None:
        """Best-effort archive of a generated diff to OSS; never raises."""
        if not self.oss:
            return
        try:
            self.oss.archive_diff(repo_full_name, issue_number, change.get("file", "unknown"), change.get("code_diff", ""))
        except Exception as e:
            logger.warning("oss_diff_archive_error", error=str(e))

    def _archive_snapshot_to_oss(self, issue: IssueRecord) -> None:
        """Best-effort archive of the issue's pipeline snapshot to OSS."""
        if not self.oss:
            return
        try:
            self.oss.archive_issue_snapshot(issue.repository, issue.issue_number, issue.to_dict())
        except Exception as e:
            logger.warning("oss_snapshot_archive_error", error=str(e))

    def _extract_content_from_diff(self, diff_text: str) -> str:
        lines = diff_text.split("\n")
        content_lines = []
        for line in lines:
            if line.startswith("+") and not line.startswith("+++"):
                content_lines.append(line[1:])
            elif not line.startswith(("-", "---", "@@", "diff", "index")):
                content_lines.append(line)
        return "\n".join(content_lines)

    def _build_pr_description(self, issue: IssueRecord, solution: dict) -> str:
        files = "\n".join(f"- `{f}`" for f in solution.get("files_to_modify", []))
        return f"""## 🤖 Automated Fix by DevInbox

Resolves issue **#{issue.issue_number}**.

### 🔍 Analysis
{solution.get('analysis', 'N/A')}

### 💡 Solution Approach
{solution.get('solution_approach', 'N/A')}

### 📁 Files Modified
{files}

### 🧪 Testing Notes
{solution.get('testing_notes', 'Please review and test manually.')}

### ⚠️ Potential Risks
{solution.get('potential_risks', 'None identified.')}

### 📊 Confidence: {solution.get('confidence', 0):.0%}

---
### 👤 Human Review Required
This is a **draft PR**. Comment `/approve` to merge.

> Generated by DevInbox AI Agent using Qwen Cloud
"""

    def _result(self, issue: IssueRecord, status: str, start_time: float) -> Dict[str, Any]:
        return {
            "status": status,
            "issue_id": issue.id,
            "issue_number": issue.issue_number,
            "processing_time_seconds": round(time.time() - start_time, 2),
            "classification": issue.classification,
            "pr_url": issue.pr_url,
            "pr_number": issue.pr_number,
        }
