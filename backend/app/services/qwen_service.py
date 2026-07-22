"""Wrapper around the Qwen Cloud API for issue classification and code
solution generation, with retries and tolerant JSON parsing."""

import json
import re
import time
from typing import Optional, Dict, List, Any
from openai import OpenAI
import structlog

logger = structlog.get_logger()


def parse_json_response(raw_content: Optional[str]) -> Dict[str, Any]:
    """Parse JSON from a model response, tolerating code fences and stray prose.

    Returns {"success": True, "data": {...}} or {"success": False, "error": "..."}.
    """
    if not raw_content:
        return {"success": False, "error": "Empty response content"}

    candidates = [raw_content.strip()]

    # Strip a markdown code fence if present.
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_content, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())

    # Otherwise fall back to the first {...} block.
    brace_match = re.search(r"\{.*\}", raw_content, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(0).strip())

    last_error = None
    for candidate in candidates:
        try:
            return {"success": True, "data": json.loads(candidate)}
        except json.JSONDecodeError as e:
            last_error = e
            continue

    logger.warning("json_parse_all_candidates_failed", error=str(last_error), raw_preview=raw_content[:200])
    return {"success": False, "error": f"Could not parse JSON from model response: {last_error}"}


class QwenService:
    CLASSIFICATION_SYSTEM_PROMPT = """You are an expert issue classifier for a software project.
Classify the GitHub issue into ONE of: bug, feature, question, documentation, spam, out_of_scope.

Respond with a JSON object ONLY, in this exact format:
{
  "classification": "bug|feature|question|documentation|spam|out_of_scope",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation",
  "is_actionable": true|false,
  "suggested_action": "what should happen next"
}
bug and feature are typically actionable (agent can generate code).
spam and out_of_scope are never actionable."""

    SOLUTION_SYSTEM_PROMPT = """You are an expert software engineer generating a code fix for a GitHub issue.
Only modify code directly related to the issue. Follow existing conventions. Never introduce breaking changes.

Respond with a JSON object ONLY, in this exact format:
{
  "analysis": "brief problem analysis",
  "solution_approach": "how you plan to fix it",
  "files_to_modify": ["path/to/file.py"],
  "primary_language": "python|javascript|etc",
  "changes": [{"file": "path/to/file.py", "description": "what changed", "code_diff": "unified diff"}],
  "confidence": 0.0-1.0,
  "testing_notes": "how to verify the fix",
  "potential_risks": "risks or side effects"
}"""

    SOLUTION_WITH_TOOLS_SYSTEM_PROMPT = SOLUTION_SYSTEM_PROMPT + """

Before proposing a fix, use the available tools to actually inspect the repository:
- Call `search_repo` to locate files that mention relevant symbols/keywords from the issue.
- Call `read_file` to read the real contents of any file you plan to modify, so your diff
  applies against real code instead of guesswork.
Both tools default to the repository's default branch. If the issue text references a specific
branch (e.g. "on the dev branch" or "in feature/x"), pass that branch name as `ref` -- note that
on non-default branches, search only matches file *names*, not file contents, since GitHub's
content search only covers the default branch; use read_file more liberally in that case.
Use as few tool calls as necessary (typically 1-4) to ground your fix in the real codebase,
then respond with the final JSON object described above and nothing else."""

    # Function-calling tool definitions for the OpenAI-compatible tools param.
    REPO_TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "search_repo",
                "description": (
                    "Search the repository's code for a keyword or symbol to locate files relevant to the "
                    "issue. Defaults to the default branch (full content search). If `ref` is set to a "
                    "non-default branch, this instead matches file names only, since GitHub cannot "
                    "full-text search non-default branches."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keyword, function name, or symbol to search for"},
                        "ref": {"type": "string", "description": "Optional branch name to search. Defaults to the repository's default branch."},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the full text contents of a specific file in the repository before proposing changes to it.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to the repository root"},
                        "ref": {"type": "string", "description": "Optional branch name to read from. Defaults to the repository's default branch."},
                    },
                    "required": ["path"],
                },
            },
        },
    ]

    def __init__(self, api_key: str, base_url: str, model: str = "qwen3.7-plus"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        logger.info("qwen_service_initialized", model=model)

    def _call_with_retry(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_retries: int = 3,
        json_mode: bool = False,
    ) -> Dict[str, Any]:
        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        last_error = None

        for attempt in range(max_retries):
            try:
                start = time.time()
                kwargs = {
                    "model": self.model,
                    "messages": full_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

                response = self.client.chat.completions.create(**kwargs)
                latency_ms = int((time.time() - start) * 1000)
                content = response.choices[0].message.content
                tokens_used = response.usage.total_tokens if response.usage else 0

                logger.info("qwen_api_call_success", attempt=attempt + 1, tokens_used=tokens_used, latency_ms=latency_ms)
                return {"success": True, "content": content, "tokens_used": tokens_used, "latency_ms": latency_ms}

            except Exception as e:
                last_error = e
                logger.warning("qwen_api_call_failed", attempt=attempt + 1, error=str(e))
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        logger.error("qwen_api_call_all_retries_failed", error=str(last_error))
        return {"success": False, "content": None, "tokens_used": 0, "latency_ms": 0, "error": str(last_error)}

    def _call_and_parse_json(self, messages: List[Dict[str, str]], system_prompt: str, **kwargs) -> Dict[str, Any]:
        """Call the model and parse its content as JSON, retrying once with a
        stricter prompt if the first response doesn't parse."""
        result = self._call_with_retry(messages=messages, system_prompt=system_prompt, json_mode=True, **kwargs)
        if not result["success"]:
            return result

        parsed = parse_json_response(result["content"])
        if parsed["success"]:
            result["content"] = json.dumps(parsed["data"])
            return result

        logger.warning("json_parse_failed_retrying_strict", error=parsed["error"])
        strict_messages = messages + [
            {"role": "assistant", "content": result["content"]},
            {"role": "user", "content": "That was not valid JSON. Respond again with ONLY the raw JSON object — no markdown fences, no commentary."},
        ]
        retry_result = self._call_with_retry(
            messages=strict_messages, system_prompt=system_prompt, json_mode=True, max_retries=1, **kwargs
        )
        if not retry_result["success"]:
            return retry_result

        retry_parsed = parse_json_response(retry_result["content"])
        if retry_parsed["success"]:
            retry_result["content"] = json.dumps(retry_parsed["data"])
            return retry_result

        return {"success": False, "content": None, "error": f"Model did not return valid JSON after retry: {retry_parsed['error']}"}

    def classify_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        user_message = (
            f"Title: {title}\n\nBody:\n{(body or '')[:3000]}\n\n"
            f"Labels: {', '.join(labels) if labels else 'None'}"
        )
        return self._call_and_parse_json(
            messages=[{"role": "user", "content": user_message}],
            system_prompt=self.CLASSIFICATION_SYSTEM_PROMPT,
            temperature=0.1,
        )

    def generate_solution(
        self, title: str, body: str, classification: str, repository_context: Optional[str] = None
    ) -> Dict[str, Any]:
        context_section = f"\n\nRepository Context:\n{repository_context}" if repository_context else ""
        user_message = (
            f"Generate a code fix for this {classification} issue:\n\n"
            f"Title: {title}\n\nDescription:\n{(body or '')[:4000]}{context_section}"
        )
        return self._call_and_parse_json(
            messages=[{"role": "user", "content": user_message}],
            system_prompt=self.SOLUTION_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=4096,
        )

    def generate_solution_with_tools(
        self,
        title: str,
        body: str,
        classification: str,
        tool_executor,
        repository_context: Optional[str] = None,
        max_tool_iterations: int = 10,
    ) -> Dict[str, Any]:
        """Like generate_solution, but lets Qwen call search_repo/read_file to
        inspect the repository before proposing a fix.

        tool_executor(name, args) runs a single tool call and returns the
        result as a string to feed back to the model.
        """
        context_section = f"\n\nRepository Context:\n{repository_context}" if repository_context else ""
        user_message = (
            f"Generate a code fix for this {classification} issue:\n\n"
            f"Title: {title}\n\nDescription:\n{(body or '')[:4000]}{context_section}"
        )
        messages = [
            {"role": "system", "content": self.SOLUTION_WITH_TOOLS_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        total_tokens = 0
        start = time.time()
        tool_calls_made = []

        for iteration in range(max_tool_iterations):
            try:
                response = self.client.chat.completions.create(
                    model=self.model, messages=messages, temperature=0.2, max_tokens=4096,
                    tools=self.REPO_TOOLS, tool_choice="auto",
                )
            except Exception as e:
                logger.error("qwen_tool_call_failed", iteration=iteration, error=str(e))
                # Fall back to the non-tool path rather than blocking the pipeline.
                return self.generate_solution(title, body, classification, repository_context)

            total_tokens += response.usage.total_tokens if response.usage else 0
            message = response.choices[0].message

            if not message.tool_calls:
                # Model is done reasoning; parse its final answer as JSON.
                latency_ms = int((time.time() - start) * 1000)
                parsed = parse_json_response(message.content)
                if not parsed["success"]:
                    logger.warning("tool_loop_final_json_parse_failed", error=parsed["error"])
                    return {
                        "success": False, "content": None, "tokens_used": total_tokens,
                        "latency_ms": latency_ms, "error": parsed["error"],
                    }
                return {
                    "success": True, "content": json.dumps(parsed["data"]), "tokens_used": total_tokens,
                    "latency_ms": latency_ms, "tool_calls": tool_calls_made,
                }

            # Model wants to call tools; execute them and feed results back.
            messages.append({
                "role": "assistant", "content": message.content,
                "tool_calls": [tc.model_dump() for tc in message.tool_calls],
            })
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls_made.append({"name": tc.function.name, "arguments": args})
                result_str = tool_executor(tc.function.name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

        logger.warning("tool_loop_max_iterations_reached", max_iterations=max_tool_iterations)
        return {
            "success": False, "content": None, "tokens_used": total_tokens,
            "latency_ms": int((time.time() - start) * 1000),
            "error": f"Exceeded {max_tool_iterations} tool-call iterations without a final answer",
        }

    def health_check(self) -> Dict[str, Any]:
        try:
            start = time.time()
            self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return {"status": "healthy", "model": self.model, "latency_ms": int((time.time() - start) * 1000)}
        except Exception as e:
            logger.error("qwen_health_check_failed", error=str(e))
            return {"status": "unhealthy", "model": self.model, "error": str(e)}
