# Decision Dashboard Output Format

## Format Specification

Use exactly this Markdown structure for the output dashboard.

### Header

```
## {DATE} Stock Decision Dashboard

{N} stocks analyzed | Buy: {n} | Hold: {n} | Sell: {n}
```

### Per-Stock Card

For each stock, output one card separated by `---`:

```
### {NAME}({CODE}) - {SIGNAL_EMOJI} {SIGNAL_EN}

| Metric | Value |
|--------|-------|
| Current Price | {price} ({change_pct:+.2f}%) |
| Composite Score | {score}/100 |
| Signal | {signal_en} |
| P/E Ratio | {pe_ratio} |
| P/B Ratio | {pb_ratio} |

**Technical Analysis**
- Phase/Position: {phase_en} | 120-day range position {range_pos_pct}% | Distance from MA60 {dist_ma60_pct:+.2f}% | 20-day change {chg_20d_pct:+.2f}%
- Relative Strength: vs {bench_en} 20D RS {rs_20d:+.2f}% | 60D RS {rs_60d:+.2f}%
- Volatility & Risk Levels: ATR {atr} ({atr_pct}%) | Annualized Vol {ann_vol_pct}% | R:R Ratio {rr_ratio}
- Trading Constraints/Events: {limit_status_en} | 30-day Unlock {unlock_pct_30d_str}
- MA: MA5={ma5} MA10={ma10} MA20={ma20} | {alignment_en}
- MACD: DIF={dif} DEA={dea} Histogram={hist} | {macd_signal_en}
- RSI: RSI6={rsi6} RSI12={rsi12} RSI24={rsi24} | {rsi_zone_en}
- Volume: Volume Ratio {vol_ratio} | {vol_trend_en}
- Bias: MA5 Bias {bias_ma5:+.2f}%

**AI Judgment**
{2-3 sentence comprehensive analysis}

**Bullish Factors**
- {factor1}
- {factor2}

**Risk Factors**
- {risk1}
- {risk2}

**Price Targets**
| Entry Price | Target Price | Stop-Loss |
|-------------|--------------|-----------|
| {entry} | {target} (+{pct}%) | {stop_loss} (-{pct}%) |

> Target/Stop-Loss prices default to script `indicators.risk` (target_suggested / stop_suggested, ATR volatility-adaptive), AI must not fabricate prices.

**News Summary**
Sentiment: {sentiment_label_en} (score {sentiment_score}, time-decay weighted) | Major Risk: {has_major_risk_en} | Event Types: {event_types_en}

**Latest News**
- [{date}] [{sentiment_en}] {title} ({event_type_en})
- [{date}] [{sentiment_en}] {title} ({event_type_en})
- [{date}] [{sentiment_en}] {title} ({event_type_en})

---
```

### Signal Emoji Mapping

| Signal | Emoji | English |
| ------ | ----- | ------- |
| strong_buy | Green Circle | Strong Buy |
| buy | Blue Circle | Buy |
| hold | Yellow Circle | Hold |
| wait | White Circle | Wait |
| sell | Orange Circle | Sell |
| strong_sell | Red Circle | Strong Sell |

### Alignment English Mapping

| English | Description |
| ------- | ----------- |
| strong_bullish | Strong Bullish Alignment |
| bullish | Bullish Alignment |
| weak_bullish | Weak Bullish Alignment |
| consolidation | Consolidation |
| weak_bearish | Weak Bearish Alignment |
| bearish | Bearish Alignment |
| strong_bearish | Strong Bearish Alignment |

### MACD Signal English Mapping

| English | Description |
| ------- | ----------- |
| golden_cross_above_zero | Golden Cross Above Zero |
| golden_cross | Golden Cross |
| crossing_above_zero | Crossing Above Zero |
| bullish | Bullish Running |
| neutral | Neutral |
| bearish | Bearish Running |
| death_cross | Death Cross |
| crossing_below_zero | Crossing Below Zero |

### Volume Trend English Mapping

| English | Description |
| ------- | ----------- |
| heavy_volume_up | High-Volume Rally |
| heavy_volume_down | High-Volume Drop |
| shrink_pullback | Volume-Contraction Pullback |
| shrink_up | Low-Volume Rally |
| normal | Normal |

### RSI Zone English Mapping

| English | Description |
| ------- | ----------- |
| overbought | Overbought |
| strong | Strong |
| neutral | Neutral |
| weak | Weak |
| oversold | Oversold |

### Phase English Mapping

| English | Description |
| ------- | ----------- |
| uptrend_pullback | Uptrend Pullback |
| downtrend_decline | Downtrend Decline |
| range_swing | Range Swing |
| insufficient_data | Insufficient Data |

### Limit Status English Mapping

| English | Description |
| ------- | ----------- |
| limit_up | Limit Up (Buy not executable today) |
| near_limit_up | Near Limit Up |
| limit_down | Limit Down (Stop-loss may not fill) |
| near_limit_down | Near Limit Down |
| normal | Normal |
| unknown | Unknown |
| not_applicable | No Limit Restriction |

### News Sentiment English Mapping

| English | Description |
| ------- | ----------- |
| positive | Bullish |
| negative | Bearish |
| neutral | Neutral |
| mixed | Mixed |

### Footer

```
> Disclaimer: The above analysis is for reference only and does not constitute investment advice. Investment involves risk; enter the market with caution.
> Data Sources: Tonghuashun / efinance / akshare / yfinance | Analysis Time: {timestamp}
```
