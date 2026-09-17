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
| Decision State | {decision_state_en} (confidence: {decision_confidence}) |
| Time Horizon | {decision_horizon} |
| Current Price | {price} {currency} ({change_pct:+.2f}%) |
| Composite Score | {score}/100 |
| Signal | {signal_en} |
| P/E Ratio | {pe_ratio} |
| P/B Ratio | {pb_ratio} |

> `Decision State` 必须取自 `decision.state`（枚举见下），不要用技术信号替代；`confidence` 取自
> `decision.confidence`。技术信号（`strong_buy` 等）仅作技术层输入，不能当作长期投资结论。

**Decision States Mapping**

| `decision.state` | English |
| ---------------- | ------- |
| STRONG_AVOID | Strong Avoid |
| AVOID | Avoid |
| WATCHLIST | Watchlist |
| BUY_CANDIDATE | Buy Candidate |
| SMALL_POSITION_ONLY | Small Position Only |
| HOLD | Hold |
| REDUCE | Reduce |
| SELL_OR_EXIT | Sell or Exit |
| RECHECK_REQUIRED | Recheck Required |

**Hard Gates**: {hard_gates_status}

> Render `fired({buy_gates})` when `trend_score.buy_gates` is non-empty; otherwise render `none`.
> Preserve the gate text from the script so a buy-grade score cannot hide an execution block.
> When `decision.hard_gates` is non-empty and `trend_score.buy_gates` is empty (e.g. RSI > 80 or
> MA5 bias > 5% blocked the candidate), render `fired({decision.hard_gates})` instead.

**Data Quality**: {data_quality_level} (score {data_quality_score}) | Fallback used: {fallback_used} | Sources: {source_chain}

> Render from `data_quality`: `level` (`high`/`medium`/`low`/`insufficient`), `score`,
> `fallback_used`, and `sources` (`ohlcv` <- `ohlcv_preferred`, plus `realtime` / `valuation`).
> If `missing_fields` is non-empty, list them; if `caveats` is non-empty, append them verbatim.
> When `confidence_impact` is `moderate` or `major`, the decision's confidence must be lowered.


**Combo Signal (phase-aware, P4-1)**
- Phase: {combo_phase_en} | Primary: {combo_primary} | Weight: {combo_weight} | Combo Score: {combo_score}
- Gate: {combo_gates_en} | Gate Blocked: {combo_gate_blocked_en} | Secondary: {combo_secondary}

> `combo_score` 为空（gate blocked / 数据不足）时输出 `N/A`——该 bar 不作为组合候选；不要用动量总分替代排名。

**Technical Analysis**
- Phase/Position: {phase_en} | 120-day range position {range_pos_pct}% | Distance from MA60 {dist_ma60_pct:+.2f}% | 20-day change {chg_20d_pct:+.2f}%
- Relative Strength: vs {bench_en} 20D RS {rs_20d:+.2f}% | 60D RS {rs_60d:+.2f}%
- Volatility & Risk Levels: ATR {atr} ({atr_pct}%) | Annualized Vol {ann_vol_pct}% | R:R Ratio {rr_ratio}
- Trading Constraints/Events: {limit_status_en} | 30-day Unlock {unlock_pct_30d_str} ({unlock_gate_status})
- Market Rules: {market_rules_en}

> Render `market_rules_en` from `market_rules`: currency, T+1, lot size, limit regime and the
> script's `execution_notes`. Never restate market rules from memory.
- MA: MA5={ma5} MA10={ma10} MA20={ma20} | {alignment_en}
- MACD: DIF={dif} DEA={dea} Histogram={hist} | {macd_signal_en}
- RSI: RSI6={rsi6} RSI12={rsi12} RSI24={rsi24} | {rsi_zone_en}
- Volume: Volume Ratio {vol_ratio} | {vol_trend_en}{bar_partial_note}
- Bias: MA5 Bias {bias_ma5:+.2f}%

**AI Judgment**
{2-3 sentence comprehensive analysis}

**Bullish Factors**
- {factor1}
- {factor2}

**Risk Factors**
- {risk1}
- {risk2}

**Decision Evidence (from `decision`)**
- Supporting: {supporting_evidence}
- Opposing: {opposing_evidence}
- Key Risks: {key_risks}
- Suggested Action Range: {suggested_action_range}
- Position Size Suggestion: {position_size_suggestion}
- Re-evaluation Triggers: {re_evaluation_triggers}

> Every line must be copied from `decision.*` (script output). Do not add evidence the script did
> not produce; do not turn the action band into a precise instruction. Render `N/A` for a `null`
> `position_size_suggestion`. When `decision.data_quality_warning` is set, show it verbatim in
> `AI Judgment` before any conclusion.

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

### Intraday Partial Bar Note (`bar_partial_note`)

When `indicators.volume.bar_partial` is `true`, the newest OHLCV bar is still forming:
the volume ratio has been **pro-rated to a full-day equivalent** using the elapsed session
fraction (`session_elapsed_pct`). Append to the Volume line, e.g.
`（盘中未收盘，量比已按已交易 74.2% 时长折算；早盘折算噪声较大）`.
When `bar_partial` is `false`, leave `bar_partial_note` empty.

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
> Disclaimer: This analysis is for personal research and decision support only. It is not financial advice, does not guarantee returns, and should not be treated as a direct instruction to buy or sell. The user remains responsible for all investment decisions.
> Data Sources: {source_chain} (as_of {as_of}, adjustment {adjustment}, data quality {data_quality_level}) | Analysis Time: {timestamp}

> The disclaimer text must match `decision.disclaimer` / the requirement verbatim; never shorten it.
> The data-source line must list what actually served the data (`data_quality.sources`), not the
> libraries that happen to be installed.
```
