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
FORWARD = {"days": [5, 10, 20]}
CACHE_LIFESPAN_DAYS = 3
TESTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "tests")

if __name__ == "__main__":
    import json
    print(json.dumps({k: v for k, v in globals().items() if k.isupper()},
                     indent=2))
