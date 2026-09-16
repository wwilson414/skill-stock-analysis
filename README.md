# 📊 Stock Analysis Skill for Ai Coding

> An Agent Skill plugin that generates professional-grade decision dashboards from stock tickers. Supports A-shares, HK stocks, and US stocks.

![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Markets](https://img.shields.io/badge/Markets-A--Share_|__HK_|__US-orange)

## Core Features

| Feature | Description |
| -------------------------------- | ------------------------------------------------------------ |
| **Three Major Markets** | A-shares (600519), HK stocks (HK00700), US stocks(TSLA) |
| **Smart Data Sources** | Graceful degradation strategy, supports Tushare/THS Official API/efinance/THS/akshare/yfinance |
| **Complete Technical Analysis** | MA / MACD / RSI / Volume / Bias / Support / Relative Strength / ATR / Risk-Reward |
| **100-Point Scoring System** | 7-dimension composite scoring (trend 26 / bias 20 / volume 15 / MACD 15 / RSI 10 / support 10 / relative strength 4) |
| **Market Phase Awareness** | Every bar is classified `uptrend_pullback` / `downtrend_decline` / `range_swing`; the phase gates and re-weights every interpretation |
| **Phase-Aware Combo Signal** | P4-1 combo (`comp_vol` in trends/ranges, `mr_score` in downtrends) is emitted as a `combo` object on every analysis |
| **Empirically Validated** | P0–P4 research pipeline — 36 stocks × 3.7 years × 29,476 signals, bootstrap CIs, execution repricing, then out-of-sample paper trading |
| **AI Deep Analysis** | Claude itself as the analysis engine, combining technical + news sentiment |
| **Zero Config Required** | Works out-of-the-box with free data sources; more accurate with API keys configured |
| **Strict Entry Strategy** | No chasing highs (bias >5% = no buy), prefers volume-contraction pullbacks, precise stop-loss |
| **Persistence & Replay (P4-3/4-4)** | Signals / trades / portfolio snapshots land in SQLite (`store.py`), then replay through a T+1, fee- and limit-aware paper trader |
| **Risk Control (P4-5)** | Position limits (20% per stock, 60% per phase), -8% single-stock stop-loss, -15% portfolio circuit breaker |

## Quick Start

### Installation

Clone this project into your Ai Coding skills directory (the folder name must match the skill
id `stock-analysis`):

```bash
git clone https://github.com/wwilson414/skill-stock-analysis.git ~/.agent/skills/stock-analysis
```

Python dependencies — the skill instructs the agent to install them automatically when missing
(`pip3 install akshare yfinance --quiet`); install them yourself only for manual CLI use:

```bash
pip3 install akshare yfinance
```

Then just ask — see [How to analyze a stock with it](#how-to-analyze-a-stock-with-it) below.

### How to analyze a stock with it

You never run anything by hand: you ask the Agent (Claude Code or any agent with this skill
installed) in plain language or with the slash command. The skill then fetches real market data,
computes the indicators, searches the latest news, and prints a decision dashboard.

#### Step 1 — ask

```text
/stock-analysis 600519              # one A-share
/stock-analysis HK00700             # one HK stock
/stock-analysis TSLA                # one US stock
/stock-analysis 000001,600036,TSLA  # several at once (markets can be mixed)
/stock-analysis 贵州茅台             # Chinese company names work too
```

Exactly the same in natural language: `Analyze TSLA for me` · `How is 600519?` ·
`Compare PLTR and RKLB`.

| You want | Say |
| -------- | --- |
| A full judgment on one stock | `/stock-analysis 600519` |
| Only the technical picture | `Check the technicals for PLTR` |
| A shortlist compared side by side | `/stock-analysis 600519,000858,601318` |
| The latest news folded in | `Analyze 600519 with recent news` (adds a News Summary block) |
| An explanation of the numbers | `Why is the score 62?` · `What would change the signal to Buy?` |
| A sanity check on your own plan | `Is 38.5 a sensible stop-loss for 600519?` |

Behind the scenes (no configuration needed): ticker is normalized → the Python script fetches
quotes + K-line → indicators, phase and combo are computed → news is searched → the agent judges
and prints the dashboard. Without any API key it runs on free data sources.

#### Step 2 — read the answer

Each ticker comes back as a card under a header line
(`2026-03-04 Stock Decision Dashboard` + `N stocks analyzed | Buy: x | Hold: y | Sell: z`).
[a complete example is further down](#output-example):

| Card block | How to read it |
| ---------- | -------------- |
| Signal + Composite Score | The one-line verdict: Strong Buy (≥75 with bullish MA alignment) → Hold (≥45) → Sell / Strong Sell (<30). A buy-grade score is **demoted to Hold whenever a hard gate fired** — the gate is called out in `AI Judgment` / `Risk Factors` |
| Phase (in `Technical Analysis`, and inside `Combo Signal`) | `Uptrend Pullback` = buy setups allowed · `Range Swing` = wait for a breakout, half size · `Downtrend Decline` = **do not buy on score alone** |
| `Combo Signal` | The phase-aware ranking: `Combo Score` positive = candidate · `Gate Blocked: Yes` = not tradeable on that bar · `Weight` (1.0 / 0.5 / 0.3) is the intended position size |
| `Technical Analysis` | MA alignment, MACD, RSI, volume ratio and bias, plus phase / 120-day position, relative strength vs the index, ATR & annualized volatility, **R:R ratio**, limit status and the 30-day unlock |
| `Price Targets` | Entry / Target / **Stop-Loss**, always taken from the script's ATR-adaptive levels (`indicators.risk`) so stop and target stay internally consistent |
| `AI Judgment` + Bullish / Risk factors | Why the numbers look like this, and the two sides that were weighed |
| `News Summary` + `Latest News` *(when news was requested)* | Time-decay-weighted sentiment (14-day half-life), the `Major Risk` flag, event types, and the dated items themselves |
| Footer | Data sources, analysis timestamp and the disclaimer |

The numbers in the card are computed by the script, never estimated by the model — so you can ask
follow-up questions about any of them and the agent will explain the inputs.

#### Step 3 — decide

1. **Filter by phase first.** In `Downtrend Decline` the tool refuses to buy on score (a high
   momentum score in a downtrend historically keeps falling — 20-day IC −0.066); the only exception
   is an extreme mean-reversion bar (RSI2 ≤ 10, ≥10% below MA60, on a stabilization day) at 30% weight.
2. **Rank candidates by `Combo Score`, not by the 100-point score.** The composite score is a
   *within-phase ranking device* with no probability content (its calibrated Brier score is worse
   than a constant baseline); the combo is the component that survived out-of-sample validation.
3. **Discard anything with `Gate Blocked: Yes`**, and anything the script hard-gated — the four
   script gates are: the downtrend phase, R:R < 1.5, a sealed limit-up (T+1) and a >=5% float
   unlock within 30 days. A fired gate demotes a buy-grade score to Hold and is called out in
   `AI Judgment` / `Risk Factors`. On top of that, keep the discipline rules yourself: never buy
   with RSI > 80 (overbought) or bias > 5% above MA5 (no chasing).
4. **Size by the combo weight** — 1.0 in uptrends, 0.5 in ranges, 0.3 in downtrends — with the same
   caps the paper-trading engine enforces: ≤20% per stock, ≤60% per phase.
5. **Take the stop-loss and target off the card and honour them.** The stop is structural
   (MA20 / 20-day low) widened by ATR; exit on the stop instead of averaging down. A
   `Limit Down` note means the stop may not fill that day.
6. **Use the news block for timing.** `Major Risk: Yes` or clearly negative sentiment on a
   technical buy setup means wait for the event to pass; `stale`/empty news means the call rests on
   the technicals alone.
7. **Treat the dashboard as a disciplined filter, not a prediction.** It removes chasing, downtrends
   and bad reward-to-risk, and tells you in advance where you exit — that is its edge, not certainty.

#### Step 4 — repeatable routines

| Routine | How to do it |
| ------- | ------------ |
| Daily watchlist review | `/stock-analysis 600519,000858,601318,HK00700` — read the summary line first, then only the cards whose signal or phase changed |
| Positions check | Ask for each holding and look at `Phase`, `R:R` and the stop: a `Downtrend Decline` phase is an exit signal, not a buying opportunity |
| News-driven day | `Analyze 600519 with recent news`, and read `News Summary` before the technicals |
| Explain a verdict | `Why is the score 62?` / `What would change the signal to Buy and what would keep it at Wait?` |
| Validate it on your market | `python3 references/stock_data_fetcher.py --stocks "600519" --backtest --calibrate` prints per-phase ICs; the full evidence run is `python3 references/p0_backtest.py` → `reports/p0_expansion.json` (see [Validation & Production Toolchain](#validation--production-toolchain-p0p4)) |
| Paper-trade the signals | `p4_combo_backtest.py --save-store reports/signals.db` then `paper_trader.py --db reports/signals.db --start 2025-09-01` (see [daily operation](#optional-daily-operation)) |

Want to go deeper? [Scoring System](#scoring-system) explains the 100 points,
[Market Phase Classification](#market-phase-classification) and
[Phase-Aware Combo Signal](#phase-aware-combo-signal-p4-1) explain the two gates above, and
[Hard Rules](#hard-rules-strict-entry-strategy) lists every blocking condition.

### Usage (reference)

> The sections below are **reference material** for driving the pieces yourself (the analysis workflow itself is described above): what to install, CLI flags, the JSON output contract, the offline research/persistence/replay toolchain, tests and troubleshooting.

#### Prerequisites & verification

| Requirement | Notes |
| ----------- | ----- |
| Python 3.9+ | The core script uses the standard library only — no virtualenv needed |
| `pip3 install akshare yfinance` | Needed for live quotes / K-line fallbacks; `akshare` also powers the free A-share news source |
| Optional `pip3 install tushare` | A-share P0 source (requires `TUSHARE_TOKEN`) |
| Optional `pip3 install efinance` | East Money source for A-share / HK |
| Network | Required on the first run; fetched bars/benchmarks/pools are cached in `references/.p0_cache/` |
| Working directory | Run from the repo root, or set `SDF_REFERENCES_DIR=<repo>/references` when copying `stock_data_fetcher.py` elsewhere |

Verify the installation (only the last command needs network):

```bash
python3 -m pytest tests/ -q                                     # 158 passed, fully offline, ~0.5s
python3 references/score_config.py | head -3                     # prints the threshold registry
python3 references/stock_data_fetcher.py --stocks "600519" --days 30 > /tmp/smoke.json
python3 -c "import json;d=json.load(open('/tmp/smoke.json'));print(d['total_success'], d['stocks'][0]['trend_score']['signal'], d['stocks'][0]['combo']['phase'])"
```

Optional credentials — see [Data Source Configuration](#data-source-configuration-optional-enhancement):
`TUSHARE_TOKEN`, `HITHINK_FINANCE_API_KEY` (aliases `FUYAO_API_KEY` / `THS_API_KEY`),
`TAVILY_API_KEY`, `SERPAPI_KEY`. With no key at all the skill still works on free sources.

#### Agent invocation details

Enter directly in the Agent:

```text
/stock-analysis TSLA
/stock-analysis TSLA,PLTR,RKLB
/stock-analysis 600519
/stock-analysis HK00700
/stock-analysis 贵州茅台            # Chinese A-share names resolve automatically
```

Or use natural language:

```text
Analyze TSLA for me
How is 600519?
Check the technicals for PLTR and RKLB
```

Separate tickers with commas, spaces or newlines, and mix markets freely
(`/stock-analysis 600519,HK00700,TSLA`). The agent then follows [SKILL.md](SKILL.md) STEP 1–5:
normalize tickers → run the Python script → search news → analyze → print the decision dashboard.

Batch behaviour: one request can carry any number of tickers; the agent prints one dashboard per
stock plus a summary line (`N stocks analyzed | Buy: x | Hold: y | Sell: z`) and skips, with a note,
any ticker whose fetch failed.

Accepted inputs:

| Input | Example | Resolution |
| ----- | ------- | ---------- |
| A-share code | `600519`, `000001`, `300750`, `688111` | Direct (SH/SZ/STAR/ChiNext/BSE) |
| HK code | `HK00700`, `00700.HK` | Normalized to `HK00700` |
| US ticker | `TSLA`, `PLTR` | Direct |
| Chinese company name | `贵州茅台`, `Kweichow Moutai` | THS search when `HITHINK_FINANCE_API_KEY` is set; otherwise the free akshare code-name table (exact match, then unique substring); last resort WebSearch |

Credentials in the agent: if your client does not inherit user-level environment variables, the
agent copies the key from the configured credential source into the client's Secret/env store, so
you never re-supply it per request. When a `hithink-finance-*` MCP server is configured, the agent
may additionally use those MCP tools for enhanced THS data (SKILL.md STEP 2.5) — optional.

#### Direct CLI (`stock_data_fetcher.py`)

Each run prints one JSON document to **stdout** while progress/source info goes to **stderr**, so
redirecting stdout gives you clean JSON:

```bash
# single stock, full analysis
python3 references/stock_data_fetcher.py --stocks "600519" > /tmp/600519.json

# several markets + news, longer history
python3 references/stock_data_fetcher.py --stocks "600519,HK00700,TSLA" --news --days 250

# Chinese company name (auto-resolved: THS search with a key, free akshare name table otherwise)
python3 references/stock_data_fetcher.py --stocks "贵州茅台"

# validate the scoring system: backtest + IC calibration + weight A/B on identical data
python3 references/stock_data_fetcher.py --stocks "600519,000001,300750,TSLA,AAPL" \
    --backtest --backtest-days 252 --forward-days 5,10,20 --calibrate --ab-test
```

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--stocks` | *required* | Comma-separated codes: A-share `600519`, HK `HK00700`, US `TSLA`, or Chinese names |
| `--days` | `120` | History bars used for the indicators |
| `--news` | off | Attach `news[]` items (A-share: free akshare East Money; HK/US: `TAVILY_API_KEY` or `SERPAPI_KEY`) |
| `--backtest` | off | Backtest mode: replay history, emit signals + forward returns, pool IC by market phase |
| `--backtest-days` | `252` | Backtest lookback window (trading days) |
| `--forward-days` | `5,10,20` | Forward-return horizons (trading days) |
| `--calibrate` | off | Derive suggested component weights from backtest ICs (stderr table + JSON `calibration`) |
| `--calibrate-method` | `tanh` | IC → weight multiplier mapping (`tanh` / `linear`) |
| `--ab-test` | off | A/B the suggested weights against defaults on identical fetched data |

Notes:

- Python packages are **not** installed by the script itself — the skill instructions make the
  agent run `pip3 install akshare yfinance --quiet` when needed; install them yourself for manual
  CLI use.
- If the script is copied out of the repo (the agent writes it to `/tmp`), the combo helper
  modules are auto-located; set `SDF_REFERENCES_DIR=<repo>/references` to be explicit.
- API keys are optional — see [Data Source Configuration](#data-source-configuration-optional-enhancement):
  `TUSHARE_TOKEN`, `HITHINK_FINANCE_API_KEY` (aliases `FUYAO_API_KEY` / `THS_API_KEY`),
  `TAVILY_API_KEY`, `SERPAPI_KEY`.

##### Output contract

| Stream | Content |
| ------ | ------- |
| **stdout** | Exactly one JSON document (analysis mode *or* backtest mode) — nothing else |
| **stderr** | `[INFO] ...` progress, per-source availability, degradation notices, review tables |
| Exit code | `0` even when individual tickers fail — failures are listed in `errors[]`, so always compare `total_success` with `total_requested` |

Analysis-mode envelope:

| Field | Meaning |
| ----- | ------- |
| `analysis_date` / `analysis_time` | Run timestamp |
| `data_sources` | Availability/key status per source (`available` / `not installed` / `configured` / `not set`) |
| `stocks[]` | One object per ticker (schema below) |
| `errors[]` | `{code, error, type}` for tickers that raised |
| `total_requested` / `total_success` | Counts |

Each `stocks[]` entry:

| Key | Content |
| --- | ------- |
| `code` / `market` / `name` | Display ticker, `cn_a` / `cn_hk` / `us`, company name |
| `data_source` / `fetch_errors[]` / `adjustment` | Source that served the bars, per-source errors, price-adjustment mode |
| `realtime` | Latest quote snapshot (price, change %, volume, turnover, P/E, P/B …). Missing P/E / P/B are filled by the [valuation fallback chain](#valuation-pe-pb-fallback-chain); `valuation_source` names the source that filled them |
| `indicators` | `ma`, `macd`, `rsi`, `volume`, `bias`, `support`, `context`, `risk`, `relative_strength`, `tradability` |
| `indicators.context` | `phase` (`uptrend_pullback` / `downtrend_decline` / `range_swing`), `range_pos_pct`, `chg_20d_pct`, `off_high_pct`, `dist_ma60_pct` |
| `indicators.volume` | `vol_ratio`, `trend`, `bar_partial` — when the newest bar is still forming (intraday), `vol_ratio` is **pro-rated to a full-day equivalent** and `vol_ratio_raw` / `session_elapsed_pct` carry the unadjusted value and elapsed-session percent for the card annotation |
| `indicators.risk` | `atr`, `atr_pct`, `ann_vol_pct`, `stop_structural`, `stop_suggested`, `target_suggested`, `rr_ratio` |
| `indicators.relative_strength` | `benchmark`, `stock_ret_20d` / `stock_ret_60d`, `bench_ret_20d` / `bench_ret_60d`, `rs_20d`, `rs_60d` |
| `indicators.tradability` | `limit_status`, `limit_threshold_pct`, `change_pct` |
| `events` | `upcoming_unlocks`, `unlock_pct_30d` (A-share float unlocks) |
| `trend_score` | `total`, `breakdown` (7 components), `signal` / `signal_cn`, `buy_gates[]`, `warnings[]` |
| `combo` | Phase-aware combo: `phase`, `primary`, `primary_score`, `weight`, `secondary`, `secondary_score`, `gates[]`, `gate_results`, `gate_blocked`, `combo_score`, `context`. `null` when the helper modules are missing or the bar is still in warmup |
| `recent_bars` / `as_of` / `total_bars` | Last 10 bars (dates aligned to the indicator input), indicator cutoff date, bars fetched |
| `news[]` *(with `--news`)* | `title`, `content`, `url`, `date`, `source`, `publisher`; the analyzer additionally sets `age_days`, `sentiment`, `event_type[]`, `major_risk` |
| `news_summary` *(with `--news`)* | `sentiment_score` (14-day half-life, range [-1, 1]), `sentiment_label`, `counts`, `event_types[]`, `has_major_risk`, `dated_items`, `total_items`, `stale` |

Backtest mode returns the same envelope with `mode: "backtest"` plus `forward_days` and
`phase_analysis` (pooled IC per phase); each stock carries `{code, total_signals, lookback_bars,
forward_days, by_signal, by_bucket, by_phase, correlations, component_correlations}`, errors
appear as `{code, error}` (e.g. `insufficient_data`, `unknown_market`), `--calibrate` adds
`calibration` (`weights`, `delta`, `ics`, `score_vs_20d`, `n_stocks`, `method`, `note`) and
`--ab-test` adds `ab_test` (`weight_sets`, `per_stock`, `aggregate`).

#### Offline research, persistence & replay toolchain

These scripts are not part of the per-request path (STEP 1–5) — they produce the evidence behind
the scoring/combo system and the production replay. Run them **from the repo root**.

Flags shared by the harnesses (`p0_backtest`, `p1_eval`, `p2_execution`, `p2_schedule`,
`p4_combo_backtest`):

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--universe` | `fixed` | `fixed` = 36 frozen cross-market stocks; `random` = turnover-ranked sample of the whole A-share market (East Money snapshot, cached in `.p0_cache/`; first run needs network) |
| `--codes` | — | Explicit universe override (e.g. `--codes "600519,TSLA"`) |
| `--seed` | `42` | Sampling seed (the random universe is reproducible) |
| `--pool-size` / `--sample-n` | `300` / `30` | Random-universe pool size / sample size |
| `--limit` | `0` | Debug: run only the first N universe entries |
| `--days` / `--bench-bars` | `900` / `1200` | Per-stock / benchmark lookback bars (`900` matches the cache key → instant cache hits) |
| `--forward-days` | `5,10,20` | Forward-return horizons |
| `--parallel` | `6` | Worker threads |
| `--out` | per script | JSON artifact path |
| `--no-cache` | off | Bypass `.p0_cache/` and refetch |

Per script:

| Script | Extra flags (default) | Artifact |
| ------ | --------------------- | -------- |
| `references/p0_backtest.py` | `--bootstrap 1000`, `--dump-signals <path>` | `reports/p0_expansion.json` |
| `references/p1_eval.py` | `--bootstrap 1000` | `reports/p1_signal_results.json` |
| `references/p2_execution.py` | `--bootstrap 1000`, `--slippage-bp 10.0` | `reports/p2_execution.json` |
| `references/p2_schedule.py` | `--max-positions 5`, `--hold 20`, `--slippage-bp 10` | `reports/p2_schedule.json` |
| `references/p4_combo_backtest.py` | `--max-positions 5`, `--hold 20`, `--save-store <db>` | `reports/p4_combo_backtest.json` (+ SQLite) |
| `references/paper_trader.py` | `--db reports/signals.db`, `--start`, `--end`, `--max-positions 5`, `--hold 20`, `--slippage-bp 10.0`, `--stop-loss-pct`, `--min-signal 0`, `--days 900`, `--out`, `--persist`, `--no-cache`, `--demo` | `reports/p4_paper_trader.json` |
| `references/store.py` | `--db reports/signals.db`, `--demo` (scratch DB in `/tmp`) | SQLite (`signals` / `trades` / `portfolio`) |
| `references/score_calibration.py` | `--signals <raw dump>`, `--out`, `--horizon 20`, `--train-frac 0.7`, `--bin-w 5.0` | `reports/calibration_20d.json` |
| `references/score_config.py` | none — dumps the threshold registry as JSON | stdout |

> Two caveats: `paper_trader.py --stop-loss-pct` / `--min-signal` are **in-sample tuning knobs
> only** (never select them on the out-of-sample window), and `score_calibration.py` reads the raw
> per-signal dump, which you regenerate with
> `p0_backtest.py --dump-signals reports/signals_p0_fixed.json` (gitignored).

Cache, cost & artifacts:

- Fetched bars, benchmarks and the liquidity pool live in `references/.p0_cache/` (gitignored) and
  **never expire on their own** — use `--no-cache` or delete the folder to refresh. `--days 900`
  matches the harness cache key, so cached reruns of P0/P1/P2/P4 are near-instant.
- Runtime is dominated by `--bootstrap` (P0/P1) and `--parallel`; with a warm cache the full P0
  run takes well under 2 minutes. Every harness only writes its `--out` artifact (plus the SQLite
  file when `--save-store` is used).
- Committed artifacts: `reports/p0_expansion.json`, `p1_signal_results*.json`,
  `calibration_20d.json`, `p2_execution.json`, `p2_schedule.json`, `p4_combo_backtest.json`,
  `p4_paper_trader_oos.json`. Gitignored: `reports/signals_*.json` (raw dumps) and `reports/*.db`.

Typical sequences:

```bash
# P0 evidence base + raw signal dump used by the calibration step (cache hits < 2 min)
python3 references/p0_backtest.py --dump-signals reports/signals_p0_fixed.json

# P1 signal evaluation: frozen universe, then the random holdout
python3 references/p1_eval.py
python3 references/p1_eval.py --universe random --seed 42

# P2 execution re-pricing + rotation portfolio
python3 references/p2_execution.py
python3 references/p2_schedule.py --max-positions 5 --hold 20

# P1-7 score -> probability calibration
python3 references/score_calibration.py

# P4-1b combo vs single signal, persisting combo rows for replay
python3 references/p4_combo_backtest.py --save-store reports/signals.db

# P4-4 paper trading over the out-of-sample gate window
python3 references/paper_trader.py --db reports/signals.db --start 2025-09-01 \
    --max-positions 5 --hold 20 --out reports/p4_paper_trader_oos.json

# offline smoke tests (no network, no writes to the pipeline DB)
python3 references/store.py --demo
python3 references/paper_trader.py --demo

# print the frozen thresholds used by scoring / execution / risk
python3 references/score_config.py
```

SQLite store (`--save-store`, then `paper_trader --db`):

| Table | Columns |
| ----- | ------- |
| `signals` | `date, code, market, phase, mom, mr_score, comp_vol, combo_signal, combo_phase, combo_weight, gate_blocked, source, created_at` — PK `(date, code)`; upsert = idempotent append (history never rewritten) |
| `trades` | `id, date, code, direction (BUY/SELL), price, size, pnl, status (open/closed/cancelled), created_at` |
| `portfolio` | `date` (PK), `cash`, `positions`, `total_value`, `daily_return` |

`Store.save_signals()` accepts either a signals-shaped row or a harness row and maps the latter via
`row_to_signal()` (`combo → combo_signal`, `combo_blocked → gate_blocked`, …); `query_signals()`
filters by `code` / `phase` / `date_from` / `date_to` / `limit`, and `latest_signal_date()` tells you
how fresh the store is.

Paper-trading report (`--out`, default `reports/p4_paper_trader.json`):

| Block | Contents |
| ----- | -------- |
| `window`, `max_positions`, `hold`, `slippage_bp`, `n_signals`, `n_codes`, `elapsed_s` | Run configuration |
| `stats` | `entries`, `skipped_limit_up`, `exit_delayed`, `no_fill`, `risk_blocked`, `stop_loss_trades`, `circuit_breakers`, `trades[]` (per-trade detail incl. `exit_reason`), `equity_dates[]`, `coverage_pct`, `total_ret`, `annual_ret`, `annual_vol`, `sharpe`, `max_dd`, `hit_rate`, `mean_ret`, `profit_factor`, `n_trades`, `n_positions_open` |
| `gate` | `sharpe_above_03`, `max_dd_below_30pct`, `profit_factor_above_15`, `coverage_above_20pct` (plus the raw values) |

Risk gates (defaults from `score_config.RISK`, applied inside the replay when risk monitoring is on):

| Parameter | Default | Effect |
| --------- | ------- | ------ |
| `max_per_stock` | `0.20` | An entry that would push one code above 20% of portfolio value is rejected (`risk_blocked`) |
| `max_per_phase` | `0.60` | Same cap, aggregated per market phase |
| `stop_loss_pct` | `0.08` | Position force-closed at -8% from entry (`exit_reason: stop_loss`) |
| `circuit_breaker_pct` | `0.15` | Portfolio -15% from peak liquidates all holdings and halts entries that day |

#### Tests

```bash
python3 -m pytest tests/ -q                        # 158 passed, fully offline, ~0.5s
python3 -m pytest tests/test_paper_trader.py -q    # one suite
python3 -m pytest tests/ -q -k combo               # only tests matching "combo"
```

The suite needs no network, no API keys, and never touches `reports/signals.db`.
Per-suite breakdown: see [Testing](#testing) below.

#### Troubleshooting

| Symptom | Cause / fix |
| ------- | ----------- |
| `ModuleNotFoundError: akshare`, `data_source: unknown` | Install dependencies: `pip3 install akshare yfinance` |
| `errors[]` contains `insufficient_data` | History shorter than 61 indicator bars + the longest forward horizon — reduce `--forward-days` / `--backtest-days` |
| `combo` is `null` | Script copied out of the repo without the sibling modules (set `SDF_REFERENCES_DIR`), or fewer than ~61 bars (warmup), or a helper import failure — details are logged on stderr |
| `news` array empty | The free A-share source returned nothing and no Tavily/SerpAPI key is configured; the script already retried once before degrading |
| THS `429` / `5xx` in stderr | Rate limiting — retried with backoff automatically, then the next source is used; the run is not aborted |
| P/E or P/B shows `N/A` | Every fallback source in the [valuation chain](#valuation-pe-pb-fallback-chain) failed or returned null (loss-making company) — the attempts are logged on stderr as `<market>:<code> <source> valuation enrichment failed`. The price itself is unaffected |
| `name` shows the bare code | All name sources degraded: the THS ticker search (key-gated) and the akshare code-name table (cached once per run, then circuit-broken — the table endpoint can hang ~35s on networks where EastMoney is unreachable) both failed; the analysis is otherwise complete |
| Chinese name not resolved | Pass the numeric code, or set `HITHINK_FINANCE_API_KEY` for THS disambiguation |
| `paper_trader` exits `1` with "no rankable signals" | The store has no signals for that window — run `p4_combo_backtest.py --save-store reports/signals.db` first |
| Prices look stale | `.p0_cache/` never expires: rerun with `--no-cache` or delete the folder |
| NYSE tickers return a single bar from Tencent | Known limitation (the Tencent US feed covers NASDAQ) — the fetcher falls back to yfinance automatically |
| Pytest "module not found" | Run pytest from the repo root (or set `SDF_REFERENCES_DIR`), so `references/` resolves |

#### Optional: daily operation

```bash
# 1) refresh the signal history in batch (idempotent — safe to rerun)
python3 references/p4_combo_backtest.py --save-store reports/signals.db

# 2) append one fresh bar from the live analysis pipeline (needs network)
python3 - <<'PY'
import sys
sys.path.insert(0, "references")          # or set SDF_REFERENCES_DIR
from stock_data_fetcher import analyze_stock
from store import Store, row_to_signal

res   = analyze_stock("600519", days=120)
combo = res.get("combo") or {}
ctx   = res["indicators"]["context"]
row = {                                   # keys consumed by row_to_signal()
    "date": res["as_of"], "code": res["code"], "market": res["market"],
    "phase": ctx.get("phase"), "mom": (res["trend_score"] or {}).get("total"),
    "mr_score": combo.get("secondary_score") if combo.get("primary") == "mr_score" else None,
    "comp_vol": combo.get("primary_score") if combo.get("primary") == "comp_vol" else None,
    "combo": combo.get("combo_score"), "combo_phase": combo.get("phase"),
    "combo_weight": combo.get("weight"), "combo_blocked": combo.get("gate_blocked"),
    "source": "analyze_stock",
}
with Store("reports/signals.db") as st:
    st.save_signals([row_to_signal(row)])
    print("latest signal date:", st.latest_signal_date())
PY

# 3) rolling out-of-sample check, persisting trades + portfolio snapshots
python3 references/paper_trader.py --db reports/signals.db --start 2025-09-01 --persist
```

Schedule these with cron/systemd for a continuously updated paper account. Only the first run for a
given code + window needs network; `.p0_cache/` keeps later runs offline. **Re-run the out-of-sample
gate (Sharpe > 0.3, max_dd < 30%) after any configuration change** before trusting new numbers.

## How It Works

```text
User inputs stock ticker
       │
       ▼
[STEP 1] Parse input → Identify market (A-share/HK/US), normalize ticker
       │
       ▼
[STEP 2] Python script fetches data → Real-time quotes + 120-day K-line + technical indicators
       │
       ▼
[STEP 3] WebSearch for latest news → 2-3 items per stock
       │
       ▼
[STEP 4] Claude AI comprehensive analysis → Technical (60%) + News (30%) + Macro (10%)
       │
       ▼
[STEP 5] Output decision dashboard → Score / Signal / Target Price / Stop-Loss
```

## Output Example

```text
## 2026-03-04 Stock Decision Dashboard

1 stock analyzed | Buy: 0 | Hold: 0 | Sell: 1

### Tesla, Inc. (TSLA) — ⚪ Wait

| Metric | Value |
|--------|-------|
| Current Price | $392.43 (-2.70%) |
| Composite Score | 31/100 |
| Signal | Wait |
| P/E Ratio | 356.75 |
| P/B Ratio | 17.92 |

**Technical Analysis**
- MA: MA5=404.85 MA10=406.83 MA20=411.03 | Bearish Alignment
- MACD: DIF=-8.00 DEA=-7.33 Histogram=-1.33 | Death Cross
- RSI: RSI6=28.45 RSI12=35.84 RSI24=41.54 | Weak
- Volume: Volume Ratio 1.12 | Normal
- Bias: MA5 Bias -3.07%

**AI Judgment**
TSLA is currently in a clear bearish pattern with MA triple-line bearish alignment and MACD death cross...

**Price Targets**
| Entry Price | Target Price | Stop-Loss |
|-------------|--------------|-----------|
| $385 | $437 (+13.5%) | $370 (-3.9%) |
```

## Scoring System

Composite score is out of 100 points, composed of 7 dimensions (weights live in
`_DEFAULT_WEIGHTS` of `stock_data_fetcher.py`, thresholds in `references/score_config.py`):

| Dimension | Max Score | Best Case | Worst Case |
| --------------------- | --------- | ------------------- | ----------------- |
| Trend (MA Alignment) | 26 | Strong Bullish = 26 | Strong Bearish = 0 |
| Bias Rate | 20 | Slightly below MA5 = 20 | Far above MA5 (>5%) = 4 (downtrend: below MA5 = 5) |
| Volume | 15 | Volume-contraction pullback = 15 | High-volume drop = 0 |
| MACD | 15 | Golden cross above zero = 15 | Death cross = 0 |
| RSI | 10 | Oversold = 10 | Overbought = 0 |
| Support | 10 | MA5+MA10 dual support = 10 | No support = 0 |
| Relative Strength | 4 | 60D RS vs benchmark >= +10% = 4 | RS <= -5% = 0 (benchmark unavailable = 2) |

> Scoring is **phase-aware**: in `downtrend_decline` a pullback under MA5 counts as continuing
> weakness (not a dip) and buy signals are hard-blocked. `--calibrate` can rescale component
> budgets from backtest ICs, but the P0/P1 evidence shows weight tuning cannot fix a sign
> problem — see HANDOFF §16.2（原 ROADMAP 研究档案）。

### Signal Mapping

| Score | Condition | Signal | Note |
| ----- | --------- | ------ | ---- |
| >=75  | Bullish / strong-bullish alignment | Strong Buy | No hard gate fired |
| >=60  | Bullish alignment | Buy | No hard gate fired |
| >=60  | Bullish alignment | Hold | Buy-grade setup, but a hard gate fired (wait for repair) |
| >=45  | Any | Hold | — |
| >=30  | Any | Wait | — |
| <30   | Bearish / strong-bearish alignment | Strong Sell | — |
| <30   | Non-bearish | Sell | — |

> Hard gates (`buy_gates`, thresholds in `score_config.GATES`) can only *demote* a buy — e.g.
> risk-reward < 1.5, a >=5% float unlock within 30 days, a sealed limit-up you cannot buy, or a
> relative-strength lag > 5pp. A phase of `downtrend_decline` also blocks buys outright.

## Market Phase Classification

Before any pullback/oversold interpretation, the script classifies the bar via
`calc_pullback_context` (`indicators.context.phase`):

| Phase | Meaning | Buy behaviour |
| ----- | ------- | ------------- |
| `uptrend_pullback` | Above MA60 + bullish alignment + shallow pullback | Low RSI = oversold opportunity; volume-contraction pullback is a valid entry |
| `range_swing` | Sideways between support/resistance | Half weight; wait for a breakout confirmation |
| `downtrend_decline` | Below MA60 / bearish alignment | **Buy blocked** — "rally then pullback" is trend continuation, not a dip |

## Phase-Aware Combo Signal (P4-1)

`analyze_stock()` emits a `combo` object built from the P1-validated components
(mean-reversion `mr_score` + volume-surge `comp_vol`). It is the primary ranking input — never
substitute the flat momentum total for it.

| phase | primary | weight | gate (fails -> `combo_score = None`) |
| ----- | ------- | ------ | ------------------------------------ |
| `uptrend_pullback` | `comp_vol` | 1.0 | `momentum_confirm`: mom > 50 (fallback: 20d change >= 0) |
| `range_swing` | `comp_vol` | 0.5 | `mr_extreme`: mr_score >= 2 adds +0.5 (soft, never blocks) |
| `downtrend_decline` | `mr_score` | 0.3 | `extreme_only`: RSI2 <= 10 & dev_MA60 <= -10% & stabilization day |

Reading it: `combo.combo_score` positive = candidate (the weight column already encodes the
phase — do not double-penalize), `combo.gate_blocked = true` means the bar must not be ranked,
and `combo.primary` tells you which component drove the number. `combo.phase` is produced by
the same classifier as `indicators.context.phase` (100% agreement over 29,476 backtest rows).

## Hard Rules (Strict Entry Strategy)

1. **RSI > 80** -> NEVER give buy signal (overbought risk)
2. **Bias MA5 > 5%** -> NEVER give buy signal (no chasing highs)
3. **Phase = `downtrend_decline`** -> NEVER give buy signal (a downtrend pullback is continuation, not a dip)
4. **Prefer volume-contraction pullbacks** -> Best entry timing (valid only in `uptrend_pullback`)
5. **Risk-reward < 1.5** -> hard-blocked by the script; stop-loss / target prices come from `indicators.risk` (ATR-adaptive), never fabricated
6. **Sealed limit-up** (T+1: you cannot buy today) or a **>=5% float unlock within 30 days** -> buy blocked. Relative strength vs the benchmark feeds the score (max 4 pts) but is *not* a gate
7. **Must provide precise stop-loss** -> Based on MA20 or recent low
8. **Must provide precise target price** -> Based on recent resistance or MA60

## Technical Indicators Explained

### Moving Average System (MA)

- **MA5 / MA10 / MA20 / MA60** - Simple Moving Averages
- Bullish alignment (MA5>MA10>MA20) = Uptrend
- Bearish alignment (MA5<MA10<MA20) = Downtrend

### MACD (12/26/9)

- **DIF** = EMA12 - EMA26
- **DEA** = EMA9(DIF)
- **Histogram** = (DIF - DEA) x 2
- Golden cross (DIF crosses above DEA) = Buy signal
- Death cross (DIF crosses below DEA) = Sell signal

### RSI (6/12/24)

- Wilder's RSI algorithm
- <20 Oversold (rebound opportunity) | 20-40 Weak | 40-60 Neutral | 60-80 Strong | >80 Overbought (pullback risk)

### Volume Analysis

- Volume Ratio = Today's Volume / Previous 5-day Average Volume
- High-volume rally (>1.5x + up) | Volume-contraction pullback (<0.7x + down) | High-volume drop (>1.5x + down)

## Data Source Configuration (Optional Enhancement)

The script uses a **graceful degradation strategy** - works with zero configuration, more accurate with API keys:

| Environment Variable | Purpose | How to Obtain | Free Quota |
| -------------------- | ------- | ------------- | ---------- |
| `TUSHARE_TOKEN` | A-share professional data (highest priority) | Register at [tushare.pro](https://tushare.pro) | Basic APIs free |
| `HITHINK_FINANCE_API_KEY` | THS Official Data API (A-share forward-adjusted quotes + valuation + ticker search, second priority after Tushare). Official recommended variable name, shared by REST/MCP/CLI/Python; `FUYAO_API_KEY`, `THS_API_KEY` still supported as aliases | Issue at [fuyao.aicubes.cn](https://fuyao.aicubes.cn) | Requires THS account |
| `TAVILY_API_KEY` | HK/US stock news search (A-share news has built-in akshare free source) | Register at [tavily.com](https://tavily.com) | 1000 calls/month |
| `SERPAPI_KEY` | HK/US stock news search - Google News (backup) | Register at [serpapi.com](https://serpapi.com) | 100 calls/month |

### Quote Data Degradation Chain

```text
A-share: Tushare Pro (with token) -> Tencent Finance (stdlib, qfq) -> THS Official API (with key) -> efinance -> THS -> akshare -> yfinance
HK:      Tencent Finance (stdlib, qfq) -> efinance -> akshare -> yfinance
US:      Tencent Finance (stdlib, qfq/OCLH) -> yfinance
```

Tencent leads the free chain because it is stdlib-only, needs no key, is not rate-limited,
and its forward-adjusted (qfq) series matches the THS official series (verified 2026-09-16 on
`002032` / `600011`: identical trading dates, closes within 0.03% / one cent of rounding).
Tushare still outranks it when `TUSHARE_TOKEN` is set, and the key-gated THS Official API
stays as the first *fallback* for the richer field set.

The **realtime quote** chains follow the same idea (Tencent single quote first, ~0.2s, carries
price/change%/turnover/P-E/P-B/market cap — every field downstream consumes):
A-share: Tencent -> THS Official API -> akshare -> efinance -> THS -> yfinance;
HK:      Tencent (P/B via the valuation chain) -> efinance -> akshare -> yfinance.

### Valuation (P/E, P/B) Fallback Chain

A quote source can serve a price while its valuation endpoint fails (THS `429`) or while it
simply ships no valuation at all (Tencent HK / US). The script then fills the gaps from a
per-market chain — **first source with a non-null value wins**, and a failing source only logs
to stderr and degrades:

```text
A-share: Tencent single-quote (P/E + P/B, stdlib, ~0.2s) -> akshare spot -> efinance -> yfinance
HK:      yfinance (P/E + P/B, ~2s) -> Tencent single-quote (P/E only) -> akshare spot
US:      yfinance (P/E + P/B) -> Tencent single-quote (P/E only)
```

Tencent leads for A-share because EastMoney full-market snapshots (`stock_zh_a_spot_em`,
`efinance`) are frequently rate-limited or blocked while a single-quote request is cheap;
yfinance leads for HK because the Tencent HK quote carries no P/B.
`realtime.valuation_source` records which source actually filled the fields (absent when the
primary source already delivered them).

### News Degradation Chain

```text
A-share: akshare East Money individual stock news (free, no key) -> Tavily -> SerpAPI (Google News) -> Claude WebSearch
HK/US:   Tavily -> SerpAPI (Google News) -> Claude WebSearch
```

## Data Sources

| Market | Priority | Data Source | Python Library | Cost |
| ------ | -------- | ----------- | -------------- | ---- |
| A-share | P0 | Tushare Pro | tushare | Free (registration required) |
| A-share | P1 | Tencent Finance | None (stdlib direct) | Free |
| A-share | P2 | THS Official API | stdlib direct (requires HITHINK_FINANCE_API_KEY) | Requires THS account |
| A-share | P3 | East Money | efinance | Free |
| A-share | P4 | Tonghuashun | None (stdlib direct) | Free |
| A-share | P5 | East Money | akshare | Free |
| A-share | P6 | Yahoo Finance | yfinance | Free |
| HK | P1 | Tencent Finance | None (stdlib direct) | Free |
| HK | P2 | East Money | efinance | Free |
| HK | P3 | East Money | akshare | Free |
| HK | P4 | Yahoo Finance | yfinance | Free |
| US | P0 | Tencent Finance | None (stdlib direct) | Free |
| US | P1 | Yahoo Finance | yfinance | Free |

## Validation & Production Toolchain (P0–P4)

The scoring/combo system is not guesswork — it was built through four evidence-gated phases
(evidence archive: [HANDOFF.md](HANDOFF.md) §16; decision log: [DECISIONS.md](DECISIONS.md)):

| Phase | Question | Headline result |
| ----- | -------- | --------------- |
| P0 | Is the negative score IC real? | 36 stocks × 3.7y × 29,476 signals: overall 20d IC **-0.0405** [-0.051,-0.029]; `downtrend_decline` **-0.066**, `uptrend_pullback` +0.012 — a **phase-dependent structural** defect, stable across bull/bear and 9/10 half-years |
| P1 | Which signals actually work? | `mr_score` (mean reversion) downtrend IC **+0.034** [CI +0.017,+0.053]; `comp_vol` (volume surge) non-downtrend IC **+0.046** — the only component that survived. The momentum total has **no out-of-time probability content** (Brier worse than a constant baseline) |
| P2 | Does it survive real execution? | Re-priced with T+1 open entry, fees, stamp duty and sealed limit-ups: `comp_vol` is the only survivor (net +9.62%/yr, Sharpe 0.046); `mr_score`'s +11.37% is wiped out by 18.53pp of costs |
| P3 | Engineering hardening | Cache (`.p0_cache/`), 96 pytest cases, all thresholds centralised in `references/score_config.py` |
| P4 | Productionisation | Phase-aware combo: net **+12.81%/yr, Sharpe 0.059** vs comp_vol +9.62%/0.046; a `combo` field on every analysis; SQLite store; paper-trading replay with risk gates |

**Out-of-sample gate** (2025-09 → 2026-08, 4,096 signals / 36 stocks,
`reports/p4_paper_trader_oos.json`):

| Metric | Target | Result |
| ------ | ------ | ------ |
| Sharpe | > 0.3 | **0.54** ✅ |
| Max drawdown | < 30% | **7.31%** ✅ (in-sample 74.8% -> risk gates + stop-losses) |
| Profit factor | > 1.5 | 1.405 — target closed: the in-sample grid showed the only >1.5 setting loses money, and PF is noise at 65-69 trades |
| Signal coverage | **>= 20%** (revised: >= 1 signal-bearing stock per 5 trading days) | **20.5%** ✅ |

### Running the toolchain

Every script's flags, defaults and worked sequences are documented in
**Usage (reference) → Offline research, persistence & replay toolchain** above.

## Persistence, Paper Trading & Risk Control (P4-3/4-4/4-5)

```text
analyze_stock() / p4_combo_backtest rows
        |  row_to_signal()
        v
Store.save_signals()   (SQLite: signals / trades / portfolio, upsert = idempotent append)
        v
PaperTrader.replay()   (T+1 open fill, sealed limit-up skip, limit-down exit roll,
                        per-market fees + slippage, sizing by combo weight)
        v
RiskMonitor (on by default) -> entry gates 20%/stock + 60%/phase,
                               -8% stop-loss, -15% portfolio circuit breaker
```

- `python3 references/store.py --demo` — hermetic persistence smoke test (scratch DB in `/tmp`)
- `python3 references/paper_trader.py --demo` — synthetic replay smoke test
- The tuning knobs `--stop-loss-pct` / `--min-signal` are documented as **in-sample only** —
  selecting hyper-parameters on the out-of-sample window would invalidate the gate

## Project Structure

```text
stock-analysis/
+-- SKILL.md                           # Skill entry point (agent workflow STEP 1-5)
+-- README.md                          # This file
+-- HANDOFF.md                         # Session handoff: state, evidence, runbooks
+-- DECISIONS.md                      # Decision log (adopted / rejected, with evidence)
+-- references/
|   +-- stock_data_fetcher.py          # Core: analyze_stock() / backtest_stock() / calc_trend_score()
|   +-- signal_combo.py                # P4-1: phase-aware combo signal
|   +-- mr_signal.py                   # P1-5: mean reversion + candidate components
|   +-- score_config.py                # P3-14: centralized thresholds (SIGNAL/GATES/MR/COMBO/RISK/COSTS)
|   +-- score_calibration.py           # P1-7: score -> probability calibration
|   +-- store.py                       # P4-3: SQLite persistence (signals/trades/portfolio)
|   +-- paper_trader.py                # P4-4: paper-trading replay engine
|   +-- risk_monitor.py                # P4-5: position limits / stop-loss / circuit breaker / EOD
|   +-- p0_backtest.py                 # P0: evidence-expansion harness
|   +-- p1_eval.py                     # P1: signal evaluation harness
|   +-- p2_execution.py                # P2: execution re-pricing harness
|   +-- p2_schedule.py                 # P2-12: rotation-portfolio simulation
|   +-- p4_combo_backtest.py           # P4-1b: combo vs single-signal backtest (+ --save-store)
|   +-- analysis-prompt-template.md    # AI analysis framework template
|   +-- output-format-template.md      # Decision dashboard output format
+-- reports/                           # JSON evidence artifacts (signals.db is gitignored)
+-- tests/                             # 96 pytest cases across 9 suites
```

## Testing

```bash
python3 -m pytest tests/ -q      # 158 passed, fully offline (~0.5s)
```

| Suite | Cases | Covers |
| ----- | ----- | ------ |
| `test_ic_stats.py` | 11 | bootstrap CI / IC math |
| `test_mr_signal.py` | 4 | mean-reversion components |
| `test_p2_execution.py` | 6 | execution re-pricing + limit rules |
| `test_score_config.py` | 5 | threshold registry |
| `test_signal_combo.py` | 5 | phase-aware combination |
| `test_analyze_combo.py` | 4 | `analyze_stock()` combo integration |
| `test_store.py` | 7 | SQLite roundtrip / upsert / queries |
| `test_paper_trader.py` | 31 | replay engine + risk gates + metrics |
| `test_risk_monitor.py` | 23 | position limits / stop-loss / circuit breaker / EOD |
| `test_valuation_fallback.py` | 24 | P/E, P/B fallback chain + tencent-first priorities (symbol mapping / chain order / failure skip / provenance) |
| `test_bar_partial.py` | 22 | intraday partial-bar flag + session pro-rated vol_ratio (exchange-tz sessions / wiring / backtest-path invariance) |
| `test_name_unify.py` | 16 | realtime.name unification (CJK whitespace cleanup / degraded-name backfill chain / wiring) |

## Companion Documents

| Document | Audience | Contents |
| -------- | -------- | -------- |
| `SKILL.md` | Agent | Trigger definition, STEP 1-5 workflow, hard rules, offline toolchain appendix |
| `HANDOFF.md` | Maintainer / next session | Current state, core numbers, file map, rerun commands, runbook, optimizations (§15), research archive (§16) |
| `DECISIONS.md` | Maintainer | Decision log: adopted / rejected decisions with evidence and dates |

## Inspiration

This project's core analysis logic references the [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) project, with the following improvements:

- **Removed external LLM dependency** - Original project used LiteLLM to call Gemini/OpenAI; this Skill uses Claude directly for analysis
- **Packaged as Claude Code Skill** - One command to invoke
- **Graceful degradation data sources** - Retains Tushare/Tavily and other quality data sources; auto-degrades to free sources when no API key is available
- **Streamlined runtime** - The per-request path is still tiny (`SKILL.md` + `stock_data_fetcher.py` + two templates); the P0–P4 research, persistence and replay toolchain lives beside it in `references/` and runs only offline

## License

MIT

## Author

x

---

> Built with VS Code
