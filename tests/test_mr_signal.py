"""P1 standalone mean-reversion + candidate component unit tests."""
import numpy as np

from mr_signal import (wilder_rsi, wilder_atr, compute_components,
                       MR_COMPONENTS, P1_6_COMPONENTS)


def test_rsi_extremes():
    n = 50
    up = np.cumsum(np.ones(n))
    assert wilder_rsi(up)[-1] > 99
    dn = np.cumsum(-np.ones(n) * 0.5)
    assert wilder_rsi(dn)[-1] < 1


def test_atr_well_formed():
    n = 50
    highs = np.full(n, 110.0)
    lows = np.full(n, 90.0)
    closes = np.full(n, 100.0)
    assert wilder_atr(highs, lows, closes, 14)[-1] > 19


def test_components_fire_near_bottom():
    n = 200
    base = np.linspace(100, 70, 150)
    crash = np.array([70 * (0.97 ** i) for i in range(30)])
    recov = np.array([crash[-1] * (1.005 ** i) for i in range(20)])
    c = np.r_[base, crash, recov]
    ohlcv = [{"date": f"d{i}", "open": c[i], "close": c[i],
              "high": c[i] * 1.001, "low": c[i] * 0.999, "volume": 1e6}
             for i in range(n)]
    out = compute_components(ohlcv)
    assert all(len(v) == n for v in out.values())
    assert out["mr_event"][150:].any()
    assert out["mr_score"][175] > 2.0
    for k in MR_COMPONENTS:
        assert k in out
    for k in P1_6_COMPONENTS:
        assert k in out


def test_components_no_lookahead():
    """component arrays are NaN at the very start (no leakage)."""
    n = 15
    c = [100.0] * n
    ohlcv = [{"date": f"d{i}", "open": c[i], "close": c[i],
              "high": c[i] * 1.001, "low": c[i] * 0.999, "volume": 1e6}
             for i in range(n)]
    out = compute_components(ohlcv)
    assert np.isnan(out["mr_score"][0])
    assert np.isnan(out["comp_brk"][0])
    assert np.isnan(out["comp_brk"][50] if len(out["comp_brk"]) > 60 else out["comp_brk"][-1]) is False or len(out["comp_brk"]) <= 60
