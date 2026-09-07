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
| **Complete Technical Analysis** | MA / MACD / RSI / Volume / Bias / Support Levels |
| **100-Point Scoring System** | 6-dimension composite scoring, auto-generates buy/sell signals |
| **AI Deep Analysis** | Claude itself as the analysis engine, combining technical + news sentiment |
| **Zero Config Required** | Works out-of-the-box with free data sources; more accurate with API keys configured |
| **Strict Entry Strategy** | No chasing highs (bias >5% = no buy), prefers volume-contraction pullbacks, precise stop-loss |

## Quick Start

### Installation

Clone this project to Ai Coding's skills directory:

```bash
git clone https://github.com/wwilson414/skill-stockAnalysis.git ~/.agent/skills/stock-analysis
```

Python dependencies will be auto-installed on first run:

```bash
pip3 install akshare yfinance
```

### Usage

Enter directly in the Agent:

```text
/stock-analysis TSLA
/stock-analysis TSLA,PLTR,RKLB
/stock-analysis 600519
/stock-analysis HK00700
```

Or use natural language in the Agent:

```text
Analyze TSLA for me
How is 600519?
Check the technicals for PLTR and RKLB
```

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

Composite score is out of 100 points, composed of 6 dimensions:

| Dimension | Max Score | Best Case | Worst Case |
| --------------------- | --------- | ------------------- | ----------------- |
| Trend (MA Alignment) | 30 | Strong Bullish = 30 | Strong Bearish = 0 |
| Bias Rate | 20 | Slightly below MA5 = 20 | Far above MA5 (>5%) = 4 |
| MACD | 15 | Golden cross above zero = 15 | Death cross = 0 |
| Volume | 15 | Volume-contraction pullback = 15 | High-volume drop = 0 |
| RSI | 10 | Oversold = 10 | Overbought = 0 |
| Support | 10 | MA5+MA10 dual support = 10 | No support = 0 |

### Signal Mapping

| Score | Condition             | Signal      |
| ----- | ----------------      | ----------- |
| >=75  | Bullish alignment     | Strong Buy  |
| >=60  | Bullish/Weak bullish  | Buy         |
| >=45  | Any                   | Hold        |
| >=30  | Any                   | Wait        |
| <30   | Bearish alignment     | Strong Sell |
| <30   | Non-bearish           | Sell        |

## Hard Rules (Strict Entry Strategy)

1. **RSI > 80** -> NEVER give buy signal (overbought risk)
2. **Bias MA5 > 5%** -> NEVER give buy signal (no chasing highs)
3. **Prefer volume-contraction pullbacks** -> Best entry timing
4. **Must provide precise stop-loss** -> Based on MA20 or recent low
5. **Must provide precise target price** -> Based on recent resistance or MA60

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
A-share: Tushare Pro -> THS Official API (with key) -> efinance -> THS -> akshare -> yfinance
HK:      efinance -> akshare -> Tencent Finance -> yfinance
US:      Tencent Finance (primary, stable domestic connection) -> yfinance
```

### News Degradation Chain

```text
A-share: akshare East Money individual stock news (free, no key) -> Tavily -> SerpAPI (Google News) -> Claude WebSearch
HK/US:   Tavily -> SerpAPI (Google News) -> Claude WebSearch
```

## Data Sources

| Market | Priority | Data Source | Python Library | Cost |
| ------ | -------- | ----------- | -------------- | ---- |
| A-share | P0 | Tushare Pro | tushare | Free (registration required) |
| A-share | P1 | THS Official API | stdlib direct (requires HITHINK_FINANCE_API_KEY) | Requires THS account |
| A-share | P2 | East Money | efinance | Free |
| A-share | P3 | Tonghuashun | None (stdlib direct) | Free |
| A-share | P4 | East Money | akshare | Free |
| A-share | P5 | Yahoo Finance | yfinance | Free |
| HK | P1 | East Money | efinance | Free |
| HK | P2 | East Money | akshare | Free |
| HK | P3 | Tencent Finance | None (stdlib direct) | Free |
| HK | P4 | Yahoo Finance | yfinance | Free |
| US | P0 | Tencent Finance | None (stdlib direct) | Free |
| US | P1 | Yahoo Finance | yfinance | Free |

## Project Structure

```text
stock-analysis/
+-- SKILL.md                           # Skill definition (Claude Code entry point)
+-- README.md                          # This file
+-- references/
    +-- stock_data_fetcher.py          # Data fetching + technical indicator calculation (~400 lines)
    +-- analysis-prompt-template.md    # AI analysis framework template
    +-- output-format-template.md      # Decision dashboard output format
```

## Inspiration

This project's core analysis logic references the [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) project, with the following improvements:

- **Removed external LLM dependency** - Original project used LiteLLM to call Gemini/OpenAI; this Skill uses Claude directly for analysis
- **Packaged as Claude Code Skill** - One command to invoke
- **Graceful degradation data sources** - Retains Tushare/Tavily and other quality data sources; auto-degrades to free sources when no API key is available
- **Streamlined architecture** - Reduced from 50+ files to 4 core files

## License

MIT

## Author

x

---

> Built with VS Code
