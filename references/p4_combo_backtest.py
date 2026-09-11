#!/usr/bin/env python3
"""P4-1b: combo vs single-signal backtest comparison (ROADMAP P4-1).

Rebuilds the P2 executable rows (same p2_execution.run_stock pricing) and
augments every row with the P4-1 phase-aware combo signal
(signal_combo.combo_signal_series, momentum scores aligned from calib's
score_total). Then runs the P2-12 rotation-portfolio simulator over the
same rows for opposed families:

    combo (weighted/gated phase switch)  vs  comp_vol / mr_score / mom

Verification (P4-1c, in reports/p4_combo_backtest.json):
    * combo net Sharpe must beat single comp_vol (~0.046), target > 0.08
    * combo max_dd < 75%
    * combo_phase must agree with the momentum backtest's phase on every row
      (the combination trusts the SAME classifier)

Usage
  python3 references/p4_combo_backtest.py
  python3 references/p4_combo_backtest.py --limit 6 --parallel 2   # smoke
  python3 references/p4_combo_backtest.py --save-store reports/signals.db
      # also persist every combo signal row into the P4-3 SQLite store,
      # so paper_trader.py (P4-4) can replay the out-of-sample gate.
Output: reports/p4_combo_backtest.json (+ signals.db when --save-store)
"""

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import p2_execution as p2
import p2_schedule as p2s
from p0_backtest import (_fetch_stock_ohlcv, _load_or_fetch_bench, _log,
                         build_universe)
from signal_combo import combo_signal_series

FAMILIES = ["combo"] + p2.FAMILIES


def run_stock_combo(task):
    """p2_execution.run_stock + P4-1 combo column.

    Row gains: combo, combo_phase, combo_gate, combo_blocked. Gate-blocked
    rows carry combo=None (excluded from the portfolio candidate set).
    """
    entry, days, forward_days, bench_rows, use_cache, slip_bp = task
    code, market = entry["code"], entry["market"]
    rec = {"code": code, "market": market}
    try:
        ohlcv, name, src = _fetch_stock_ohlcv(entry, days, use_cache)
        min_bars = 80 + max(forward_days) + 1
        if len(ohlcv) < min_bars:
            rec["error"] = f"insufficient_data ({len(ohlcv)} < {min_bars})"
            return rec
        bench_aligned, _m = p2._align_bench_to_stock(ohlcv, bench_rows)
        calib = p2.backtest_stock(code, days=days, forward_days=forward_days,
                                  ohlcv_data=ohlcv, bench_closes=bench_aligned,
                                  return_signals=True)
        if "error" in calib:
            rec["error"] = calib["error"]
            return rec
        # momentum scores aligned to ohlcv bars (score_total per date)
        mom_by_date = {s["date"]: s.get("score_total")
                       for s in calib.get("signals", [])}
        mom_scores = [mom_by_date.get(b.get("date")) for b in ohlcv]
        combo_series = combo_signal_series(ohlcv, mom_scores=mom_scores)
        comp = p2.compute_components(ohlcv)
        date_idx = {b["date"]: i for i, b in enumerate(ohlcv)}
        is_a = market == "cn_a"
        th = p2._limit_threshold_pct(code) if is_a else None
        th_dec = th / 100.0 if th is not None else None
        fee_b, fee_s = p2.FEE_TABLE[p2._market_group(market)]
        slip = slip_bp / 100.0
        n = len(ohlcv)
        rows = []
        for s in calib.get("signals", []):
            i = date_idx.get(s.get("date"))
            if i is None:
                continue
            j_entry = i + 1 if is_a else i
            row = {"code": code, "market": market, "date": s["date"],
                   "phase": s.get("phase", "unknown"),
                   "mom": p2._num(s.get("score_total")),
                   "mr_score": p2._num(float(comp["mr_score"][i]))
                   if np.isfinite(comp["mr_score"][i]) else None,
                   "comp_vol": p2._num(float(comp["comp_vol"][i]))
                   if np.isfinite(comp["comp_vol"][i]) else None,
                   "exit_delayed": False, "entry_skipped": False}
            combo = combo_series[i] if i < len(combo_series) else None
            row["combo_phase"] = combo["phase"] if combo else None
            row["combo_gate"] = combo["gate_results"] if combo else {}
            row["combo_blocked"] = bool(combo and combo["gate_blocked"])
            row["combo"] = (combo["combo_score"]
                            if combo and not combo["gate_blocked"]
                            and combo["combo_score"] is not None else None)
            row["combo_weight"] = combo["weight"] if combo else None
            # entry executability (A-share T+1: cannot buy a limit-up open)
            if is_a and j_entry < n and th_dec is not None:
                prev_c = p2._num(ohlcv[i].get("close"))
                open_e = p2._num(ohlcv[j_entry].get("open"))
                if prev_c and open_e and open_e >= prev_c * (1 + th_dec - 0.002):
                    row["entry_skipped"] = True
            delays = {}
            for h in forward_days:
                j = i + h
                d = 0
                while (is_a and th_dec is not None and d < 5 and j + d < n - 1):
                    c_exit = p2._num(ohlcv[j + d].get("close"))
                    c_prev = p2._num(ohlcv[j + d - 1].get("close"))
                    if c_exit is not None and c_prev is not None \
                            and c_exit <= c_prev * (1 - th_dec + 0.002):
                        d += 1
                    else:
                        break
                delays[h] = d
            row["exit_delayed"] = any(v > 0 for v in delays.values())
            oi = j_entry if j_entry < n else None
            open_px = p2._num(ohlcv[oi].get("open")) if oi is not None else None
            for h in forward_days:
                j = i + h
                jd = j + delays[h]
                c0 = p2._num(ohlcv[i].get("close"))
                cb = p2._num(ohlcv[j].get("close")) if j < n else None
                ce = p2._num(ohlcv[jd].get("close")) if jd < n else None
                base = (cb / c0 - 1) * 100 if c0 and cb else None
                if row["entry_skipped"] or open_px is None or ce is None:
                    openg = None
                else:
                    openg = (ce / open_px - 1) * 100
                if openg is None:
                    net_fee = net = None
                else:
                    net_fee = openg - fee_b - fee_s
                    net = openg - fee_b - fee_s - 2 * slip
                row[f"fwd{h}_base"] = base
                row[f"fwd{h}_open"] = openg
                row[f"fwd{h}_net_fee"] = net_fee
                row[f"fwd{h}_net"] = net
            rows.append(row)
        rec["rows"] = rows
        rec["n"] = len(rows)
        rec["source"] = src
    except Exception as e:      # network/code defects -> skip the stock
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def _build_rows_combo(args):
    """Per-stock rows with combo column (cached OHLCV, sequential or pool)."""
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
        _log(f"[p4b] bench {m}: {len(rows_b)} bars")
    tasks = [(e, args.days, forward_days, bench[e["market"]],
              not args.no_cache, args.slippage_bp) for e in entries]
    recs = []
    if args.parallel > 1 and len(tasks) > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=args.parallel) as ex:
            for r in as_completed([ex.submit(run_stock_combo, t) for t in tasks]):
                recs.append(r.result())
    else:
        for t in tasks:
            recs.append(run_stock_combo(t))
    rows = []
    for rec in recs:
        if rec.get("error"):
            _log(f"[p4b] {rec['code']}: SKIP {rec['error']}")
            continue
        rows.extend(rec.get("rows", []))
    _log(f"[p4b] rebuilt {len(rows)} rows from cache")
    return rows, forward_days, recs
def _phase_agreement(rows):
    """combo_phase vs backtest phase agreement on signal rows."""
    agree = disagree = n_missing = 0
    per_phase = {}
    for r in rows:
        cp, bp = r.get("combo_phase"), r.get("phase")
        if cp is None:
            n_missing += 1
            continue
        per_phase.setdefault(bp, [0, 0])
        per_phase[bp][1] += 1
        if cp == bp:
            agree += 1
            per_phase[bp][0] += 1
        else:
            disagree += 1
    total = agree + disagree
    return {
        "agree": agree, "disagree": disagree,
        "agreement_pct": round(100.0 * agree / total, 2) if total else None,
        "per_phase": {k: {"agree": v[0], "total": v[1]} for k, v in
                      sorted(per_phase.items())},
    }


def _verdict(table, agreement):
    a = lambda f, v, k: (table.get(f, {}).get(v, {}) or {}).get(k)
    net_c = a("combo", "net", "sharpe")
    net_v = a("comp_vol", "net", "sharpe")
    dd_c = a("combo", "net", "max_dd")
    return {
        "combo_net_sharpe": net_c,
        "combo_net_annual_ret": a("combo", "net", "annual_ret"),
        "combo_net_max_dd": dd_c,
        "combo_net_hit_rate": a("combo", "net", "hit_rate"),
        "comp_vol_net_sharpe": net_v,
        "combo_beats_comp_vol": (net_c is not None and net_v is not None
                                 and net_c > net_v),
        "combo_sharpe_above_008": (net_c is not None and net_c > 0.08),
        "combo_max_dd_below_75pct": (dd_c is not None and dd_c < 0.75),
        "phase_agreement": agreement,
    }


def main():
    ap = argparse.ArgumentParser(description="P4-1b combo vs single backtest")
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
    ap.add_argument("--out", type=str, default="reports/p4_combo_backtest.json")
    ap.add_argument("--save-store", default=None,
                    help="SQLite path to persist combo signal rows "
                         "(P4-3 store, e.g. reports/signals.db)")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    rows, forward_days, recs = _build_rows_combo(args)
    h = max(forward_days)
    out = {"horizon_main": f"{h}d", "max_positions": args.max_positions,
           "hold_days": args.hold, "slippage_bp": args.slippage_bp,
           "n_rows": len(rows), "n_stocks_ok": sum(1 for r in recs
                                                   if not r.get("error")),
           "n_stocks_skip": sum(1 for r in recs if r.get("error"))}
    table = {}
    for fam in FAMILIES:
        table[fam] = {}
        for var in p2.VARIANTS:
            st = p2s._simulate(rows, h, var, fam, args.max_positions,
                               args.hold)
            table[fam][var] = st
            if st:
                _log(f"[p4b] {fam:<9} {var:<8} ann={st['annual_ret']:>7.2f}% "
                     f"shp={st['sharpe']:>5.3f} dd={st['max_dd']:>5.1%} "
                     f"n={st['n']}")
    out["rotation_table"] = table
    out["phase_agreement"] = _phase_agreement(rows)
    out["verdict"] = _verdict(table, out["phase_agreement"])

    _log("\n=== P4-1c verdict ===")
    for k, v in out["verdict"].items():
        _log(f"  {k}: {v}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    _log(f"[p4b] written -> {args.out}")

    if args.save_store:
        _save_rows_to_store(rows, args.save_store)


def _save_rows_to_store(rows: list, db: str) -> None:
    """Persist combo signal rows into the P4-3 SQLite store (upsert).

    Every rebuilt row (including warmup days with combo_signal=None) is mapped
    by store.row_to_signal and upserted on (date, code) — re-running the
    harness never duplicates history. Gate-blocked bars are kept as rows with
    gate_blocked=1 so paper_trader can account for skipped candidates.
    """
    from store import Store
    for r in rows:
        r.setdefault("source", "p4_backtest")
    with Store(db) as st:
        n = st.save_signals(rows)
        latest = st.latest_signal_date()
    _log(f"[p4b] saved {n} rows -> {db}")
    _log(f"[p4b]   latest_signal_date: {latest}")


if __name__ == "__main__":
    main()