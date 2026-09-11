#!/usr/bin/env python3
"""P4-5: risk monitoring (ROADMAP P4-5).

Enforces position limits, stop-loss, circuit breaker, and produces
end-of-day risk reports. Designed to wrap any trading system that
exposes positions + portfolio value (paper_trader, live trader, ...).

Risk gates
----------
* **Per-stock limit** — no single name may exceed ``max_per_stock`` (default 20%)
  of total portfolio value at entry time.
* **Per-phase limit** — no single market phase (uptrend_pullback / range_swing /
  downtrend_decline) may exceed ``max_per_phase`` (default 60%) of total portfolio.
* **Stop-loss** — if a position loses more than ``stop_loss_pct`` (default 8%)
  from its entry price, it is flagged for immediate exit.
* **Circuit breaker** — if total portfolio value drops more than
  ``circuit_breaker_pct`` (default 15%) from its peak, ALL positions are
  flagged for liquidation.

Usage
-----
    from risk_monitor import RiskMonitor

    mon = RiskMonitor()
    allowed, reason = mon.can_enter("600519", "uptrend_pullback", 1.0,
                                    portfolio_value=100000,
                                    positions={"000333": {"value": 15000, "phase": "uptrend_pullback"}})
    if allowed:
        # place order ...

    # end of day
    report = mon.eod_report("2026-09-10", positions, 98500, peak=100000)
    print(report["alerts"])
"""

import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from score_config import RISK


class RiskMonitor:
    """P4-5: position limits, stop-loss, circuit breaker, EOD report.

    Parameters (defaults pulled from ``score_config.RISK``)::
        max_per_stock:       float  # fraction, e.g. 0.20
        max_per_phase:       float  # fraction, e.g. 0.60
        stop_loss_pct:       float  # fraction, e.g. 0.08
        circuit_breaker_pct: float  # fraction, e.g. 0.15
    """

    def __init__(self, max_per_stock=None, max_per_phase=None,
                 stop_loss_pct=None, circuit_breaker_pct=None):
        self.max_per_stock = max_per_stock if max_per_stock is not None else RISK["max_per_stock"]
        self.max_per_phase = max_per_phase if max_per_phase is not None else RISK["max_per_phase"]
        self.stop_loss_pct = stop_loss_pct if stop_loss_pct is not None else RISK["stop_loss_pct"]
        self.circuit_breaker_pct = (circuit_breaker_pct if circuit_breaker_pct is not None
                                    else RISK["circuit_breaker_pct"])

    # ------------------------------------------------------------------
    # entry gate
    # ------------------------------------------------------------------
    def can_enter(self, code: str, phase: str, weight: float,
                  portfolio_value: float, positions: dict) -> tuple:
        """Check whether a new entry complies with position limits.

        Parameters
        ----------
        code : str
            Stock code to enter.
        phase : str
            Market phase (uptrend_pullback / range_swing / downtrend_decline).
        weight : float
            Combo weight (0.3 / 0.5 / 1.0) — scales the notional.
        portfolio_value : float
            Total portfolio value (cash + positions) at decision time.
        positions : dict
            Current holdings: {code: {"value": float, "phase": str,
                                       "weight": float, "entry_price": float}}.

        Returns
        -------
        (allowed, reason) : (bool, str)
            allowed=True means the entry complies; reason explains a rejection.
        """
        if portfolio_value <= 0:
            return False, "portfolio_value <= 0"

        # --- per-stock limit ---------------------------------------------
        existing_value = positions.get(code, {}).get("value", 0.0)
        proposed_frac = weight
        total_stock_frac = (existing_value / portfolio_value) + proposed_frac
        if total_stock_frac > self.max_per_stock + 1e-9:
            return False, (f"per-stock limit: {code} would be "
                           f"{total_stock_frac:.1%} > {self.max_per_stock:.1%}")

        # --- per-phase limit ---------------------------------------------
        phase_value = sum(p["value"] for p in positions.values()
                          if p.get("phase") == phase)
        total_phase_frac = (phase_value / portfolio_value) + proposed_frac
        if total_phase_frac > self.max_per_phase + 1e-9:
            return False, (f"per-phase limit: {phase} would be "
                           f"{total_phase_frac:.1%} > {self.max_per_phase:.1%}")

        return True, "ok"

    # ------------------------------------------------------------------
    # stop-loss
    # ------------------------------------------------------------------
    def check_stop_loss(self, code: str, entry_price: float,
                        current_price: float) -> bool:
        """True if a position has lost more than stop_loss_pct from entry."""
        if entry_price <= 0:
            return False
        loss = (entry_price - current_price) / entry_price
        return loss >= self.stop_loss_pct

    def stop_loss_level(self, entry_price: float) -> float:
        """Absolute price level that triggers stop-loss."""
        return entry_price * (1 - self.stop_loss_pct)

    # ------------------------------------------------------------------
    # circuit breaker
    # ------------------------------------------------------------------
    def check_circuit_breaker(self, portfolio_value: float,
                              peak_value: float) -> bool:
        """True if portfolio has dropped more than circuit_breaker_pct from peak."""
        if peak_value <= 0:
            return False
        dd = (peak_value - portfolio_value) / peak_value
        return dd >= self.circuit_breaker_pct

    # ------------------------------------------------------------------
    # end-of-day report
    # ------------------------------------------------------------------
    def eod_report(self, date: str, positions: dict, portfolio_value: float,
                   peak_value: float, trades_today: list = None) -> dict:
        """Generate end-of-day risk report.

        Parameters
        ----------
        date : str
            Trading date (YYYY-MM-DD).
        positions : dict
            Current holdings: {code: {"value", "phase", "weight", "entry_price",
                                      "current_price"}}.
        portfolio_value : float
            Total portfolio value at end of day.
        peak_value : float
            Peak portfolio value (trailing high-water mark).
        trades_today : list, optional
            Trades executed today: [{code, direction, price, pnl}].

        Returns
        -------
        dict with keys: date, portfolio_value, peak_value, drawdown,
            n_positions, phase_exposure, stock_exposure, stop_loss_alerts,
            circuit_breaker, alerts (list of str).
        """
        alerts = []

        # --- drawdown ----------------------------------------------------
        drawdown = 0.0
        if peak_value > 0:
            drawdown = (peak_value - portfolio_value) / peak_value

        # --- exposure ----------------------------------------------------
        phase_exposure = defaultdict(float)
        stock_exposure = {}
        for code, pos in positions.items():
            val = pos.get("value", 0.0)
            frac = val / portfolio_value if portfolio_value > 0 else 0.0
            stock_exposure[code] = round(frac, 4)
            ph = pos.get("phase", "unknown")
            phase_exposure[ph] += frac
        phase_exposure = {k: round(v, 4) for k, v in phase_exposure.items()}

        # --- stop-loss alerts -------------------------------------------
        stop_alerts = []
        for code, pos in positions.items():
            ep = pos.get("entry_price")
            cp = pos.get("current_price")
            if ep and cp and self.check_stop_loss(code, ep, cp):
                loss = (ep - cp) / ep
                stop_alerts.append({
                    "code": code, "entry_price": ep,
                    "current_price": cp, "loss_pct": round(loss, 4),
                })
                alerts.append(f"STOP-LOSS {code}: -{loss:.1%}")

        # --- circuit breaker ---------------------------------------------
        cb = self.check_circuit_breaker(portfolio_value, peak_value)
        if cb:
            alerts.append(f"CIRCUIT BREAKER: drawdown {drawdown:.1%} >= "
                          f"{self.circuit_breaker_pct:.1%}")

        # --- concentration alerts ---------------------------------------
        for code, frac in stock_exposure.items():
            if frac > self.max_per_stock + 1e-9:
                alerts.append(f"CONCENTRATION {code}: {frac:.1%}")
        for ph, frac in phase_exposure.items():
            if frac > self.max_per_phase + 1e-9:
                alerts.append(f"CONCENTRATION phase={ph}: {frac:.1%}")

        return {
            "date": date,
            "portfolio_value": round(portfolio_value, 2),
            "peak_value": round(peak_value, 2),
            "drawdown": round(drawdown, 4),
            "n_positions": len(positions),
            "phase_exposure": phase_exposure,
            "stock_exposure": stock_exposure,
            "stop_loss_alerts": stop_alerts,
            "circuit_breaker": cb,
            "alerts": alerts,
        }

# ---------------------------------------------------------------------------
# demo / smoke
# ---------------------------------------------------------------------------
def _demo():
    mon = RiskMonitor()

    # --- entry gate ---
    positions = {
        "000333": {"value": 15000, "phase": "uptrend_pullback", "weight": 0.15,
                   "entry_price": 100.0},
    }
    allowed, reason = mon.can_enter("600519", "uptrend_pullback", 0.20,
                                    portfolio_value=100000, positions=positions)
    print(f"[risk] enter 600519: allowed={allowed}, reason={reason}")

    allowed, reason = mon.can_enter("000333", "uptrend_pullback", 0.10,
                                    portfolio_value=100000, positions=positions)
    print(f"[risk] enter 000333 (add): allowed={allowed}, reason={reason}")

    # --- stop-loss ---
    print(f"[risk] stop-loss 600519 @92 (entry 100): "
          f"{mon.check_stop_loss('600519', 100.0, 92.0)}")
    print(f"[risk] stop-loss 600519 @93 (entry 100): "
          f"{mon.check_stop_loss('600519', 100.0, 93.0)}")

    # --- circuit breaker ---
    print(f"[risk] circuit breaker 85k / peak 100k: "
          f"{mon.check_circuit_breaker(85000, 100000)}")
    print(f"[risk] circuit breaker 86k / peak 100k: "
          f"{mon.check_circuit_breaker(86000, 100000)}")

    # --- EOD report ---
    positions_eod = {
        "000333": {"value": 15000, "phase": "uptrend_pullback",
                   "weight": 0.15, "entry_price": 100.0, "current_price": 91.0},
        "600519": {"value": 20000, "phase": "range_swing",
                   "weight": 0.20, "entry_price": 50.0, "current_price": 52.0},
    }
    report = mon.eod_report("2026-09-10", positions_eod, 98500, 100000)
    print("[risk] eod report:")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("[risk] demo OK")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="P4-5 risk monitor smoke")
    args = ap.parse_args()
    _demo()


if __name__ == "__main__":
    main()
