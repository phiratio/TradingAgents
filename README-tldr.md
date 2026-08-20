# TradingAgents — TL;DR

Multi-agent trading research inside Claude Code. No LLM API keys.

## Setup (once)

Requires Python **3.10+** and [Claude Code](https://claude.com/claude-code).
Check first — `python3 --version` — and if it prints 3.9 or older (common with
pyenv/macOS defaults), use a versioned interpreter like `python3.12` below.

```bash
git clone https://github.com/phiratio/TradingAgents.git
cd TradingAgents
python3.12 -m venv .venv   # or python3 if it is 3.10+
.venv/bin/pip install -e .
```

The venv must be named `.venv` in the repo root — the /tradingagents skill and
the pre-approved permissions in `.claude/settings.json` invoke
`.venv/bin/python`.

## Run

Start Claude Code in the repo:

```bash
claude
```

Then:

```
/tradingagents                             # interactive: pick ticker, date, analysts, depth
/tradingagents NVDA                        # analyze a ticker as of today
/tradingagents 0700.HK 2026-08-15          # historical analysis date
/tradingagents BTC-USD --depth medium      # deeper debate (shallow|medium|deep = 1|3|5 rounds)
/tradingagents SPY --analysts market,news  # subset of the analyst team
```

Or just ask in plain language: *"run a trading analysis on Tesla"*.

The result: a rating (Buy / Overweight / Hold / Underweight / Sell) in chat and
a full report tree in `./reports/{TICKER}_{timestamp}/` (analyst reports,
bull/bear debate, trader plan, risk debate, final decision,
`complete_report.md`).

## Optional (more data)

Copy `.env.example` to `.env` and set:

- `FRED_API_KEY` — macro data (CPI, rates, yield curve); [free key](https://fred.stlouisfed.org/docs/api/api_key.html)
- `ALPHA_VANTAGE_API_KEY` — alternative price/news vendor; [free key](https://www.alphavantage.co/support/#api-key)

Everything else runs keyless out of the box.

## Poke the data layer directly (optional)

```bash
.venv/bin/python -m tradingagents.data_cli --help
.venv/bin/python -m tradingagents.data_cli snapshot NVDA 2026-08-19
```

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

> Research tool only — not financial, investment, or trading advice.
