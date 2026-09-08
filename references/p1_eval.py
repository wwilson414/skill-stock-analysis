#!/usr/bin/env python3
"""
P1 evaluation harness (ROADMAP P1-5 / P1-6 / P1-8).

Answers, on the same 36-stock x 3.7y sample as P0 (offline via .p0_cache):
* P1-5  does the standalone MR signal (mr_signal.py) rank downtrend bounces?
        Acceptance: downtrend_decline IC > 0 with bootstrap CI excluding 0.
* P1-6  do the candidate components (volume surge / price-volume divergence /
        platform breakout / gap) add IC in non-downtrend phases?
* P1-8  does the negative momentum IC survive excess-return and ATR-normalized
        forward returns (i.e. is it a high-volatility artifact)?

Also runs robustness views: non-overlapping-window ICs, per-stock cross
sections, discrete MR event study.

Usage
-----
python3 references/p0_backtest.py-style:
  python3 references/p1_eval.py                     # fixed universe (cached)
  python3 references/p1_eval.py --universe random   # holdout validation
Summary JSON -> reports/p1_signal_results.json
"""

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mr_signal import compute_components
from p0_backtest import (_align_bench_to_stock, _cross_section, _fetch_stock_ohlcv,
                         _ic_stats, _load_or_fetch_bench, _log, build_universe,
                         label_market_regimes)
from stock_data_fetcher import backtest_stock

SIGNAL_FAMILIES = ["mom", "mr_score", "c_rsi", "c_dev", "c_stop",
                   "comp_vol", "comp_pv", "comp_brk", "comp_gap"]


def _num(v):
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def _bench_fwd_pct(bench_aligned: list, horizon: int) -> list:
    """Benchmark forward return (%) aligned to stock bar index."""
    n = len(bench_aligned)
    out = [None] * n
    for i in range(n):
        j = i + horizon
        if j < n and bench_aligned[i] and bench_aligned[j]:
            out[i] = (bench_aligned[j] / bench_aligned[i] - 1.0) * 100.0
    return out


def run_stock(task):
    """Worker: cached OHLCV -> backtest signals + MR/P1-6 components,
    joined per signal date. Returns {'code','market','rows':[...]}, or error."""
    entry, days, forward_days, bench_rows, use_cache = task
    code = entry["code"]
    rec = {"code": code, "market": entry["market"]}
    try:
        ohlcv, name, src = _fetch_stock_ohlcv(entry, days, use_cache)
        min_bars = 80 + max(forward_days) + 1
        if len(ohlcv) < min_bars:
            rec["error"] = f"insufficient_data ({len(ohlcv)} < {min_bars})"
            return rec
        bench_aligned, _misses = _align_bench_to_stock(ohlcv, bench_rows)
        calib = backtest_stock(code, days=days, forward_days=forward_days,
                               ohlcv_data=ohlcv, bench_closes=bench_aligned,
                               return_signals=True)
        if "error" in calib:
            rec["error"] = calib["error"]
            return rec
        comp = compute_components(ohlcv)
        date_idx = {b["date"]: i for i, b in enumerate(ohlcv)}
        rows = []
        for s in calib.get("signals", []):
            i = date_idx.get(s.get("date"))
            if i is None:
                continue
            fr = s.get("forward_returns") or {}
            row = {"code": code, "market": entry["market"],
                   "date": s["date"], "phase": s.get("phase", "unknown"),
                   "mom": _num(s.get("score_total")),
                   "atr_pct": _num(comp["atr_pct"][i]),
                   "mr_event": bool(comp["mr_event"][i])}
            for h in forward_days:
                row[f"fwd{h}"] = _num(fr.get(f"{h}d"))
            for fam in SIGNAL_FAMILIES:
                if fam == "mom":
                    continue
                v = comp[fam][i]
                row[fam] = _num(float(v)) if np.isfinite(v) else None
            rows.append(row)
        for h in forward_days:
            bf = _bench_fwd_pct(bench_aligned, h)
            for row in rows:
                row[f"bench{h}"] = _num(bf[date_idx[row["date"]]])
        rec["rows"] = rows
        rec["n"] = len(rows)
        rec["date_start"] = ohlcv[0]["date"]
        rec["date_end"] = ohlcv[-1]["date"]
        rec["source"] = src
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec



def _pairs(rows, fam, ykey):
    return [(r[fam], r[ykey]) for r in rows
            if r.get(fam) is not None and r.get(ykey) is not None]


def _cells(groups, fams, ykey, bootstrap):
    out = {}
    for k, rs in sorted(groups.items()):
        d = {"n": len(rs)}
        for fam in fams:
            d[fam] = _ic_stats(_pairs(rs, fam, ykey), bootstrap)
        out[k] = d
    return out


def _stats(r3, mean_k="mean"):
    v = [x for x in r3 if x is not None]
    if not v:
        return {"n": 0}
    v.sort()
    return {"n": len(v), "mean": round(sum(v) / len(v), 3),
            "median": round(v[len(v) // 2], 3),
            "win": round(100.0 * sum(1 for x in v if x > 0) / len(v), 1)}


def nonoverlap_rows(rows, step):
    """Per stock keep every `step`-th signal (date order) so 20d windows
    stop overlapping — bootstrap CI then reflects independent draws."""
    by_code = defaultdict(list)
    order = []
    for r in rows:
        if r["code"] not in by_code:
            order.append(r["code"])
        by_code[r["code"]].append(r)
    out = []
    for c in order:
        out.extend(by_code[c][::step])
    return out


def event_study(rows, forward_days):
    """Discrete MR-event study inside downtrend_decline, vs phase baseline."""
    out = {}
    base = [r for r in rows if r["phase"] == "downtrend_decline"]
    ev = [r for r in base if r["mr_event"]]
    for h in forward_days:
        yk = f"fwd{h}"
        out[f"fwd{h}"] = {
            "baseline": _stats([r.get(yk) for r in base]),
            "event": _stats([r.get(yk) for r in ev]),
        }
    # per-market breakdown on the main horizon (max)
    h = max(forward_days)
    yk = f"fwd{h}"
    by_mkt = defaultdict(list)
    for r in ev:
        by_mkt[r["market"]].append(r.get(yk))
    out[f"event_by_market_fwd{h}"] = {k: _stats(v) for k, v in by_mkt.items()}
    out["n_events_total"] = len(ev)
    out["n_downtrend_total"] = len(base)
    return out


def risk_adjusted(rows, forward_days, bootstrap):
    """P1-8: momentum IC on raw / excess (vs benchmark) / ATR-normalized."""
    h = max(forward_days)
    yk, bk = f"fwd{h}", f"bench{h}"
    variants = {"raw": lambda r: r.get(yk),
                "excess": lambda r: (r[yk] - r[bk])
                         if r.get(yk) is not None and r.get(bk) is not None else None,
                "atr_norm": lambda r: (r[yk] / (r["atr_pct"] * 100.0
                                                * math.sqrt(h)))
                         if r.get(yk) is not None and r.get("atr_pct") else None}
    groups = defaultdict(list)
    for r in rows:
        groups[r["phase"]].append(r)
    groups["__all__"] = rows
    out = {}
    for ph, rs in sorted(groups.items()):
        d = {"n": len(rs)}
        for vn, fn in variants.items():
            pairs = [(r["mom"], fn(r)) for r in rs if fn(r) is not None
                     and r.get("mom") is not None]
            d[vn] = _ic_stats(pairs, bootstrap)
        out[ph] = d
    return out


def component_composite(rows, forward_days, bootstrap):
    """P1-6: keep components whose pooled non-downtrend IC CI excludes 0
    (positively), then evaluate the equal-weight sum by phase."""
    h = max(forward_days)
    yk = f"fwd{h}"
    nondt = [r for r in rows if r["phase"] != "downtrend_decline"]
    chosen, detail = [], {}
    for fam in ("comp_vol", "comp_pv", "comp_brk", "comp_gap"):
        st = _ic_stats(_pairs(nondt, fam, yk), bootstrap)
        detail[fam] = st
        if st.get("ic") is not None and st.get("ci95") and st["ci95"][0] > 0:
            chosen.append(fam)
    out = {"horizon": yk, "pooled_nondowntrend": detail,
           "chosen": chosen,
           "combo_note": ("components with non-downtrend IC CI lower bound > 0; "
                          "equal-weight sum, no refitting") if chosen
                          else "no component qualified (CI lower bound <= 0)"}
    if chosen:
        for r in rows:
            vals = [r.get(f) for f in chosen]
            if all(v is not None for v in vals):
                r["combo"] = sum(vals)
            else:
                r["combo"] = None
        groups = defaultdict(list)
        for r in rows:
            groups[r["phase"]].append(r)
        groups["__all__"] = rows
        out["combo_by_phase"] = {
            ph: _ic_stats(_pairs(rs, "combo", yk), bootstrap)
            for ph, rs in sorted(groups.items())}
    return out



def main():
    ap = argparse.ArgumentParser(
        description="P1 evaluation harness (ROADMAP P1-5/P1-6/P1-8)")
    ap.add_argument("--universe", choices=["fixed", "random"], default="fixed")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pool-size", type=int, default=300)
    ap.add_argument("--sample-n", type=int, default=30)
    ap.add_argument("--codes", type=str, default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--bench-bars", type=int, default=1200)
    ap.add_argument("--forward-days", type=str, default="5,10,20")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--out", type=str, default="reports/p1_signal_results.json")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    forward_days = [int(x) for x in args.forward_days.split(",") if x.strip()]
    entries, rule, uni_meta = build_universe(args)
    if args.limit:
        entries = entries[:args.limit]
    _log(f"[p1] universe: {len(entries)} stocks | {rule[:90]}...")

    markets = sorted({e["market"] for e in entries})
    bench_rows_map = {}
    for m in markets:
        rows_b, src = _load_or_fetch_bench(m, args.bench_bars,
                                           not args.no_cache)
        bench_rows_map[m] = rows_b
    tasks = [(e, args.days, forward_days, bench_rows_map[e["market"]],
              not args.no_cache) for e in entries]

    records = []
    if args.parallel > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=args.parallel) as ex:
            futs = [ex.submit(run_stock, t) for t in tasks]
            for fut in as_completed(futs):
                rec = fut.result()
                if rec.get("error"):
                    _log(f"[p1] {rec['code']}: ERROR {rec['error']}")
                records.append(rec)
    else:
        for t in tasks:
            records.append(run_stock(t))
    records.sort(key=lambda r: r["code"])
    ok = [r for r in records if not r.get("error")]
    rows = [r for rec in ok for r in rec.get("rows", [])]
    _log(f"[p1] {len(ok)}/{len(records)} stocks, {len(rows)} joined signals "
         f"in {time.time() - t0:.0f}s")

    h = max(forward_days)
    yk = f"fwd{h}"
    fams = ["mom", "mr_score", "c_rsi", "c_dev", "c_stop"]

    # --- P1-5: MR signal by phase (primary acceptance) ---
    by_phase = defaultdict(list)
    by_mkt_phase = defaultdict(list)
    for r in rows:
        by_phase[r["phase"]].append(r)
        by_mkt_phase[f"{r['market']}|{r['phase']}"].append(r)
    p15 = {"horizon": yk,
           "by_phase": _cells(by_phase, fams, yk, args.bootstrap),
           "by_market_x_phase": _cells(by_mkt_phase, ["mr_score"], yk,
                                       args.bootstrap)}
    dt_ic = p15["by_phase"].get("downtrend_decline", {}).get("mr_score", {})
    dt_ci = dt_ic.get("ci95") or [None, None]
    p15["acceptance"] = {
        "criterion": "downtrend_decline mr_score IC > 0 and bootstrap CI low > 0",
        "ic": dt_ic.get("ic"), "ci95": dt_ci, "n": dt_ic.get("n"),
        "passed": bool(dt_ic.get("ic") is not None and dt_ci[0] is not None
                       and dt_ci[0] > 0),
    }
    p15["event_study"] = event_study(rows, forward_days)
    dt_by_code = defaultdict(list)
    for r in rows:
        if (r["phase"] == "downtrend_decline"
                and r.get("mr_score") is not None):
            dt_by_code[r["code"]].append(r)
    xs = []
    for c, rs in sorted(dt_by_code.items()):
        st = _ic_stats(_pairs(rs, "mr_score", yk), 0)
        if st.get("ic") is not None:
            xs.append(st["ic"])
    p15["cross_section_dt"] = _cross_section(xs)


    # --- P1-8: risk-adjusted momentum IC ---
    p18 = {"horizon": yk,
           "momentum_ic_variants": risk_adjusted(rows, forward_days,
                                                 args.bootstrap)}
    nonov = nonoverlap_rows(rows, h)
    nov = defaultdict(list)
    for r in nonov:
        nov[r["phase"]].append(r)
    p18["nonoverlap_20d_momentum"] = {
        "step": h, "n": len(nonov),
        "by_phase": {ph: _ic_stats(_pairs(rs, "mom", yk), args.bootstrap)
                     for ph, rs in sorted(nov.items())}}

    # --- P1-6: candidate components ---
    p16 = component_composite(rows, forward_days, args.bootstrap)

    # --- print summary ---
    _log("\n=== P1-5 standalone MR signal (20d) ===")
    for ph, d in p15["by_phase"].items():
        s = d.get("mr_score", {})
        _log(f"{ph:<22} ic={s.get('ic')} ci={s.get('ci95')} n={s.get('n')}")
    a = p15["acceptance"]
    _log(f"ACCEPTANCE P1-5: {'PASS' if a['passed'] else 'FAIL'} "
         f"(ic={a['ic']}, ci={a['ci95']}, n={a['n']})")
    es = p15["event_study"][yk]
    b, e = es["baseline"], es["event"]
    _log(f"event study {yk}: baseline mean={b.get('mean')}% (n={b.get('n')}) "
         f"-> event mean={e.get('mean')}% (n={e.get('n')})")
    _log("\n=== P1-8 momentum IC variants (20d) ===")
    for ph, d in p18["momentum_ic_variants"].items():
        _log(f"{ph:<22} raw={d['raw'].get('ic')} excess={d['excess'].get('ic')} "
             f"atr_norm={d['atr_norm'].get('ic')} n={d['n']}")
    _log("\n=== P1-6 candidate components (non-downtrend, 20d) ===")
    for fam, st in p16["pooled_nondowntrend"].items():
        _log(f"{fam:<10} ic={st.get('ic')} ci={st.get('ci95')} n={st.get('n')}")
    _log(f"chosen: {p16['chosen'] or 'none'}")

    output = {
        "generated_at": __import__("datetime").datetime.now().isoformat(
            timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 1),
        "args": dict(vars(args)),
        "universe": {"rule": rule, "meta": uni_meta},
        "p1_5": p15, "p1_6": p16, "p1_8": p18,
        "n_stocks_ok": len(ok), "n_stocks_total": len(records),
        "n_signals": len(rows),
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)
    _log(f"[p1] summary written -> {args.out}")


if __name__ == "__main__":
    main()
