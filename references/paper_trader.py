#!/usr/bin/env python3
"""P4-4: paper-trading replay (ROADMAP P4-4).

Replays combo signals (P4-1/P4-3) through an execution-realistic daily loop.
This is the out-of-sample gate before anything touches real money.

Execution model
---------------
* T+1 entry: a signal on day t fills at day t+1's open; if that open is
  limit-up sealed (open >= prev_close * (1 + th - 0.2pp)) the entry is
  SKIPPED (a trade you could never have; counted, not repriced).
* Exit at close of day t+hold; a limit-down sealed close rolls the sale
  forward, max 5 rolls (exit_delayed).
* Costs (percent of notional, per side) from score_config.COSTS per market,
  plus slippage bp per side applied to both entry and exit prices.
* Position sizing: unit = 1/max_positions of initial capital, scaled by
  combo_weight (uptrend 1.0 / range 0.5 / downtrend 0.3); candidates ranked
  by combo_signal, one position per code.
* Daily mark-to-market at close (net of estimated round-trip costs);
  end-of-day snapshots/trades optionally persisted via store (P4-3).
* Risk gates (P4-5, RiskMonitor, on by default): entry blocked when a code
  would exceed 20% of portfolio or its phase 60%; a position whose last close
  is -8% below entry is force-closed (stop-loss); a portfolio -15% from peak
  liquidates all holdings (circuit breaker, entries halted that day).

Gate (out-of-sample 2025-09 ~ 2026-09): Sharpe > 0.3, max_dd < 30%,
profit_factor > 1.5, coverage >= 20% (revised: >=1 stock with signal per
5 trading days — the original "daily signal" bar is unreachable by design,
see ROADMAP P4 acceptance table).

Usage
  python3 references/paper_trader.py --db reports/signals.db \\
      --start 2025-09-01 --out reports/p4_paper_trader.json
  python3 references/paper_trader.py --demo        # synthetic smoke
"""

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from score_config import COSTS, RISK
from risk_monitor import RiskMonitor
from store import Store

PERIODS_PER_YEAR = 252
EXIT_ROLL_MAX = 5
SEAL_BUFFER = 0.002          # mirrors p2_execution's 0.2pp seal buffer


def _limit_threshold_pct(code: str):
    """A-share daily price-limit threshold (mirrors p2_execution); None = none."""
    if code.startswith(("688", "689", "300", "301", "302")):
        return 20.0
    if code.startswith(("43", "83", "87", "88", "92")):
        return 30.0
    return 10.0


def _std(xs, ddof=1):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - ddof))


def _max_dd(series):
    if not series:
        return 0.0
    peak = series[0]
    dd = 0.0
    for v in series:
        if v > peak:
            peak = v
        if peak > 0:
            dd = max(dd, (peak - v) / peak)
    return dd


def _profit_factor(rets: list) -> float:
    """Avg win / avg loss (profit factor). None when there is no losing trade.

    rets are percent returns (ret_pct). wins = rets > 0, losses = rets < 0.
    """
    if not rets:
        return None
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    if not wins:
        return 0.0                      # nothing won at all
    avg_win = sum(wins) / len(wins)
    if not losses:
        return None                     # no losing trade -> undefined/infinite
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return None
    return round(avg_win / abs(avg_loss), 3)


class PaperTrader:
    """Daily-loop replay engine over {code: [bars]} prices + combo signals."""

    def __init__(self, prices: dict, max_positions: int = 5, hold: int = 20,
                 slippage_bp: float = 10.0, store: Store = None,
                 persist: bool = False, use_risk: bool = True,
                 stop_loss_pct: float = None):
        """prices: {code: [ {date, open, close, ...}, ... ]} chronological.

        use_risk=True (default) enables RiskMonitor gates inside replay():
        per-stock/per-phase entry limits, per-stock stop-loss (default -8%,
        overridable via stop_loss_pct for in-sample tuning), and the
        -15% portfolio circuit breaker. Set False to reproduce legacy runs.
        """
        self.prices = prices
        self.max_positions = max_positions
        self.hold = hold
        self.slip = slippage_bp / 10000.0    # 10bp -> 0.001 (decimal per side)
        self.store = store if persist else None
        self.persist = persist
        self.use_risk = use_risk
        self.risk = (RiskMonitor(stop_loss_pct=stop_loss_pct)
                     if use_risk else None)
        # per-code date -> bar index and trading calendar (union of codes)
        self.px = {code: {b["date"]: b for b in bars if b.get("date")}
                   for code, bars in prices.items()}
        cal = set()
        for p in self.px.values():
            cal.update(p.keys())
        self.dates = sorted(cal)
        self.date_idx = {d: i for i, d in enumerate(self.dates)}

    # -- cost helpers -------------------------------------------------------
    def _costs(self, market):
        fee_b, fee_s = COSTS.get(market, (0.0, 0.0))
        return fee_b, fee_s

    def _threshold(self, code, market):
        return _limit_threshold_pct(code) if market == "cn_a" else None

    def _mtm_ratio(self, pos):
        """Net-of-cost equity ratio of an open position at its last close."""
        cur = pos.get("cur_close") or pos["last_close"]
        entry_eff = pos["entry_px"] * (1 + self.slip)
        exit_eff = cur * (1 - self.slip)
        return exit_eff / entry_eff * (1 - pos["fee_total"] / 100.0)

    def _open_position(self, code, sig, bar, prev_close, d):
        fee_b, fee_s = self._costs(sig.get("market"))
        weight = sig.get("combo_weight") or 1.0
        alloc = (1.0 / self.max_positions) * weight
        pos = {
            "code": code, "entry_date": d, "entry_px": bar["open"],
            "alloc": alloc, "weight": weight, "market": sig.get("market"),
            "fee_total": fee_b + fee_s, "exit_due": self.date_idx[d] + self.hold,
            "rolls": 0, "last_close": bar["open"], "cur_close": None,
            "phase": sig.get("phase"), "combo_signal": sig.get("combo_signal"),
            "trade_id": None,
        }
        if self.store is not None:
            pos["trade_id"] = self.store.save_trade(
                d, code, "BUY", bar["open"], round(alloc, 6))
        return pos

    def _close_position(self, pos, exit_px, d, stats, reason="hold"):
        entry_eff = pos["entry_px"] * (1 + self.slip)
        exit_eff = exit_px * (1 - self.slip)
        proceeds = pos["alloc"] * exit_eff / entry_eff \
            * (1 - pos["fee_total"] / 100.0)
        pnl = proceeds - pos["alloc"]
        ret_pct = (proceeds / pos["alloc"] - 1.0) * 100.0
        stats["trades"].append({
            "code": pos["code"], "entry_date": pos["entry_date"],
            "exit_date": d, "entry_px": round(pos["entry_px"], 4),
            "exit_px": round(exit_px, 4), "weight": pos["weight"],
            "ret_pct": round(ret_pct, 3), "pnl": round(pnl, 6),
            "rolls": pos["rolls"], "phase": pos["phase"],
            "exit_reason": reason,
        })
        if self.store is not None and pos.get("trade_id"):
            self.store.close_trade(pos["trade_id"], round(pnl, 6))
        return proceeds

    # -- risk integration ---------------------------------------------------
    def _positions_view(self, positions: dict, d: str) -> dict:
        """RiskMonitor-compatible holdings: {code: {value, phase, ...}}.

        value = mark-to-market notional (alloc * mtm_ratio * portfolio_scale
        is unitless here, so we use alloc * mtm_ratio as the *fractional*
        value). RiskMonitor only compares *fractions* of the portfolio, so
        feeding it fractions of total unit capital is consistent as long as
        portfolio_value is normalised to the same scale. We pass
        portfolio_value=1.0 (total = cash + sum(alloc*mtm) with unit cash).
        """
        out = {}
        for code, pos in positions.items():
            frac = pos["alloc"] * self._mtm_ratio(pos)
            out[code] = {"value": frac, "phase": pos.get("phase", "unknown"),
                         "weight": pos.get("weight", 1.0),
                         "entry_price": pos.get("entry_px")}
            bar = self.px.get(code, {}).get(d)
            if bar:
                out[code]["current_price"] = bar.get("close")
        return out

    # -- main loop ----------------------------------------------------------
    def replay(self, signals: list) -> dict:
        """Run the daily loop over signals; returns stats dict.

        signals: [{date, code, combo_signal, combo_weight, market, phase}]
        — rows with combo_signal None (gate-blocked) are ignored.
        """
        sig_by_date = defaultdict(list)
        for s in signals:
            if s.get("combo_signal") is None or not s.get("code"):
                continue
            sig_by_date[s["date"]].append(s)
        for d in sig_by_date:
            sig_by_date[d].sort(key=lambda s: s["combo_signal"], reverse=True)

        unit = 1.0 / self.max_positions
        cash = 1.0
        positions = {}                     # code -> pos dict
        stats = {"entries": 0, "skipped_limit_up": 0, "exit_delayed": 0,
                 "no_fill": 0, "risk_blocked": 0, "stop_loss_trades": 0,
                 "circuit_breakers": 0, "trades": [], "equity_dates": []}
        equity = []
        prev_daily = None
        peak_mv = 0.0                       # trailing high-water mark (circuit bkr)
        breaker_tripped = False

        for di, d in enumerate(self.dates):
            breaker_tripped = False

            # 1) exits: positions due today (limit-down rolls forward)
            for code in list(positions):
                pos = positions[code]
                if di < pos["exit_due"]:
                    continue
                bar = self.px.get(code, {}).get(d)
                if bar is None or not bar.get("close"):
                    # trading halt on exit day -> push one day
                    pos["exit_due"] = di + 1
                    continue
                th = self._threshold(code, pos["market"])
                if th is not None and pos["rolls"] < EXIT_ROLL_MAX \
                        and bar["close"] <= pos["last_close"] * (1 - th / 100 + SEAL_BUFFER):
                    pos["rolls"] += 1
                    pos["exit_due"] = di + 1
                    stats["exit_delayed"] += 1
                    continue
                proceeds = self._close_position(pos, bar["close"], d, stats)
                cash += proceeds
                del positions[code]

            # 1b) stop-loss: position whose last known close is -8% vs entry
            # is force-close today (at today's close if available).
            if self.use_risk and self.risk is not None:
                for code in list(positions):
                    pos = positions[code]
                    if pos.get("last_close") and \
                            pos["last_close"] <= self.risk.stop_loss_level(
                                pos["entry_px"]):
                        bar = self.px.get(code, {}).get(d)
                        px = bar["close"] if bar and bar.get("close") \
                            else pos["last_close"]
                        proceeds = self._close_position(
                            pos, px, d, stats, reason="stop_loss")
                        cash += proceeds
                        stats["stop_loss_trades"] += 1
                        del positions[code]

            # 2) entries: yesterday's signals fill at today's open (T+1)
            if di > 0 and not breaker_tripped:
                yday = self.dates[di - 1]
                for s in sig_by_date.get(yday, []):
                    if len(positions) >= self.max_positions:
                        break
                    code = s["code"]
                    if code in positions:
                        continue
                    weight = s.get("combo_weight") or 1.0
                    alloc = unit * weight
                    if cash < alloc:
                        stats["no_fill"] += 1
                        continue
                    bar = self.px.get(code, {}).get(d)
                    if bar is None or not bar.get("open"):
                        continue
                    prev_bar = self.px.get(code, {}).get(yday)
                    prev_close = prev_bar.get("close") if prev_bar else None
                    th = self._threshold(code, s.get("market"))
                    if th is not None and prev_close \
                            and bar["open"] >= prev_close * (1 + th / 100 - SEAL_BUFFER):
                        stats["skipped_limit_up"] += 1
                        continue
                    # risk entry gate: per-stock / per-phase position limits
                    if self.use_risk and self.risk is not None:
                        pv = peak_mv if peak_mv > 0 else 1.0
                        allowed, _r = self.risk.can_enter(
                            code, s.get("phase"), alloc, pv,
                            self._positions_view(positions, d))
                        if not allowed:
                            stats["risk_blocked"] += 1
                            continue
                    pos = self._open_position(code, s, bar, prev_close, d)
                    positions[code] = pos
                    cash -= alloc
                    stats["entries"] += 1

            # 3) mark to market at close
            mv = cash
            for code, pos in positions.items():
                bar = self.px.get(code, {}).get(d)
                if bar and bar.get("close"):
                    pos["cur_close"] = bar["close"]
                    pos["last_close"] = bar["close"]
                mv += pos["alloc"] * self._mtm_ratio(pos)
            equity.append(mv)
            peak_mv = max(peak_mv, mv)
            daily_ret = (mv / prev_daily - 1.0) if prev_daily else 0.0
            stats["equity_dates"].append(d)
            if self.store is not None:
                self.store.save_portfolio_snapshot(
                    d, round(cash, 6), len(positions), round(mv, 6),
                    round(daily_ret * 100, 4))
            prev_daily = mv

            # 3b) circuit breaker: portfolio -15% from peak -> liquidate all
            if self.use_risk and self.risk is not None and positions:
                if self.risk.check_circuit_breaker(mv, peak_mv):
                    stats["circuit_breakers"] += 1
                    breaker_tripped = True
                    for code in list(positions):
                        pos = positions[code]
                        bar = self.px.get(code, {}).get(d)
                        px = bar["close"] if bar and bar.get("close") \
                            else pos["last_close"]
                        proceeds = self._close_position(
                            pos, px, d, stats, reason="circuit_breaker")
                        cash += proceeds
                        del positions[code]

        # coverage: days with at least one rankable signal / total trading days
        stats["coverage_pct"] = round(
            100.0 * len(sig_by_date) / max(1, len(self.dates)), 1)
        stats.update(self._performance(equity, stats["trades"]))
        stats["n_positions_open"] = len(positions)
        return stats

    # -- performance --------------------------------------------------------
    def _performance(self, equity: list, trades: list) -> dict:
        if not equity:
            return {"total_ret": 0.0, "annual_ret": 0.0, "annual_vol": 0.0,
                    "sharpe": 0.0, "max_dd": 0.0, "hit_rate": None,
                    "mean_ret": None, "profit_factor": None, "n_trades": 0}
        daily = [equity[i] / equity[i - 1] - 1.0
                 for i in range(1, len(equity)) if equity[i - 1] > 0]
        sd = _std(daily)
        ann_vol = sd * math.sqrt(PERIODS_PER_YEAR) * 100.0 if sd > 0 else 0.0
        years = len(equity) / PERIODS_PER_YEAR if len(equity) > 0 else 1.0
        ann_ret = ((equity[-1] / equity[0]) ** (1.0 / years) - 1.0) * 100.0 \
            if equity[0] > 0 and years > 0 else 0.0
        sharpe = ann_ret / ann_vol if ann_vol > 1e-9 else 0.0
        rets = [t["ret_pct"] for t in trades]
        return {
            "total_ret": round((equity[-1] / equity[0] - 1.0) * 100.0, 2),
            "annual_ret": round(ann_ret, 2),
            "annual_vol": round(ann_vol, 2),
            "sharpe": round(sharpe, 3),
            "max_dd": round(_max_dd(equity), 4),
            "hit_rate": round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1)
            if rets else None,
            "mean_ret": round(sum(rets) / len(rets), 3) if rets else None,
            "profit_factor": _profit_factor(rets),
            "n_trades": len(rets),
        }


# -- data loading (CLI glue) ------------------------------------------------
def _load_signals_from_store(db: str, start: str = None, end: str = None):
    """Combo-rankable signals from the P4-3 store (gate-blocked excluded)."""
    with Store(db) as st:
        rows = st.query_signals(date_from=start, date_to=end)
    return [r for r in rows if r.get("combo_signal") is not None]


def _load_prices(codes: list, days: int, use_cache: bool = True) -> dict:
    """code -> ohlcv bars via the P0 cache chain (classify via code)."""
    from p0_backtest import _fetch_stock_ohlcv, _log
    from stock_data_fetcher import classify_stock
    prices = {}
    for code in codes:
        market, normalized, _display = classify_stock(code)
        entry = {"code": code, "market": market, "normalized": normalized}
        try:
            ohlcv, _name, _src = _fetch_stock_ohlcv(entry, days, use_cache)
            prices[code] = ohlcv
        except Exception as e:
            _log(f"[p4t] {code}: price fetch failed ({e}) — skipped")
    return prices


def _demo():
    """Synthetic replay smoke: 2 stocks, 60 bars, exercises the full loop."""
    def bars(code, px0, drift, limit_day=None):
        out = []
        px = px0
        for i in range(60):
            o = px * (1 + drift) if i else px
            c = px * (1 + drift)
            if limit_day is not None and i == limit_day:
                o = px * 1.099            # limit-up sealed open (cn_a 10%)
            out.append({"date": f"2026-{(i // 22) + 1:02d}-{(i % 22) + 1:02d}",
                        "open": round(o, 4), "close": round(c, 4),
                        "high": round(max(o, c) * 1.001, 4),
                        "low": round(min(o, c) * 0.999, 4), "volume": 1e6})
            px = c
        return out

    prices = {
        "600519": bars("600519", 1000.0, 0.004, limit_day=10),
        "AAPL": bars("AAPL", 200.0, 0.003),
    }
    signals = []
    for i in range(0, 40, 3):
        signals.append({"date": f"2026-01-{i + 1:02d}" if i < 22 else
                        f"2026-02-{i - 21:02d}", "code": "600519",
                        "combo_signal": 1.2, "combo_weight": 1.0,
                        "market": "cn_a", "phase": "uptrend_pullback"})
        signals.append({"date": f"2026-01-{i + 1:02d}" if i < 22 else
                        f"2026-02-{i - 21:02d}", "code": "AAPL",
                        "combo_signal": 0.9, "combo_weight": 0.5,
                        "market": "us", "phase": "range_swing"})
    trader = PaperTrader(prices, max_positions=4, hold=10, slippage_bp=10.0)
    stats = trader.replay(signals)
    print("[p4t] demo stats:")
    for k in ("entries", "skipped_limit_up", "exit_delayed", "n_trades",
              "total_ret", "annual_ret", "annual_vol", "sharpe", "max_dd",
              "hit_rate", "profit_factor", "coverage_pct",
              "stop_loss_trades", "circuit_breakers", "risk_blocked"):
        print(f"  {k}: {stats[k]}")
    assert stats["entries"] > 0
    assert stats["n_trades"] > 0
    assert stats["total_ret"] > 0          # uptrending fixtures must profit
    print("[p4t] demo OK")


def main():
    ap = argparse.ArgumentParser(description="P4-4 paper-trading replay")
    ap.add_argument("--db", default="reports/signals.db")
    ap.add_argument("--start", default=None, help="signal date >= (oos: 2025-09-01)")
    ap.add_argument("--end", default=None)
    ap.add_argument("--max-positions", type=int, default=5)
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--slippage-bp", type=float, default=10.0)
    ap.add_argument("--stop-loss-pct", type=float, default=None,
                    help="override RISK.stop_loss_pct (tuning, in-sample only)")
    ap.add_argument("--min-signal", type=float, default=0.0,
                    help="drop signals with combo_signal < this (tuning)")
    ap.add_argument("--days", type=int, default=900,
                    help="price history bars (900 matches the p4 harness "
                         "cache key -> instant cache hits)")
    ap.add_argument("--out", default="reports/p4_paper_trader.json")
    ap.add_argument("--persist", action="store_true",
                    help="write trades/portfolio snapshots into the store")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.demo:
        _demo()
        return

    t0 = time.time()
    signals = _load_signals_from_store(args.db, args.start, args.end)
    if args.min_signal > 0:
        n0 = len(signals)
        signals = [s for s in signals
                   if (s.get("combo_signal") or 0) >= args.min_signal]
        print(f"[p4t] min-signal {args.min_signal}: {n0} -> {len(signals)} signals")
    if not signals:
        print(f"[p4t] no rankable signals in {args.db} "
              f"[{args.start}..{args.end}] — run p4_combo_backtest --save-store first")
        sys.exit(1)
    codes = sorted({s["code"] for s in signals})
    print(f"[p4t] {len(signals)} signals, {len(codes)} codes "
          f"[{args.start}..{args.end or 'now'}]")
    prices = _load_prices(codes, args.days, not args.no_cache)

    store = Store(args.db) if args.persist else None
    try:
        trader = PaperTrader(prices, max_positions=args.max_positions,
                             hold=args.hold, slippage_bp=args.slippage_bp,
                             store=store, persist=args.persist,
                             stop_loss_pct=args.stop_loss_pct)
        stats = trader.replay(signals)
    finally:
        if store is not None:
            store.close()

    gate = {"sharpe": stats.get("sharpe"), "max_dd": stats.get("max_dd"),
            "profit_factor": stats.get("profit_factor"),
            "coverage_pct": stats.get("coverage_pct"),
            "sharpe_above_03": (stats.get("sharpe") or 0) > 0.3,
            "max_dd_below_30pct": (stats.get("max_dd") is not None
                                   and stats.get("max_dd") < 0.30),
            "profit_factor_above_15": (stats.get("profit_factor") or 0) > 1.5,
            "coverage_above_20pct": (stats.get("coverage_pct") or 0) >= 20.0}
    out = {"window": {"start": args.start, "end": args.end},
           "max_positions": args.max_positions, "hold": args.hold,
           "slippage_bp": args.slippage_bp, "n_signals": len(signals),
           "n_codes": len(codes), "gate": gate, "stats": stats,
           "elapsed_s": round(time.time() - t0, 1)}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[p4t] gate:", json.dumps(gate))
    print(f"[p4t] written -> {args.out}")


if __name__ == "__main__":
    main()