#!/usr/bin/env python3
"""P2-12: rotation-portfolio simulation (ROADMAP P2).

Inputs : reports/p2_execution.json  (universe, horizon, fee table)
         references/.p0_cache       (per-stock OHLCV -> cache hits)
Rebuilds per-stock signal rows via p2.run_stock (cached), then simulates a
date-driven rotation portfolio:

  * max N concurrent positions (default 5)
  * long-only, next-open entry (variant in base/open/net_fee/net)
  * per-stock 1-position-at-a-time: skip entries overlapping an open slot
  * per-position hold = forward_days (20d); realized at exit date
  * daily mark-to-market: open position amortized linearly over hold window
    (standard proxy when only the endpoint return is known)
  * equal-risk unit notional per leg; cash accumulates realized proceeds

Reports total return, annualized Sharpe (252), max drawdown, turnover,
hit rate, phase attribution, and a base-vs-net fee drag table.

Usage
  python3 references/p2_schedule.py
  python3 references/p2_schedule.py --max-positions 3 --hold 10
Output: reports/p2_schedule.json
"""

import argparse
import json
import math
import os
import sys
import time
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import p2_execution as p2
from p0_backtest import (_log, build_universe, _load_or_fetch_bench)

PERIODS_PER_YEAR = 252


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _std(xs, ddof=1):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - ddof))


def _max_dd(series):
    series = [x for x in series if x is not None and x > 0]
    if not series:
        return None
    peak = series[0]
    dd = 0.0
    for v in series:
        if v > peak:
            peak = v
        dd = max(dd, (peak - v) / peak if peak > 0 else 0.0)
    return dd


def _build_rows(args):
    """Rebuild per-stock signal rows from cache via p2.run_stock."""
    forward_days = [int(x) for x in args.forward_days.split(",") if x.strip()]
    entries, _rule, _meta = build_universe(args)
    if args.limit:
        entries = entries[:args.limit]
    markets = sorted({e["market"] for e in entries})
    bench = {}
    for m in markets:
        rows_b, src = _load_or_fetch_bench(m, args.bench_bars,
                                           not args.no_cache)
        bench[m] = rows_b
        _log(f"[p2s] bench {m}: {len(rows_b)} bars")
    tasks = [(e, args.days, forward_days, bench[e["market"]],
              not args.no_cache, args.slippage_bp) for e in entries]
    recs = []
    if args.parallel > 1 and len(tasks) > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=args.parallel) as ex:
            for r in as_completed([ex.submit(p2.run_stock, t) for t in tasks]):
                recs.append(r.result())
    else:
        for t in tasks:
            recs.append(p2.run_stock(t))
    rows = []
    for rec in recs:
        if rec.get("error"):
            continue
        rows.extend(rec.get("rows", []))
    _log(f"[p2s] rebuilt {len(rows)} rows from cache")
    return rows, forward_days


def _simulate(rows, h, variant, family, max_pos, hold):
    """Equal-weight rotation portfolio. Signals ranked by `family` (score);
    entry/return use the `fwd{h}_{variant}` column. Returns stats dict."""
    ret_key = f"fwd{h}_{variant}"
    cands = [r for r in rows if r.get(ret_key) is not None
             and r.get(family) is not None]
    if not cands:
        return None
    dates = sorted({r["date"] for r in cands})
    idx = {d: i for i, d in enumerate(dates)}
    cal = len(dates)
    if cal < hold + 2:
        return None
    unit = 1.0 / max_pos          # equal-weight allocation per position
    cash = 1.0                    # start with 1.0 total capital
    active = {}                   # code -> (entry_idx, ret, phase)
    filled = []
    phase_counts = Counter()
    port = []
    for di, d in enumerate(dates):
        # 1) exit positions whose hold window ends today
        expired = [c for c, (e, _r, _p) in active.items()
                   if di >= e + hold]
        for c in expired:
            e, ret, _ph = active.pop(c)
            cash += unit * (1.0 + ret / 100.0)
        # 2) mark-to-market: cash + linearly-amortized active positions
        mv = cash
        for code, (e, ret, ph) in active.items():
            frac = (di - e) / hold
            mv += unit * (1.0 + ret / 100.0) * frac
        port.append(mv)
        # 3) enter top-score eligible candidates for today
        today = [r for r in cands if r["date"] == d
                 and r["code"] not in active and idx[d] + hold < cal]
        today.sort(key=lambda r: r.get(family, float("-inf")), reverse=True)
        room = max_pos - len(active)
        for r in today[:room]:
            if cash < unit:       # no capital left
                break
            ret = r[ret_key]
            active[r["code"]] = (di, ret, r.get("phase"))
            cash -= unit
            filled.append(ret)
            phase_counts[r.get("phase", "unknown")] += 1
    # close remaining at expiry
    for code, (e, ret, ph) in active.items():
        cash += unit * (1.0 + ret / 100.0)
    if port:
        port[-1] = cash

    total_ret = (port[-1] - port[0]) * 100.0 if port else 0.0
    daily = [port[i] / port[i - 1] - 1.0 for i in range(1, len(port))
             if port[i - 1] > 0]
    sd_daily = _std(daily)
    ann_vol = sd_daily * math.sqrt(PERIODS_PER_YEAR) * 100.0 if sd_daily > 0 else 0.0
    n_years = cal / PERIODS_PER_YEAR if cal > 0 else 1.0
    ann_ret = ((port[-1] / port[0]) ** (1.0 / n_years) - 1.0) * 100.0 \
        if port and port[0] > 0 else 0.0
    sharpe = ann_ret / ann_vol if ann_vol > 1e-9 else 0.0
    return {
        "n": len(filled),
        "total_ret": round(total_ret, 2),
        "annual_ret": round(ann_ret, 2),
        "annual_vol": round(ann_vol, 2),
        "sharpe": round(sharpe, 3),
        "max_dd": round(_max_dd(port) or 0.0, 4),
        "hit_rate": round(100.0 * sum(1 for r in filled if r > 0)
                         / len(filled), 1) if filled else None,
        "mean_ret": round(_mean(filled), 3) if filled else None,
        "n_candidates": len(cands),
        "phase_mix": dict(phase_counts),
    }


def run_schedule(rows, forward_days, args):
    h = max(forward_days)
    out = {"horizon_main": f"{h}d", "max_positions": args.max_positions,
           "hold_days": args.hold, "slippage_bp": args.slippage_bp}
    table = {}
    for fam in p2.FAMILIES:
        table[fam] = {}
        for var in p2.VARIANTS:
            st = _simulate(rows, h, var, fam, args.max_positions,
                           args.hold)
            table[fam][var] = st
            if st:
                _log(f"[p2s] {fam:<9} {var:<8} {json.dumps(st)}")
    out["rotation_table"] = table
    out["verdict"] = _verdict(table)
    return out


def _verdict(table):
    """P2-12 acceptance gates on the rotation portfolio."""
    def a(f, v, k):
        c = table.get(f, {}).get(v, {})
        return c.get(k) if isinstance(c, dict) else None

    v = {
        "mr_score_net_sharpe": a("mr_score", "net", "sharpe"),
        "mr_score_net_ann_ret": a("mr_score", "net", "annual_ret"),
        "mr_score_base_sharpe": a("mr_score", "base", "sharpe"),
        "comp_vol_net_ann_ret": a("comp_vol", "net", "annual_ret"),
        "comp_vol_net_sharpe": a("comp_vol", "net", "sharpe"),
        "comp_vol_base_sharpe": a("comp_vol", "base", "sharpe"),
        "mom_net_ann_ret": a("mom", "net", "annual_ret"),
        "mom_net_sharpe": a("mom", "net", "sharpe"),
    }
    surv = []
    if (v["mr_score_net_ann_ret"] or -9) > 0:
        surv.append("mr_score net annual ret>0")
    if (v["comp_vol_net_ann_ret"] or -9) > 0:
        surv.append("comp_vol net annual ret>0")
    v["survivors"] = surv or ["no family survives full execution pricing"]
    v["mom_still_negative"] = (v["mom_net_ann_ret"] or 0) < 0
    v["fee_drag_mr_score"] = (a("mr_score", "base", "annual_ret") or 0) - \
                              (a("mr_score", "net", "annual_ret") or 0)
    v["fee_drag_mom"] = (a("mom", "base", "annual_ret") or 0) - \
                        (a("mom", "net", "annual_ret") or 0)
    return v


def main():
    ap = argparse.ArgumentParser(description="P2-12 rotation-portfolio sim")
    ap.add_argument("--max-positions", type=int, default=5)
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--universe", choices=["fixed", "random"], default="fixed")
    ap.add_argument("--codes", type=str, default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--bench-bars", type=int, default=1200)
    ap.add_argument("--forward-days", type=str, default="5,10,20")
    ap.add_argument("--slippage-bp", type=int, default=10)
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--out", type=str, default="reports/p2_schedule.json")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    rows, forward_days = _build_rows(args)
    out = run_schedule(rows, forward_days, args)
    out["elapsed_s"] = round(time.time() - t0, 1)
    out["n_rows"] = len(rows)

    _log("\n=== P2-12 verdict ===")
    _log(f"survivors: {out['verdict']['survivors']}")
    _log(f"mom_still_negative: {out['verdict']['mom_still_negative']}")
    _log(f"fee_drag: mr_score={out['verdict']['fee_drag_mr_score']:.2f}pp "
         f"mom={out['verdict']['fee_drag_mom']:.2f}pp")
    for fam in p2.FAMILIES:
        _log(f"--- {fam} ---")
        for var, st in out["rotation_table"][fam].items():
            if st:
                _log(f"  {var:<8} ann={st['annual_ret']:>7.2f}% "
                     f"shp={st['sharpe']:>5.2f} dd={st['max_dd']:>5.1%} "
                     f"n={st['n']}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    _log(f"[p2s] written -> {args.out}")


if __name__ == "__main__":
    main()
