"""Command-line data backend for the Claude Code TradingAgents pipeline.

Every agent in the rewritten pipeline is a Claude Code subagent; this CLI is
the only thing they shell out to. It exposes the same data-tool surface the
LangGraph-era agents had as LangChain tools, with identical output formats,
routed through the same vendor registry (``route_to_vendor``).

Conventions:
- All results print to stdout as plain text (or JSON where noted).
- "No data" is an answer, not an error: ``NO_DATA_AVAILABLE:`` /
  ``DATA_UNAVAILABLE:`` sentinel strings pass through verbatim with exit
  code 0 — they carry anti-fabrication instructions the agent must see.
- Missing API keys for a *configured* vendor on a core data category exit 2
  with an actionable message naming the env var. Optional categories (macro
  data, prediction markets) degrade to a ``DATA_UNAVAILABLE:`` sentinel with
  exit 0 instead — the pipeline continues without them.
- Other data-level problems (bad indicator name, malformed series, vendor
  errors) print an ``ERROR:`` line to stdout with exit 0 so the calling agent
  can read and react to them.
- Usage errors exit 2; unexpected failures exit 1.

Run ``python -m tradingagents.data_cli --help`` for the command list.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
)
from tradingagents.dataflows.interface import get_config, route_to_vendor

_KEY_HINTS = {
    "alpha_vantage": "ALPHA_VANTAGE_API_KEY",
    "fred": "FRED_API_KEY",
}


def _print(text: str) -> None:
    # Data payloads may contain characters outside the terminal encoding on
    # some platforms; never let that crash a fetch.
    sys.stdout.buffer.write((text if text.endswith("\n") else text + "\n").encode("utf-8"))


def _no_data_message(exc: NoMarketDataError) -> str:
    sym = getattr(exc, "symbol", "") or ""
    canonical = getattr(exc, "canonical", None)
    detail = getattr(exc, "detail", "") or ""
    resolved = f" (resolved to '{canonical}')" if canonical and canonical != sym else ""
    detail_part = f" ({detail})" if detail else ""
    return (
        f"NO_DATA_AVAILABLE: No usable market data for '{sym}'{resolved}{detail_part}. "
        "The symbol may be invalid, delisted, not covered, or the vendor returned "
        "stale data. Do not estimate or fabricate values — report that data is "
        "unavailable for this symbol."
    )


def _run_routed(method: str, *args, **kwargs) -> int:
    """Call route_to_vendor and translate its error contract to CLI output."""
    try:
        _print(route_to_vendor(method, *args, **kwargs))
        return 0
    except NoMarketDataError as exc:
        _print(_no_data_message(exc))
        return 0
    except VendorNotConfiguredError as exc:
        hint = ", ".join(f"{v} needs {k}" for v, k in _KEY_HINTS.items())
        print(f"CONFIG ERROR: {exc} (set the vendor's API key: {hint})", file=sys.stderr)
        return 2
    except ValueError as exc:
        # Bad indicator names, malformed FRED series, vendor 400s: the agent
        # should read this and correct its call.
        _print(f"ERROR: {exc}")
        return 0


# ---------------------------------------------------------------------------
# Market data commands
# ---------------------------------------------------------------------------

def cmd_stock_data(args) -> int:
    return _run_routed("get_stock_data", args.symbol, args.start_date, args.end_date)


def cmd_indicators(args) -> int:
    # Comma-splitting with per-indicator error capture, matching the old
    # LangChain wrapper: LLMs pass "rsi, macd" as one call, and an invalid
    # name must come back as readable text rather than kill the whole call.
    indicators = [i.strip().lower() for i in args.indicator.split(",") if i.strip()]
    results = []
    status = 0
    for ind in indicators:
        try:
            results.append(
                route_to_vendor("get_indicators", args.symbol, ind, args.curr_date, args.look_back_days)
            )
        except NoMarketDataError as exc:
            results.append(_no_data_message(exc))
        except VendorNotConfiguredError as exc:
            print(f"CONFIG ERROR: {exc}", file=sys.stderr)
            status = 2
            break
        except ValueError as exc:
            results.append(str(exc))
    _print("\n\n".join(results))
    return status


def cmd_snapshot(args) -> int:
    from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot

    try:
        _print(build_verified_market_snapshot(args.symbol, args.curr_date, args.look_back_days))
        return 0
    except NoMarketDataError as exc:
        _print(_no_data_message(exc))
        return 0
    except ValueError as exc:
        _print(
            f"NO_DATA_AVAILABLE: could not build a verified snapshot for "
            f"'{args.symbol}' on {args.curr_date} ({exc}). Do not estimate or "
            "fabricate values — report that data is unavailable."
        )
        return 0


def cmd_fundamentals(args) -> int:
    return _run_routed("get_fundamentals", args.ticker, args.curr_date)


def _financials(method: str, args) -> int:
    return _run_routed(method, args.ticker, args.freq, args.curr_date)


def cmd_news(args) -> int:
    return _run_routed("get_news", args.ticker, args.start_date, args.end_date)


def cmd_global_news(args) -> int:
    # Forward only the flags the caller set: an explicit None would override
    # vendor-side defaults that are not None-safe (alpha_vantage's 7/50).
    kwargs = {}
    if args.look_back_days is not None:
        kwargs["look_back_days"] = args.look_back_days
    if args.limit is not None:
        kwargs["limit"] = args.limit
    return _run_routed("get_global_news", args.curr_date, **kwargs)


def cmd_insider_transactions(args) -> int:
    return _run_routed("get_insider_transactions", args.ticker)


def cmd_macro(args) -> int:
    return _run_routed("get_macro_indicators", args.indicator, args.curr_date, args.look_back_days)


def cmd_prediction_markets(args) -> int:
    return _run_routed("get_prediction_markets", args.topic, args.limit)


def cmd_social(args) -> int:
    """Pre-fetched sentiment bundle: ticker news + StockTwits + Reddit.

    Emits the exact delimited blocks the sentiment analyst's prompt expects.
    Every fetcher degrades gracefully to a placeholder string, so the output
    always renders — either real data or an explicit "<unavailable>" marker.
    """
    from tradingagents.dataflows.reddit import fetch_reddit_posts
    from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages

    end_date = args.curr_date
    start_date = (
        datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=7)
    ).strftime("%Y-%m-%d")

    try:
        news_block = route_to_vendor("get_news", args.ticker, start_date, end_date)
    except NoMarketDataError as exc:
        news_block = _no_data_message(exc)
    except Exception as exc:  # noqa: BLE001 — sentiment sources degrade, never abort
        news_block = f"<news unavailable: {type(exc).__name__}>"
    stocktwits_block = fetch_stocktwits_messages(args.ticker, limit=30)
    reddit_block = fetch_reddit_posts(args.ticker)

    _print(
        f"""## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days ({start_date} to {end_date})
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages — retail-trader social platform indexed by cashtag
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Reddit posts — r/wallstreetbets, r/stocks, r/investing (past 7 days)
Community discussion. Engagement signal via upvote score and comment count. Subreddit character matters (r/wallstreetbets is often contrarian/exuberant; r/stocks more measured; r/investing longer-term).

<start_of_reddit>
{reddit_block}
<end_of_reddit>"""
    )
    return 0


# ---------------------------------------------------------------------------
# Run-setup commands
# ---------------------------------------------------------------------------

def cmd_identity(args) -> int:
    from tradingagents.dataflows.identity import (
        build_instrument_context,
        detect_asset_type,
        resolve_instrument_identity,
    )
    from tradingagents.dataflows.symbol_utils import normalize_symbol
    from tradingagents.outcomes import resolve_benchmark

    canonical = normalize_symbol(args.ticker)
    asset_type = args.asset_type or detect_asset_type(args.ticker)
    identity = resolve_instrument_identity(args.ticker)
    _print(json.dumps({
        "input": args.ticker,
        "canonical": canonical,
        "asset_type": asset_type,
        "identity": identity,
        "instrument_context": build_instrument_context(canonical, asset_type, identity),
        "benchmark": resolve_benchmark(canonical, get_config()),
    }, indent=2))
    return 0


def cmd_config(args) -> int:
    cfg = get_config()
    # Provenance for the orchestrator: config values alone cannot distinguish
    # "env var explicitly set to the default" from "unset", and env pins must
    # win over skill-level defaults like the --depth round mapping.
    cfg["env_overrides"] = sorted(
        k for k, v in os.environ.items() if k.startswith("TRADINGAGENTS_") and v
    )
    _print(json.dumps(cfg, indent=2))
    return 0


def cmd_report(args) -> int:
    from tradingagents.reporting import write_report_tree

    with open(args.state_json, encoding="utf-8") as f:
        state = json.load(f)
    ticker = state.get("company_of_interest")
    if not ticker:
        print("CONFIG ERROR: state JSON must contain 'company_of_interest'", file=sys.stderr)
        return 2
    out = write_report_tree(state, ticker, args.out_dir)
    _print(str(out))
    return 0


# ---------------------------------------------------------------------------
# Memory commands (decision log)
# ---------------------------------------------------------------------------

def _memory_log():
    from tradingagents.memory import TradingMemoryLog

    return TradingMemoryLog(get_config())


def _read_text_arg(inline: str | None, file_path: str | None, what: str) -> str | None:
    if inline is not None:
        return inline
    if file_path:
        if file_path == "-":
            return sys.stdin.read()
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    print(f"CONFIG ERROR: provide --{what} or --{what}-file", file=sys.stderr)
    return None


def cmd_memory_context(args) -> int:
    _print(_memory_log().get_past_context(args.ticker))
    return 0


def cmd_memory_store(args) -> int:
    decision = _read_text_arg(args.decision, args.decision_file, "decision")
    if decision is None:
        return 2
    if _memory_log().store_decision(args.ticker, args.trade_date, decision.strip()):
        _print(f"Stored pending decision for {args.ticker} on {args.trade_date}.")
    else:
        _print(
            f"Not stored: memory logging is disabled or a pending entry for "
            f"{args.ticker} on {args.trade_date} already exists."
        )
    return 0


def cmd_memory_pending(args) -> int:
    """List same-ticker pending entries with realized returns where available.

    For each entry whose outcome can already be measured, the JSON includes
    raw/alpha returns (both as floats and pre-formatted percentages) so the
    caller can write a reflection and pass the figures to ``memory resolve``.
    Entries that are too recent report ``"resolvable": false`` and are
    retried on the next run.
    """
    from tradingagents.outcomes import fetch_returns, resolve_benchmark

    log = _memory_log()
    pending = [e for e in log.get_pending_entries() if e["ticker"] == args.ticker]
    benchmark = resolve_benchmark(args.ticker, get_config())
    out = []
    for entry in pending:
        raw, alpha, days = fetch_returns(args.ticker, entry["date"], benchmark=benchmark)
        item = {
            "ticker": args.ticker,
            "trade_date": entry["date"],
            "rating": entry["rating"],
            "decision": entry["decision"],
            "benchmark": benchmark,
            "resolvable": raw is not None,
        }
        if raw is not None:
            item.update({
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "raw_pct": f"{raw:+.1%}",
                "alpha_pct": f"{alpha:+.1%}",
            })
        out.append(item)
    _print(json.dumps(out, indent=2))
    return 0


def cmd_memory_resolve(args) -> int:
    reflection = _read_text_arg(args.reflection, args.reflection_file, "reflection")
    if reflection is None:
        return 2
    updated = _memory_log().update_with_outcome(
        ticker=args.ticker,
        trade_date=args.trade_date,
        raw_return=args.raw,
        alpha_return=args.alpha,
        holding_days=args.days,
        reflection=reflection.strip(),
    )
    if not updated:
        print(
            f"CONFIG ERROR: no pending memory entry for {args.ticker} on "
            f"{args.trade_date} (nothing was updated).",
            file=sys.stderr,
        )
        return 2
    _print(f"Resolved {args.ticker} {args.trade_date}: {args.raw:+.1%} raw, {args.alpha:+.1%} alpha.")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tradingagents.data_cli",
        description="Market-data backend for the Claude Code TradingAgents pipeline.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("stock-data", help="OHLCV price history (CSV)")
    sp.add_argument("symbol")
    sp.add_argument("start_date", help="YYYY-MM-DD")
    sp.add_argument("end_date", help="YYYY-MM-DD (inclusive)")
    sp.set_defaults(func=cmd_stock_data)

    sp = sub.add_parser(
        "indicators",
        help="Technical indicator window. Valid names: close_50_sma, close_200_sma, "
             "close_10_ema, macd, macds, macdh, rsi, boll, boll_ub, boll_lb, atr, vwma, mfi",
    )
    sp.add_argument("symbol")
    sp.add_argument("indicator", help="one name or a comma-separated list, e.g. 'rsi,macd'")
    sp.add_argument("curr_date", help="YYYY-MM-DD")
    sp.add_argument("--look-back-days", type=int, default=30)
    sp.set_defaults(func=cmd_indicators)

    sp = sub.add_parser("snapshot", help="Verified market snapshot (ground truth for exact numbers)")
    sp.add_argument("symbol")
    sp.add_argument("curr_date", help="YYYY-MM-DD")
    sp.add_argument("--look-back-days", type=int, default=30)
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("fundamentals", help="Company overview / key financial metrics")
    sp.add_argument("ticker")
    sp.add_argument("curr_date", nargs="?", default=None, help="YYYY-MM-DD (optional)")
    sp.set_defaults(func=cmd_fundamentals)

    for name, method in (
        ("balance-sheet", "get_balance_sheet"),
        ("cashflow", "get_cashflow"),
        ("income-statement", "get_income_statement"),
    ):
        sp = sub.add_parser(name, help=f"{name.replace('-', ' ').title()} statements")
        sp.add_argument("ticker")
        sp.add_argument("--freq", choices=["annual", "quarterly"], default="quarterly")
        sp.add_argument("--curr-date", default=None, help="YYYY-MM-DD look-ahead cutoff")
        sp.set_defaults(func=lambda a, m=method: _financials(m, a))

    sp = sub.add_parser("news", help="Ticker news in a date window")
    sp.add_argument("ticker")
    sp.add_argument("start_date", help="YYYY-MM-DD")
    sp.add_argument("end_date", help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_news)

    sp = sub.add_parser("global-news", help="Global macro news")
    sp.add_argument("curr_date", help="YYYY-MM-DD")
    sp.add_argument("--look-back-days", type=int, default=None)
    sp.add_argument("--limit", type=int, default=None)
    sp.set_defaults(func=cmd_global_news)

    sp = sub.add_parser("insider-transactions", help="Insider transactions")
    sp.add_argument("ticker")
    sp.set_defaults(func=cmd_insider_transactions)

    sp = sub.add_parser(
        "macro",
        help="Macro indicator from FRED (aliases like cpi, core_pce, unemployment, "
             "fed_funds_rate, 10y_treasury, yield_curve, vix — or a raw FRED series ID)",
    )
    sp.add_argument("indicator")
    sp.add_argument("curr_date", help="YYYY-MM-DD")
    sp.add_argument("--look-back-days", type=int, default=None, help="default 365")
    sp.set_defaults(func=cmd_macro)

    sp = sub.add_parser("prediction-markets", help="Polymarket market-implied probabilities")
    sp.add_argument("topic", help="e.g. 'Fed rate cut', 'NVDA earnings'")
    sp.add_argument("--limit", type=int, default=None)
    sp.set_defaults(func=cmd_prediction_markets)

    sp = sub.add_parser("social", help="Sentiment bundle: news + StockTwits + Reddit (pre-fetched blocks)")
    sp.add_argument("ticker")
    sp.add_argument("curr_date", help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_social)

    sp = sub.add_parser("identity", help="Resolve ticker identity, asset type, and benchmark (JSON)")
    sp.add_argument("ticker")
    sp.add_argument("--asset-type", choices=["stock", "crypto"], default=None,
                    help="override auto-detection")
    sp.set_defaults(func=cmd_identity)

    sp = sub.add_parser("config", help="Print the effective configuration (JSON)")
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("report", help="Write the markdown report tree from a final-state JSON")
    sp.add_argument("state_json", help="path to the final state JSON file")
    sp.add_argument("out_dir", help="directory for the report tree")
    sp.set_defaults(func=cmd_report)

    mem = sub.add_parser("memory", help="Decision-log operations")
    memsub = mem.add_subparsers(dest="memory_command", required=True)

    sp = memsub.add_parser("context", help="Past-context block for the Portfolio Manager prompt")
    sp.add_argument("ticker")
    sp.set_defaults(func=cmd_memory_context)

    sp = memsub.add_parser("store", help="Append a pending decision entry")
    sp.add_argument("ticker")
    sp.add_argument("trade_date", help="YYYY-MM-DD")
    sp.add_argument("--decision", default=None, help="decision text inline")
    sp.add_argument("--decision-file", default=None, help="file with decision text ('-' = stdin)")
    sp.set_defaults(func=cmd_memory_store)

    sp = memsub.add_parser("pending", help="Same-ticker pending entries with realized returns (JSON)")
    sp.add_argument("ticker")
    sp.set_defaults(func=cmd_memory_pending)

    sp = memsub.add_parser("resolve", help="Mark a pending entry resolved with returns + reflection")
    sp.add_argument("ticker")
    sp.add_argument("trade_date", help="YYYY-MM-DD")
    sp.add_argument("--raw", type=float, required=True, help="raw return as a decimal, e.g. 0.042")
    sp.add_argument("--alpha", type=float, required=True, help="alpha return as a decimal")
    sp.add_argument("--days", type=int, required=True, help="actual holding days")
    sp.add_argument("--reflection", default=None, help="2-4 sentence reflection inline")
    sp.add_argument("--reflection-file", default=None, help="file with reflection ('-' = stdin)")
    sp.set_defaults(func=cmd_memory_resolve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 0
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
