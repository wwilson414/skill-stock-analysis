"""P4-1: phase-aware signal combination unit tests.

Covers the P4-1 division-of-labour table (uptrend / range / downtrend), the
hard gates (momentum_confirm, extreme_only) and the range_swing soft boost.
Phase fixtures are constructed so they land on the intended bucket of
calc_pullback_context (verified against the classifier, see P4-1 notes).
"""
import numpy as np
import pytest

from signal_combo import (_assemble_combo, combo_signal_series,
                          compute_combo_signal)


def _seg(n, a, b):
    return list(np.linspace(a, b, n).astype(float))


def _ohlcv(closes, vols=None):
    rows = []
    for i, c in enumerate(closes):
        rows.append({"date": f"d{i:04d}", "open": c, "close": c,
                     "high": c * 1.001, "low": c * 0.999,
                     "volume": (float(vols[i]) if vols else 1e6)})
    return rows


# --- fixtures（phase 分类已用真实 calc_pullback_context 验证） ---
UP_OHLCV = _ohlcv(_seg(156, 90, 110) + _seg(41, 110, 111.5) + [111.5, 111.3, 111.0])
RG_OHLCV = _ohlcv(_seg(186, 110, 95) + _seg(14, 95, 96.5))
DT_OK = _seg(80, 125, 85) + _seg(30, 85, 60) + [58, 58]          # 极端超卖 + 止跌日
DT_OHLCV = _ohlcv(DT_OK)
DT_BLOCKED_OHLCV = _ohlcv(_seg(80, 125, 85) + _seg(30, 85, 60) + [58, 56.5])  # 仍下降


def test_combo_uptrend_primary_and_momentum_gate():
    out = compute_combo_signal(UP_OHLCV)
    assert out["phase"] == "uptrend_pullback"
    assert out["primary"] == "comp_vol"
    assert out["weight"] == 1.0
    assert out["gates"] == ["momentum_confirm"]
    # fallback：20d 动量为正 → gate 通过
    assert out["gate_blocked"] is False
    assert out["combo_score"] is not None
    # 提供 mom 分数：>=50 确认动量；<50 阻断
    n = len(UP_OHLCV)
    ok = compute_combo_signal(UP_OHLCV, mom_scores=[55.0] * n)
    assert ok["gate_blocked"] is False
    low = compute_combo_signal(UP_OHLCV, mom_scores=[40.0] * n)
    assert low["gate_blocked"] is True
    assert low["combo_score"] is None


def test_combo_range_swing_half_weight():
    out = compute_combo_signal(RG_OHLCV)
    assert out["phase"] == "range_swing"
    assert out["primary"] == "comp_vol"
    assert out["weight"] == 0.5
    assert out["gate_blocked"] is False
    # volume 恒定 → comp_vol=0 → combo = 0.5*0 = 0
    assert out["combo_score"] == pytest.approx(0.0)


def test_combo_range_mr_extreme_boost():
    comp = {"comp_vol": np.array([1.0]), "mr_score": np.array([2.5]),
            "mr_event": np.array([False])}
    ctx = {"chg_20d_pct": 0.5}
    out = _assemble_combo("range_swing", ctx, comp, 0)
    assert out["gate_results"]["mr_extreme"] is True
    assert out["combo_score"] == pytest.approx(0.5 * 1.0 + 0.5)


def test_combo_downtrend_extreme_only_gate():
    out = compute_combo_signal(DT_OHLCV)
    assert out["phase"] == "downtrend_decline"
    assert out["primary"] == "mr_score"
    assert out["weight"] == 0.3
    assert out["gates"] == ["extreme_only"]
    # 极端超卖（RSI2<=10 & dev60<=-10%）且止跌日确认 → 通过
    assert out["gate_blocked"] is False
    assert out["combo_score"] is not None
    # 最后一根仍下降（未止跌）→ extreme_only 阻断
    bad = compute_combo_signal(DT_BLOCKED_OHLCV)
    assert bad["phase"] == "downtrend_decline"
    assert bad["gate_blocked"] is True and bad["combo_score"] is None


def test_combo_series_aligned_and_no_lookahead():
    closes = _seg(156, 90, 110) + _seg(41, 110, 111.5) + [111.5, 111.3, 111.0]
    oh = _ohlcv(closes)
    ser = combo_signal_series(oh)
    assert len(ser) == len(oh)
    assert ser[0] is None and ser[60] is None      # warmup 前无信号
    assert ser[70] is not None                      # warmup 后每根 bar 都有 dict
    assert ser[-1]["phase"] == "uptrend_pullback"
    assert ser[-1]["combo_score"] is not None
    # 0..i 的数据只影响 bar i：前半段不能看到尾部信息
    assert ser[70]["phase"] in ("uptrend_pullback", "range_swing",
                                "downtrend_decline")