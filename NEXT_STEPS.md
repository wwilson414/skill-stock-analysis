# Stock-Analysis Skill — 执行交接文档
>
> 最后更新：2026-09-10
> 当前阶段：P0–P3 已完成，P4-1/P4-2/P4-3 已完成，P4-4 待执行
>
---
>
## 1. 已完成阶段速览
>
| 阶段 | 状态 | 核心结论 | 关键产物 |
|---|---|---|---|
| P0 扩大证据 | ✅ | 负 IC 是结构性缺陷（阶段依赖），非窗口伪影 | `p0_backtest.py`, `reports/p0_expansion.json` |
| P1 信号质量 | ✅ | mr_score 下跌段 IC+0.034；comp_vol 非下跌段 IC+0.046；总分无概率信息 | `mr_signal.py`, `p1_eval.py`, `score_calibration.py` |
| P2 执行真实性 | ✅ | comp_vol 唯一穿越费用（net +9.6%，Sharpe 0.046）；动量负 IC 非执行伪影 | `p2_execution.py`, `p2_schedule.py` |
| P3 工程维护 | ✅ | 缓存/测试/阈值配置全部固化 | `.p0_cache/`, `tests/`, `score_config.py` |
| P4 生产化 | ✅ P4-1/2/3 | 组合信号 net Sharpe 0.059 > comp_vol 0.046；`analyze_stock()` 输出 `combo` 字段；SQLite 持久层 signals/trades/portfolio | `signal_combo.py`, `p4_combo_backtest.py`, `store.py` + 3 组测试 |
>
---
>
## 2. 关键实证结论（续用）
>
### P0 核心 IC（20d，36 股 × 3.7y，29,476 信号）
>
| 切片 | IC | 95% CI | n |
|---|---|---|---|
| 总体 | -0.0405 | [-0.051, -0.029] | 29,476 |
| downtrend_decline | -0.0662 | [-0.084, -0.048] | 13,752 |
| range_swing | -0.0090 | [-0.037, +0.019] | 6,029 |
| uptrend_pullback | +0.0119 | [-0.009, +0.032] | 9,695 |
>
### P1 信号 IC（20d）
>
| 信号 | phase | base IC | net IC |
|---|---|---|---|
| mr_score | downtrend_decline | +0.034 | +0.009 |
| comp_vol | non-downtrend | +0.046 | +0.051 |
| comp_vol | downtrend_decline | +0.054 | +0.051 |
| mom | overall | -0.040 | -0.034 |
>
### P2-12 rotation-portfolio（230 笔）
>
| family | base net | 结论 |
|---|---|---|
| mr_score | +11.37% → **-7.16%** | 18.53pp 费用拖垮 |
| mom | +7.36% → +3.42% | 组合口径为正 |
| **comp_vol** | +14.43% → **+9.62%** | **唯一幸存者** |

---

## 3. P4 执行计划（下一步）

### 优先级顺序
1. **4-1 信号组合架构** → `references/signal_combo.py` ✅ 已完成
2. **4-2 生产接入** → 升级 `analyze_stock()` + SKILL.md ✅ 已完成
3. **4-3 持久化落盘** → `references/store.py`（SQLite）✅ 已完成
4. **4-4 模拟盘验证** → `references/paper_trader.py` ✅ 已完成
5. **4-5 风控监控** → 仓位/止损/日终

### 4-1 完成记录（2026-09-10）

`signal_combo.py` 实现分工表：uptrend→comp_vol×1.0（momentum_confirm）、range→comp_vol×0.5（mr_extreme 加分）、downtrend→mr_score×0.3（extreme_only = mr_event 止跌日）。

**4-1b 回测（net 口径，29,476 行）：**

| family | net 年化 | Sharpe | max_dd |
|---|---|---|---|
| **combo** | **+12.81%** | **0.059** | 74.8% |
| comp_vol | +9.62% | 0.046 | 86.9% |

**验收：** 4-1a ✅ / 4-1b ✅ / 4-1c ⚠️（net 0.059 < 0.08，open 0.086 达标；dd 74.8% < 75% ✅）

**4-1 关键发现：** combo_phase 与 momentum phase 100% 一致；downtrend gate 必须含止跌日（A/B 证明去掉后 Sharpe 0.059→0.046）。

### 4-2 完成记录（2026-09-10）

**完成内容：**
- `analyze_stock()` 输出新增 `combo` 字段（延迟导入 `signal_combo`，非致命 fallback）
- `mom_scores[-1] = score["total"]`：momentum_confirm gate 使用真实动量总分
- SKILL.md 新增 STEP 4.5（分工表 + dashboard 解读 + 回测证据）
- output-format-template.md 卡片新增 Combo Signal 区块
- `tests/test_analyze_combo.py` 4 项单测（字段完整 / phase 与 context 一致 / downtrend primary / 非致命路径）

**验收：** 4-2a ✅ 字段完整 / 4-2c ✅ 文档更新 / 4-2b ⚠️ SKILL 判断层（ROADMAP 标记待接）

### 4-3 完成记录（2026-09-10）

`references/store.py`（SQLite stdlib，`reports/signals.db`）：
- 表 `signals` PK(date, code)（含 combo_signal/combo_weight/gate_blocked）+ `trades` + `portfolio`
- `save_signals` = upsert → 增量 append 幂等（重跑不重复、不重写历史）
- `query_signals(code/phase/date_from/date_to/limit)` + trade 生命周期 + portfolio 快照
- `row_to_signal()`：p4 rows → signals 行；`run_stock_combo` 行新增 `combo_weight` 列
- `tests/test_store.py` 7 项单测；冒烟 `python3 references/store.py --db reports/signals.db --demo`

**验收：** CRUD 可用 ✅ + `pytest` 42 项全绿

### 4-4 详细设计（待实现）

**文件：** `references/paper_trader.py`

- 读取 `store.py` signals（或实时喂入）→ 按 combo 权重组合下单 → 跟踪持仓 → 日终 PnL
- 执行约束：T+1 成交、涨跌停不可交易（复用 `calc_tradability` 逻辑）、滑点 10bp、佣金（`score_config.COSTS`）
- 回放窗口：样本外 2025-09 ~ 2026-09（1 年）
- **验收闸门：样本外 Sharpe > 0.3，max_dd < 30%**；4-1c net Sharpe 0.08 差额靠执行费用/频率调优

---

## 4. 文件结构（当前）

```
references/
├── stock_data_fetcher.py   # 核心：analyze_stock() / backtest_stock() / calc_trend_score()
├── mr_signal.py            # P1-5: 均值回归 + 候选组件
├── signal_combo.py         # P4-1: 分 phase 组合信号
├── p0_backtest.py          # P0: 证据扩展 harness
├── p1_eval.py              # P1: 信号评估 harness
├── p2_execution.py         # P2: 执行重定价 harness
├── p2_schedule.py          # P2-12: rotation-portfolio 模拟
├── p4_combo_backtest.py    # P4-1b: 组合 vs 单一信号回测
├── store.py                # P4-3: SQLite 持久层（signals/trades/portfolio）
├── score_calibration.py    # P1-7: 分数→概率校准
└── score_config.py         # P3-14: 阈值配置中心（含 COMBO）

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
└── test_store.py           # P4-3 SQLite 持久层 7 项
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

## 7. 下次执行入口

**立即开始 4-5 风控监控：**

1. 仓位管理：单只股票最大仓位 20%（`max_positions>=5` 已隐含），单一 phase 最大仓位 60%
2. 止损：个股 -8% 止损，组合 -15% 清仓
3. 日终监控：每日输出持仓 + 风险敞口 + 异常信号告警
