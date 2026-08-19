"""Tests for the ``tradingagents.data_cli`` command surface.

The data CLI is the only thing the Claude Code subagents shell out to, so its
contract is exercised end-to-end through ``main(argv)`` — exit codes plus
captured stdout/stderr — with ``route_to_vendor`` stubbed out. No network.
"""

import json

import pytest

from tradingagents import data_cli
from tradingagents.data_cli import main
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError

pytestmark = pytest.mark.unit


class _VendorStub:
    """Recording stand-in for ``route_to_vendor``.

    ``responder`` receives ``(method, *args, **kwargs)`` and either returns the
    payload string or raises, letting one stub model per-call failures.
    """

    def __init__(self, responder=None):
        self.calls = []
        self._responder = responder or (lambda method, *a, **k: "STUB_PAYLOAD")

    def __call__(self, method, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        return self._responder(method, *args, **kwargs)


@pytest.fixture()
def stub_vendor(monkeypatch):
    """Patch ``route_to_vendor`` in the data_cli namespace with a recorder."""

    def _install(responder=None):
        stub = _VendorStub(responder)
        monkeypatch.setattr(data_cli, "route_to_vendor", stub)
        return stub

    return _install


# ---------------------------------------------------------------------------
# indicators: comma-splitting and per-indicator error capture
# ---------------------------------------------------------------------------


class TestIndicators:
    def test_comma_split_calls_vendor_once_per_indicator(self, stub_vendor, capsys):
        stub = stub_vendor(lambda method, symbol, ind, date, days: f"{ind}-report")

        rc = main([
            "indicators", "AAPL", " RSI , MACD ,", "2024-01-05",
            "--look-back-days", "14",
        ])

        assert rc == 0
        # One vendor call per comma-separated indicator, names stripped and
        # lowercased, remaining args passed through positionally.
        assert stub.calls == [
            ("get_indicators", ("AAPL", "rsi", "2024-01-05", 14), {}),
            ("get_indicators", ("AAPL", "macd", "2024-01-05", 14), {}),
        ]
        out = capsys.readouterr().out
        assert out == "rsi-report\n\nmacd-report\n"

    def test_invalid_indicator_message_inline_others_still_render(self, stub_vendor, capsys):
        def responder(method, symbol, ind, date, days):
            if ind == "bogus":
                raise ValueError("bogus is not a supported indicator")
            return f"{ind}-report"

        stub = stub_vendor(responder)

        rc = main(["indicators", "AAPL", "rsi,bogus,macd", "2024-01-05"])

        assert rc == 0
        assert len(stub.calls) == 3
        # Default look-back window applies when the flag is omitted.
        assert stub.calls[0] == ("get_indicators", ("AAPL", "rsi", "2024-01-05", 30), {})
        out = capsys.readouterr().out
        # The bad indicator's message appears inline between the good results.
        assert out == "rsi-report\n\nbogus is not a supported indicator\n\nmacd-report\n"

    def test_vendor_not_configured_stops_loop_with_exit_2(self, stub_vendor, capsys):
        # VendorNotConfiguredError is also a ValueError: it must NOT be
        # swallowed into the inline-error path — it aborts with exit 2.
        def responder(method, symbol, ind, date, days):
            if ind == "macd":
                raise VendorNotConfiguredError("alpha_vantage requires ALPHA_VANTAGE_API_KEY")
            return f"{ind}-report"

        stub_vendor(responder)

        rc = main(["indicators", "AAPL", "rsi,macd,mfi", "2024-01-05"])

        assert rc == 2
        captured = capsys.readouterr()
        assert "CONFIG ERROR" in captured.err
        assert "ALPHA_VANTAGE_API_KEY" in captured.err
        # Results gathered before the config error still render.
        assert "rsi-report" in captured.out
        assert "mfi-report" not in captured.out


# ---------------------------------------------------------------------------
# Routed commands: argument passing spot-checks
# ---------------------------------------------------------------------------


class TestRoutedCommands:
    def test_stock_data_passes_symbol_and_date_window_in_order(self, stub_vendor, capsys):
        stub = stub_vendor(lambda *a, **k: "Date,Open,High,Low,Close,Volume")

        rc = main(["stock-data", "NVDA", "2024-01-01", "2024-01-31"])

        assert rc == 0
        assert stub.calls == [
            ("get_stock_data", ("NVDA", "2024-01-01", "2024-01-31"), {}),
        ]
        assert capsys.readouterr().out == "Date,Open,High,Low,Close,Volume\n"

    def test_macro_passes_indicator_date_and_lookback(self, stub_vendor, capsys):
        stub = stub_vendor(lambda *a, **k: "CPI series")

        rc = main(["macro", "cpi", "2024-06-03", "--look-back-days", "90"])

        assert rc == 0
        assert stub.calls == [
            ("get_macro_indicators", ("cpi", "2024-06-03", 90), {}),
        ]
        assert capsys.readouterr().out == "CPI series\n"

    def test_macro_lookback_defaults_to_none(self, stub_vendor):
        stub = stub_vendor()

        rc = main(["macro", "vix", "2024-06-03"])

        assert rc == 0
        assert stub.calls == [
            ("get_macro_indicators", ("vix", "2024-06-03", None), {}),
        ]


# ---------------------------------------------------------------------------
# Vendor error contract
# ---------------------------------------------------------------------------


class TestVendorErrors:
    def test_no_market_data_prints_sentinel_and_exits_zero(self, stub_vendor, capsys):
        def responder(*a, **k):
            raise NoMarketDataError("ZZZZ", canonical="ZZZZ-USD", detail="empty frame")

        stub_vendor(responder)

        rc = main(["stock-data", "ZZZZ", "2024-01-01", "2024-01-31"])

        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("NO_DATA_AVAILABLE:")
        assert "'ZZZZ'" in captured.out
        assert "(resolved to 'ZZZZ-USD')" in captured.out
        assert "empty frame" in captured.out
        # Anti-fabrication instruction must reach the agent verbatim.
        assert "Do not estimate or fabricate" in captured.out
        assert captured.err == ""

    def test_vendor_not_configured_exits_2_with_stderr(self, stub_vendor, capsys):
        def responder(*a, **k):
            raise VendorNotConfiguredError("fred requires FRED_API_KEY")

        stub_vendor(responder)

        rc = main(["news", "AAPL", "2024-01-01", "2024-01-31"])

        assert rc == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("CONFIG ERROR")
        assert "fred requires FRED_API_KEY" in captured.err


# ---------------------------------------------------------------------------
# memory: store -> resolve -> context round-trip in tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture()
def memory_log_path(tmp_path, monkeypatch):
    """Point the memory log at tmp_path.

    ``TRADINGAGENTS_MEMORY_LOG_PATH`` is read at ``default_config`` import
    time and cached in the dataflows config singleton, so the env var alone
    cannot retarget an already-imported module. Patch ``data_cli.get_config``
    (the only config source ``_memory_log`` uses) and set the env var too so
    any re-derived config agrees.
    """
    log_path = tmp_path / "memory" / "trading_memory.md"
    monkeypatch.setenv("TRADINGAGENTS_MEMORY_LOG_PATH", str(log_path))
    cfg = {"memory_log_path": str(log_path), "memory_log_max_entries": None}
    monkeypatch.setattr(data_cli, "get_config", lambda: dict(cfg))
    return log_path


class TestMemoryRoundTrip:
    DECISION = "Rating: Buy. Momentum and earnings revisions both point up."
    REFLECTION = "The momentum call was right; hold winners longer."

    def test_store_resolve_context_round_trip(self, memory_log_path, capsys):
        # --- store writes a pending entry ---
        rc = main([
            "memory", "store", "AAPL", "2024-05-01", "--decision", self.DECISION,
        ])
        assert rc == 0
        assert "Stored pending decision for AAPL on 2024-05-01." in capsys.readouterr().out

        content = memory_log_path.read_text(encoding="utf-8")
        assert "[2024-05-01 | AAPL | Buy | pending]" in content
        assert f"DECISION:\n{self.DECISION}" in content
        assert "<!-- ENTRY_END -->" in content

        # --- duplicate store is idempotent: file byte-identical ---
        rc = main([
            "memory", "store", "AAPL", "2024-05-01", "--decision", self.DECISION,
        ])
        assert rc == 0
        capsys.readouterr()
        assert memory_log_path.read_text(encoding="utf-8") == content
        assert content.count("[2024-05-01 | AAPL |") == 1

        # --- resolve rewrites the tag and appends REFLECTION ---
        rc = main([
            "memory", "resolve", "AAPL", "2024-05-01",
            "--raw", "0.042", "--alpha", "0.021", "--days", "5",
            "--reflection", self.REFLECTION,
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Resolved AAPL 2024-05-01: +4.2% raw, +2.1% alpha." in out

        resolved = memory_log_path.read_text(encoding="utf-8")
        assert "| pending]" not in resolved
        assert "[2024-05-01 | AAPL | Buy | +4.2% | +2.1% | 5d]" in resolved
        assert f"REFLECTION:\n{self.REFLECTION}" in resolved
        # The decision body survives the rewrite.
        assert f"DECISION:\n{self.DECISION}" in resolved

        # --- context prints the resolved entry ---
        rc = main(["memory", "context", "AAPL"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Past analyses of AAPL" in out
        assert "[2024-05-01 | AAPL | Buy | +4.2% | +2.1% | 5d]" in out
        assert self.DECISION in out
        assert self.REFLECTION in out

    def test_store_requires_decision_text(self, memory_log_path, capsys):
        rc = main(["memory", "store", "AAPL", "2024-05-01"])
        assert rc == 2
        assert "CONFIG ERROR" in capsys.readouterr().err
        assert not memory_log_path.exists()


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


class TestReport:
    def test_report_writes_tree_from_state_json(self, tmp_path, capsys):
        state = {
            "company_of_interest": "AAPL",
            "trade_date": "2024-05-01",
            "market_report": "The 50-day SMA is trending up.",
        }
        state_json = tmp_path / "state.json"
        state_json.write_text(json.dumps(state), encoding="utf-8")
        out_dir = tmp_path / "report"

        rc = main(["report", str(state_json), str(out_dir)])

        assert rc == 0
        complete = out_dir / "complete_report.md"
        market = out_dir / "1_analysts" / "market.md"
        assert complete.exists()
        assert market.exists()
        assert market.read_text(encoding="utf-8") == state["market_report"]
        assert "AAPL" in complete.read_text(encoding="utf-8")
        # The CLI prints the path of the consolidated report.
        assert str(complete) in capsys.readouterr().out

    def test_report_without_ticker_is_config_error(self, tmp_path, capsys):
        state_json = tmp_path / "state.json"
        state_json.write_text(json.dumps({"market_report": "orphan"}), encoding="utf-8")

        rc = main(["report", str(state_json), str(tmp_path / "report")])

        assert rc == 2
        assert "company_of_interest" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_config_prints_valid_json_with_data_vendors(self, capsys):
        rc = main(["config"])

        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert "data_vendors" in parsed
        assert isinstance(parsed["data_vendors"], dict)
