---
name: tradingagents
description: Run the TradingAgents multi-agent trading analysis for a ticker — parallel analysts, bull/bear research debate, trader proposal, risk debate, and a final Portfolio Manager rating. Use when the user asks to analyze a stock, crypto, or ticker, run TradingAgents, or wants a Buy/Sell/Hold-style research report. Arguments: TICKER [DATE] [--depth shallow|medium|deep] [--analysts market,social,news,fundamentals]
---

# TradingAgents pipeline orchestration

You are the orchestrator of a multi-agent trading-research pipeline. Claude Code
subagents play every role; a Python CLI provides all market data. **You never
fabricate market data and never execute trades** — the output is a research
report and a rating, nothing else.

> This framework is for research. It is not financial, investment, or trading
> advice. Always include this disclaimer in the final response.

## 0. Setup

All commands run from the repository root. The data backend is:

```
DATA=".venv/bin/python -m tradingagents.data_cli"
```

If `.venv` does not exist or `import yfinance` fails, bootstrap it first:

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
```

(Use `python3.12`/`python3.13` explicitly if `python3` is older than 3.10.)

## 1. Arguments

From the skill invocation (`/tradingagents TICKER [DATE] [flags]`):

- **TICKER** — default `SPY` if omitted. Valid chars: alphanumerics plus `._-^=`, max 32.
- **DATE** — `YYYY-MM-DD`, default today. Reject future dates.
- **--depth** — `shallow` (default) | `medium` | `deep` → sets BOTH debate-round
  values to 1 | 3 | 5, unless `TRADINGAGENTS_MAX_DEBATE_ROUNDS` /
  `TRADINGAGENTS_MAX_RISK_ROUNDS` are pinned in the environment (those win
  per-key). Detect pins via the `env_overrides` list in the `config` output —
  not by comparing values, which cannot distinguish "pinned to the default"
  from "unset".
- **--analysts** — comma list from `market,social,news,fundamentals`; default all
  four. (`social` is the sentiment analyst — the wire name is historical.)

## 2. Run setup (two CLI calls)

```bash
$DATA identity TICKER
$DATA config
```

From `identity`: `canonical` (use it as THE ticker from here on), `asset_type`,
`instrument_context`, `benchmark`. If `asset_type` is `crypto`, drop the
fundamentals analyst from the selection.

From `config`: `output_language`, `results_dir`, and the rounds — use
`max_debate_rounds` / `max_risk_discuss_rounds` from the config only when
`env_overrides` contains `TRADINGAGENTS_MAX_DEBATE_ROUNDS` /
`TRADINGAGENTS_MAX_RISK_ROUNDS` respectively; otherwise use the --depth
mapping.

**Language rule**: if `output_language` is not English, append this exact
sentence to every subagent task prompt: ` Write your entire response in {lang}.`

## 3. Resolve past decisions (memory Phase B)

```bash
$DATA memory pending TICKER
```

For every entry with `"resolvable": true`, write a reflection and store it:

- The reflection is exactly 2–4 sentences of plain prose (no bullets, no
  headers, no markdown), covering in order: (1) was the directional call
  correct? — cite the alpha figure; (2) which part of the investment thesis
  held or failed; (3) one concrete lesson for the next similar analysis. Be
  specific and terse; it is stored verbatim and re-read by future analyses.
- Base it on the entry's `decision`, `raw_pct`, `alpha_pct`, and `benchmark`.

```bash
$DATA memory resolve TICKER TRADE_DATE --raw {raw_return} --alpha {alpha_return} --days {holding_days} --reflection "..."
```

Entries with `"resolvable": false` are simply left for a future run.

Then fetch the context the Portfolio Manager will receive:

```bash
$DATA memory context TICKER
```

Save the output as `past_context` (may be empty).

## 4. Analyst phase (parallel subagents)

Launch the selected analysts **in parallel** (one message, multiple Agent
calls). Subagent types and their report keys:

| selection key | subagent | report key |
|---|---|---|
| `market` | `tradingagents-market-analyst` | `market_report` |
| `social` | `tradingagents-sentiment-analyst` | `sentiment_report` |
| `news` | `tradingagents-news-analyst` | `news_report` |
| `fundamentals` | `tradingagents-fundamentals-analyst` | `fundamentals_report` |

Each analyst task prompt must contain, verbatim where quoted:

```
Ticker: {canonical}
Analysis date: {date} — treat it as 'now' for all analysis and data-call date ranges.
{instrument_context}
Data CLI: run data commands from the repository root with `.venv/bin/python -m tradingagents.data_cli ...` (this exact relative form — it is pre-approved in .claude/settings.json)
{language sentence, if non-English}
Produce your report now.
```

Each subagent's final message IS its report — capture it whole. If an analyst
returns a `NO_DATA_AVAILABLE`/`DATA_UNAVAILABLE` styled report, keep it; the
downstream agents are built to reason about missing data honestly. If the
market analyst reports no usable price data for the ticker, stop the pipeline
and tell the user the symbol appears invalid or unsupported.

For a skipped analyst, its report is the empty string.

## 5. Investment debate (Bull vs Bear, sequential)

State: `history` (string, starts empty), `bull_history`, `bear_history`,
`current_response` (last argument), `count` (starts 0).

Loop while `count < 2 * max_debate_rounds`. **Bull speaks first**, then they
alternate strictly (Bull → Bear → Bull → …).

- Bull turn → subagent `tradingagents-bull-researcher`
- Bear turn → subagent `tradingagents-bear-researcher`

Task prompt for each debate turn (both sides, same structure):

```
Analysis date: {date} — treat it as 'now'.
{instrument_context}
Market research report: {market_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals label}: {fundamentals_report}
Conversation history of the debate: {history}
Last opposing argument: {current_response}
{language sentence, if non-English}
```

`{fundamentals label}` is `Company fundamentals report` for stocks, or
`Asset fundamentals report (may be unavailable for crypto)` for crypto.

After each turn, with `argument = "Bull Analyst: {response}"` (or
`"Bear Analyst: {response}"`): append `"\n" + argument` to `history` and to the
speaker's own history, set `current_response = argument`, increment `count`.
The speaker-name prefixes are load-bearing — keep them exact.

## 6. Research Manager

Subagent `tradingagents-research-manager`. Task prompt:

```
Analysis date: {date} — treat it as 'now'.
{instrument_context}
Debate History:
{history}
{language sentence, if non-English}
```

Its response is `investment_plan` (contains `**Recommendation**` /
`**Rationale**` / `**Strategic Actions**`).

## 7. Trader

Subagent `tradingagents-trader`. Task prompt:

```
Analysis date: {date} — treat it as 'now'.
Based on a comprehensive analysis by a team of analysts, here is an investment plan tailored for {canonical}. {instrument_context} This plan incorporates insights from current technical market trends, macroeconomic indicators, and social media sentiment. Use this plan as a foundation for evaluating your next trading decision.

Proposed Investment Plan: {investment_plan}

Leverage these insights to make an informed and strategic decision.
{language sentence, if non-English}
```

Its response is `trader_investment_plan` (ends with
`FINAL TRANSACTION PROPOSAL: **BUY|HOLD|SELL**`).

## 8. Risk debate (three analysts, cycling)

State: `history` (starts empty), per-analyst histories, latest responses
(`current_risky_response`, `current_safe_response`, `current_neutral_response`
— empty strings before a speaker's first turn), `count` (starts 0).

Loop while `count < 3 * max_risk_discuss_rounds`, speaker order strictly
cycling **Aggressive → Conservative → Neutral → Aggressive → …**

- `tradingagents-risk-aggressive`, `tradingagents-risk-conservative`,
  `tradingagents-risk-neutral`

Task prompt for each turn:

```
Analysis date: {date} — treat it as 'now'.
{instrument_context}
The trader's decision: {trader_investment_plan}
Market Research Report: {market_report}
Social Media Sentiment Report: {sentiment_report}
Latest World Affairs Report: {news_report}
Company Fundamentals Report: {fundamentals_report}
Conversation history so far: {history}
Last aggressive-analyst response: {current_risky_response}
Last conservative-analyst response: {current_safe_response}
Last neutral-analyst response: {current_neutral_response}
{language sentence, if non-English}
```

(Omit the speaker's own "last response" line for its own turn.) After each
turn, with `argument = "{Aggressive|Conservative|Neutral} Analyst: {response}"`:
append to `history` and the speaker's history, update the speaker's latest
response, increment `count`.

## 9. Portfolio Manager (final decision)

Subagent `tradingagents-portfolio-manager`. Task prompt:

```
Analysis date: {date} — treat it as 'now'.
{instrument_context}

Context:
- Research Manager's investment plan: **{investment_plan}**
- Trader's transaction proposal: **{trader_investment_plan}**
{lessons line}
Risk Analysts Debate History:
{risk history}
{language sentence, if non-English}
```

`{lessons line}`: only when `past_context` is non-empty, include exactly:

```
- Lessons from prior decisions and outcomes:
{past_context}
```

Omit the line entirely when `past_context` is empty.

The response is `final_trade_decision` (contains `**Rating**` /
`**Executive Summary**` / `**Investment Thesis**`).

## 10. Persist results

Write the final state JSON — this exact shape — using the Write tool:

```json
{
  "company_of_interest": canonical, "trade_date": date,
  "market_report": ..., "sentiment_report": ..., "news_report": ..., "fundamentals_report": ...,
  "investment_debate_state": {"bull_history": ..., "bear_history": ..., "history": ...,
    "current_response": ..., "judge_decision": investment_plan},
  "investment_plan": investment_plan,
  "trader_investment_decision": trader_investment_plan,
  "trader_investment_plan": trader_investment_plan,
  "risk_debate_state": {"aggressive_history": ..., "conservative_history": ...,
    "neutral_history": ..., "history": ..., "judge_decision": final_trade_decision},
  "final_trade_decision": final_trade_decision
}
```

1. Save it to `{results_dir}/{canonical}/TradingAgentsStrategy_logs/full_states_log_{date}.json`.
2. Write the report tree: `$DATA report {state json path} reports/{canonical}_{YYYYmmdd_HHMMSS}`
   (relative to the current working directory).
3. Store the decision in memory: write `final_trade_decision` to a temp file, then
   `$DATA memory store TICKER DATE --decision-file {tmp}`.

## 11. Final response to the user

Lead with the rating — the word after `**Rating**:` in the final decision
(one of Buy / Overweight / Hold / Underweight / Sell; if the line is missing,
say the decision was unrated and quote its conclusion instead). Then:

- The Portfolio Manager's executive summary and key evidence from the debate.
- A short table: each analyst's one-line takeaway + the trader's action.
- The path to the saved report tree.
- The research disclaimer.

Never place, offer to place, or simulate placing an order.

## Failure handling

- Data commands degrade to readable text (`NO_DATA_AVAILABLE`, `<stocktwits
  unavailable: ...>`); pass them to agents as-is — they are instructed not to
  fabricate around gaps.
- `CONFIG ERROR` on stderr (exit 2) means a configured vendor lacks its API
  key (`FRED_API_KEY`, `ALPHA_VANTAGE_API_KEY`). Tell the user which env var
  to set; the pipeline continues without that data source.
- If a subagent fails, retry it once with the same prompt before surfacing
  the failure.
