#!/usr/bin/env python3
"""P4-4 paper_trader.py unit tests.

Covers the execution-realistic daily loop: T+1 entry, limit-up/down handling,
position sizing by combo_weight, hold-period exits, cost application, and
the performance-metric calculations (Sharpe / max_dd / hit_rate).
"""

import math
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "references"))

from paper_trader import PaperTrader, _limit_threshold_pct, _max_dd, _std


# ---------------------------------------------------------------------------
# fixtures: synthetic price bars
# ---------------------------------------------------------------------------

def _bars(n, start=100.0, drift=0.005, seed=42):
    """Deterministic upward-drifting close series with matching open."""
    import random
    rng = random.Random(seed)
    out = []
    p = start
    for i in range(n):
        p *= (1.0 + drift)
        noise = rng.uniform(-0.005, 0.005)
        o = p * (1.0 + noise)
        c = p * (1.0 - noise)
        out.append({"date": f"2026-01-{i + 1:02d}" if i < 22 else
                            f"2026-02-{i - 21:02d}" if i < 50 else
                            f"2026-03-{i - 49:02d}",
                    "open": round(o, 4), "close": round(c, 4)})
    return out


def _signal(date, code="AAPL", combo_signal=0.9, combo_weight=1.0,
            market="us", phase="uptrend_pullback"):
    return {"date": date, "code": code, "combo_signal": combo_signal,
            "combo_weight": combo_weight, "market": market, "phase": phase}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestLimitThreshold:
    def test_star_20pct(self):
        assert _limit_threshold_pct("300001") == 20.0

    def test_main_10pct(self):
        assert _limit_threshold_pct("000333") == 10.0

    def test_bse_30pct(self):
        assert _limit_threshold_pct("830000") == 30.0


class TestBasicReplay:
    def test_uptrend_profits(self):
        """Uptrending bars + long signal must produce positive return."""
        bars = _bars(60, drift=0.01)
        prices = {"AAPL": bars}
        sigs = [_signal("2026-01-02", code="AAPL", combo_weight=1.0)]
        t = PaperTrader(prices, max_positions=5, hold=10, slippage_bp=0.0)
        stats = t.replay(sigs)
        assert stats["entries"] == 1
        assert stats["n_trades"] == 1
        assert stats["total_ret"] > 0
        assert stats["hit_rate"] == 100.0

    def test_gate_blocked_ignored(self):
        """Signals with combo_signal=None are skipped."""
        bars = _bars(30)
        prices = {"AAPL": bars}
        sigs = [_signal("2026-01-02", combo_signal=None)]
        t = PaperTrader(prices, max_positions=5, hold=10)
        stats = t.replay(sigs)
        assert stats["entries"] == 0
        assert stats["n_trades"] == 0

    def test_no_signals(self):
        bars = _bars(30)
        t = PaperTrader({"AAPL": bars}, max_positions=5, hold=10)
        stats = t.replay([])
        assert stats["entries"] == 0
        assert stats["total_ret"] == 0.0
        assert stats["hit_rate"] is None


class TestTPlusOne:
    def test_entry_fills_next_day(self):
        """Signal on day t fills at day t+1 open (T+1)."""
        bars = [{"date": "2026-01-01", "open": 100.0, "close": 100.0},
                {"date": "2026-01-02", "open": 110.0, "close": 110.0},
                {"date": "2026-01-03", "open": 120.0, "close": 120.0},
                {"date": "2026-01-04", "open": 130.0, "close": 130.0}]
        prices = {"AAPL": bars}
        sigs = [_signal("2026-01-01", code="AAPL", combo_weight=1.0)]
        t = PaperTrader(prices, max_positions=5, hold=2, slippage_bp=0.0)
        stats = t.replay(sigs)
        assert stats["entries"] == 1
        trade = stats["trades"][0]
        assert trade["entry_px"] == 110.0
        assert trade["entry_date"] == "2026-01-02"


class TestLimitUpSkip:
    def test_limit_up_skips_entry(self):
        """Entry skipped when open >= prev_close * (1 + th/100 - SEAL_BUFFER)."""
        # threshold = 100 * (1 + 0.10 - 0.002) = 109.8
        bars = [{"date": "2026-01-01", "open": 100.0, "close": 100.0},
                {"date": "2026-01-02", "open": 109.9, "close": 109.9},
                {"date": "2026-01-03", "open": 110.0, "close": 110.0}]
        prices = {"000333": bars}
        sigs = [_signal("2026-01-01", code="000333", market="cn_a",
                        combo_weight=1.0)]
        t = PaperTrader(prices, max_positions=5, hold=2, slippage_bp=0.0)
        stats = t.replay(sigs)
        assert stats["entries"] == 0
        assert stats["skipped_limit_up"] == 1

    def test_normal_open_fills(self):
        """Entry fills when open is below the limit-up seal."""
        bars = [{"date": "2026-01-01", "open": 100.0, "close": 100.0},
                {"date": "2026-01-02", "open": 105.0, "close": 105.0},
                {"date": "2026-01-03", "open": 110.0, "close": 110.0}]
        prices = {"000333": bars}
        sigs = [_signal("2026-01-01", code="000333", market="cn_a",
                        combo_weight=1.0)]
        t = PaperTrader(prices, max_positions=5, hold=2, slippage_bp=0.0)
        stats = t.replay(sigs)
        assert stats["entries"] == 1
        assert stats["skipped_limit_up"] == 0


class TestLimitDownRoll:
    def test_limit_down_rolls_exit(self):
        """Exit rolls forward when close <= last_close * (1 - th/100 + SEAL_BUFFER)."""
        bars = [{"date": "2026-01-01", "open": 100.0, "close": 100.0},
                {"date": "2026-01-02", "open": 100.0, "close": 100.0},
                {"date": "2026-01-03", "open": 90.0, "close": 90.0},
                {"date": "2026-01-04", "open": 81.0, "close": 81.0},
                {"date": "2026-01-05", "open": 100.0, "close": 100.0}]
        prices = {"000333": bars}
        sigs = [_signal("2026-01-01", code="000333", market="cn_a",
                        combo_weight=1.0)]
        t = PaperTrader(prices, max_positions=5, hold=1, slippage_bp=0.0)
        stats = t.replay(sigs)
        assert stats["entries"] == 1
        assert stats["exit_delayed"] >= 1
        assert stats["trades"][0]["rolls"] >= 1


class TestPositionSizing:
    def test_combo_weight_scales_alloc(self):
        """alloc = (1/max_positions) * combo_weight — lower weight, smaller ret."""
        bars_a = [{"date": f"2026-01-{i + 1:02d}" if i < 22 else
                            f"2026-02-{i - 21:02d}",
                   "open": 100.0, "close": 100.0} for i in range(30)]
        bars_b = [{"date": f"2026-01-{i + 1:02d}" if i < 22 else
                            f"2026-02-{i - 21:02d}",
                   "open": 100.0, "close": 100.0} for i in range(30)]
        sigs_a = [_signal("2026-01-02", code="AAPL", combo_weight=0.5)]
        sigs_b = [_signal("2026-01-02", code="AAPL", combo_weight=1.0)]
        t_half = PaperTrader({"AAPL": bars_a}, max_positions=4, hold=10,
                             slippage_bp=0.0)
        t_full = PaperTrader({"AAPL": bars_b}, max_positions=4, hold=10,
                             slippage_bp=0.0)
        s_half = t_half.replay(sigs_a)
        s_full = t_full.replay(sigs_b)
        # Both have zero PnL (flat prices, no costs); weight difference shows
        # in the trade record
        assert s_full["trades"][0]["weight"] == 1.0
        assert s_half["trades"][0]["weight"] == 0.5


class TestMaxPositions:
    def test_respects_max_positions(self):
        """Never hold more than max_positions simultaneously."""
        prices = {f"S{i:02d}": _bars(30, drift=0.001, seed=i) for i in range(5)}
        sigs = [_signal("2026-01-02", code=f"S{i:02d}", combo_signal=0.9 - i * 0.01)
                for i in range(5)]
        t = PaperTrader(prices, max_positions=3, hold=20, slippage_bp=0.0)
        stats = t.replay(sigs)
        assert stats["entries"] <= 3


class TestHoldPeriod:
    def test_exits_after_hold_days(self):
        """Position exits at close of day entry_idx + hold."""
        bars = [{"date": f"2026-01-{i + 1:02d}", "open": 100.0 + i, "close": 100.0 + i}
                for i in range(20)]
        prices = {"AAPL": bars}
        sigs = [_signal("2026-01-01", code="AAPL", combo_weight=1.0)]
        t = PaperTrader(prices, max_positions=5, hold=5, slippage_bp=0.0)
        stats = t.replay(sigs)
        assert stats["entries"] == 1
        # entry at day 2 (T+1), exit due at day 2+5=7
        assert stats["trades"][0]["exit_date"] == "2026-01-07"


class TestCosts:
    def test_slippage_reduces_return(self):
        """Positive slippage reduces return vs zero-slippage baseline."""
        bars_a = _bars(30, drift=0.005)
        bars_b = _bars(30, drift=0.005)
        sigs = [_signal("2026-01-02", code="AAPL", combo_weight=1.0, market="us")]
        t0 = PaperTrader({"AAPL": bars_a}, max_positions=5, hold=10, slippage_bp=0.0)
        t10 = PaperTrader({"AAPL": bars_b}, max_positions=5, hold=10, slippage_bp=10.0)
        s0 = t0.replay(sigs)
        s10 = t10.replay(sigs)
        assert s10["total_ret"] < s0["total_ret"]


class TestPerformanceMetrics:
    def test_max_dd_basic(self):
        eq = [100, 110, 105, 115, 90, 100]
        assert _max_dd(eq) == pytest.approx(1 - 90 / 115, abs=1e-9)

    def test_max_dd_empty(self):
        assert _max_dd([]) == 0.0

    def test_max_dd_monotonic(self):
        assert _max_dd([100, 101, 102]) == 0.0

    def test_std_basic(self):
        assert _std([1, 2, 3, 4, 5]) == pytest.approx(math.sqrt(2.5), abs=1e-9)

    def test_std_single(self):
        assert _std([42.0]) == 0.0

    def test_sharpe_zero_vol(self):
        """Zero-vol equity curve -> sharpe 0 (not NaN/inf)."""
        t = PaperTrader({"AAPL": []}, max_positions=5, hold=5, slippage_bp=0.0)
        perf = t._performance([1.0, 1.0, 1.0, 1.0], [])
        assert perf["sharpe"] == 0.0
        assert perf["annual_vol"] == 0.0
        assert not math.isnan(perf["sharpe"])
