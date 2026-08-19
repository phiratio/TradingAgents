"""Tests for tradingagents.dataflows.identity.

Ports the still-relevant contracts from the deleted CLI/agent-layer tests
(test_cli_symbol_handling.py, test_crypto_asset_mode.py,
test_ticker_symbol_handling.py, test_instrument_identity.py):

- #981/#982: asset type is classified on the *canonical* Yahoo symbol, so raw
  broker forms (``BTCUSD``, ``BTCUSDT``) classify as crypto.
- #814: deterministic identity resolution via yfinance, fail-open on error,
  placeholder values cleaned out, injected verbatim into the agent context.
- The context must mention the exact ticker and the preserve-suffix rule.
"""

from unittest.mock import patch

import pytest

from tradingagents.dataflows.identity import (
    CRYPTO_SUFFIXES,
    build_instrument_context,
    detect_asset_type,
    resolve_instrument_identity,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# detect_asset_type — #981/#982: classify on the canonical symbol
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "BTC-USD",
        "ETH-USD",
        "eth-usd",  # case-insensitive
        "BTCUSD",  # raw broker form -> BTC-USD
        "BTCUSDT",  # stablecoin-quoted -> BTC-USD (#982)
        "BTC-USDT",
        "ETHUSD",
    ],
)
def test_detect_asset_type_crypto(raw):
    assert detect_asset_type(raw) == "crypto"


@pytest.mark.parametrize(
    "raw",
    [
        "AAPL",
        "SPY",
        "0700.HK",
        "600519.SS",
        "GC=F",  # futures are "stock" for pipeline purposes
        "EURUSD=X",  # forex too
    ],
)
def test_detect_asset_type_stock(raw):
    assert detect_asset_type(raw) == "stock"


def test_crypto_suffixes_are_yahoo_quote_suffixes():
    assert "-USD" in CRYPTO_SUFFIXES
    assert all(suffix.startswith("-") for suffix in CRYPTO_SUFFIXES)


# ---------------------------------------------------------------------------
# build_instrument_context
# ---------------------------------------------------------------------------


def test_context_mentions_exact_symbol_without_identity():
    context = build_instrument_context("7203.T")
    assert "`7203.T`" in context
    assert "exchange suffix" in context
    assert "Resolved identity" not in context
    assert "instrument to analyze" in context


def test_context_injects_resolved_identity():
    context = build_instrument_context(
        "TOTDY",
        "stock",
        {
            "company_name": "TOTO LTD.",
            "sector": "Industrials",
            "industry": "Building Products & Equipment",
            "exchange": "PNK",
        },
    )
    assert "Resolved identity:" in context
    assert "Company: TOTO LTD." in context
    assert "Business classification: Industrials / Building Products & Equipment" in context
    assert "Exchange: PNK" in context
    assert "Do not substitute a different company" in context


def test_context_crypto_uses_asset_and_name_labels():
    context = build_instrument_context(
        "BTC-USD", "crypto", {"company_name": "Bitcoin USD"}
    )
    assert "asset to analyze" in context
    assert "`BTC-USD`" in context
    assert "Name: Bitcoin USD" in context
    assert "Company:" not in context
    assert "crypto asset rather than a company" in context
    assert "company fundamentals" in context


def test_context_crypto_hint_appended_even_without_identity():
    context = build_instrument_context("ETH-USD", "crypto")
    assert "asset to analyze" in context
    assert "crypto asset rather than a company" in context
    assert "Resolved identity" not in context


def test_context_sector_only():
    context = build_instrument_context("TOTDY", "stock", {"sector": "Industrials"})
    assert "Sector: Industrials" in context
    assert "Business classification" not in context
    assert "Industry:" not in context


def test_context_industry_only():
    context = build_instrument_context(
        "TOTDY", "stock", {"industry": "Building Products & Equipment"}
    )
    assert "Industry: Building Products & Equipment" in context
    assert "Business classification" not in context
    assert "Sector:" not in context


def test_context_empty_identity_mapping_adds_no_identity_block():
    context = build_instrument_context("AAPL", "stock", {})
    assert "Resolved identity" not in context


# ---------------------------------------------------------------------------
# resolve_instrument_identity — #814, fail-open, lru_cache
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_identity_cache():
    resolve_instrument_identity.cache_clear()
    yield
    resolve_instrument_identity.cache_clear()


def test_resolves_company_metadata_from_yfinance():
    with patch("tradingagents.dataflows.identity.yf.Ticker") as mock:
        mock.return_value.info = {
            "longName": "TOTO LTD.",
            "shortName": "TOTO",
            "sector": "Industrials",
            "industry": "Building Products & Equipment",
            "exchange": "PNK",
            "quoteType": "EQUITY",
        }
        identity = resolve_instrument_identity("totdy")
    # Normalized (upper-cased) symbol is what gets resolved (#983).
    mock.assert_called_once_with("TOTDY")
    assert identity == {
        "company_name": "TOTO LTD.",
        "sector": "Industrials",
        "industry": "Building Products & Equipment",
        "exchange": "PNK",
        "quote_type": "EQUITY",
    }


def test_falls_back_to_short_name():
    with patch("tradingagents.dataflows.identity.yf.Ticker") as mock:
        mock.return_value.info = {"shortName": "TOTO", "sector": "Industrials"}
        identity = resolve_instrument_identity("SHORTY")
    assert identity["company_name"] == "TOTO"


def test_skips_placeholder_values():
    with patch("tradingagents.dataflows.identity.yf.Ticker") as mock:
        mock.return_value.info = {
            "longName": "  ",
            "shortName": "N/A",
            "sector": "None",
            "industry": "n/a",
            "exchange": None,
            "quoteType": 42,  # non-str dropped
        }
        identity = resolve_instrument_identity("PLCHLD")
    assert identity == {}


def test_fails_open_on_exception():
    with patch(
        "tradingagents.dataflows.identity.yf.Ticker",
        side_effect=RuntimeError("rate limited"),
    ):
        assert resolve_instrument_identity("FAILS") == {}


def test_fails_open_on_none_info():
    with patch("tradingagents.dataflows.identity.yf.Ticker") as mock:
        mock.return_value.info = None
        assert resolve_instrument_identity("NOINFO") == {}


def test_result_is_cached():
    with patch("tradingagents.dataflows.identity.yf.Ticker") as mock:
        mock.return_value.info = {"longName": "TOTO LTD."}
        first = resolve_instrument_identity("CACHED")
        second = resolve_instrument_identity("CACHED")
    mock.assert_called_once()  # second call served from cache
    assert first == second == {"company_name": "TOTO LTD."}


def test_resolves_broker_alias_to_canonical_symbol():
    # XAUUSD must resolve identity for the same instrument the price path
    # fetches: the COMEX gold future GC=F (#983).
    with patch("tradingagents.dataflows.identity.yf.Ticker") as mock:
        mock.return_value.info = {"shortName": "Gold"}
        resolve_instrument_identity("XAUUSD")
    mock.assert_called_once_with("GC=F")
