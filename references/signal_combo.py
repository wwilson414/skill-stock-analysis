#!/usr/bin/env python3
"""P4-1: phase-aware signal combination (ROADMAP P4).

Builds a signal that *switches* the primary driver by market phase instead of
mixing everything into one momentum total score. Each phase picks the P1-validated
signal with the best segmented IC and applies the P4-1 division-of-labour table:

  | 市况 (phase)          | 主信号    | 辅助过滤                     | 仓位权重 |
  |-----------------------|-----------|------------------------------|----------|
  | uptrend_pullback      | comp_vol  | mom > 50 确认动量            | 100%     |
  | range_swing           | comp_vol  | mr_score 极端值（加分）       | 50%      |
  | downtrend_decline     | mr_score  | RSI2<10 + 乖离>10% + 止跌    | 30%      |

Design rules
------------
* phase is re-derived per bar via calc_pullback_context (same classifier the
  momentum backtest uses), so combo and the P0/P1/P2 phase slices are comparable.
* comp_vol / mr_score come from mr_signal.compute_components (P1-5/P1-6,
  no-lookahead arrays indexed by bar).
* combo_score = weight * primary_score when the phase's hard gate passes,
  otherwise None (the candidate is excluded). range_swing adds a small boost
  when mr_score is extreme (the secondary filter of the division table).
* momentum_confirm on uptrend_pullback prefers a caller-provided mom score
  (e.g. the momentum score_total aligned to the bar); without it, a 20d
  positive drift is used as fallback.
* Thresholds live in score_config.COMBO (single source of truth, P3-14).

API
---
  compute_combo_signal(ohlcv, mom_scores=None, bench_closes=None) -> dict
      One-bar decision (for production entry points).
  combo_signal_series(ohlcv, mom_scores=None, min_bars=None) -> list
      Per-bar decisions aligned to ohlcv (for backtests). warmup bars are
      None; compute_components is evaluated once, phases are re-derived per
      bar -> O(n * window), not O(n^2).
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mr_signal import compute_components
from score_config import COMBO
from stock_data_fetcher import calc_ma, calc_pullback_context

PHASES = ("uptrend_pullback", "range_swing", "downtrend_decline")
CONTEXT_KEYS = ("range_pos_pct", "chg_20d_pct", "off_high_pct", "dist_ma60_pct")


def _safe(v, default=0.0):
    """float(v) with NaN/inf -> default."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


def _mom_value(mom_scores, i):
    """Caller mom score at bar i, or None when unavailable/not a number."""
    if mom_scores is None or i >= len(mom_scores):
        return None
    v = _safe(mom_scores[i], np.nan)
    return None if not np.isfinite(v) else v


def _assemble_combo(phase, ctx, comp, i, mom=None):
    """Assemble the combo signal dict for component bar i under `phase`.

    Pure function over pre-computed arrays (used by both entry points).
    Returns None combo_score when the phase's hard gate blocks the signal.
    """
    comp_vol = _safe(comp["comp_vol"][i])
    mr_score = _safe(comp["mr_score"][i])
    mr_event = bool(comp["mr_event"][i])   # RSI2<=10 & dev60<=-10% & 止跌确认

    gates, results = [], {}
    blocked = False
    extra = 0.0

    if phase == "uptrend_pullback":
        primary, primary_score = "comp_vol", comp_vol
        weight = COMBO["weight_uptrend"]
        secondary, secondary_score = "mr_score", mr_score
        gates = ["momentum_confirm"]
        if mom is not None:
            results["momentum_confirm"] = mom >= COMBO["mom_confirm_min"]
        else:
            chg20 = ctx.get("chg_20d_pct")
            results["momentum_confirm"] = (
                chg20 is not None and chg20 >= COMBO["chg20d_confirm_min"])
        blocked = not results["momentum_confirm"]
    elif phase == "range_swing":
        primary, primary_score = "comp_vol", comp_vol
        weight = COMBO["weight_range"]
        secondary, secondary_score = "mr_score", mr_score
        gates = ["mr_extreme"]
        results["mr_extreme"] = mr_score >= COMBO["mr_extreme_min"]
        if results["mr_extreme"]:
            extra = COMBO["mr_extreme_boost"]
    else:   # downtrend_decline
        primary, primary_score = "mr_score", mr_score
        weight = COMBO["weight_downtrend"]
        secondary, secondary_score = "comp_vol", comp_vol
        gates = ["extreme_only"]
        # 分工表辅助过滤：RSI2<10 + 乖离>10%；且均值回归买入须在止跌日确认
        # （c_stop 组件语义）→ 即 P1-5 事件规则 mr_event
        results["extreme_only"] = mr_event
        blocked = not results["extreme_only"]

    if blocked:
        combo_score = None
    elif phase == "range_swing":
        combo_score = weight * primary_score + extra
    else:
        combo_score = weight * primary_score

    return {
        "phase": phase,
        "primary": primary,
        "primary_score": round(primary_score, 4),
        "weight": float(weight),
        "secondary": secondary,
        "secondary_score": round(secondary_score, 4),
        "gates": gates,
        "gate_results": results,
        "gate_blocked": blocked,
        "combo_score": round(combo_score, 4) if combo_score is not None else None,
        "context": {k: ctx.get(k) for k in CONTEXT_KEYS},
    }


def compute_combo_signal(ohlcv, mom_scores=None, bench_closes=None):
    """One-bar phase-aware combo signal.

    ohlcv rows: {date, open, close, high, low, volume} (mr_signal format).
    mom_scores: optional array aligned to ohlcv with momentum total scores
    (e.g. score_total); used by the uptrend momentum_confirm gate.
    bench_closes: reserved for future benchmark features (interface parity
    with stock_data_fetcher), not consumed today.
    """
    closes = [b["close"] for b in ohlcv if b.get("close") is not None]
    if len(closes) < 25:
        return {"phase": "insufficient_data", "primary": None,
                "primary_score": None, "weight": None, "secondary": None,
                "secondary_score": None, "gates": [], "gate_results": {},
                "gate_blocked": True, "combo_score": None, "context": {}}
    last = len(ohlcv) - 1
    ma = calc_ma(closes, [5, 10, 20, 60])
    ctx = calc_pullback_context(closes, ma)
    phase = ctx.get("phase", "range_swing")
    mom = _mom_value(mom_scores, last)
    comp = compute_components(ohlcv)
    return _assemble_combo(phase, ctx, comp, last, mom)


def combo_signal_series(ohlcv, mom_scores=None, min_bars=None):
    """Per-bar combo signals aligned to ohlcv (warmup bars are None).

    compute_components runs once over the whole series (no look-ahead);
    the phase classifier is re-evaluated for each bar on its own window,
    matching the momentum backtest's per-bar phase decision.
    """
    if min_bars is None:
        min_bars = int(COMBO["min_bars"])
    n = len(ohlcv)
    if n < 61:
        return [None] * n
    comp = compute_components(ohlcv)
    closes = [b["close"] for b in ohlcv if b.get("close") is not None]
    out = [None] * min_bars
    for i in range(min_bars, n):
        w_closes = closes[:min(i + 1, len(closes))]
        if len(w_closes) < 25:
            out.append(None)
            continue
        ma = calc_ma(w_closes, [5, 10, 20, 60])
        ctx = calc_pullback_context(w_closes, ma)
        phase = ctx.get("phase", "range_swing")
        mom = _mom_value(mom_scores, i)
        out.append(_assemble_combo(phase, ctx, comp, i, mom))
    return out