# Requirement Prompt: Enhance an Existing AI-Oriented Python Stock Analysis Tool

## Background

There is an existing Python-based stock analysis program designed for AI-agent execution.

The current system:

- Exposes capabilities through `skill.md`
- Allows an AI agent to read skill documentation and execute Python workflows
- Supports multiple financial data sources
- Supports Mainland China A-share, Hong Kong, and U.S. stock markets
- Fetches real-time quotes and historical K-line data
- Calculates technical indicators and composite scores
- Supports news search and sentiment-assisted analysis
- Provides decision dashboards

The goal is not to rebuild the system from scratch.  
The goal is to enhance it into a complete personal investment decision-support system.

---

## Role

You are a senior AI agent architect, Python developer, quantitative analyst, financial analyst, and product architect.

Please help improve the existing AI-callable Python stock analysis tool, including:

1. Skill design
2. `skill.md` structure
3. Data retrieval reliability
4. Multi-market data normalization
5. Technical analysis workflow
6. Fundamental analysis workflow
7. Valuation analysis workflow
8. Risk analysis workflow
9. Portfolio-aware decision support
10. Investment thesis and review workflow
11. Backtest and calibration workflow
12. Report generation

---

## Core Objective

Enhance the existing tool so that an AI agent can help an individual investor:

1. Analyze stocks across China A-share, Hong Kong, and U.S. markets.
2. Retrieve and normalize real market data from multiple sources.
3. Evaluate technical setup, company quality, valuation, and risks.
4. Generate explainable decision-support states.
5. Support both short-to-medium-term trading analysis and long-term investment analysis.
6. Analyze portfolio exposure and concentration risk.
7. Record and review investment theses.
8. Provide structured, evidence-based reports.
9. Avoid black-box or overconfident buy/sell recommendations.

---

## Design Principles

The enhanced system must follow these principles:

- Build on the existing Python program and `skill.md` mechanism.
- Do not rewrite the system unless necessary.
- Treat `skill.md` as the primary AI-agent interface.
- Use structured inputs and JSON-compatible outputs.
- Use Markdown for human-readable reports.
- Separate data fetching, analysis, decision support, and reporting.
- Keep all scoring and risk rules explainable.
- Support multiple data sources with graceful degradation.
- Support market-specific rules for A-share, HK, and US stocks.
- Do not fabricate data, prices, news, indicators, or conclusions.
- Do not provide guaranteed investment advice.
- Always include assumptions, data source, update time, limitations, and disclaimer.

---

## Supported Markets

### Mainland China A-share

Considerations:

- Shanghai, Shenzhen, Beijing exchanges if available
- Currency: CNY
- Ticker examples: `600519`, `000001`, `300750`
- T+1 trading
- Limit-up / limit-down rules
- Policy and regulatory sensitivity
- Different financial statement formats

### Hong Kong Market

Considerations:

- HKEX stocks
- Currency: HKD
- Ticker examples: `HK00700`, `HK09988`, `0700.HK`
- Different lot sizes
- Mainland exposure
- USD/HKD linked exchange rate environment

### U.S. Market

Considerations:

- NYSE, NASDAQ, AMEX
- Currency: USD
- Ticker examples: `AAPL`, `MSFT`, `TSLA`
- Quarterly reporting
- GAAP / non-GAAP differences
- Stock-based compensation
- Buybacks
- Interest rate sensitivity

---

## Skill Architecture Requirements

The system should expose capabilities through clear AI-readable skills.

Each skill should include:

```markdown
## Skill Name

### Purpose
What this skill does.

### When to Use
Typical user requests.

### When Not to Use
Cases where the skill is inappropriate.

### Inputs
Required and optional inputs.

### Output
Structured output schema.

### Execution
Python command, function, or workflow.

### Examples
Example calls and outputs.

### Limitations
Known caveats and assumptions.
```

---

## Recommended Skill Categories

The current `stock-analysis` skill may remain as the main router, but should support or delegate to the following workflows:

1. `stock-data-retrieval`
2. `stock-technical-analysis`
3. `stock-news-sentiment`
4. `stock-fundamental-analysis`
5. `stock-valuation-analysis`
6. `stock-risk-analysis`
7. `stock-investment-decision`
8. `stock-portfolio-analysis`
9. `stock-investment-thesis`
10. `stock-backtest-calibration`
11. `stock-report-generation`

---

## Intent Routing

Before running analysis, classify user intent.

| Intent | Example Request | Workflow |
|---|---|---|
| Technical analysis | “Analyze TSLA trend” | Technical workflow |
| Swing opportunity | “Can I buy this pullback?” | Phase-aware technical workflow |
| Long-term investment | “Is Tencent suitable for long-term holding?” | Fundamental investment workflow |
| Valuation review | “Is AAPL expensive now?” | Valuation workflow |
| Risk review | “What are the risks?” | Risk workflow |
| Portfolio context | “How does this fit my portfolio?” | Portfolio workflow |
| Backtest | “Validate this signal” | Backtest workflow |

If the horizon is unclear:

- Technical requests: assume 5-20 trading days.
- Investment requests: assume 1-3 years.
- State the assumption clearly.

---

## Decision States

Use decision-support states instead of absolute buy/sell commands.

Allowed states:

```text
STRONG_AVOID
AVOID
WATCHLIST
BUY_CANDIDATE
SMALL_POSITION_ONLY
HOLD
REDUCE
SELL_OR_EXIT
RECHECK_REQUIRED
```

Every decision must include:

- Supporting evidence
- Opposing evidence
- Key risks
- Confidence level
- Time horizon
- Data date
- Re-evaluation triggers
- Disclaimer

---

## Data Source Requirements

Required data categories:

- Company profile
- Real-time quote
- Historical K-line data
- Adjusted price data
- Technical indicators
- Financial statements
- Financial indicators
- Valuation metrics
- Dividend history
- Corporate actions
- Industry classification
- Peer data
- Market index data
- Exchange rate data
- News and announcements
- Portfolio holdings
- Investment notes

Data source layer should support:

- Graceful degradation
- Fallback source selection
- Local caching
- Field normalization
- Data validation
- Source conflict handling
- Update-time tracking
- Missing-data warnings

---

## Data Source Degradation

Quote data fallback:

```text
A-share: Tushare Pro -> Tencent Finance -> THS Official API -> efinance -> THS -> akshare -> yfinance
HK: Tencent Finance -> efinance -> akshare -> yfinance
US: Tencent Finance -> yfinance
```

Valuation fallback:

```text
A-share: Tencent quote -> akshare spot -> efinance -> yfinance
HK: yfinance -> Tencent quote -> akshare spot
US: yfinance -> Tencent quote
```

News fallback:

```text
Tavily -> SerpAPI -> WebSearch
```

---

## Credential Safety

- Use environment variables or secure secret storage.
- Never print, log, persist, or expose API keys.
- Do not ask users to paste keys unless explicitly necessary.
- If keys are unavailable, use free fallback sources.
- Report degraded data quality when fallback sources are used.

Environment variables may include:

```text
TUSHARE_TOKEN
HITHINK_FINANCE_API_KEY
FUYAO_API_KEY
THS_API_KEY
TAVILY_API_KEY
SERPAPI_KEY
```

---

## Data Retrieval Workflow

The AI agent should be able to execute:

```bash
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2,CODE3" --news
```

If dependencies are missing:

```bash
pip3 install akshare yfinance efinance --quiet
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2,CODE3" --news
```

Expected JSON output:

- Ticker
- Market
- Company name
- Currency
- Real-time quote
- Historical data
- Technical indicators
- Composite score
- Phase/context
- Combo signal
- Risk-reward data
- Stop-loss suggestion
- Target suggestion
- Valuation fields
- News
- Data sources
- `as_of`
- `adjustment`
- Warnings

---

## Technical Analysis Requirements

Technical analysis should include:

- MA alignment
- MACD
- RSI
- Volume
- Bias
- Relative strength
- Benchmark comparison
- Trend phase/context
- Risk-reward ratio
- ATR-based stop-loss and target
- Phase-aware combo signal

Technical hard rules:

- RSI > 80 -> never mark as `BUY_CANDIDATE`.
- Bias MA5 > 5% -> never mark as `BUY_CANDIDATE`.
- Downtrend decline -> never mark as `BUY_CANDIDATE`, unless confirmed mean-reversion setup.
- Risk-reward ratio below 1.5 -> block `BUY_CANDIDATE`.
- Stop-loss and target must come from script output.
- Do not fabricate stop-loss or target prices.
- A-share execution constraints must be reported.

---

## Phase-Aware Combo Signal

When available, use `combo` as the primary technical ranking signal.

| Phase | Primary Signal | Weight | Gate |
|---|---|---:|---|
| `uptrend_pullback` | `comp_vol` | 1.0 | momentum confirmation |
| `range_swing` | `comp_vol` | 0.5 | soft MR extreme bonus |
| `downtrend_decline` | `mr_score` | 0.3 | extreme oversold + stabilization |

Rules:

- `combo.combo_score > 0` means potential technical edge.
- `combo.gate_blocked = true` means do not rank as candidate.
- Do not double-penalize phase.
- High momentum alone cannot override downtrend gate.

---

## Fundamental Analysis Requirements

Use when user asks about long-term investment, business quality, valuation, dividend safety, or financial risk.

Analyze:

- Revenue growth
- Net profit growth
- Gross margin
- Net margin
- ROE
- ROIC
- Operating cash flow
- Free cash flow
- Cash conversion ratio
- Debt-to-asset ratio
- Interest-bearing debt
- Liquidity
- Dividend history
- Industry comparison
- Competitive position

Do not classify a stock as a long-term buy candidate based only on technical indicators.

Suggested long-term weights:

```text
Business quality: 35%
Valuation: 25%
Financial safety: 20%
Growth outlook: 10%
Technical timing: 10%
```

---

## Valuation Analysis Requirements

Analyze:

- PE
- PB
- PS
- PCF
- EV/EBITDA if available
- Dividend yield
- Historical valuation percentile
- Peer comparison
- Market comparison
- Growth-adjusted valuation
- Margin of safety

Valuation classification:

```text
LOW
REASONABLE
SLIGHTLY_EXPENSIVE
EXPENSIVE
EXTREME
INSUFFICIENT_DATA
```

For long-term investment analysis, provide valuation range instead of a single precise target price when possible.

---

## Risk Analysis Requirements

Risk categories:

```text
Financial risk
Business risk
Valuation risk
Technical risk
Market risk
Liquidity risk
Policy/regulatory risk
Currency risk
Portfolio concentration risk
```

Risk levels:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

Each risk flag should include:

- Category
- Severity
- Evidence
- Impact
- Monitoring indicator

---

## Investment Decision Workflow

Generate a decision state based on:

- User intent
- Time horizon
- Technical setup
- Company quality
- Valuation
- Risk level
- Portfolio context
- Data reliability

Output must include:

- Decision state
- Confidence
- Supporting reasons
- Opposing reasons
- Key risks
- Suggested action range
- Position-size suggestion if appropriate
- Re-evaluation triggers
- Data quality warning
- Disclaimer

---

## Portfolio Analysis Requirements

If holdings are provided, analyze:

- Total portfolio value
- Single-stock weight
- Industry exposure
- Market exposure
- Currency exposure
- High-risk exposure
- Unrealized gain/loss
- Dividend contribution
- Volatility
- Maximum drawdown if available
- Concentration risk
- Rebalancing suggestions

Suggested rules:

```text
Single stock max weight: 5%-15%
Single industry max weight: 20%-30%
High-risk stock total weight: <=20%
Minimum cash weight: configurable
```

---

## Investment Thesis Workflow

Allow users to create or review investment theses.

Record:

- Ticker
- Market
- Company name
- Buy date
- Buy price
- Position size
- Currency
- Buy reason
- Core thesis
- Expected holding period
- Expected return drivers
- Main risks
- Exit conditions
- Follow-up indicators
- Review schedule
- Review notes

Review status:

```text
THESIS_VALID
THESIS_WEAKENED
THESIS_BROKEN
INSUFFICIENT_DATA
```

---

## Report Requirements

Reports should be concise, structured, and evidence-based.

Single stock report sections:

```text
1. Basic Info
2. Decision State
3. Time Horizon
4. Confidence
5. Price / Valuation Snapshot
6. Technical Summary
7. Fundamental Summary
8. News Summary
9. Supporting Evidence
10. Opposing Evidence
11. Risk Flags
12. Stop-loss / Target or Valuation Range
13. Portfolio Fit
14. Re-evaluation Triggers
15. Data Sources and as_of
16. Disclaimer
```

Portfolio report sections:

```text
1. Portfolio Summary
2. Position Weights
3. Market Exposure
4. Currency Exposure
5. Industry Exposure
6. Risk Concentration
7. Performance Summary
8. Rebalancing Suggestions
9. Key Risks
10. Disclaimer
```

Language:

- Use the same language as the user.
- Keep prices in original currency:
  - A-share: CNY
  - HK: HKD
  - US: USD

---

## Backtest and Calibration Requirements

Support commands:

```bash
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2" --backtest --backtest-days 252 --forward-days 5,10,20
```

```bash
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2" --backtest --calibrate --backtest-days 252
```

```bash
python3 /tmp/stock_data_fetcher.py --stocks "CODE1,CODE2" --backtest --calibrate --ab-test
```

Backtest output should include:

- Score-return correlation
- Forward returns by signal type
- Forward returns by score bucket
- Component IC
- Phase-segmented IC
- Calibration suggestions
- A/B validation result

Rules:

- Do not adopt weights from anti-predictive samples.
- Adopt calibrated weights only when A/B validation clearly improves aggregate IC.
- Segment by market regime when possible.
- Treat tuning knobs as in-sample only.

---

## Error Handling

Structured error format:

```json
{
  "success": false,
  "error": {
    "code": "",
    "message": "",
    "retryable": true
  }
}
```

Common error codes:

```text
INVALID_TICKER
UNSUPPORTED_MARKET
DATA_SOURCE_UNAVAILABLE
DATA_NOT_FOUND
MISSING_REQUIRED_INPUT
INSUFFICIENT_HISTORY
RATE_LIMITED
CALCULATION_ERROR
VALIDATION_FAILED
TIMEOUT
UNKNOWN_ERROR
```

Handling rules:

- Invalid ticker: ask for clarification.
- Missing dependency: install and retry.
- Data failure: continue with other stocks and warn.
- Market closed: use latest trading day.
- News unavailable: state no significant recent news found.
- Fundamental data missing: continue and lower confidence.
- API key unavailable: use free fallback source.

---

## Data Reliability Requirements

Every report must include:

- Data source
- Update time
- `as_of` date
- Adjustment type
- Whether fallback source was used
- Missing data warnings
- Data quality caveats
- Confidence impact

---

## Testing Requirements

Tests should cover:

- Skill input validation
- Output schema consistency
- Error handling
- Ticker normalization
- Market-specific logic
- Data fallback
- Currency handling
- Technical indicator calculations
- Fundamental metric calculations
- Scoring logic
- Risk flags
- Portfolio exposure calculations
- Report generation
- Backtest output consistency

---

## Non-Goals

The system should not:

- Guarantee investment returns.
- Provide black-box buy/sell commands.
- Focus only on next-day prediction.
- Encourage frequent trading.
- Ignore data limitations.
- Hide assumptions.
- Treat all markets identically.
- Fabricate unavailable data.
- Replace professional financial, legal, or tax advice.

---

## Final Expected Outcome

The enhanced tool should allow an AI agent to answer:

1. What stock is the user asking about?
2. Which market does it belong to?
3. What data is available and reliable?
4. What is the technical setup?
5. Is the company financially healthy?
6. Is the valuation reasonable?
7. What are the key risks?
8. What decision state is appropriate?
9. How does it fit into the portfolio?
10. What should be monitored next?
11. When should the thesis be reviewed?

The system should support disciplined, explainable, risk-aware personal investment decisions.

---

## Required Disclaimer

Every user-facing report must include:

```text
This analysis is for personal research and decision support only. It is not financial advice, does not guarantee returns, and should not be treated as a direct instruction to buy or sell. The user remains responsible for all investment decisions.
```
