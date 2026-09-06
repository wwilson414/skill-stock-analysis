# Decision Dashboard Output Format

## Format Specification

Use exactly this Markdown structure for the output dashboard.

### Header

```
## {DATE} 股票决策看板

{N} 只股票分析完成 | 买入: {n} | 持有: {n} | 卖出: {n}
```

### Per-Stock Card

For each stock, output one card separated by `---`:

```
### {NAME}({CODE}) — {SIGNAL_EMOJI} {SIGNAL_CN}

| 指标 | 数值 |
|------|------|
| 现价 | {price} ({change_pct:+.2f}%) |
| 综合评分 | {score}/100 |
| 信号 | {signal_cn} |
| 市盈率 | {pe_ratio} |
| 市净率 | {pb_ratio} |

**技术面**
- 阶段/位置: {phase_cn} | 120日区间位置 {range_pos_pct}% | 距MA60 {dist_ma60_pct:+.2f}% | 20日涨跌 {chg_20d_pct:+.2f}%
- 相对强度: vs{bench_cn} 20日RS {rs_20d:+.2f}% | 60日RS {rs_60d:+.2f}%
- 波动与风险位: ATR {atr} ({atr_pct}%) | 年化波动 {ann_vol_pct}% | 盈亏比 {rr_ratio}
- 交易约束/事件: {limit_status_cn} | 30日内解禁 {unlock_pct_30d_str}
- 均线: MA5={ma5} MA10={ma10} MA20={ma20} | {alignment_cn}
- MACD: DIF={dif} DEA={dea} 柱={hist} | {macd_signal_cn}
- RSI: RSI6={rsi6} RSI12={rsi12} RSI24={rsi24} | {rsi_zone_cn}
- 量能: 量比 {vol_ratio} | {vol_trend_cn}
- 乖离率: MA5乖离 {bias_ma5:+.2f}%

**AI 判断**
{2-3 sentence comprehensive analysis}

**看多因素**
- {factor1}
- {factor2}

**风险因素**
- {risk1}
- {risk2}

**价格目标**
| 入场价 | 目标价 | 止损价 |
|--------|--------|--------|
| {entry} | {target} (+{pct}%) | {stop_loss} (-{pct}%) |

> 目标价/止损价默认取自脚本 `indicators.risk`（target_suggested / stop_suggested，ATR 波动率自适应），AI 不得另行编造。

**消息面汇总**
情绪: {sentiment_label_cn} (score {sentiment_score}, 时间衰减加权) | 重大风险: {has_major_risk_cn} | 事件类型: {event_types_cn}

**最新消息**
- [{date}] [{sentiment_cn}] {title}（{event_type_cn}）
- [{date}] [{sentiment_cn}] {title}（{event_type_cn}）
- [{date}] [{sentiment_cn}] {title}（{event_type_cn}）

---
```

### Signal Emoji Mapping

| Signal | Emoji | Chinese |
|--------|-------|---------|
| strong_buy | 🟢 | 强烈买入 |
| buy | 🔵 | 买入 |
| hold | 🟡 | 持有 |
| wait | ⚪ | 观望 |
| sell | 🟠 | 卖出 |
| strong_sell | 🔴 | 强烈卖出 |

### Alignment Chinese Mapping

| English | Chinese |
|---------|---------|
| strong_bullish | 强势多头排列 |
| bullish | 多头排列 |
| weak_bullish | 弱多排列 |
| consolidation | 盘整 |
| weak_bearish | 弱空排列 |
| bearish | 空头排列 |
| strong_bearish | 强势空头排列 |

### MACD Signal Chinese Mapping

| English | Chinese |
|---------|---------|
| golden_cross_above_zero | 零轴上金叉 |
| golden_cross | 金叉 |
| crossing_above_zero | 上穿零轴 |
| bullish | 多头运行 |
| neutral | 中性 |
| bearish | 空头运行 |
| death_cross | 死叉 |
| crossing_below_zero | 下穿零轴 |

### Volume Trend Chinese Mapping

| English | Chinese |
|---------|---------|
| heavy_volume_up | 放量上涨 |
| heavy_volume_down | 放量下跌 |
| shrink_pullback | 缩量回调 |
| shrink_up | 缩量上涨 |
| normal | 正常 |

### RSI Zone Chinese Mapping

| English | Chinese |
|---------|---------|
| overbought | 超买 |
| strong | 强势 |
| neutral | 中性 |
| weak | 弱势 |
| oversold | 超卖 |

### Phase (阶段) Chinese Mapping

| English | Chinese |
|---------|---------|
| uptrend_pullback | 上升趋势回调 |
| downtrend_decline | 下跌趋势阴跌 |
| range_swing | 区间震荡 |
| insufficient_data | 数据不足 |

### Limit Status (涨跌停状态) Chinese Mapping

| English | Chinese |
|---------|---------|
| limit_up | 涨停封板（今日买入不可执行） |
| near_limit_up | 接近涨停 |
| limit_down | 跌停（止损可能无法成交） |
| near_limit_down | 接近跌停 |
| normal | 正常 |
| unknown | 未知 |
| not_applicable | 无涨跌停限制 |

### News Sentiment Chinese Mapping

| English | Chinese |
|---------|---------|
| positive | 偏多 |
| negative | 偏空 |
| neutral | 中性 |
| mixed | 多空交织 |

### Footer

```
> 免责声明: 以上分析仅供参考，不构成投资建议。投资有风险，入市需谨慎。
> 数据来源: 同花顺 / efinance / akshare / yfinance | 分析时间: {timestamp}
```
