"""Tests for TRADINGAGENTS_* env-var overlay onto DEFAULT_CONFIG."""

from __future__ import annotations

import importlib
import os

import pytest

import tradingagents.default_config as default_config_module


def _reload_with_env(monkeypatch, **overrides):
    """Set/clear env vars then reload default_config to re-evaluate DEFAULT_CONFIG."""
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    for key in list(os.environ):
        if key.startswith(default_config_module._VENDOR_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)
    return importlib.reload(default_config_module)


def test_no_env_uses_built_in_defaults(monkeypatch):
    dc = _reload_with_env(monkeypatch)
    assert dc.DEFAULT_CONFIG["output_language"] == "English"
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 1
    assert dc.DEFAULT_CONFIG["max_risk_discuss_rounds"] == 1
    assert dc.DEFAULT_CONFIG["benchmark_ticker"] is None


def test_string_overrides(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_OUTPUT_LANGUAGE="Chinese",
        TRADINGAGENTS_BENCHMARK_TICKER="QQQ",
    )
    assert dc.DEFAULT_CONFIG["output_language"] == "Chinese"
    assert dc.DEFAULT_CONFIG["benchmark_ticker"] == "QQQ"


def test_int_coercion(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="3",
        TRADINGAGENTS_MAX_RISK_ROUNDS="2",
    )
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 3
    assert isinstance(dc.DEFAULT_CONFIG["max_debate_rounds"], int)
    assert dc.DEFAULT_CONFIG["max_risk_discuss_rounds"] == 2
    assert isinstance(dc.DEFAULT_CONFIG["max_risk_discuss_rounds"], int)


def test_empty_env_value_is_passthrough(monkeypatch):
    """Empty TRADINGAGENTS_* values must not clobber the built-in default."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_OUTPUT_LANGUAGE="",
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="",
    )
    assert dc.DEFAULT_CONFIG["output_language"] == "English"
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 1


def test_invalid_int_raises(monkeypatch):
    """Garbage int values should surface a ValueError at import, not silently misconfigure."""
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "not-a-number")
    with pytest.raises(ValueError, match="TRADINGAGENTS_MAX_DEBATE_ROUNDS"):
        importlib.reload(default_config_module)
    # Restore module state for subsequent tests in this process
    monkeypatch.delenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", raising=False)
    importlib.reload(default_config_module)


def test_unknown_env_var_is_ignored(monkeypatch):
    """Env vars outside _ENV_OVERRIDES must not bleed into DEFAULT_CONFIG."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_NONEXISTENT_KEY="oops",
    )
    assert "nonexistent_key" not in dc.DEFAULT_CONFIG


def test_vendor_env_override(monkeypatch):
    """TRADINGAGENTS_VENDOR_<CATEGORY> selects the vendor chain for that category."""
    dc = _reload_with_env(
        monkeypatch,
        TRADINGAGENTS_VENDOR_NEWS_DATA="alpha_vantage",
        TRADINGAGENTS_VENDOR_CORE_STOCK_APIS="alpha_vantage,yfinance",
    )
    assert dc.DEFAULT_CONFIG["data_vendors"]["news_data"] == "alpha_vantage"
    assert dc.DEFAULT_CONFIG["data_vendors"]["core_stock_apis"] == "alpha_vantage,yfinance"
    # Untouched categories keep their built-in defaults.
    assert dc.DEFAULT_CONFIG["data_vendors"]["fundamental_data"] == "yfinance"


def test_vendor_env_unset_or_empty_keeps_default(monkeypatch):
    """Unset/empty vendor env vars leave the built-in vendor chain in place."""
    dc = _reload_with_env(monkeypatch, TRADINGAGENTS_VENDOR_NEWS_DATA="")
    assert dc.DEFAULT_CONFIG["data_vendors"]["news_data"] == "yfinance"


def test_vendor_env_unknown_category_is_ignored(monkeypatch):
    """A vendor env var for a category that doesn't exist must not create one."""
    dc = _reload_with_env(monkeypatch, TRADINGAGENTS_VENDOR_NOT_A_CATEGORY="yfinance")
    assert "not_a_category" not in dc.DEFAULT_CONFIG["data_vendors"]
