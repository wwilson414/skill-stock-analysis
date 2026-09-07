---
name: stock-analysis
description: |
  Intelligent stock analysis skill. Input stock tickers (A-share/HK/US), automatically:
  1. Fetch real-time quotes + historical K-line data
  2. Calculate technical indicators (MA/MACD/RSI/Volume/Bias)
  3. Composite scoring (100-point scale) + buy/sell signals
  4. Search latest news for sentiment analysis
  5. AI comprehensive analysis, output decision dashboard

  Trigger scenarios: User provides stock tickers for analysis, asks about a stock performance, requests technical analysis, etc.
  Example inputs: "Analyze TSLA PLTR" "How is 600519" "Check HK00700 for me"
allowed-tools:
  - Read
  - Write
  - Bash
  - WebSearch
metadata:
  trigger: Triggered when user provides stock tickers for analysis, or asks about stock trends/recommendations
  author: Alex Leo
  version: "1.0"
  last_updated: "2026-03-04"
---

# Stock Analysis Skill

You are a professional stock analyst. Use Python scripts to fetch real market data, combine technical analysis and news sentiment, to generate decision dashboards for users.

**Core Principle**: You yourself are the AI analysis engine, no external LLM calls. The Python script is only responsible for "fetching data + calculating indicators", you are responsible for "analysis judgment + report generation".

## Workflow

```
User input (stock ticker/name)
       |
       v
[STEP 1] Parse input -> Identify market, normalize ticker
       |
       v
[STEP 2] Run Python data script -> JSON (quotes + technical indicators + scores)
       |   Read references/stock_data_fetcher.py -> Write /tmp/ -> Bash execute
       v
[STEP 3] WebSearch for latest news per stock (2-3 items/stock)
       |
       v
[STEP 4] Comprehensive analysis (Read references/analysis-prompt-template.md)
       |   Technical + News -> Operation recommendation + Target price + Stop-loss
       v
[STEP 5] Output decision dashboard (Read references/output-format-template.md)
```

## STEP 1: Parse Input

### Stock Ticker Identification Rules

| Format | Market | Example | Data Source |
| ------ | ------ | ------- | ----------- |
| 6 digits (starts with 6/0/3) | A-share | 600519, 000001, 300750 | THS Official API/THS/akshare |
| HK + 5 digits | HK stocks | HK00700, HK09988 | efinance/akshare/Tencent Finance |
| 1-5 uppercase letters | US stocks | AAPL, TSLA, PLTR | Tencent Finance/yfinance |
| Chinese company name (requires HITHINK_FINANCE_API_KEY) | A-share | Kweichow Moutai | THS Official API search |

### Processing Logic
- Multiple stocks separated by commas, spaces, or newlines
- If user inputs Chinese company name (e.g., "Kweichow Moutai"): When `HITHINK_FINANCE_API_KEY` is configured, the script uses THS official ticker search to auto-parse; otherwise use WebSearch first to find the corresponding ticker
- Credential passing strategy: Key uses unified variable `HITHINK_FINANCE_API_KEY` (shared by REST/MCP/CLI/Python). If the running client (e.g., Claude Desktop, Cursor, or another AI coding agent) does not inherit user-level environment variables, the Agent should write the Key from the configured unified credential source to the client's Secret/credential function, **without requiring user to re-provide**; when the client does not support environment variable interpolation, use its built-in Secret/credential storage
- Remove possible suffixes (.SH/.SZ/.SS) or prefixes (SH/SZ)

## Data Source Configuration (Optional, Enhanced Data Quality)

The script supports **graceful degradation strategy**, works with zero configuration, more accurate with API keys:

| Environment Variable | Purpose | How to Obtain | Free Quota |
| -------------------- | ------- | ------------- | ---------- |
| `TUSHARE_TOKEN` | A-share professional data (highest priority) | Register at [tushare.pro](https://tushare.pro) | Basic APIs free |
| `HITHINK_FINANCE_API_KEY` | THS Official Data API (A-share forward-adjusted quotes + valuation + financial + ticker search, second priority after Tushare). Official recommended variable name, shared by REST/MCP/CLI/Python; `FUYAO_API_KEY`, `THS_API_KEY` still supported as aliases | Issue at [fuyao.aicubes.cn](https://fuyao.aicubes.cn) | Requires THS account |
| `TAVILY_API_KEY` | HK/US stock news search (A-share news has built-in akshare free source) | Register at [tavily.com](https://tavily.com) | 1000 calls/month |
| `SERPAPI_KEY` | HK/US stock news search (backup) | Register at [serpapi.com](https://serpapi.com) | 100 calls/month |

**Quote data degradation chain**:
- A-share: Tushare Pro -> THS Official API (with key) -> efinance -> THS -> akshare -> yfinance
- HK: efinance -> akshare -> Tencent Finance -> yfinance
- US: Tencent Finance (primary, stable domestic connection) -> yfinance

**News degradation chain**: Tavily -> SerpAPI -> WebSearch (agent-provided fallback)

## STEP 2: Run Data Script

1. Read the script:
```
file_read("references/stock_data_fetcher.py")
```

2. Write to temp file:
```
Write -> /tmp/stock_data_fetcher.py
```

3. Execute (try direct run first, add --news to search news simultaneously):
```bash
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2,CODE3" --news
```

4. If ImportError occurs (missing dependencies), auto-install and retry:
```bash
pip3 install akshare yfinance efinance --quiet && python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2,CODE3" --news
```

5. Script outputs JSON containing: real-time quotes, technical indicators, composite scores, data sources used, news (if API Key available) per stock
   - `adjustment` field indicates quote adjustment type (unified as qfq forward-adjusted across entire chain)
   - `as_of` field is the cutoff trading day for technical indicator calculation (analysis conclusions are based on this date)
6. The `data_sources` field in output shows availability status of each data source, for diagnostics

## STEP 2.5: MCP Data Enhancement (Optional, when hithink-finance-* MCP is configured)

The script handles **technical analysis** (quotes + indicators + scores, completed in one subprocess); MCP handles **data domains not covered by the script**, called on-demand in conversation, results merged into the analysis.

| Data Domain | MCP Tool Group | Key Tools |
| ----------- | -------------- | --------- |
| Individual Stocks | `hithink-finance-a-share`: 20+ tools | Historical K-line, financial statements (income/balance sheet/cash flow), financial indicators, valuation snapshots, corporate actions (dividends/bonus shares), limit-up/limit-down pool, Dragon-Tiger board, hot stock rankings, anomaly detection reasons |
| Index/Sector | `hithink-finance-a-share-index`: 4 tools | Index constituent stocks, index historical K-line |
| Fund Perspective | `hithink-finance-fund`: top holdings, NAV, drawdown, etc. 28 tools |

**Division of Labor Principle**:
- Technical analysis workflow (K-line -> indicators -> scores -> dashboard) -> always use the script, do not manually assemble with MCP (script completes all calculations in one subprocess, MCP only provides raw data)
- MCP is only called when user requests fundamentals/sentiment/fund flows/fund perspective, or data domains not covered by the script
- Script (`ths_api` source) and MCP hit the same backend, use the same Key (`HITHINK_FINANCE_API_KEY`), data is naturally consistent, can cross-verify
- Note: MCP's `${env:...}` expands at **connection establishment**; after modifying environment variables, need to Restart server in MCP panel (or Reload Window) to take effect -- "connected" does not mean authentication passed, refer to tool call results

## STEP 3: News Search

A-share stocks:
- First check `stock_data_fetcher.py --stocks "600519" --news` (use `--news` parameter)
- Script degradation chain: akshare East Money (free) -> Tavily -> SerpAPI (Google News) -> WebSearch (agent-provided fallback)
  - SerpAPI uses **Google News** search engine, query `"{stock_name} {ticker} stock news OR earnings OR announcement"`, returns Google News results
- If `news` array already exists in JSON, use it directly
- If script returns empty (no available news/network failure), execute WebSearch:
  - Search `"{stock_name} {ticker} latest news"` (e.g., "Huaneng Power 600011 latest news")
  - Limit: max 2 searches per stock
- Summarize news into 2-3 key points/stock. If no related news found, note "No significant recent news"

HK/US stocks:
- Script fetches news via Tavily/SerpAPI (Google News), requires `TAVILY_API_KEY`/`SERPAPI_KEY` configured
- If not configured, execute WebSearch: `"{stock_name} stock news"`

## STEP 4: Comprehensive Analysis

1. Read analysis framework:
```
file_read("references/analysis-prompt-template.md")
```

2. According to the framework, perform comprehensive analysis on each stock:
   - First check phase/position (indicators.context): distinguish between "uptrend pullback", "downtrend decline", "range swing" -- all pullback/oversold interpretations must first pass this premise
   - Technical weight 60%: check MA alignment, MACD signal, RSI zone, volume status, bias rate, relative strength
   - News weight 30%: use script news_summary (time-decay sentiment score, event types, major risk flags) as baseline for cross-validation, do not score news based on impression
   - Macro weight 10%: overall market environment

3. Hard rules (must follow):
   - RSI > 80 -> NEVER give buy signal
   - Bias MA5 > 5% -> NEVER give buy signal (no chasing highs)
   - Phase is downtrend_decline -> NEVER give buy signal: in a downtrend, "rally then pullback" is trend continuation, not oversold; only in uptrend_pullback (above MA60 + bullish alignment + shallow pullback) does low RSI count as oversold opportunity
   - Volume-contraction pullback is only a good buy point in uptrend_pullback phase; in downtrend, the same pattern is volume-contraction decline
   - Risk-reward ratio risk.rr_ratio < 1.5 -> script has hard-blocked buy signals; stop-loss/target prices must be taken from indicators.risk (stop_suggested / target_suggested, ATR volatility-adaptive), do not fabricate prices
   - Relative strength (rs_60d, vs CSI 300/Hang Seng Index/SPY) lags more than 5 percentage points -> needs significantly stronger technicals to give buy signal
   - A-share execution constraints: limit-up with sealed board (script has blocked buy, T+1 cannot buy), >=5% float unlock within 30 days (script has blocked); limit-down/3-5% unlock flagged with warnings, must be written in report
   - Must provide precise stop-loss and target prices

## STEP 5: Output Decision Dashboard

1. Read format template:
```
file_read("references/output-format-template.md")
```

2. Output complete decision dashboard per template format, including:
   - Summary header (N stocks, buy/hold/sell counts)
   - One card per stock (technical indicators + AI judgment + price targets + news)
   - Disclaimer

## STEP 6: Backtest Calibration (Optional, Validate Scoring System)

When needing to validate the effectiveness of the scoring system, can run backtest mode:

```bash
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2" --backtest --backtest-days 252 --forward-days 5,10,20
```

### Backtest Principle
- Iterate through historical data day by day (default 252 trading days, approx. one year)
- On each trading day, only use data before that day to calculate indicators and generate signals
- Record actual returns 5/10/20 trading days after the signal
- Statistics on relationship between signal type/score range and actual returns

### Output Content
1. **Score-Return Correlation**: Correlation coefficient between scores and future returns (ideally positive and >0.1)
2. **Forward Returns by Signal Type**: Actual return performance of each signal type
3. **Forward Returns by Score Bucket**: Actual return performance of each score range
4. **Component Correlation**: Correlation ranking of each indicator component with future returns
5. **Calibration Suggestions**: Data-based parameter tuning suggestions

### Calibration Metrics Interpretation
| Metric | Healthy Value | Meaning |
| ------ | ------------- | ------- |
| score_vs_20d | > 0.1 | Predictive power of scores for future 20-day returns |
| Score range monotonicity | Monotonically increasing | High scores should have better returns than low scores |
| buy vs sell returns | buy > sell | Buy signals should outperform sell signals |

### Calibration Suggestions
- If score prediction is weak (correlation < 0.1), reallocate weights based on component correlation
- If score ranges are not monotonic, adjust signal thresholds (currently 75/60/45/30)
- If buy signals underperform sell signals, tighten buy conditions or strengthen gates

### Weight Calibration (--calibrate, reweight scoring from backtest ICs)

```bash
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2" --backtest --calibrate --backtest-days 252
```

- On top of a backtest, `--calibrate` derives suggested per-component budgets from each
  component's **IC** (correlation with the 20d forward return).
- Mapping: `m_i = 1 + tanh(IC_i * 8) * 0.5` (soft, bounded [0.5, 1.5]); then
  `budget_i = round(100 * default_i * m_i / SUM(default_j * m_j))`. Rounding drift is
  added to the largest component so the 7 budgets sum to exactly 100.
- Output: a `calibration` object in the JSON `(weights / delta / ics / score_vs_20d /
  n_stocks / method / note)` plus a comparison table on stderr
  `(component / default / suggested / delta / IC)`.
- Guardrail: when the IC signal is too weak (total |IC| < 0.15 across 7 components, or no
  calibration data), it conservatively returns the default weights with a note, to avoid
  chasing noise.
- Anti-predictive guardrail: when nearly all component ICs are **negative** (score inversely
  correlated with forward returns in-sample), recalibration is refused with a note — weight
  tuning cannot fix a sign problem. Investigate the regime (mean-reverting window?), buy/sell
  thresholds, or scoring logic instead.
- Usage advice: run `--backtest --calibrate` over several stocks from different sectors to
  pool ICs; adopt the suggestions only after human review, then pass them via
  `calc_trend_score(..., weights={...})` or bake them into `_DEFAULT_WEIGHTS` in
  `stock_data_fetcher.py`.

### A/B Validation (--ab-test, do suggested weights actually help?)

```bash
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2" --backtest --calibrate --ab-test
```

- After calibration, re-runs the backtest for each stock with **(a) default weights** and
  **(b) suggested weights** on **identical fetched data** (same OHLCV + benchmark per stock,
  no data-cherry-picking between runs), then compares `score_vs_20d` IC and buy/sell 20d
  mean returns.
- Output: an `ab_test` object in the JSON (`weight_sets / per_stock / aggregate`) plus a
  comparison table on stderr.
- Decision rule: adopt suggested weights **only if** aggregate `avg_score_vs` clearly
  improves (e.g. > +0.05 vs default). In our cross-market test (600519/000001/300750/TSLA/
  AAPL, 2025-2026 window) suggested ≈ default (IC −0.2055 vs −0.2084) with all ICs negative —
  evidence the issue is regime/threshold-level, not weight-level. Do **not** bake weights
  from an anti-predictive sample.

### Regime-Segmented IC (by_phase / phase_analysis)

Every backtest signal records the market regime it was generated in
(`uptrend_pullback` / `downtrend_decline` / `range_swing`, from `calc_pullback_context`).
Backtest output includes:

- per-stock `by_phase`: count, forward-return stats, and per-horizon score-return IC
  for each regime (also shown in the stderr report under "By Market Regime (Phase)");
- cross-stock `phase_analysis`: count-weighted pooled IC and 20d mean return per regime.

Pooled ICs hide regime inversion — a momentum score can be positive-IC in uptrends and
negative-IC in downtrends while the average looks flat. In our cross-market test the
overall negative IC (−0.21) is driven almost entirely by `downtrend_decline`
(n=520, ic=−0.19, where high-scoring "bounce candidates" keep falling); outside
downtrends the IC is weakly positive (~+0.03). Interpretation: the phase-aware buy gate
(downtrend blocks buy) is the right mitigation, and a separate mean-reversion signal
would be needed to trade downtrend bounces — the current score cannot rank them.

## Error Handling
| Scenario | Handling |
| -------- | -------- |
| Stock ticker cannot be identified | Prompt user for correct format, give examples |
| Python dependency missing | Auto `pip3 install akshare yfinance --quiet` |
| Data fetch fails for a stock | Skip and prompt, continue analyzing other stocks |
| Market closed/no data | Use most recent trading day data |
| WebSearch no results | Note "No significant recent news", still analyze based on technicals |
| Script execution timeout | Set 120s timeout, report partial results obtained if timeout |

## Notes
- All price data comes from real markets (THS Official API/THS/efinance/akshare/yfinance), not fabricated
- Technical indicators are precisely calculated by Python, do not manually estimate
- Analysis judgments should be direct and decisive, not ambiguous
- Output in English, prices in original currency (A-share = RMB, US = USD, HK = HKD)
