# TradingAgents — TL;DR

Multi-agent trading research inside Claude Code. No LLM API keys.

## Setup (once)

```bash
git clone https://github.com/phiratio/TradingAgents.git
cd TradingAgents
python3 -m venv .venv
.venv/bin/pip install -e .
```

Requires Python 3.10+ (`python3.12 -m venv .venv` if your default is older) and
[Claude Code](https://claude.com/claude-code).

## Run

Start Claude Code in the repo:

```bash
claude
```

Then:

```
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
