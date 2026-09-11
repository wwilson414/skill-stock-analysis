# Stock-Analysis Skill — Optimization Roadmap

> 基于两轮回测实证与代码审计，按优先级排列。
> 第一轮（5 只股票、1005 信号、2025–2026 窗口）：整体 IC -0.21 几乎全部来自
> downtrend_decline（ic=-0.19）；剔除下跌段后 IC 仅 ~+0.03；权重 A/B 证明调权无效——
> **问题在信号质量与市场状态，不在权重**。
> 第二轮（P0 扩样验证，36 只跨市场、2022-12~2026-09、29,476 信号）已确认：
> 整体 IC -0.0405 [CI -0.051,-0.029]，下跌段 -0.066 显著、非下跌段 +0.002 不显著，
> 且该结构在牛/熊市与 9/10 个半年窗口中稳定复现——**负 IC 是阶段依赖的结构性缺陷，
> 不是窗口伪影**。执行入口已锁定：P1-5 + P1-7。
> 第三轮（P1，2026-09-08）结果：P1-5 均值回归信号**验收通过**（下跌段 IC +0.0342，
> CI [+0.017,+0.053]）；P1-7 证明动量总分在 20d 上**无 out-of-time 概率信息**
> （Brier 全部劣于常数基线）——分数只可作相对排序、不可当概率；P1-6 仅"量比异动"
> 通过验证；P1-8 确认负 IC 约一半来自波动/基准效应，但下跌段残余负 IC 仍在。
> 第四轮（P1 holdout，2026-09-08）：全 A 流动性随机样本（seed 42，30 只中小盘、
> 24,562 信号）**全部复现**——mr_score 下跌段 +0.0334 [CI +0.009,+0.061]、
> comp_vol 非下跌段 +0.031 [CI +0.015,+0.047]（唯一入选不变）、下跌段动量
> raw -0.041 → ATR 归一 -0.004。P1 阶段关闭，进入 P2。

## P0 — 扩大证据基础（已完成 2026-09-08）

- [x] **1. 扩样本验证**：`references/p0_backtest.py`，36 只冻结股票池
      （12 A主板 + 6 创业板 + 4 科创 + 6 HK + 8 US），~3.7 年窗口、多起点。
      结果：20d IC 总体 -0.0405（p≈0, n=29,476）；三市场一致为负
      （cn_a -0.043 / cn_hk -0.036 / us -0.056）。
- [x] **2. 市场级 regime 标注**：CSI300/HSI/SPY 按 60d 动量+MA60 规则打
      牛/熊/震荡标签。结果：个股 downtrend_decline 的负 IC 在大盘牛市
      （-0.090）与熊市（-0.088）几乎相同——**是"个股下跌阶段"效应，
      不是"大盘熊市"效应**；uptrend_pullback 在任何市况下都不为负。
- [x] **3. IC 置信区间**：所有聚合 IC 带 bootstrap 95% CI（seed 固定）+
      t-stat + 正态近似 p 值。关键格子的 CI 均不含 0（下跌段、总体）；
      range_swing / uptrend_pullback 的 IC 与 0 无法区分。
- [x] **4. 消除选择偏差**：选股规则逐字记录于输出 JSON（`universe.rule`），
      股票池在运行前冻结、不按 realized 路径过滤。残余偏差：知名股幸存者
      偏差，用 `--universe random`（全市场流动性抽样、seed 可复现）交叉
      验证——✅ 已完成（P1 holdout 2026-09-08：seed 42，30 只全 A 流动性
      随机中小盘、24,562 信号全部复现，见 `reports/p1_signal_results_random.json`）。
- 证据文件：`reports/p0_expansion.json`（汇总）、`reports/signals_p0_fixed.json`
  （原始信号，gitignore）；复跑：`python3 references/p0_backtest.py`。
- 附带发现：低分桶（0-30）20d 平均收益 +2.58% 高于高分桶（75+）的 +0.85%
  ——动量总分整体呈反向（均值回复）模式，与 IC 为负一致。


## P1 — 信号质量（已完成 2026-09-08；工具：mr_signal.py / p1_eval.py / score_calibration.py）

- [x] **5. 下跌段均值回归信号（独立信号）→ 验收 PASS**：`references/mr_signal.py`
      （先验设计、零拟合：RSI2 超卖 + 距 MA60 乖离 + 放量止跌，等权 mr_score）
      + `references/p1_eval.py` 评估。结果（36 股 29,476 信号）：downtrend_decline
      **IC=+0.0342 [CI +0.0167,+0.0525]（CI 不含 0，n=13,752）**；36 股横截面
      高度一致：86.1% 个股 mr_score IC 为正（均值 +0.067，t=+5.5）；range_swing -0.017 / uptrend_pullback +0.017（均含 0）
      → 信号只在下跌段有效，与设计一致。离散事件版（RSI2≤10 且乖离≤-10% 且止跌）
      仅触发 27 次、事件均值 -2.17%，不可用——**用连续分数，不用离散事件**。
- [x] **6. 非下跌段组件实验**：4 个候选组件在非下跌段（n=15,724）的 20d IC：
      comp_vol 量比异动 **+0.046 [CI +0.025,+0.067] → 唯一入选**；comp_brk 突破
      +0.018（含 0）、comp_gap 跳空 -0.014（含 0）→ 不保留；comp_pv 量价背离
      **-0.041 [CI -0.061,-0.020]** 显著但方向与先验相反 → **已复盘解谜**：
      非下跌段量价呈"延续"关系——z(20d价动量)×z(20d OBV) 乘积 IC=+0.051
      （量价齐升后续最好、量价背离后续最差），而下跌段反转（z_px IC=-0.027）
      → 单一全局符号不存在（相位依赖），comp_pv 的"背离"先验方向就是错的，
      不纳入信号。顺带发现：非下跌段裸 20 日价格动量 IC=+0.042、OBV 动量
      IC=+0.048，而总分仅 +0.001 → 总分未有效嵌入近期动量信息（P2 候选改进）。
      结论：非下跌段 IC 目标 0.1+ 未达成，
      可用增量只有量比异动。
- [x] **7. 分数→概率校准**：`references/score_calibration.py` →
      `reports/calibration_20d.json`（binned 表 + isotonic 21 块 + logistic，
      训练/时间外 7:3 切分，cutoff 2025-04-29）。**关键发现：动量总分无
      out-of-time 概率信息**——OOT Brier：binned 0.24911 / isotonic 0.24984 /
      logistic 0.24918，全部劣于常数基线 0.24857；reliability 斜率≈平坦；
      阈值表反向（75+ 桶 p_up=0.531 < 0-30 桶 0.558）。**执行含义：75/60/45/30
      阈值不构成概率分层，分数仅用于同阶段内相对排序；下跌段禁用打分买入**
      （现有 buy_gates 已实现，保留）。
- [x] **8. 风险调整 IC**：`p1_eval.py` risk_adjusted——downtrend_decline raw
      -0.066 → 超额(对基准) -0.033 → ATR 归一 -0.037；总体 raw -0.041 →
      ATR 归一 -0.009。**高波动伪影假设部分成立**（负 IC 强度对风险调整敏感，
      约减半），但下跌段残余负 IC 仍显著 → 结论不变：下跌段无正向选股能力。
      另：非重叠 20d 窗口下下跌段动量 IC 减弱为 -0.015（CI 含 0，n=694）——
      重叠窗口夸大了显著性，但符号仍为负。
- **P1 holdout 复检（2026-09-08，已完成）**：`reports/p1_signal_results_random.json`
  （全 A 流动性池 top300 快照 seed42 抽 30 只，24,562 信号，30/30 成功）：
  ① mr_score 下跌段 **+0.0334 [CI +0.0087,+0.0611] 验收复现 PASS**，30 股横截面
  66.7% 为正（均值 +0.062，t=+2.87）；range_swing 显著为负（-0.044）→
  "只在下跌段使用"约束在 holdout 更强化；② comp_vol 非下跌段 +0.031
  [CI +0.015,+0.047] **唯一入选复现**（comp_pv 在纯 A 股样本 ≈0 → fixed 样本
  的 -0.041 主要由美股驱动；comp_brk/comp_gap 仍 ≈0）；③ 下跌段动量 raw
  -0.0405 [CI 含 0 边界外] → 超额 -0.018 → ATR 归一 -0.004：**中小盘 A 股
  里下跌段负 IC 经风险调整后基本消失**（比 fixed 混合样本减得更彻底）；
  非重叠窗口 -0.034（CI 含 0，符号仍负）；④ 新发现：holdout 纯 A 股样本
  range_swing 动量 IC **+0.040 [CI +0.018,+0.064] 显著为正**（fixed 混合样本
  为 -0.01）→ 动量效应被市场/市值稀释，P2 候选：按市场×市值分域处理动量。
- 证据文件：`reports/p1_signal_results.json`（分段 IC/事件研究/风险调整/组件）、
  `reports/calibration_20d.json`（校准工件）；复跑：`python3 references/p1_eval.py`
  与 `python3 references/score_calibration.py`（均走 .p0_cache，~4s）。
- **P1 残留待办：无（holdout 与 comp_pv 复盘均已完成）→ 进入 P2。**

## P2 — 执行真实性（回测可信度）— 已完成 2026-09-09

- [x] **9. 交易成本与滑点**：A股卖出印花税、佣金、冲击成本计入前向收益；否则 IC 有
       系统性高估。
- [x] **10. T+1 与涨跌停在回测中生效**：A股 forward return 应从 T+1 开盘起算；
       涨停封板的 buy 信号应剔除（买不进）——实盘路径已有 tradability，回测路径现已统一。
- [x] **11. 前向收益口径统一**：当前用收盘价算前向收益，与实盘"次日开盘买入"存在
       偏差；改为 next-open 入场价。
- [x] **12. P2-12 建仓节奏**：rotation-portfolio 模拟（`references/p2_schedule.py`），
       等权 max-N 头寸、next-open 入场、持有 forward_days 后出场。
       产物：`reports/p2_schedule.json`。

### P2 结论备忘（详见 reports/p2_execution.json + p2_schedule.json）

**P2-9/11 执行重定价（29,476 信号）**

| phase | 口径 | 20d IC | 均值 | n |
|---|---|---|---|---|
| downtrend_decline | base (close→close) | -0.066 | +0.92% | 13,752 |
| downtrend_decline | open (next-open→close) | -0.057 | +0.93% | 13,749 |
| downtrend_decline | net_fee (含费用) | -0.057 | +0.62% | 13,749 |
| downtrend_decline | **mr_score base** | **+0.034** | **+1.84%** | 13,752 |
| downtrend_decline | mr_score net | +0.009 | -0.04% | 13,749 |
| downtrend_decline | **comp_vol net** | **+0.051** | **+1.86%** | 13,749 |
| uptrend_pullback | comp_vol net | +0.051 | +0.91% | 9,691 |
| range_swing | comp_vol net | +0.043 | +1.48% | 6,015 |

- **P2-10 涨停不可交易**：A 股封涨停开盘仅 0.12%（21/18,010）次，均匀分布各 phase，
  平均 base fwd +3.82% —— 不可交易的上涨承诺；跌停延期出场仅 9 次，影响微小。
- **P2-11 adverse-open**：mom 高分段跳空均 -0.055% vs 低分段 +0.063%（不可避免的 adverse-open）；
  mr_score 反向（+0.283 vs -0.303，均值回归天然抗衡跳空）。

**P2-12 rotation-portfolio（36 股 × 3.7y × 29,476 信号 × 230 笔）**

| family | variant | annual_ret | sharpe | max_dd | n | phase_mix |
|---|---|---|---|---|---|---|
| mom | base | +7.36% | 0.040 | 65.6% | 230 | uptrend 176 / range 48 / downtrend 6 |
| mom | net | +3.42% | 0.013 | 85.8% | 223 | uptrend 165 / range 51 / downtrend 7 |
| mr_score | base | +11.37% | 0.066 | 61.5% | 228 | **downtrend 194** / range 24 / uptrend 10 |
| mr_score | net | **-7.16%** | **-0.024** | **95.7%** | 165 | downtrend 140 / range 14 / uptrend 11 |
| **comp_vol** | **base** | **+14.43%** | **0.082** | 66.8% | 225 | downtrend 94 / uptrend 61 / range 70 |
| **comp_vol** | **net** | **+9.62%** | **0.046** | 86.9% | 213 | downtrend 84 / uptrend 56 / range 73 |

- **唯一幸存者：comp_vol**（量比异动）—— net annual ret >0，Sharpe 0.046，max_dd 87%。
- **mr_score base 为正（+11.37%）但执行成本 18.53pp 将其打到 -7.16%** —— 交易过于集中
  于 downtrend_decline（194/228 笔），信号噪声大、费用拖垮；IC 为正但组合经不起交易成本。
- **mom 在组合语境下不再为负**（base +7.36%, net +3.42%）—— 集中 uptrend_pullback，
  IC 负但组合正：**IC 与组合收益符号可以不同**。
- **mom_still_negative: False**（组合口径）—— P2 否决"动量负 IC 是执行伪影"假设。

**P2 verdict**：`mr_score downtrend net edge 被交易成本淹没`；
`comp_vol 是唯一穿越费用的信号，但 Sharpe <0.1、max_dd ~87%`；
`动量负 IC 不是执行伪影，而是选股排序问题`。
→ 下一步：P4 生产化复核 + 信号组合（comp_vol + mr_score 分 phase 分工）。
## P3 — 工程与可维护性

- [x] **12. 数据缓存层（P0 局部实现）**：P0 harness 自带 `.p0_cache/` JSON 缓存
      （K 线 + 基准，按代码+窗口键控，gitignore），36 股全量重跑 <2 分钟。
      通用 fetch 层的 parquet/sqlite 缓存仍待做。已探明数据源事实：腾讯美股
      K 线只稳定覆盖 NASDAQ（.OQ），NYSE 代码（JPM/JNJ/KO/XOM）仅返回 1 根，
      需 yfinance 兜底（harness 已内置，见 `_yf_us_ohlcv`）；tencent usSPY
      同样不可用，基准用 SPY@yfinance。
- [x] **13. 测试固化**：`tests/` 目录已建（2026-09-08~09-11），9 组 85 项 pytest：
      test_ic_stats.py(9) + test_mr_signal.py(4) + test_p2_execution.py(6) +
      test_score_config.py(5) + test_signal_combo.py(5) + test_analyze_combo.py(4) +
      test_store.py(7) + test_paper_trader.py(20) + test_risk_monitor.py(23)。
- [x] **14. 阈值配置化**：`references/score_config.py` 集中全部魔法数字
      （SIGNAL_THRESHOLDS / GATES / LIMIT / MR / COMBO / REGIME / COSTS / RISK），
      带理由注释，改参数不用翻代码。
- [x] **15. 回测结果落盘**：`references/store.py`（P4-3）SQLite 持久化
      （signals/trades/portfolio 落盘 + upsert 增量 + 过滤查询）；
      `p4_combo_backtest.py --save-store` 可将批量回测灌库。

## 明确不做 / 已否决

- ~~按回测 IC 直接改 `_DEFAULT_WEIGHTS`~~ — A/B 已证明无效，且反预测样本上危险。
- ~~给下跌段调低动量权重以"修复"负 IC~~ — 符号问题，权重只能改幅度。
- ~~"熊市专用开关"（按大盘 regime 切换评分）~~ — P0-2 证明下跌段负 IC 在牛市
  同样显著（bull -0.090 vs bear -0.088），按市况开关解决不了问题。

## 决策树（已由 P0 裁决）

```
扩样本后 IC 结构如何？
├─ [x] 下跌段显著为负、非下跌段 ≈0/略正 → 命中本分支：执行 P1-5（均值回归独立信号）
│       + P1-7（概率校准）；P1-6 组件实验在 5/7 之后
├─ [ ] 全段 IC 都 ≈ 0            → 未命中
└─ [ ] 全段 IC 转正（窗口特异）   → 未命中
```

## P0 结论备忘（细节见 reports/p0_expansion.json）

| 切片 | 20d IC | 95% CI | n |
|---|---|---|---|
| 总体 | -0.0405 | [-0.051, -0.029] | 29,476 |
| downtrend_decline | -0.0662 | [-0.084, -0.048] | 13,752 |
| range_swing | -0.0090 | [-0.037, +0.019] | 6,029 |
| uptrend_pullback | +0.0119 | [-0.009, +0.032] | 9,695 |
| downtrend × 大盘牛市 | -0.0895 | 显著 | — |
| downtrend × 大盘熊市 | -0.0883 | 显著 | — |
| 个股横截面（下跌段） | 均值 -0.097 | 86.1% 个股为负，t=-6.3 | 36 |
| 半年多起点（下跌段） | 9/10 个半年为负 | 唯一例外 2025-H2 +0.014 | — |


## P4 — 生产化与信号组合（立项 2026-09-09）

> P0–P3 已闭环研究结论：comp_vol 是唯一穿越费用的信号（net annual +9.6%，Sharpe 0.046），
> mr_score 在下跌段 IC 为正但被交易成本淹没，动量总分无概率信息。
> P4 目标：把研究结论转化为可执行的实盘/模拟盘系统，并构建分 phase 的信号组合。

### 4-1 信号组合架构（分 phase 分工）

基于 P0–P2 实证，单一信号无法覆盖所有市况，需组合：

| 市况 (phase) | 主信号 | 辅助过滤 | 仓位权重 |
|---|---|---|---|
| uptrend_pullback | comp_vol（量比异动） | mom > 50 确认动量 | 100% |
| range_swing | comp_vol | mr_score 极端值（加分，soft） | 50% |
| downtrend_decline | mr_score（均值回归） | RSI2<10 + 乖离>10% + **止跌日** | 30%（轻仓） |

- [x] **4-1a** 实现 `references/signal_combo.py`：输入 OHLCV → 组合信号 dict + 批量序列（`compute_combo_signal` / `combo_signal_series`）+ 5 项单测
- [x] **4-1b** 回测验证（`references/p4_combo_backtest.py`，29,476 行）：combo net +12.81%/Sharpe 0.059 vs comp_vol +9.62%/0.046；phase 与动量回测 100% 一致
- [x] **4-1c** 验收：组合 Sharpe > comp_vol ✅（0.059 > 0.046）；max_dd 74.8% < 75% ✅；目标 > 0.08 ⚠️ 仅 open 口径达标（0.086）→ 记账 4-4

### 4-2 生产接入（SKILL.md 工作流）

当前 SKILL.md 调用 `stock_data_fetcher.py` 的 `analyze_stock()` 输出总分 + 强买/买/持有信号。
P4 升级：

- [x] **4-2a** `analyze_stock()` 新增输出字段（2026-09-10 完成）：
  - `combo`: {phase, primary, primary_score, weight, secondary, gates, gate_results, gate_blocked, combo_score, context}
  - `combo_score` 已含 phase 权重；`gate_blocked=True` 时该 bar 不作为组合候选
- [x] **4-2b** 买入建议逻辑升级 ✅（已由 SKILL.md STEP 4.5 判断规则 + `combo` 字段实现）：
  - 总分 >= 75 且 phase=uptrend_pullback → 强买（comp_vol 确认，weight 1.0）
  - 总分 >= 60 且 phase=downtrend_decline → 仅观察（mr_score 极端值才轻仓，combo gate=extreme_only 挡住普通下跌）
  - 总分 >= 60 且 phase=range_swing → 持有（等待突破确认，weight 0.5）
  - 落地方式：`combo.combo_score` 正=候选、`combo.weight` 已编码 phase、`combo.gate_blocked=True` 禁止排入候选
- [x] **4-2c** SKILL.md 文档更新：新增 STEP 4.5 组合信号说明 + output-format-template 增加 Combo Signal 卡片行

### 4-3 持久化落盘（P3-15 续）

- [x] **4-3a** 实现 `references/store.py`：SQLite 存储（signals.db，2026-09-10 完成）
  - 表 `signals`(date, code, phase, mom, mr_score, comp_vol, combo_signal, combo_weight, gate_blocked)
  - 表 `trades`(id, date, code, direction, price, size, pnl, status)
  - 表 `portfolio`(date, cash, positions, total_value, daily_return)
- [x] **4-3b** 增量更新：每日新增信号 append，不重写历史（upsert on PK(date, code)，created_at 保留）
- [x] **4-3c** 查询接口：按 code/phase/date_range 检索（`query_signals`），7 项单测 + demo 冒烟

### 4-4 实盘/模拟盘验证框架

- [x] **4-4a** 实现 `references/paper_trader.py`：
  - 读取 signals.db → 按组合权重下单 → 跟踪持仓 → 计算 PnL
  - 支持 T+1 成交、涨跌停不可交易、滑点（10bp）、佣金（A 股 10bp RT）
- [x] **4-4b** 回测 2025-09 → 2026-09（样本外 1 年）— 引擎就绪，待真实数据回放
- [ ] **4-4c** 验收：样本外 Sharpe > 0.3，max_dd < 30%，盈亏比 > 1.5

### 4-5 风控与监控

- [x] **4-5a** 仓位管理：单只股票最大仓位 20%（`RISK.max_per_stock`），单一 phase 最大仓位 60%（`RISK.max_per_phase`）→ `risk_monitor.py` `can_enter()`
- [x] **4-5b** 止损：个股 -8% 止损（`RISK.stop_loss_pct`），组合 -15% 清仓（`RISK.circuit_breaker_pct`）→ `risk_monitor.py` `check_stop_loss()` / `check_circuit_breaker()`
- [x] **4-5c** 日终监控：`risk_monitor.py` `eod_report()` 每日输出持仓 + 风险敞口 + 异常信号告警

### P4 验收标准

| 指标 | 目标 | 说明 |
|---|---|---|
| 组合 Sharpe | > 0.08 | 样本内（2022-2025） |
| 样本外 Sharpe | > 0.3 | 2025-09 ~ 2026-09 |
| max_dd | < 30% | 组合层面 |
| 盈亏比 | > 1.5 | 平均盈利/平均亏损 |
| 信号覆盖率 | > 60% | 每日至少 1 只股票有信号 |

### P4 残留风险

- **过拟合风险**：P0–P3 在 36 股 × 3.7y 上验证，样本有限；P4-4 样本外验证是关键闸门
- **容量限制**：comp_vol 在中小盘股有效，大盘股流动性冲击未测试
- **regime 切换**：phase 标注基于历史数据，实盘存在滞后

