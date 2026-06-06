"""
Unit tests for the Agent-1 cost optimizations:
  - prompt caching: stable prefix carries cache_control; volatile tail is separate
  - force_tool_json: returns the tool_use block's parsed dict, no JSON-string parsing
  - truncation surfaces as LLMTruncationError (non-retryable)
  - transient API errors map to LLMTransientError (retryable)
  - compact Excel/CSV text is tab-delimited and lossless
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from shared.llm_provider import (
    LLMProvider,
    LLMTransientError,
    LLMTruncationError,
)


def _provider() -> LLMProvider:
    # Avoid real client construction / Secrets Manager.
    p = LLMProvider.__new__(LLMProvider)
    p.provider = "anthropic_api"
    p.model = "claude-haiku-4-5-20251001"
    p.report_language = "es"
    p.client = MagicMock()
    return p


# ── Prompt caching: block structure ─────────────────────────────────────────────

def test_system_blocks_cache_prefix_and_separate_suffix():
    p = _provider()
    blocks = p._anthropic_system_blocks("STATIC PROMPT", tenant_id="t1", job_id="j1")
    # First block is the stable prompt and is the one marked for caching.
    assert blocks[0]["text"] == "STATIC PROMPT"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    # Volatile tenant/language tail is a separate, UNCACHED block so it can't
    # invalidate the cached prefix.
    assert len(blocks) == 2
    assert "cache_control" not in blocks[1]
    assert "t1" in blocks[1]["text"]


def test_system_blocks_prefix_is_stable_across_tenants():
    """The cached prefix must be byte-identical regardless of tenant/job — that's what
    lets the cache be reused across analyses within the TTL."""
    p = _provider()
    a = p._anthropic_system_blocks("STATIC", tenant_id="ta", job_id="ja")
    b = p._anthropic_system_blocks("STATIC", tenant_id="tb", job_id="jb")
    assert a[0] == b[0]            # identical cached prefix block
    assert a[1]["text"] != b[1]["text"]  # different volatile tails


def test_system_blocks_no_suffix_when_english_and_anon():
    p = _provider()
    p.report_language = "en"
    blocks = p._anthropic_system_blocks("STATIC", tenant_id=None, job_id=None)
    assert len(blocks) == 1  # nothing volatile to append


# ── force_tool_json ──────────────────────────────────────────────────────────────

def test_force_tool_json_returns_tool_input_dict():
    p = _provider()
    expected = {"periods": ["2025-06"], "accounts": [{"a": 1}]}
    tool_block = SimpleNamespace(type="tool_use", name=p._JSON_TOOL_NAME, input=expected)
    p.client.messages.create.return_value = SimpleNamespace(
        stop_reason="tool_use",
        content=[tool_block],
        usage=SimpleNamespace(cache_read_input_tokens=0, cache_creation_input_tokens=10,
                              input_tokens=5, output_tokens=20),
    )
    out = p.generate_json("sys", "user", force_tool_json=True, max_tokens=1000)
    assert out == expected
    # Forced the tool so the model must answer through it.
    _, kwargs = p.client.messages.create.call_args
    assert kwargs["tool_choice"] == {"type": "tool", "name": p._JSON_TOOL_NAME}


def test_force_tool_json_raises_truncation_on_max_tokens():
    p = _provider()
    p.client.messages.create.return_value = SimpleNamespace(
        stop_reason="max_tokens", content=[],
        usage=SimpleNamespace(cache_read_input_tokens=0, cache_creation_input_tokens=0,
                              input_tokens=5, output_tokens=1000),
    )
    with pytest.raises(LLMTruncationError):
        p.generate_json("sys", "user", force_tool_json=True, max_tokens=10)


# ── error classification ─────────────────────────────────────────────────────────

def test_wrap_api_error_maps_transient():
    import anthropic
    p = _provider()
    # APIConnectionError is a transient type; construct minimally.
    exc = anthropic.APIConnectionError(request=MagicMock())
    assert isinstance(p._wrap_api_error(exc), LLMTransientError)


def test_wrap_api_error_passthrough_non_transient():
    p = _provider()
    exc = ValueError("nope")
    assert p._wrap_api_error(exc) is exc


# ── compact text (item 5) ────────────────────────────────────────────────────────

def test_df_to_compact_text_is_tab_delimited_and_lossless():
    from agents.document_extractor.handler import _df_to_compact_text
    df = pd.DataFrame([["Activos", "1,234", "5,678"], ["Efectivo", "10", ""]])
    text = _df_to_compact_text(df)
    assert text == "Activos\t1,234\t5,678\nEfectivo\t10\t"
    # No multi-space padding (the to_string waste we removed).
    assert "  " not in text
    # Comma thousands separators preserved.
    assert "1,234" in text
