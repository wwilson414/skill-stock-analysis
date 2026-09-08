# Stock-Analysis Skill — Handoff 文档

> **创建日期**：2026-09-09
> **当前阶段**：P0–P3 已完成 ✅，P4 已立项 🆕
> **下一步**：执行 P4-1 信号组合架构

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

### P4 — 生产化与信号组合 🆕
- **状态**：已立项，待执行
- **产物**：—

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
├── p0_backtest.py          # P0: 证据扩展 harness
├── p1_eval.py              # P1: 信号评估 harness
├── p2_execution.py         # P2: 执行重定价 harness（四口径）
├── p2_schedule.py          # P2-12: rotation-portfolio 模拟
├── score_calibration.py    # P1-7: 分数→概率校准
└── score_config.py         # P3-14: 阈值配置中心

reports/
├── p0_expansion.json       # P0 证据
├── p1_signal_results.json  # P1 信号评估
├── p1_signal_results_random.json  # P1 holdout
├── calibration_20d.json    # P1-7 校准工件
├── p2_execution.json       # P2 执行重定价
└── p2_schedule.json        # P2-12 组合模拟

tests/
├── conftest.py
├── test_ic_stats.py        # P0 统计 9 项
├── test_mr_signal.py       # P1 组件 4 项
├── test_p2_execution.py    # P2 执行 6 项
└── test_score_config.py    # P3-14 阈值 5 项
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

## 7. 下一步：P4-1 信号组合架构

### 目标
构建分 phase 分工的信号组合，超越单一 comp_vol（Sharpe 0.046）。

### 分工表

| 市况 (phase) | 主信号 | 辅助过滤 | 仓位权重 |
|---|---|---|---|
| uptrend_pullback | comp_vol（量比异动） | mom > 50 确认动量 | 100% |
| range_swing | comp_vol | mr_score 极端值 | 50% |
| downtrend_decline | mr_score（均值回归） | RSI2<10 + 乖离>10% | 30% |

### 待实现

**文件**：`references/signal_combo.py`

```python
#!/usr/bin/env python3
"""P4-1: phase-aware signal combination."""
import numpy as np
from mr_signal import compute_components
from stock_data_fetcher import calc_pullback_context


def compute_combo_signal(ohlcv: list, bench_closes: list = None) -> dict:
    """输入 OHLCV → 输出组合信号。"""
    closes = [b["close"] for b in ohlcv if b.get("close") is not None]
    ma = calc_ma(closes, [5, 10, 20, 60])
    ctx = calc_pullback_context(closes, ma)
    phase = ctx.get("phase", "range_swing")
    comp = compute_components(ohlcv)
    last = len(ohlcv) - 1
    comp_vol = comp["comp_vol"][last] if np.isfinite(comp["comp_vol"][last]) else 0.0
    mr_score = comp["mr_score"][last] if np.isfinite(comp["mr_score"][last]) else 0.0
    if phase == "uptrend_pullback":
        return {"phase": phase, "primary": "comp_vol", "weight": 1.0,
                "primary_score": comp_vol, "gates": []}
    elif phase == "range_swing":
        return {"phase": phase, "primary": "comp_vol", "weight": 0.5,
                "primary_score": comp_vol, "secondary": "mr_score",
                "secondary_score": mr_score, "gates": []}
    else:  # downtrend_decline
        return {"phase": phase, "primary": "mr_score", "weight": 0.3,
                "primary_score": mr_score, "gates": ["extreme_only"]}
```

### 验收
- 4-1a: `signal_combo.py` 实现 + 3 项单元测试
- 4-1b: 组合 vs 单一 comp_vol 回测对比
- 4-1c: 组合 Sharpe > 0.08，max_dd < 75%

---

## 8. P4 完整任务清单

| 任务 | 内容 | 验收 |
|---|---|---|
| 4-1 信号组合架构 | `signal_combo.py`，分 phase 分工 | Sharpe > 0.08 |
| 4-2 生产接入 | `analyze_stock()` 新增 combo 字段 + SKILL.md | 字段完整 |
| 4-3 持久化落盘 | `store.py`（SQLite） | CRUD 可用 |
| 4-4 模拟盘验证 | `paper_trader.py`，样本外 1 年 | Sharpe > 0.3, max_dd < 30% |
| 4-5 风控监控 | 仓位/止损/日终 | 监控面板 |

---

## 9. 风控参数（已固化到 score_config.py）

```python
SIGNAL_THRESHOLDS = {"strong_buy": 75.0, "buy": 60.0, "hold": 45.0, "wait": 30.0}
GATES = {"rr_ratio_min": 1.5, "unlock_block_pct_30d": 5.0}
LIMIT = {"STAR_ChiNext_BSE_pct": 20.0, "main_pct": 10.0, "ST_pct": 5.0}
COSTS = {"cn_a": (0.025, 0.075), "cn_hk": (0.12, 0.12), "us": (0.02, 0.02)}
```

---

## 10. 关键 Git Commits

```
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
python3 -m pytest tests/ -q          # 26 passed
python3 references/p0_backtest.py    # P0 全量（缓存 <2 min）

# 4. 开始 P4-1
# → 新建 references/signal_combo.py
# → 按第 7 节设计实现
# → 写 3 项单元测试
# → 回测对比
```

---

## 12. 注意事项

1. **不要修改 `stock_data_fetcher.py` 的核心评分逻辑**（`_DEFAULT_WEIGHTS` / `calc_trend_score`）除非有新的 IC 证据——A/B 已证明调权无效
2. **不要在下坡段依赖动量总分**——IC 为负，权重只能改幅度不能改符号
3. **comp_vol 是唯一稳健信号**——任何新组件必须与它对比
4. **样本外验证是闸门**——P4-4 的 2025-09~2026-09 样本外 Sharpe > 0.3 是上线前提
5. **缓存 `.p0_cache/` 已 gitignore**——冷启动 <2 分钟，不要提交
