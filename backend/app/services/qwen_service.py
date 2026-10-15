"""Backward-compatible alias: the Qwen-only client is now provider-agnostic.
Import from `llm_service` in new code."""

from .llm_service import LLMService as QwenService, parse_json_response  # noqa: F401

__all__ = ["QwenService", "parse_json_response"]
