"""Tests for QwenService's tolerant JSON parsing (code-fenced or
prose-wrapped model output shouldn't crash json.loads)."""

from app.services.qwen_service import parse_json_response


def test_parses_clean_json():
    result = parse_json_response('{"classification": "bug", "confidence": 0.9}')
    assert result["success"] is True
    assert result["data"]["classification"] == "bug"


def test_parses_json_wrapped_in_markdown_fence():
    raw = '```json\n{"classification": "bug", "confidence": 0.9}\n```'
    result = parse_json_response(raw)
    assert result["success"] is True
    assert result["data"]["classification"] == "bug"


def test_parses_json_wrapped_in_plain_fence():
    raw = '```\n{"classification": "feature", "confidence": 0.7}\n```'
    result = parse_json_response(raw)
    assert result["success"] is True
    assert result["data"]["classification"] == "feature"


def test_parses_json_with_surrounding_prose():
    raw = 'Sure, here is the classification:\n\n{"classification": "spam", "confidence": 0.99}\n\nLet me know if you need anything else!'
    result = parse_json_response(raw)
    assert result["success"] is True
    assert result["data"]["classification"] == "spam"


def test_returns_failure_for_empty_content():
    result = parse_json_response(None)
    assert result["success"] is False
    assert "Empty response" in result["error"]


def test_returns_failure_for_unparseable_content():
    result = parse_json_response("this is not json at all")
    assert result["success"] is False
    assert "error" in result
