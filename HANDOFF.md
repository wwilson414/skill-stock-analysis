# Stock-Analysis Skill — Handoff 文档

> **创建日期**：2026-09-09
> **当前阶段**：P0–P3 已完成 ✅；P4-1 ✅ / P4-2 ✅ / P4-3 ✅；**P4-4、P4-5 ⚠️ 部分完成**（引擎/独立类就绪，集成与验收待做）
> **下一步**：按 §7.5 未完成项清单补齐——RiskMonitor 接入模拟盘引擎 → 盈亏比/覆盖率指标 → 全量样本外回放验收（4-4c）

---

## 1. 项目概述

对股票分析 skill 的评分系统进行实证优化。原始评分（`calc_trend_score`）在 20d 前向收益上 IC 为 -0.04，几乎全部来自下跌段（downtrend_decline IC -0.066）。通过四阶段迭代，定位问题根源并构建可执行的信号体系。

---

## 2. 阶段成果

### P0 — 扩大证据基础 ✅
- **结论**：负 IC 是结构性缺陷（阶段依赖），非窗口伪影
- **方法**：36 股 × 3.7y × 29,476 信号，bootstrap CI，分 phase/market/halfyear 分段
- **产物**：`references/p0_backtest.py`, `reports/p0_expansion.json`

### P1 — 信号质量 ✅
- **结论**：
  - `mr_score`（均值回归）下跌段 IC +0.034 ✅
  - `comp_vol`（量比异动）非下跌段 IC +0.046 ✅
  - 动量总分无 out-of-time 概率信息（Brier 全部劣于常数基线）
- **产物**：`references/mr_signal.py`, `p1_eval.py`, `score_calibration.py`

### P2 — 执行真实性 ✅
- **结论**：
  - 四口径（base/open/net_fee/net）IC 均稳健
  - A 股涨停封板仅 0.12% 信号受影响
  - **rotation-portfolio**：comp_vol 唯一幸存者（net annual +9.6%，Sharpe 0.046）
  - mr_score base +11.37% 但被 18.53pp 费用拖垮 → net -7.16%
- **产物**：`references/p2_execution.py`, `p2_schedule.py`

### P3 — 工程与可维护性 ✅
- **结论**：缓存/测试/阈值配置全部固化
- **产物**：`.p0_cache/`, `tests/`（26 项 pytest），`references/score_config.py`

### P4 — 生产化与信号组合 ✅ 1/2/3，⚠️ 4/5（集成与验收待做）
- **状态**：P4-1 ✅、P4-2 ✅、P4-3 ✅、**P4-4 ⚠️**（引擎就绪、4-4c 未验收）、**P4-5 ⚠️**（独立类就绪、未接入引擎）
- **产物**：
  - `references/signal_combo.py` — 分 phase 组合信号（P4-1）
  - `references/p4_combo_backtest.py` — 4-1b 回测对比 harness + `--save-store` 灌库（P4-3/4-4）
  - `reports/p4_combo_backtest.json` — 4-1c 验收数据
  - `tests/test_signal_combo.py` — 5 项单测
  - `analyze_stock()` 输出新增 `combo` 字段（P4-2，调用 `compute_combo_signal`，非致命 fallback）
  - `SKILL.md` STEP 4.5 组合信号说明 + `output-format-template.md` Combo Signal 卡片行
  - `tests/test_analyze_combo.py` — 4 项单测
  - `references/store.py` — SQLite 持久层（P4-3：signals/trades/portfolio，upsert 增量 + 过滤查询）
  - `tests/test_store.py` — 7 项单测
  - `references/paper_trader.py` + `tests/test_paper_trader.py`（P4-4 引擎 20 项单测）
  - `references/risk_monitor.py` + `tests/test_risk_monitor.py`（P4-5 独立类 23 项单测）
- **验收**：
  - 4-1b ✅（combo net Sharpe 0.059 > comp_vol 0.046，年化 +12.81% vs +9.62%，max_dd 74.8% < 86.9%）
  - 4-1c Sharpe>0.08 ⚠️ 仅 open 口径达标（0.086），net 0.059 → 留给 4-4 模拟盘调优
  - 4-2 ✅ 字段完整（phase/primary/weight/gates/gate_blocked/combo_score/context），combo.phase 与 indicators.context.phase 一致（同一分类器）
  - **4-4/4-5 ⚠️ 未验收**：盈亏比未实现、RiskMonitor 未接入引擎、覆盖率未实现、样本外回放未跑（详见 §7.5）

---

## 3. 核心数据（必须记住）

### P0 核心 IC（20d，29,476 信号）

| 切片 | IC | 95% CI | n |
|---|---|---|---|
| 总体 | -0.0405 | [-0.051, -0.029] | 29,476 |
| downtrend_decline | -0.0662 | [-0.084, -0.048] | 13,752 |
| range_swing | -0.0090 | [-0.037, +0.019] | 6,029 |
| uptrend_pullback | +0.0119 | [-0.009, +0.032] | 9,695 |

### P1 信号 IC（20d）

| 信号 | phase | base IC | net IC |
|---|---|---|---|
| mr_score | downtrend_decline | +0.034 | +0.009 |
| comp_vol | non-downtrend | +0.046 | +0.051 |
| comp_vol | downtrend_decline | +0.054 | +0.051 |
| mom | overall | -0.040 | -0.034 |

### P2-12 rotation-portfolio（230 笔）

| family | base net | 结论 |
|---|---|---|
| mr_score | +11.37% → **-7.16%** | 18.53pp 费用拖垮 |
| mom | +7.36% → +3.42% | 组合口径为正 |
| **comp_vol** | +14.43% → **+9.62%** | **唯一幸存者** |

---

## 4. 文件结构

```
references/
├── stock_data_fetcher.py   # 核心：analyze_stock() / backtest_stock() / calc_trend_score()
├── mr_signal.py            # P1-5: 均值回归 + 候选组件（compute_components）
├── signal_combo.py         # P4-1: 分 phase 组合信号（compute_combo_signal / combo_signal_series）
├── p0_backtest.py          # P0: 证据扩展 harness
├── p1_eval.py              # P1: 信号评估 harness
├── p2_execution.py         # P2: 执行重定价 harness（四口径）
├── p2_schedule.py          # P2-12: rotation-portfolio 模拟
├── p4_combo_backtest.py    # P4-1b: 组合 vs 单一信号回测对比
├── store.py                # P4-3: SQLite 持久层（signals/trades/portfolio）
├── risk_monitor.py         # P4-5: 风控监控（仓位/止损/熔断/EOD 报告）
├── score_calibration.py    # P1-7: 分数→概率校准
└── score_config.py         # P3-14: 阈值配置中心（含 COMBO 组合参数 + RISK 风控参数）

reports/
├── p0_expansion.json       # P0 证据
├── p1_signal_results.json  # P1 信号评估
├── p1_signal_results_random.json  # P1 holdout
├── calibration_20d.json    # P1-7 校准工件
├── p2_execution.json       # P2 执行重定价
├── p2_schedule.json        # P2-12 组合模拟
└── p4_combo_backtest.json  # P4-1b/1c 组合回测验收

tests/
├── conftest.py
├── test_ic_stats.py        # P0 统计 9 项
├── test_mr_signal.py       # P1 组件 4 项
├── test_p2_execution.py    # P2 执行 6 项
├── test_score_config.py    # P3-14 阈值 5 项
├── test_signal_combo.py    # P4-1 组合信号 5 项
├── test_analyze_combo.py   # P4-2 analyze_stock combo 接入 4 项
├── test_store.py           # P4-3 SQLite 持久层 7 项
├── test_paper_trader.py   # P4-4 模拟盘引擎 20 项
└── test_risk_monitor.py   # P4-5 风控监控 23 项
```

---

## 5. 复跑命令

```bash
# P0 全量（缓存命中 <2 分钟）
python3 references/p0_backtest.py

# P1 全量
python3 references/p1_eval.py

# P1 holdout（随机样本）
python3 references/p1_eval.py --universe random --seed 42

# P2 执行重定价
python3 references/p2_execution.py

# P2-12 组合模拟
python3 references/p2_schedule.py

# P4-1b 组合 vs 单一信号回测（缓存命中 ~1.5 分钟）
python3 references/p4_combo_backtest.py

# P1-7 校准
python3 references/score_calibration.py

# 测试套件
python3 -m pytest tests/ -q
```

---

## 6. 已知数据源事实

- 腾讯美股 K 线仅覆盖 NASDAQ（.OQ），NYSE 代码（JPM/JNJ/KO/XOM）仅返回 1 根 → yfinance 兜底
- tencent usSPY 不可用 → 基准用 SPY@yfinance
- eastmoney push2 主域偶发 502 → 已加重试 + push2delay 镜像回退
- 缓存 `.p0_cache/` 已 gitignore，冷启动 <2 分钟

---

## 7. P4-1 信号组合架构 ✅（2026-09-10）

### 目标
构建分 phase 分工的信号组合，超越单一 comp_vol（Sharpe 0.046）。**已达成**。

### 实现：`references/signal_combo.py`

分工表（沿用立项设计）：

| 市况 (phase) | 主信号 | 辅助过滤（gate） | 仓位权重 |
|---|---|---|---|
| uptrend_pullback | comp_vol（量比异动） | `momentum_confirm`：mom>50（无 mom 时回退 20d 动量为正） | 100% |
| range_swing | comp_vol | `mr_extreme`：mr_score≥2 时 combo 加分 +0.5（soft） | 50% |
| downtrend_decline | mr_score（均值回归） | `extreme_only`：RSI2<10 + 乖离>10% + **止跌日**（= mr_event） | 30% |

API：
- `compute_combo_signal(ohlcv, mom_scores=None, bench_closes=None) -> dict`（单点，生产入口）
- `combo_signal_series(ohlcv, mom_scores=None, min_bars=None) -> list`（批量回测，warmup 前为 None）
- `combo_score = weight*primary_score`，gate 阻断 → `None`；阈值在 `score_config.COMBO`

### 4-1b 回测对比（`references/p4_combo_backtest.py`，29,476 行，net 口径）

| family | net 年化 | net Sharpe | net max_dd | n |
|---|---|---|---|---|
| **combo** | **+12.81%** | **0.059** | **74.8%** | 218 |
| comp_vol | +9.62% | 0.046 | 86.9% | 213 |
| mr_score | -7.16% | -0.024 | 95.7% | 165 |
| mom | +4.37% | 0.019 | 78.2% | 223 |

### 验收
- 4-1a ✅：`signal_combo.py` + `tests/test_signal_combo.py`（5 项，31 项全绿）
- 4-1b ✅：combo net +12.81% / Sharpe 0.059 均超越 comp_vol；max_dd 74.8% < 86.9%
- 4-1c ⚠️ 部分达标：max_dd 74.8% < 75% ✅；Sharpe 0.08 目标仅 **open** 口径达标（0.086），**net** 0.059 → 记账为 4-4 模拟盘调优入口（执行费用/频率，非信号）

### 关键实证发现（必须记住）
1. **combo_phase 与 momentum backtest phase 100% 一致**（29,476 行 / 3 相位零失配）——组合信号与 P0/P1/P2 切片完全可比
2. **downtrend 的 gate 必须是 mr_event（含止跌日）**：A/B 实验证明去掉止跌条件（仅 RSI2+乖离）后 net Sharpe 从 0.059 → 0.046、dd 81%——下跌段"极端超卖但未止跌"的笔是坏的
3. 组合主力来自 uptrend（147 笔）+ range（69 笔）；downtrend 仅 2 笔入选（extreme_only 刻意保守）

### P4-2 生产接入 ✅（2026-09-10）

**完成内容：**
- `analyze_stock()` 输出新增 `combo` 字段：{phase, primary, primary_score, weight, secondary, secondary_score, gates, gate_results, gate_blocked, combo_score, context}
  - 通过延迟 `from signal_combo import compute_combo_signal` 接入（避免循环导入：signal_combo 反向依赖 stock_data_fetcher）
  - `mom_scores[-1] = score["total"]`（momentum_confirm gate 使用真实动量总分）
  - 非致命：combo 计算失败仅记日志 → `combo: None`，不阻塞整体分析
- `SKILL.md` 新增 **STEP 4.5: Phase-Aware Combo Signal**（分工表 + dashboard 解读规则 + 回测证据）
- `output-format-template.md` 卡片新增 **Combo Signal** 区块
- `tests/test_analyze_combo.py` 4 项单测（字段完整 / phase 与 context 一致 / downtrend primary / 非致命故障路径）

**验收：**
- 4-2a ✅ `analyze_stock()` 输出 `combo` 字段完整
- 4-2c ✅ SKILL.md 文档更新
- 4-2b ✅ 买入建议判断规则写入 SKILL.md STEP 4.5（强买/仅观察/持有 + combo 字段落地方式）

**实证校准（重要）：** 合成信号上 `trend_score.total`（momentum）直接充当 momentum_confirm 的 mom 输入；combo.phase 与 `indicators.context.phase` 100% 一致（同一 `calc_pullback_context`）。

### P4-3 持久化落盘 ✅（2026-09-11）

**完成内容：**
- `references/store.py`（SQLite stdlib，`reports/signals.db`，gitignore 排除 `reports/*.db`）
  - 表 `signals`：PK(date, code)，列 market/phase/mom/mr_score/comp_vol/combo_signal/combo_phase/combo_weight/gate_blocked/source/created_at + 3 索引
  - 表 `trades`：AUTOINCREMENT id + direction/price/size/pnl/status
  - 表 `portfolio`：PK(date)，cash/positions/total_value/daily_return
  - `save_signals` = upsert（ON CONFLICT 刷新信号列、保留 created_at）→ 增量 append 幂等
  - `query_signals(code/phase/date_from/date_to/limit)`、`latest_signal_date`、trade 生命周期（save/close/query）、portfolio 快照 + 序列
  - `row_to_signal()`：p4 harness 行 → signals 行（None-safe，blocked→0/1）
- `p4_combo_backtest.run_stock_combo` 行新增 `combo_weight` 列（signals 表 weight 来源，向后兼容）
- `tests/test_store.py` 7 项单测（roundtrip / upsert 幂等 / 过滤 / trade 生命周期 / portfolio 同日更新 / 目录创建 / None-safe 映射）

**验收：** CRUD 可用 ✅（`python3 references/store.py --db reports/signals.db --demo` 冒烟 + 42 项 pytest 全绿）

**数据流（已打通）：** `analyze_stock()` combo 字段 / p4 rows → `row_to_signal()` → `Store.save_signals()` → `paper_trader.py`（4-4）读取回放。

**灌库链路（2026-09-11 补全）：** `p4_combo_backtest.py --save-store <db>` 现已真实存在（此前文档写了但代码缺失，已补上并全链路验证：3 股真实数据 → 2,457 行入库 → 1,068 行可排序 / 1,389 行 gate-blocked 保留 → paper_trader 回放成功）。

### P4-4 模拟盘验证 ⚠️ 部分完成（2026-09-11）

**引擎完成内容：** `references/paper_trader.py` + `tests/test_paper_trader.py`（20 项单测）
- T+1 成交（信号日 t → t+1 open 填单）
- 涨停封板跳过 / 跌停封板滚仓（最多 5 天）
- 费用：`COSTS[market]` 佣金 + slippage bp/边（双向）
- 仓位：`(1/max_positions) × combo_weight`（uptrend 1.0 / range 0.5 / downtrend 0.3）
- 绩效：total_ret / annual_ret / annual_vol / sharpe / max_dd / hit_rate / mean_ret / n_trades
- 可选 `Store` 落盘 trades + portfolio snapshots
- 引擎冒烟：3 股真实数据回放成功（sharpe -0.38 仅为链路验证值，非验收）

**⚠️ 4-4 尚有 3 处未完成（详见 §7.5）**

### P4-5 风控监控 ⚠️ 独立类通过、未接入模拟盘引擎（2026-09-11）

**完成内容：**
- `references/risk_monitor.py`（`RiskMonitor` 类，~220 行）
  - `can_enter(code, phase, weight, portfolio_value, positions)` → 入场闸门（单股 ≤20%、单 phase ≤60%）
  - `check_stop_loss(code, entry_price, current_price)` → 个股 -8% 止损
  - `check_circuit_breaker(portfolio_value, peak)` → 组合 -15% 熔断清仓
  - `eod_report(date, positions, portfolio_value, peak)` → 日终风险报告（drawdown / phase_exposure / stock_exposure / stop_loss_alerts / circuit_breaker / alerts）
- `score_config.py` 新增 `RISK` 字典（max_per_stock 0.20 / max_per_phase 0.60 / stop_loss_pct 0.08 / circuit_breaker_pct 0.15）
- `tests/test_risk_monitor.py` 23 项单测（入场闸门 5 + 止损 6 + 熔断 5 + EOD 报告 7）

**验收：** 85 项 pytest 全绿（62 + 23）

**⚠️ 集成缺口：** `RiskMonitor` 是独立类，`paper_trader.py` **0 处调用**（`grep risk_monitor paper_trader.py` 无结果）——回放日循环里止损 / 熔断 / 仓位限制**从未生效**。须在下次会话集成（见 §7.5 第 3 项）。

### ⚠️ 未完成项清单（2026-09-11 审计，下次会话据此执行）

> 上轮把 P4 全部标记为 ✅，经逐项核代码后发现以下缺口。**信号/回测/持久化/模块本身均已就绪且验证过，缺的是末段集成与验收。**

| # | 未完成项 | 现状 | 需要做什么 | 验收标准 |
|---|---|---|---|---|
| 1 | **4-4c 样本外回放验收** | 从未跑过（36 股全量灌库 + `--start 2025-09-01` 回放未执行；仅 3 股冒烟 sharp -0.38） | `python3 references/p4_combo_backtest.py --save-store reports/signals.db` 全量灌库 → `python3 references/paper_trader.py --db reports/signals.db --start 2025-09-01` | 样本外 Sharpe > 0.3，max_dd < 30%，盈亏比 > 1.5 |
| 2 | **盈亏比（profit factor）指标未实现** | `paper_trader._performance()` 只输出 hit_rate / mean_ret，**无盈亏比**；ROADMAP 4-4c 明确要求 > 1.5 | 在 `_performance()` 增加 `profit_factor = 平均盈利笔均 / 平均亏损笔均`，纳入 stats + gate 判定 | 单测覆盖（盈利亏损笔构造）；4-4c 输出该指标 |
| 3 | **RiskMonitor 未接入 paper_trader 引擎** | `paper_trader.py` 无一处 import/调用 risk_monitor；止损/熔断/仓位限制在回放中从未生效 | 日循环中：(a) 入场时 `can_enter`（单股/单 phase 上限）；(b) 每日 `check_stop_loss`（个股 -8% 立即卖出）；(c) equity 从峰值跌 15% → `check_circuit_breaker` 全清仓；(d) stats 新增 `stop_loss_trades` / `circuit_breakers` 计数 | 单测覆盖：止损触发卖出 / 熔断清仓 / 仓位超限拒绝入场；回放 stats 含新字段 |
| 4 | **信号覆盖率 > 60% 指标未实现** | ROADMAP P4 验收表要求（每日至少 1 只有信号），代码库 grep 覆盖率 = 0 | 统计 `coverage_pct = 有信号的交易日 / 总交易日`，纳入 4-4c gate / 输出 | 回放 JSON 输出 coverage_pct |
| 5 | **组合 net Sharpe > 0.08 目标未达成** | `p4_combo_backtest.json` `combo_sharpe_above_008: False`（net 0.059 < 0.08；open 0.086 达标） | 记入 4-4 模拟盘调优（执行费用/频率，**禁改信号逻辑**）——与第 1 项一并评估 | net Sharpe ≥ 0.08（尽力，非阻塞上线闸门） |
| 6 | **P0-4 残余偏差预警** | 已用 `--universe random` 复现（P1 holdout seed 42）| 无代码动作，仅记档 | — |

> 优先级：**3（集成止损/熔断/仓位）→ 2（盈亏比）→ 4（覆盖率）→ 1（全量回放验收）**。1 依赖于 2/3/4 完成后再跑才有意义。

---

## 8. P4 完整任务清单

| 任务 | 内容 | 验收 | 状态 |
|---|---|---|---|
| 4-1 信号组合架构 | `signal_combo.py`，分 phase 分工 | Sharpe > 0.08 | ✅（net 0.059 / open 0.086，dd 74.8%；**net >0.08 未达成 → §7.5 #5**） |
| 4-2 生产接入 | `analyze_stock()` 新增 combo 字段 + SKILL.md | 字段完整 | ✅（combo 字段 + STEP 4.5 判断规则 + 模板） |
| 4-3 持久化落盘 | `store.py`（SQLite） | CRUD 可用 | ✅（signals/trades/portfolio + upsert 增量 + 过滤查询 + **`--save-store` 灌库链路**） |
| 4-4 模拟盘验证 | `paper_trader.py`，样本外 1 年 | Sharpe > 0.3, max_dd < 30% | ⚠️ 引擎 ✅（+20 单测）；**4-4c 样本外验收未跑** + **盈亏比未实现** → §7.5 #1/#2 |
| 4-5 风控监控 | `risk_monitor.py`，仓位/止损/日终 | 监控面板 | ⚠️ 独立类 ✅（23 项单测）；**未接入 paper_trader 引擎** → §7.5 #3 |

---

## 9. 风控参数（已固化到 score_config.py）

```python
SIGNAL_THRESHOLDS = {"strong_buy": 75.0, "buy": 60.0, "hold": 45.0, "wait": 30.0}
GATES = {"rr_ratio_min": 1.5, "unlock_block_pct_30d": 5.0}
LIMIT = {"STAR_ChiNext_BSE_pct": 20.0, "main_pct": 10.0, "ST_pct": 5.0}
COSTS = {"cn_a": (0.025, 0.075), "cn_hk": (0.12, 0.12), "us": (0.02, 0.02)}
RISK = {"max_per_stock": 0.20, "max_per_phase": 0.60, "stop_loss_pct": 0.08, "circuit_breaker_pct": 0.15}
```

---

## 10. 关键 Git Commits

```
d898c73 P4-3/4-4: add --save-store to p4_combo_backtest (SQLite 灌库链路)
1b58c08 docs: sync completed items (P0-4/13-15/4-2b); 仅 4-4c 剩余
33ed21b docs: mark P4-5 complete (风控)
9d60468 P4-5: risk monitor (仓位限制/止损/熔断/EOD 报告, 23 项单测)
635388b docs: mark P4-4 complete, next step P4-5 risk monitoring
ee0d5c4 P4-4: paper_trader unit tests (20)
ad8e32e P4-4: paper-trading replay engine
53bbf8b P4-3: store.py SQLite 持久层（signals/trades/portfolio + upsert 增量）
2acf0aa P4-2: analyze_stock() combo 字段 + SKILL.md STEP 4.5（生产接入）
ad45ffa P4-1: signal_combo.py 分 phase 组合 + 4-1b 回测（net Sharpe 0.059）
a20c71d P4 立项：生产化与信号组合
0738eec chore: clarify .gitignore comments
2b924c1 P2-12: rotation-portfolio sim + full P2 verdict
e4973ec P2 result: reports/p2_execution.json
ce16e83 P3: threshold config, pytest suite, p2 harness
93ee58d P1 complete: MR signal passes acceptance
c798383 P0: expand backtest evidence base
```

---

## 11. 新会话启动清单

```bash
# 1. 进入目录
cd /home/wwei/workspace/skill-stock-analysis

# 2. 读交接文档
cat HANDOFF.md

# 3. 验证环境
python3 -m pytest tests/ -q          # 85 passed
python3 references/p0_backtest.py    # P0 全量（缓存 <2 min）

# 4. P4 剩余工作（按 §7.5 优先级）——P4 尚未真正完成
# 3) RiskMonitor 接入 paper_trader 引擎（止损/熔断/仓位限制）→ 2) 盈亏比指标 → 4) 覆盖率指标
# → 1) 全量灌库 + 样本外回放验收
#    python3 references/p4_combo_backtest.py --save-store reports/signals.db
#    python3 references/paper_trader.py --db reports/signals.db --start 2025-09-01
# → 验收闸门：样本外 Sharpe > 0.3，max_dd < 30%，盈亏比 > 1.5
```

---

## 12. 注意事项

1. **不要修改 `stock_data_fetcher.py` 的核心评分逻辑**（`_DEFAULT_WEIGHTS` / `calc_trend_score`）除非有新的 IC 证据——A/B 已证明调权无效
2. **不要在下坡段依赖动量总分**——IC 为负，权重只能改幅度不能改符号
3. **comp_vol 是唯一稳健信号**——任何新组件必须与它对比
4. **样本外验证是闸门**——P4-4 的 2025-09~2026-09 样本外 Sharpe > 0.3 是上线前提
5. **缓存 `.p0_cache/` 已 gitignore**——冷启动 <2 分钟，不要提交
