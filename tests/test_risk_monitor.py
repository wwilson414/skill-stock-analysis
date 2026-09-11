#!/usr/bin/env python3
"""P4-5 risk_monitor.py unit tests.

Covers the four risk gates: per-stock limit, per-phase limit, stop-loss,
circuit breaker, and the end-of-day report.
"""

import math
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "references"))

from risk_monitor import RiskMonitor


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _positions():
    return {
        "000333": {"value": 15000, "phase": "uptrend_pullback",
                   "weight": 0.15, "entry_price": 100.0},
        "600519": {"value": 20000, "phase": "range_swing",
                   "weight": 0.20, "entry_price": 50.0},
    }


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestEntryPoint:
    def test_allows_within_limit(self):
        """New stock within per-stock limit is allowed."""
        mon = RiskMonitor()
        positions = _positions()
        allowed, reason = mon.can_enter("002475", "uptrend_pullback", 0.20,
                                        portfolio_value=100000, positions=positions)
        assert allowed is True
        assert reason == "ok"

    def test_blocks_per_stock_exceeded(self):
        """Adding to an existing position beyond 20% is blocked."""
        mon = RiskMonitor()
        positions = _positions()
        # 000333 already at 15%, adding 10% would make 25% > 20%
        allowed, reason = mon.can_enter("000333", "uptrend_pullback", 0.10,
                                        portfolio_value=100000, positions=positions)
        assert allowed is False
        assert "per-stock limit" in reason

    def test_blocks_per_phase_exceeded(self):
        """Phase concentration beyond 60% is blocked."""
        mon = RiskMonitor()
        positions = {
            "A": {"value": 30000, "phase": "uptrend_pullback", "entry_price": 100},
            "B": {"value": 30000, "phase": "uptrend_pullback", "entry_price": 50},
        }
        # uptrend already at 60%, adding another would exceed
        allowed, reason = mon.can_enter("C", "uptrend_pullback", 0.05,
                                        portfolio_value=100000, positions=positions)
        assert allowed is False
        assert "per-phase limit" in reason

    def test_allows_when_at_exact_limit(self):
        """Position exactly at the limit is allowed (boundary)."""
        mon = RiskMonitor()
        positions = {
            "000333": {"value": 20000, "phase": "uptrend_pullback",
                       "entry_price": 100.0},
        }
        # 000333 at exactly 20%, adding 0% should be allowed
        allowed, reason = mon.can_enter("000333", "uptrend_pullback", 0.0,
                                        portfolio_value=100000, positions=positions)
        assert allowed is True

    def test_rejects_zero_portfolio(self):
        """Zero or negative portfolio value rejects entry."""
        mon = RiskMonitor()
        allowed, reason = mon.can_enter("600519", "uptrend_pullback", 0.20,
                                        portfolio_value=0, positions={})
        assert allowed is False
        assert "portfolio_value" in reason


class TestStopLoss:
    def test_triggers_at_threshold(self):
        """Stop-loss triggers at exactly 8% loss."""
        mon = RiskMonitor()
        assert mon.check_stop_loss("X", 100.0, 92.0) is True

    def test_triggers_beyond_threshold(self):
        """Stop-loss triggers beyond 8% loss."""
        mon = RiskMonitor()
        assert mon.check_stop_loss("X", 100.0, 90.0) is True

    def test_does_not_trigger_below_threshold(self):
        """Stop-loss does not trigger below 8% loss."""
        mon = RiskMonitor()
        assert mon.check_stop_loss("X", 100.0, 93.0) is False

    def test_does_not_trigger_at_profit(self):
        """Stop-loss does not trigger when in profit."""
        mon = RiskMonitor()
        assert mon.check_stop_loss("X", 100.0, 110.0) is False

    def test_zero_entry_safe(self):
        """Zero entry price does not crash."""
        mon = RiskMonitor()
        assert mon.check_stop_loss("X", 0.0, 0.0) is False

    def test_stop_loss_level(self):
        """Stop-loss level is entry * (1 - pct)."""
        mon = RiskMonitor()
        assert mon.stop_loss_level(100.0) == pytest.approx(92.0)
        assert mon.stop_loss_level(50.0) == pytest.approx(46.0)


class TestCircuitBreaker:
    def test_triggers_at_threshold(self):
        """Circuit breaker triggers at exactly 15% drawdown."""
        mon = RiskMonitor()
        assert mon.check_circuit_breaker(85000, 100000) is True

    def test_triggers_beyond_threshold(self):
        """Circuit breaker triggers beyond 15% drawdown."""
        mon = RiskMonitor()
        assert mon.check_circuit_breaker(80000, 100000) is True

    def test_does_not_trigger_below_threshold(self):
        """Circuit breaker does not trigger below 15% drawdown."""
        mon = RiskMonitor()
        assert mon.check_circuit_breaker(86000, 100000) is False

    def test_does_not_trigger_at_peak(self):
        """Circuit breaker does not trigger at peak."""
        mon = RiskMonitor()
        assert mon.check_circuit_breaker(100000, 100000) is False

    def test_zero_peak_safe(self):
        """Zero peak does not crash."""
        mon = RiskMonitor()
        assert mon.check_circuit_breaker(0, 0) is False


class TestEodReport:
    def test_basic_structure(self):
        """EOD report has all expected keys."""
        mon = RiskMonitor()
        positions = _positions()
        report = mon.eod_report("2026-09-10", positions, 100000, 100000)
        assert report["date"] == "2026-09-10"
        assert report["portfolio_value"] == 100000
        assert report["peak_value"] == 100000
        assert report["drawdown"] == 0.0
        assert report["n_positions"] == 2
        assert "phase_exposure" in report
        assert "stock_exposure" in report
        assert "stop_loss_alerts" in report
        assert "circuit_breaker" in report
        assert "alerts" in report

    def test_drawdown_calculation(self):
        """Drawdown is computed correctly."""
        mon = RiskMonitor()
        report = mon.eod_report("2026-09-10", {}, 85000, 100000)
        assert report["drawdown"] == pytest.approx(0.15)

    def test_stop_loss_alert_generated(self):
        """Stop-loss alert is generated for losing positions."""
        mon = RiskMonitor()
        positions = {
            "000333": {"value": 15000, "phase": "uptrend_pullback",
                       "entry_price": 100.0, "current_price": 91.0},
        }
        report = mon.eod_report("2026-09-10", positions, 100000, 100000)
        assert len(report["stop_loss_alerts"]) == 1
        assert report["stop_loss_alerts"][0]["code"] == "000333"
        assert "STOP-LOSS 000333" in report["alerts"][0]

    def test_circuit_breaker_alert(self):
        """Circuit breaker alert is generated when triggered."""
        mon = RiskMonitor()
        report = mon.eod_report("2026-09-10", {}, 85000, 100000)
        assert report["circuit_breaker"] is True
        assert any("CIRCUIT BREAKER" in a for a in report["alerts"])

    def test_concentration_alert(self):
        """Concentration alert for stock exceeding limit."""
        mon = RiskMonitor()
        positions = {
            "000333": {"value": 25000, "phase": "uptrend_pullback",
                       "entry_price": 100.0, "current_price": 100.0},
        }
        report = mon.eod_report("2026-09-10", positions, 100000, 100000)
        assert any("CONCENTRATION 000333" in a for a in report["alerts"])

    def test_phase_exposure_sum(self):
        """Phase exposure sums correctly."""
        mon = RiskMonitor()
        positions = _positions()
        report = mon.eod_report("2026-09-10", positions, 100000, 100000)
        total = sum(report["phase_exposure"].values())
        assert total == pytest.approx(0.35, abs=0.01)

    def test_empty_positions(self):
        """EOD report with no positions."""
        mon = RiskMonitor()
        report = mon.eod_report("2026-09-10", {}, 100000, 100000)
        assert report["n_positions"] == 0
        assert report["phase_exposure"] == {}
        assert report["stock_exposure"] == {}
        assert report["alerts"] == []
