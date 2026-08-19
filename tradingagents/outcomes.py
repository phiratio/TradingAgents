"""Realized-return computation for the decision log (Phase B of memory).

After a decision has been on the books for a few trading days, the next run
for the same ticker fetches what actually happened — the raw return over the
holding window and the alpha versus the ticker's exchange benchmark — so a
reflection can be written into the memory log. Ported from the LangGraph-era
``TradingAgentsGraph._fetch_returns`` / ``_resolve_benchmark`` with identical
semantics (5-trading-day horizon, +7 calendar-day fetch buffer, <2-rows →
skip-and-retry-next-run, symbol normalization, suffix-mapped benchmark).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import yfinance as yf

from tradingagents.dataflows.symbol_utils import normalize_symbol

logger = logging.getLogger(__name__)


def resolve_benchmark(ticker: str, config: dict) -> str:
    """Pick the benchmark ticker for alpha calculation against ``ticker``.

    ``config["benchmark_ticker"]`` overrides everything when set; otherwise
    the suffix map matches the ticker's exchange suffix (e.g. ``.T`` for
    Tokyo). US-listed tickers without a dotted suffix fall through to the
    empty-suffix entry (SPY by default). Unrecognised suffixes (including
    US tickers with dots like ``BRK.B``) also fall back to the empty-suffix
    entry, which is the right default because the alpha calculation works
    in USD.
    """
    explicit = config.get("benchmark_ticker")
    if explicit:
        return explicit
    benchmark_map = config.get("benchmark_map", {})
    ticker_upper = ticker.upper()
    for suffix, benchmark in benchmark_map.items():
        if suffix and ticker_upper.endswith(suffix.upper()):
            return benchmark
    return benchmark_map.get("", "SPY")


def fetch_returns(
    ticker: str,
    trade_date: str,
    holding_days: int = 5,
    benchmark: str = "SPY",
) -> tuple[float | None, float | None, int | None]:
    """Fetch raw and alpha return for ticker over holding_days from trade_date.

    ``benchmark`` is the index used as the alpha baseline (resolved by the
    caller via :func:`resolve_benchmark`). Returns ``(raw_return, alpha_return,
    actual_holding_days)`` or ``(None, None, None)`` if price data is
    unavailable (too recent, delisted, or network error) — the caller should
    leave the entry pending and retry on the next run.
    """
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d")
        end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
        end_str = end.strftime("%Y-%m-%d")

        # Normalize so the realized-return lookup hits the same instrument
        # the analysis priced (e.g. XAUUSD -> GC=F) (#984). The benchmark is
        # already a canonical Yahoo symbol from ``resolve_benchmark``.
        stock = yf.Ticker(normalize_symbol(ticker)).history(start=trade_date, end=end_str)
        bench = yf.Ticker(benchmark).history(start=trade_date, end=end_str)

        if len(stock) < 2 or len(bench) < 2:
            return None, None, None

        actual_days = min(holding_days, len(stock) - 1, len(bench) - 1)
        raw = float(
            (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
            / stock["Close"].iloc[0]
        )
        bench_ret = float(
            (bench["Close"].iloc[actual_days] - bench["Close"].iloc[0])
            / bench["Close"].iloc[0]
        )
        alpha = raw - bench_ret
        return raw, alpha, actual_days
    except Exception as e:  # noqa: BLE001 — degrade to retry-next-run
        logger.warning(
            "Could not resolve outcome for %s on %s vs %s (will retry next run): %s",
            ticker, trade_date, benchmark, e,
        )
        return None, None, None
