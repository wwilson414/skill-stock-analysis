# Stock-Analysis Skill — 决策记录（Decision Log）

> **定位**：记录本项目所有**已定案的方法论 / 架构 / 产品决策**及其证据与拒绝项。
> 与其他文档的分工：`README.md` = 使用者入口；`SKILL.md` = Agent 工作流；`HANDOFF.md` = 当前状态 / 待办 / 优化方向 / 研究档案（§16，原 ROADMAP）；**本文件 = 为什么是这样**。
> **变更规则**：新增或修改决策必须附证据（报告文件或实验数字）与日期；被推翻的决策**不删除**，改标 ❌ 并注明推翻证据。

**图例**：✅ 生效 | ⚠️ 有条件生效 | ❌ 已否决 / 关闭

---

## D1 ✅ 信号排序以 combo_score 为准，动量总分仅作 phase 内展示

- **背景**：P1-7 证明动量总分无 out-of-time 概率信息（Brier 0.24911/0.24984/0.24918 全部劣于常数基线 0.24857）；P0 证明总分与 20d 前向收益负相关（IC -0.0405）。
- **决策**：dashboard 与组合排序使用 `combo_score`（分 phase 主信号 × 权重）；总分保留为展示层与 uptrend `momentum_confirm` 的输入。
- **证据**：`reports/calibration_20d.json`、HANDOFF §16.2（原 ROADMAP P1-7）。**日期**：2026-09-08（P1 关闭）。

## D2 ✅ downtrend_decline 阶段禁买（脚本硬闸门 + combo extreme_only）

- **背景**：P0 下跌段 IC -0.0662；P4-1 A/B 证明 downtrend gate 必须含止跌日（去掉后 net Sharpe 0.059→0.046）。
- **决策**：`buy_gates` 强制包含 phase 闸门；combo 侧 `extreme_only`（RSI2≤10 + 距 MA60 ≥10% + 止跌日），权重 0.3。
- **已知代价**：OOS downtrend 段交易数为 0（保守）→ 待优化项 O-9。**日期**：2026-09-10。

## D3 ✅ 所有验收以 net 执行口径为准（四口径全输出，open 仅参考）

- **背景**：P2 证明 mr_score 毛收益 +11.37% → 净 -7.16%（费用杀伤 18.53pp）；四口径结论方向一致。
- **决策**：佣金/印花税/滑点/涨跌停建模进验收；任何"毛收益好看"不构成通过依据。**日期**：2026-09-09。

## D4 ❌ 已否决：按 IC 调整评分权重

- **证据**：A/B（同一份数据）suggested ≈ default（score IC -0.2055 vs -0.2084），且全部为负 IC——**符号问题调权改不了**。
- **现状**：`--calibrate` / `--ab-test` 保留为诊断工具；不自动落地权重；`score_config.GATES.ic_min_magnitude` 防呆。**日期**：2026-09-08。

## D5 ❌ 已关闭：profit_factor > 1.5 目标

- **证据**：样本内网格（sl∈{8,10,12%} × min_sig∈{0,0.3,0.5}）唯一 PF>1.5 的组合（sl=10%/ms=0.5，PF 1.537）代价是年化 -1.9%、Sharpe -0.15；样本内最优组合（Sharpe 0.717）样本外确认 PF 1.345 < 基线 1.405——**PF 在 65-69 笔上是噪声指标**。
- **现状**：PF 保留为诊断指标；实测 1.405。**日期**：2026-09-11。

## D6 ✅ coverage 口径修订：日频 60% → ≥20%（每 5 个交易日 ≥1 只有信号）

- **依据**：低频设计（combo gate 严 + downtrend 仅止跌日）天然达不到日频；原 60% 目标与设计矛盾。
- **结果**：OOS 实测 20.5% 通过。**日期**：2026-09-11。

## D7 ✅ 风控参数固化（score_config.RISK）并默认启用（use_risk=True）

- **参数**：单股 ≤20%、单 phase ≤60%、个股 -8% 止损、组合 -15% 熔断。
- **实证**：OOS 止损 14 笔（占交易 20%）、risk_blocked 36 次、熔断 0 次；max_dd 样本内 74.8% → 样本外 7.31%。**日期**：2026-09-11。

## D8 ✅ 样本外闸门与失效条件明文化

- **闸门**：Sharpe > 0.3 且 max_dd < 30%（2025-09~2026-08 实测 **0.54 / 7.31%**，4/4 达成）。
- **失效条件**：任何配置/参数变更后必须重跑样本外闸门，不达标即停用新版；闸门口径以 HANDOFF §16.6 P4 验收表为准。**日期**：2026-09-11。

## D9 ✅ 验证方法论作为一切后续研究的强制流程

- **流程**：先验设计（零拟合）→ 冻结股票池 → `--universe random` holdout 复现 → 样本内调参 → 样本外验收。
- **反例警示**：离散事件版均值回归（RSI2≤10 且乖离≤-10% 且止跌）仅 27 次触发、事件均值 -2.17%，被否决——改用连续分数。
- **含义**：任何"提高 coverage / PF / 让曲线好看"的调参默认拒绝（见 D4/D5 先例）。**日期**：2026-09-08 起。

## D10 ✅ 数据链路硬化方案（2026-09-14）

- THS 429/5xx 退避重试（≤3 次、尊重 `Retry-After`、上限 6s）+ 网络错误补 1 次；
- 中文名检索失败回退 akshare 免费代码表（精确匹配优先，多命中不猜测）；
- `stock_news_em` 中文列名别名兼容 + 空载荷行过滤 + 空结果重试 1 次再降级；
- `_ensure_reference_modules()` 模块自动定位（`$SDF_REFERENCES_DIR` → 脚本目录 → `<script>/references` → `<cwd>/references` → 向上逐级）。

## D11 ✅ 文档体系收敛（2026-09-15）

- `NEXT_STEPS.md` 内容并入 `HANDOFF.md`（§14 收尾修复 / §15 优化方向）后删除；
- `ROADMAP.md` 并入 `HANDOFF.md` §16 研究档案后删除（2026-09-15）；
- 新增本文件（DECISIONS.md）；四份文档分工见文首"定位"。

---

## D12 ✅ 估值（P/E、P/B）兜底链与源优先级（2026-09-16）

- **背景**：O-1——THS valuation snapshot 偶发 429，或价格来源本身不带估值时，卡片 P/E、P/B 显示 N/A。
- **决策**：按市场定义回退链，**首个非空结果胜出**，单源异常只记日志不中断；真正填充时记 `realtime.valuation_source`（只填空缺，不覆盖已有值）：

  | 市场 | 链序 |
  |---|---|
  | A 股 | tencent → akshare → efinance → yfinance |
  | 港股 | yfinance → tencent → akshare |
  | 美股 | yfinance → tencent |

- **A 股为何腾讯优先**：腾讯单票接口 0.1–0.2s 返回动态 P/E + P/B（stdlib、无需 key）；东财系在本机不可用（`stock_zh_a_spot_em()` 连 push2 35.5s 后 ConnectionError、`efinance.get_base_info()` JSONDecodeError），且全市场快照远贵于单票接口。
- **港股为何 yfinance 优先**：腾讯港股行情只有动态 P/E、无 P/B（实测 0700.HK），yfinance 约 2s 给全 P/E + P/B。
- **证据**：`tests/test_valuation_fallback.py` 24 项 + `tests/test_bar_partial.py` 22 项 + 全套 142 passed；patch `_fuyao_get` 强制 valuations 429 的端到端验证（A 股 PE/PB 齐全、`valuation_source=tencent`；港股禁用东财源后 P/B 由 yfinance 补）；2026-09-16 三次连续实跑 P/E、P/B 齐全（含 HK00700 实测腾讯给 P/E 15.89、yfinance 补 P/B 2.95，`valuation_source=yfinance`）。详见 HANDOFF §15.1。

---

## D14 ✅ vol_ratio 盘中口径：全日等价折算 + `bar_partial` 标注（2026-09-16）

- **背景**：O-2——盘中运行时当日 bar 未走完，`vol_ratio = 当日部分量 / 前 5 日全日均值` 被系统性低估（华能国际 14:24 → 0.60、苏泊尔上午 → 0.16），卡片无提示。
- **决策**：`analyze_stock` 实盘路径按交易所时区计算已交易时段占比（A股 240min / 港股 330min / 美股 390min，zoneinfo 处理夏令时），当日量先折算成全日等价量再算 vol_ratio；JSON 输出 `bar_partial` / `session_elapsed_pct` / `vol_ratio_raw`，卡片 Volume 行加折算标注。**回测路径不变**（`compute_signal_from_ohlcv` 不传 frac，历史信号语义与研究数字零变化）。
- **证据**：实跑 14:37（盘中）三市场标记正确——A股 elapsed 90.4%、港股 74.8%（各自时段公式吻合）；raw→adjusted 0.53→0.59 / 0.81→0.90 / 0.49→0.65；单测 22 项含"回测路径不变性"。详见 HANDOFF §15.3。**日期**：2026-09-16。

---

## D13 ✅ A 股数据源腾讯优先（K 线 + 实时行情，2026-09-16）

- **背景**：东财系端点（akshare spot / efinance）在本机长期不可达（35s 超时 / JSONDecodeError），THS 官方 API 需 key 且偶发 429，THS 免费 last.js 出现连接重置；腾讯（qt.gtimg.cn / fqkline）0.1–0.3s 稳定、仅 stdlib、免 key、无限流。
- **决策**：A 股 K 线链改为 Tushare(有 token) > **腾讯 fqkline** > THS 官方 API > efinance > THS > akshare > yfinance；港股 K 线链改为**腾讯 fqkline** > efinance > akshare > yfinance（原 efinance 优先在本机每次先撞 ~35s 超时）；A 股实时行情链改为**腾讯单票** > THS 官方 API > akshare > efinance > THS > yfinance；港股实时行情链改为**腾讯单票**（P/B 走估值兜底链）> efinance > akshare > yfinance。腾讯 volume 由手 ×100 换算为股与其它源对齐。
- **口径证据**：与 THS 官方 qfq 序列交易日 120/120 对齐、历史 close 差 ≤1 分钱（双方舍入差）；`analyze_stock` 全字段扁平 diff 中信号级输出（phase / signal / combo_score）一致，vol_ratio 一致（volume 仅进比值，单位不变）。盘中 bar 的分钟级漂移属实时源固有，非源差异。港股端到端实测 **2 秒**（原 ~5 分钟）。
- **边界**：腾讯美股 K 线仅 NASDAQ（.OQ）、usSPY 不可用（§6）→ 美股保持腾讯优先 + yfinance 兜底不变；p0 K 线缓存键 `_v2`→`_v3`，换源后 harness 整体重抓、不混用旧缓存，既有 `reports/*.json` 存档数字不变。详见 HANDOFF §15.2。**日期**：2026-09-16。

---

## 优化决策（2026-09-15 评审定稿，行动清单见 HANDOFF §15）

- ✅ **采纳，立即做（P1，确定性缺口）**：估值字段兜底（O-1 已于 2026-09-16 完成 ✅）/ 盘中 bar 标记与 vol_ratio 口径（O-2 已于 2026-09-16 完成 ✅，见 D14）/ 实时名称回填 / 解禁替代源 / 卡片硬闸门行 / `daily_update.py` 日常自动化。
- ⚠️ **有条件采纳（P2，走 D9 流程）**：mom_confirm 换裸 20 日动量或 OBV / 组件级概率校准驱动仓位 / downtrend 死分支放宽（研究）或删除（简化）/ 新闻事件分类与公司专属过滤 / 按 phase 差异化持有期。
- ❌ **默认拒绝**：为提高 coverage / PF / 曲线美观而调参（D4/D5 先例）；用总分做概率或跨 phase 比较（P1-7 证伪）。
