"""Threshold config regression tests (ROADMAP P3-14)."""
import score_config as sc

SIGNAL_THRESHOLDS = sc.SIGNAL_THRESHOLDS
GATES = sc.GATES
LIMIT = sc.LIMIT
MR = sc.MR
COSTS = sc.COSTS



def test_signal_thresholds_healthy():
    assert SIGNAL_THRESHOLDS["strong_buy"] > SIGNAL_THRESHOLDS["buy"]
    assert SIGNAL_THRESHOLDS["buy"] > SIGNAL_THRESHOLDS["hold"]
    assert SIGNAL_THRESHOLDS["hold"] > SIGNAL_THRESHOLDS["wait"]


def test_gate_values():
    assert GATES["rr_ratio_min"] == 1.5
    assert GATES["ic_min_magnitude"] == 0.15


def test_limit_thresholds():
    assert LIMIT["STAR_ChiNext_BSE_pct"] == 20.0
    assert LIMIT["main_pct"] == 10.0
    assert LIMIT["ST_pct"] == 5.0


def test_cost_round_trip_cn():
    fb, fs = COSTS["cn_a"]
    rt = fb + fs
    assert 0.09 <= rt <= 0.11  # ~10bp per round trip


def test_mr_oversold_levels():
    assert MR["rsi2_oversold"] == 10.0
    assert MR["dev_ma60_oversold"] == 0.10
