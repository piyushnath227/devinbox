"""Tests for the provider-agnostic LLM client: retry policy, fallbacks,
usage tracking, JSON-mode downgrade, factory precedence."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from app.services.llm_service import (
    LLMConfigError, LLMService, PROVIDER_PRESETS, build_llm_service, is_llm_configured,
)


def _resp(content='{"ok": true}', total=10):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=6, completion_tokens=4, total_tokens=total),
    )


def _api_error(cls, status, msg="boom", headers=None):
    req = httpx.Request("POST", "https://x/v1/chat/completions")
    return cls(msg, response=httpx.Response(status, request=req, headers=headers or {}), body=None)


@pytest.fixture
def svc():
    s = LLMService("k", "https://x/v1", "primary", fallback_models=["backup"])
    s._sleep = lambda *_: None
    s.client = MagicMock()
    return s, s.client.chat.completions.create


def test_success_tracks_usage(svc):
    s, create = svc
    create.return_value = _resp(total=10)
    r = s._call_with_retry([{"role": "user", "content": "hi"}], "sys")
    assert r["success"] and r["model"] == "primary"
    assert s.usage == {"calls": 1, "prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10}


def test_retries_transient_429_then_succeeds(svc):
    s, create = svc
    create.side_effect = [_api_error(openai.RateLimitError, 429), _resp()]
    assert s._call_with_retry([], "sys")["success"] is True
    assert create.call_count == 2


def test_auth_error_fails_fast_without_retry_or_fallback(svc):
    s, create = svc
    create.side_effect = _api_error(openai.AuthenticationError, 401)
    r = s._call_with_retry([], "sys")
    assert r["success"] is False and "authentication" in r["error"].lower()
    assert create.call_count == 1


def test_falls_back_to_next_model_when_primary_exhausted(svc):
    s, create = svc

    def fake(**kw):
        if kw["model"] == "primary":
            raise _api_error(openai.InternalServerError, 503)
        return _resp()
    create.side_effect = fake
    r = s._call_with_retry([], "sys", max_retries=2)
    assert r["success"] and r["model"] == "backup"


def test_non_retryable_model_error_skips_straight_to_fallback(svc):
    s, create = svc

    def fake(**kw):
        if kw["model"] == "primary":
            raise _api_error(openai.NotFoundError, 404, "model not found")
        return _resp()
    create.side_effect = fake
    r = s._call_with_retry([], "sys")
    assert r["model"] == "backup"
    assert create.call_count == 2  # one failed primary attempt, then fallback


def test_all_models_fail_returns_error(svc):
    s, create = svc
    create.side_effect = _api_error(openai.InternalServerError, 500)
    r = s._call_with_retry([], "sys", max_retries=2)
    assert r["success"] is False and create.call_count == 4  # 2 models x 2 attempts


def test_json_mode_rejected_is_disabled_and_retried(svc):
    s, create = svc
    create.side_effect = [_api_error(openai.BadRequestError, 400, "response_format not supported"), _resp()]
    r = s._call_with_retry([], "sys", json_mode=True)
    assert r["success"] and s._json_mode_supported is False
    assert "response_format" not in create.call_args.kwargs


def test_retry_after_header_is_honoured():
    e = _api_error(openai.RateLimitError, 429, headers={"retry-after": "7"})
    s = LLMService("k", "https://x/v1", "m")
    assert s._backoff(0, e) == 7.0


def test_long_retry_after_is_not_shortened():
    e = _api_error(openai.RateLimitError, 429, headers={"retry-after": "999"})
    assert LLMService("k", "https://x/v1", "m")._backoff(0, e) == 999.0


# ---------- factory ----------
def _settings(**kw):
    base = dict(LLM_PROVIDER="", LLM_BASE_URL="", LLM_MODEL="", LLM_API_KEY="", LLM_FALLBACK_MODELS="",
                LLM_TIMEOUT_SECONDS=30.0, LLM_MAX_CONCURRENCY=2)
    base.update(kw)
    return SimpleNamespace(**base)


def _km(creds=None):
    km = MagicMock(); km.get_llm_credentials.return_value = creds
    return km


def test_factory_gemini_preset_and_fallbacks():
    s = build_llm_service(_km({"provider": "gemini", "api_key": "k", "model": "m1", "fallback_models": "m2, m3"}), _settings())
    assert s.base_url == PROVIDER_PRESETS["gemini"]["base_url"]
    assert s.fallback_models == ["m2", "m3"] and s.provider == "gemini"


def test_factory_legacy_qwen_record_still_works():
    s = build_llm_service(_km({"api_key": "k", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "model": "qwen3.7-plus"}), _settings())
    assert s.provider == "qwen" and s.model == "qwen3.7-plus"


def test_factory_ollama_needs_no_key_but_needs_model():
    assert build_llm_service(_km(), _settings(LLM_PROVIDER="ollama", LLM_MODEL="llama")).api_key == "ollama"
    with pytest.raises(LLMConfigError):
        build_llm_service(_km(), _settings(LLM_PROVIDER="ollama"))


def test_factory_requires_key_unless_told_otherwise():
    st = _settings(LLM_PROVIDER="groq", LLM_MODEL="m")
    with pytest.raises(LLMConfigError):
        build_llm_service(_km(), st)
    assert build_llm_service(_km(), st, require_key=False).api_key == "unused"


def test_factory_env_fallback_and_is_configured():
    st = _settings(LLM_PROVIDER="openrouter", LLM_MODEL="m", LLM_API_KEY="envkey")
    assert build_llm_service(_km(), st).api_key == "envkey"
    assert is_llm_configured(_km(), st) is True
    assert is_llm_configured(_km(), _settings()) is False


def test_groq_budget_pacing_and_tool_retry(svc):
    s, create = svc
    s.provider = "groq"
    s.max_tokens = 900
    s.min_request_interval = 61
    s._sleep = MagicMock()
    create.side_effect = [_resp(), _api_error(openai.RateLimitError, 429,
                          "Please try again in 31.534285714s."), _resp()]
    assert s._call_with_retry([], "sys")["success"]
    assert s.generate_solution_with_tools("bug", "body", "bug", lambda *_: "")['success']
    assert all(c.kwargs["max_tokens"] == 900 for c in create.call_args_list)
    assert len(s._sleep.call_args_list) == 2
    assert all(c.args[0] > 60 for c in s._sleep.call_args_list)


def test_groq_oversized_request_does_not_retry_unchanged(svc):
    s, create = svc
    s.provider = "groq"
    s.fallback_models = []
    create.side_effect = _api_error(openai.RateLimitError, 429, "Request too large on input tokens per minute")
    result = s.generate_solution_with_tools("bug", "body", "bug", lambda *_: "")
    assert not result['success'] and 'token budget' in result['error']
    assert create.call_count == 1


def test_truncated_solution_is_not_accepted(svc):
    s, create = svc
    response = _resp()
    response.choices[0].finish_reason = "length"
    create.return_value = response
    result = s.generate_solution_with_tools("bug", "body", "bug", lambda *_: "")
    assert not result['success'] and 'output budget' in result['error']
    assert create.call_count == 1


def test_factory_applies_token_and_pacing_settings():
    s = build_llm_service(_km(), _settings(LLM_PROVIDER="groq", LLM_MODEL="m", LLM_API_KEY="k",
                          LLM_MAX_TOKENS=900, LLM_MIN_REQUEST_INTERVAL_SECONDS=61))
    assert s.max_tokens == 900 and s.min_request_interval == 61


def test_groq_long_quota_wait_fails_without_retry_storm(svc):
    s, create = svc
    s.provider = 'groq'
    s._sleep = MagicMock()
    create.side_effect = _api_error(openai.RateLimitError, 429, headers={'retry-after': '999'})
    result = s.generate_solution_with_tools('bug', 'body', 'bug', lambda *_: '')
    assert not result['success'] and 'quota exhausted' in result['error']
    assert create.call_count == 1
    s._sleep.assert_not_called()
