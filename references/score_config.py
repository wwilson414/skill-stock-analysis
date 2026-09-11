#!/usr/bin/env python3
"""Centralized threshold / parameter registry (ROADMAP P3-14).

Single source of truth for scoring, execution, and calibration knobs.
Modules import from here instead of hard-coding magic numbers.
"""
import os

SIGNAL_THRESHOLDS = {
    "strong_buy": 75.0, "buy": 60.0, "hold": 45.0, "wait": 30.0,
}
GATES = {
    "rr_ratio_min": 1.5,            # rr_ratio < this -> hard block buy
    "rs_60d_lag_sp": 5.0,           # relative-strength lag vs bench (pp)
    "support_ma5_pct": 1.0,         # |close-ma5|/close*100 <= this => support
    "support_ma10_pct": 1.5,
    "unlock_block_pct_30d": 5.0,    # >= this of float unlocking -> block
    "unlock_warn_pct_30d": 3.0,
    "ic_min_magnitude": 0.15,       # |total IC| below this -> no weight tuning
    "ic_weak_predictive": 0.10,     # score-corr < this -> weak signal
    "ic_moderate_predictive": 0.30,
    "phase_split_detect": 0.05,     # up_ic>+x & dn_ic<-x flags regime split
}
LIMIT = {
    "STAR_ChiNext_BSE_pct": 20.0,   # 688/689/300-302 + BSE (43/83/87/88/92)
    "main_pct": 10.0,
    "ST_pct": 5.0,
    "seal_buffer_pct": 0.2,         # within 0.2% of limit -> treated sealed
    "near_limit_pct": 0.8,          # >= 80% of limit -> near_limit
}
MR = {
    "rsi2_oversold": 10.0,
    "dev_ma60_oversold": 0.10,       # 10% below MA60
    "down_streak_min": 2,
    "brk_horizon_ref": 0.02,         # 2% breakout reference
    "gap_ref": 0.02,
}
COMBO = {                           # P4-1: phase-aware signal combination
    # 分工表：| phase | 主信号 | 辅助过滤 | 仓位权重 |
    "weight_uptrend": 1.0,          # uptrend_pullback: comp_vol 主，mom 确认动量
    "weight_range": 0.5,            # range_swing: comp_vol 主，mr_score 极端辅助
    "weight_downtrend": 0.3,        # downtrend_decline: mr_score 主，extreme_only
    "mom_confirm_min": 50.0,        # uptrend: mom > 50 才进入组合（动量确认）
    "chg20d_confirm_min": 0.0,      # uptrend fallback: 20d 动量为正（无 mom 分数时）
    "mr_extreme_min": 2.0,          # range_swing: mr_score >= 2 视为极端超卖
    "mr_extreme_boost": 0.5,        # range_swing: 极端超卖时对 combo 的加分
    "min_bars": 61,                 # 组合信号 warmup（与 _MIN_BARS_FOR_INDICATORS 一致）
}
REGIME = {
    "warmup_bars": 60,
    "bull_chg60_min_pct": 5.0,
    "bear_chg60_max_pct": -5.0,
}
COSTS = {                           # per-side, percent of notional
    "cn_a": (0.025, 0.075),         # 2.5bp comm + 5bp sell stamp
    "cn_hk": (0.12, 0.12),
    "us": (0.02, 0.02),
    "slippage_bp_default": 10.0,
}
RISK = {                            # P4-5: risk monitoring thresholds
    "max_per_stock": 0.20,          # single stock max 20% of portfolio
    "max_per_phase": 0.60,          # single phase max 60% of portfolio
    "stop_loss_pct": 0.08,          # individual stock -8% stop-loss
    "circuit_breaker_pct": 0.15,    # portfolio -15% from peak -> liquidate all
}
FORWARD = {"days": [5, 10, 20]}
CACHE_LIFESPAN_DAYS = 3
TESTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "tests")

if __name__ == "__main__":
    import json
    print(json.dumps({k: v for k, v in globals().items() if k.isupper()},
                     indent=2))
