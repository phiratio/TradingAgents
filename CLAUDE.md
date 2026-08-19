# TradingAgents (Claude Code edition)

Multi-agent trading-research framework where **Claude Code is the reasoning
engine**. There are no LLM SDKs, no LLM API keys, and no LangGraph: every agent
role (analysts, researchers, trader, risk team, portfolio manager) is a Claude
Code subagent in `.claude/agents/`, orchestrated by the `/tradingagents` skill
in `.claude/skills/tradingagents/SKILL.md`. The Python package is a keyless
market-data backend.

## Layout

- `tradingagents/dataflows/` — data vendors: yfinance (OHLCV, indicators,
  fundamentals, news), Reddit RSS, StockTwits, FRED (needs `FRED_API_KEY`),
  Polymarket, optional Alpha Vantage (needs `ALPHA_VANTAGE_API_KEY`). Vendor
  routing lives in `interface.py`; symbol normalization in `symbol_utils.py`.
- `tradingagents/data_cli.py` — the only entry point agents use:
  `.venv/bin/python -m tradingagents.data_cli <command>`. Run `--help` for the
  command list (`stock-data`, `indicators`, `snapshot`, `news`, `social`,
  `macro`, `identity`, `memory ...`, `report`, ...).
- `tradingagents/memory.py` + `outcomes.py` — append-only decision log at
  `~/.tradingagents/memory/trading_memory.md` with realized-return resolution.
- `tradingagents/reporting.py` — markdown report-tree writer.
- `.claude/agents/tradingagents-*.md` — the 12 agent role definitions.
- `.claude/skills/tradingagents/SKILL.md` — pipeline orchestration (the source
  of truth for phase order, debate arithmetic, and persistence formats).

## Rules

- To analyze a ticker, always go through the `/tradingagents` skill — do not
  improvise the pipeline.
- Never fabricate market data; everything numeric comes from the data CLI. The
  `snapshot` command is the ground truth for exact prices/indicator values.
- Never execute, simulate, or offer to execute trades. Output is research only,
  and responses must carry the not-financial-advice disclaimer.
- Output-format contracts (the `**Rating**:` line, the 5-tier
  Buy/Overweight/Hold/Underweight/Sell scale, the memory-log entry format,
  the report tree layout) are load-bearing — do not restyle them.

## Development

- Setup: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
- Tests: `.venv/bin/python -m pytest`
- Lint: `.venv/bin/python -m ruff check .`
