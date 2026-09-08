#!/usr/bin/env python3
"""
P2 execution-realism harness (ROADMAP P2-9 / P2-10 / P2-11).

Re-prices every P0/P1 signal under an executable path and quantifies the
drag, per horizon x phase x signal family (mom / mr_score / comp_vol):

  base    close(t) -> close(t+h)               (P0/P1 convention, gross)
  open    open(t+1) -> close(t+h)              (P2-11 next-open entry, gross)
  net_fee open entry + fees, no slippage       (P2-9 fees only)
  net     open entry + fees + slippage         (full executable return)

Execution rules
---------------
* A-share entries: T+1 -> fill at next-day open ONLY if that open is not
  limit-up sealed (open >= prev_close * (1 + (th-0.2)/100) -> skipped,
  th = 20% STAR/ChiNext, 10% main board, 5% ST; same rule as
  calc_tradability). Skipped entries are excluded from return stats and
  counted in exec_stats (they are trades you could never have).
* A-share exits: if exit-day close is limit-down sealed, the sale rolls to
  the next day's close, up to 5 rolls (recorded as exit_delayed).
* HK/US: no price limits, T+0; entry at next open as well.
* Fees (per side): cn_a commission 2.5bp, sell stamp 5bp; hk 12bp each
  side (commission+levies+stamp avg); us 2bp each side. Slippage default
  10bp per side (--slippage-bp), reported separately so fee-only drag is
  visible.

Reading the output
------------------
Pearson IC is invariant to a constant per-trade cost, so costs show up in
MEANS (net edge sign), while IC shifts come from the next-open re-pricing
and the limit-up skip selection effect. The harness reports both.

Usage
-----
python3 references/p2_execution.py                 # fixed universe (cached)
python3 references/p2_execution.py --slippage-bp 0 # isolate fee-only drag
Summary JSON -> reports/p2_execution.json
"""

import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mr_signal import compute_components
from p0_backtest import (_align_bench_to_stock, _fetch_stock_ohlcv, _ic_stats,
                         _load_or_fetch_bench, _log, build_universe)
from stock_data_fetcher import backtest_stock

FAMILIES = ["mom", "mr_score", "comp_vol"]
VARIANTS = ["base", "open", "net_fee", "net"]

# per-side cost buckets, percent of notional: (buy, sell)
# cn_a: commission 2.5bp both sides + 5bp stamp tax on sell -> RT ~10bp
# cn_hk: commission+levies+stamp ~12bp per side; us: ~2bp per side
FEE_TABLE = {
    "cn_a": (0.025, 0.025 + 0.05),
    "cn_hk": (0.12, 0.12),
    "us": (0.02, 0.02),
}


def _limit_threshold_pct(code: str) -> float:
    """A-share daily price-limit threshold (mirrors calc_tradability)."""
    if code.startswith(("688", "689", "300", "301", "302")):
        return 20.0
    if code.startswith(("43", "83", "87", "88", "92")):
        return 30.0
    return 10.0


def _market_group(market: str) -> str:
    return market  # cn_a / cn_hk / us already group correctly


def _num(v):
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None



def run_stock(task):
    """Worker: produce per-signal variant returns + exec flags for one stock.

    Row: {code, market, date, phase, mom, mr_score, comp_vol,
          fwd{h} x variant, open_gap, exit_delayed, entry_skipped}
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
        bench_aligned, _m = _align_bench_to_stock(ohlcv, bench_rows)
        calib = backtest_stock(code, days=days, forward_days=forward_days,
                               ohlcv_data=ohlcv, bench_closes=bench_aligned,
                               return_signals=True)
        if "error" in calib:
            rec["error"] = calib["error"]
            return rec
        comp = compute_components(ohlcv)
        date_idx = {b["date"]: i for i, b in enumerate(ohlcv)}
        is_a = market == "cn_a"
        th = _limit_threshold_pct(code) if is_a else None
        th_dec = th / 100.0 if th is not None else None
        fee_b, fee_s = FEE_TABLE[_market_group(market)]
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
                   "mom": _num(s.get("score_total")),
                   "mr_score": _num(float(comp["mr_score"][i]))
                   if np.isfinite(comp["mr_score"][i]) else None,
                   "comp_vol": _num(float(comp["comp_vol"][i]))
                   if np.isfinite(comp["comp_vol"][i]) else None,
                   "exit_delayed": False, "entry_skipped": False}
            # entry executability (A-share T+1: cannot buy a limit-up open;
            # near-limit heads are allowed but pay the observed open price)
            if is_a and j_entry < n and th_dec is not None:
                prev_c = _num(ohlcv[i].get("close"))
                open_e = _num(ohlcv[j_entry].get("open"))
                if prev_c and open_e and open_e >= prev_c * (1 + th_dec - 0.002):
                    row["entry_skipped"] = True
            # exit rolling for limit-down seals (A-share only)
            delays = {}
            for h in forward_days:
                j = i + h
                d = 0
                while (is_a and th_dec is not None and d < 5 and j + d < n - 1):
                    c_exit = _num(ohlcv[j + d].get("close"))
                    c_prev = _num(ohlcv[j + d - 1].get("close"))
                    if c_exit is not None and c_prev is not None \
                            and c_exit <= c_prev * (1 - th_dec + 0.002):
                        d += 1
                    else:
                        break
                delays[h] = d
            row["exit_delayed"] = any(v > 0 for v in delays.values())
            # open entry price (P2-11)
            oi = j_entry if j_entry < n else None
            open_px = _num(ohlcv[oi].get("open")) if oi is not None else None
            for h in forward_days:
                j = i + h
                jd = j + delays[h]
                c0 = _num(ohlcv[i].get("close"))
                # base keeps the P0 close-to-close convention (no exit delay);
                # executable variants fill at next open and honor limit-down
                # exit rolls (jd).
                cb = _num(ohlcv[j].get("close")) if j < n else None
                ce = _num(ohlcv[jd].get("close")) if jd < n else None
                base = (cb / c0 - 1) * 100 if c0 and cb else None
                if row["entry_skipped"] or open_px is None or ce is None:
                    openg = None
                else:
                    openg = (ce / open_px - 1) * 100
                # net = open gross - fees - slippage (per-side approx on
                # round trip; fee_b on buy, fee_s on sell)
                if openg is None:
                    net_fee = net = None
                else:
                    net_fee = openg - fee_b - fee_s
                    net = openg - fee_b - fee_s - 2 * slip
                row[f"fwd{h}_base"] = base
                row[f"fwd{h}_open"] = openg
                row[f"fwd{h}_net_fee"] = net_fee
                row[f"fwd{h}_net"] = net
            row["open_gap"] = (_num((open_px / ohlcv[i]["close"] - 1) * 100)
                               if open_px and _num(ohlcv[i].get("close")) else None)
            rows.append(row)
        rec["rows"] = rows
        rec["n"] = len(rows)
        rec["source"] = src
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def _mean(v):
    v = [x for x in v if x is not None]
    return round(sum(v) / len(v), 3) if v else None


def _tercile_eval(sub, fam, key, bootstrap):
    """IC on all + top/bottom-tercile mean returns by family score."""
    vals = [(r.get(fam), r.get(key)) for r in sub
            if r.get(fam) is not None and r.get(key) is not None]
    out = {"n": len(vals)}
    if len(vals) < 30:
        return out
    out["ic"] = _ic_stats(vals, bootstrap)
    s = sorted(vals, key=lambda p: p[0])
    k = max(1, len(s) // 3)
    top = [y for _, y in s[-k:]]
    bot = [y for _, y in s[:k]]
    out["top_mean"] = _mean(top)
    out["bot_mean"] = _mean(bot)
    if out["top_mean"] is not None and out["bot_mean"] is not None:
        out["spread"] = round(out["top_mean"] - out["bot_mean"], 3)
    return out


def aggregate(records, forward_days, bootstrap):
    rows = [r for rec in records if not rec.get("error") for r in rec.get("rows", [])]
    h_main = max(forward_days)
    out = {"n_signals": len(rows), "horizon_main": f"{h_main}d"}

    # -- P2-10: executability stats
    ex = defaultdict(lambda: {"n": 0, "skipped": 0, "delayed": 0})
    for r in rows:
        e = ex[r["market"]]
        e["n"] += 1
        e["skipped"] += 1 if r["entry_skipped"] else 0
        e["delayed"] += 1 if r["exit_delayed"] else 0
    out["exec_stats"] = {k: {**v,
                             "skip_pct": round(100 * v["skipped"] / v["n"], 2),
                             "delay_pct": round(100 * v["delayed"] / v["n"], 2)}
                         for k, v in sorted(ex.items()) if v["n"]}

    # unattainable trades: limit-up-open entries you could never fill
    skipped = [r for r in rows if r["entry_skipped"]]
    out["skipped_entry_profile"] = {
        "n": len(skipped),
        "mean_base_fwd": _mean([r.get(f"fwd{h_main}_base") for r in skipped]),
        "mean_open_gap": _mean([r.get("open_gap") for r in skipped]),
        "phase_mix": dict(Counter(r["phase"] for r in skipped).items()),
    }

    # -- variant means without any selection (pure cost drag on the universe)
    by_mkt = defaultdict(list)
    for r in rows:
        by_mkt[r["market"]].append(r)
    vm = {}
    for mkt, rs in sorted(by_mkt.items()):
        vm[mkt] = {v: _mean([r.get(f"fwd{h_main}_{v}") for r in rs])
                   for v in VARIANTS}
    out["variant_means_all"] = vm

    # -- variant x phase x family (IC + tercile means per variant)
    groups = defaultdict(list)
    for r in rows:
        groups[r["phase"]].append(r)
    groups["__all__"] = rows
    table = {}
    for ph, rs in sorted(groups.items()):
        d = {"n": len(rs)}
        for fam in FAMILIES:
            d[fam] = {v: _tercile_eval(rs, fam, f"fwd{h_main}_{v}", bootstrap)
                      for v in VARIANTS}
        table[ph] = d
    out["phase_family_variants"] = table

    # -- P2-11: open-vs-base repricing by signal strength (adverse-open check)
    adverse = {}
    for fam in FAMILIES:
        sub = [r for r in rows if r.get(fam) is not None
               and r.get("open_gap") is not None]
        if len(sub) < 100:
            continue
        s = sorted(sub, key=lambda r: r[fam])
        k = max(1, len(s) // 3)
        adverse[fam] = {
            "open_gap_top_tercile": _mean([r["open_gap"] for r in s[-k:]]),
            "open_gap_bot_tercile": _mean([r["open_gap"] for r in s[:k]]),
        }
    out["adverse_open_check"] = adverse
    return out


def verdict(agg):
    """P2 acceptance summary: what survives full executable pricing?"""
    h = agg["horizon_main"]
    tbl = agg["phase_family_variants"]
    dn = tbl.get("downtrend_decline", {})
    ndt = {k: v for k, v in tbl.items()
           if k not in ("downtrend_decline", "__all__", "unknown", "warmup")}

    def topnet(phase_d, fam):
        vals = []
        for ph, d in phase_d.items():
            st = d.get(fam, {}).get("net", {})
            if st.get("top_mean") is not None:
                vals.append(st["top_mean"])
        return _mean(vals) if vals else None

    checks = {
        "mr_downtrend_net_top_mean":
            topnet({"d": dn}, "mr_score") if dn else None,
        "compvol_nondt_net_top_mean": topnet(ndt, "comp_vol"),
        "momentum_net_spread_all":
            (tbl.get("__all__", {}).get("mom", {}).get("net", {})
             .get("spread")),
        "momentum_base_spread_all":
            (tbl.get("__all__", {}).get("mom", {}).get("base", {})
             .get("spread")),
    }
    a_cn = agg["exec_stats"].get("cn_a", {})
    checks["cn_a_skip_pct"] = a_cn.get("skip_pct")
    checks["cn_a_delay_pct"] = a_cn.get("delay_pct")
    vm = agg["variant_means_all"]
    if "cn_a" in vm and vm["cn_a"].get("base") is not None \
            and vm["cn_a"].get("open") is not None:
        checks["cn_a_open_entry_drag"] = round(vm["cn_a"]["open"]
                                               - vm["cn_a"]["base"], 3)
    if "cn_a" in vm and vm["cn_a"].get("open") is not None \
            and vm["cn_a"].get("net") is not None:
        checks["cn_a_cost_drag"] = round(vm["cn_a"]["net"]
                                         - vm["cn_a"]["open"], 3)
    surv = []
    if (checks["mr_downtrend_net_top_mean"] or 0) > 0:
        surv.append("mr_score downtrend net edge survives")
    if (checks["compvol_nondt_net_top_mean"] or 0) > 0:
        surv.append("comp_vol non-downtrend net edge survives")
    checks["survivors"] = surv or ["no family keeps a positive net top-tercile "
                                   "mean in its target regime"]
    return checks


def _fmt(st):
    if not st or st.get("top_mean") is None:
        return "n/a"
    s = f"top={st['top_mean']}"
    if st.get("bot_mean") is not None:
        s += f" bot={st['bot_mean']} spread={st.get('spread')}"
    if st.get("ic", {}) and st["ic"].get("ic") is not None:
        s += f" ic={st['ic']['ic']}"
    return s + f" n={st['n']}"


def main():
    ap = argparse.ArgumentParser(
        description="P2 execution-realism harness (ROADMAP P2-9/10/11)")
    ap.add_argument("--universe", choices=["fixed", "random"], default="fixed")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pool-size", type=int, default=300)
    ap.add_argument("--sample-n", type=int, default=30)
    ap.add_argument("--codes", type=str, default="",
                    help="comma-separated explicit universe override")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--bench-bars", type=int, default=1200)
    ap.add_argument("--forward-days", type=str, default="5,10,20")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--slippage-bp", type=float, default=10.0)
    ap.add_argument("--out", type=str, default="reports/p2_execution.json")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    forward_days = [int(x) for x in args.forward_days.split(",") if x.strip()]
    entries, rule, _meta = build_universe(args)
    if args.limit:
        entries = entries[:args.limit]
    _log(f"[p2] universe: {len(entries)} | slip={args.slippage_bp}bp/side")

    markets = sorted({e["market"] for e in entries})
    bench_map = {}
    for m in markets:
        rows, src = _load_or_fetch_bench(m, args.bench_bars, not args.no_cache)
        bench_map[m] = rows
        _log(f"[p2] bench {m}: {len(rows)} bars via {src}")

    tasks = [(e, args.days, forward_days, bench_map[e["market"]],
              not args.no_cache, args.slippage_bp) for e in entries]
    records = []
    if args.parallel > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=args.parallel) as ex:
            futs = {ex.submit(run_stock, t): t[0]["code"] for t in tasks}
            for fut in as_completed(futs):
                rec = fut.result()
                if rec.get("error"):
                    _log(f"[p2] {rec['code']}: ERROR {rec['error']}")
                records.append(rec)
    else:
        for t in tasks:
            records.append(run_stock(t))
    records.sort(key=lambda r: r["code"])
    ok = [r for r in records if not r.get("error")]
    _log(f"[p2] {len(ok)}/{len(records)} stocks in {time.time() - t0:.0f}s")

    agg = aggregate(records, forward_days, args.bootstrap)
    checks = verdict(agg)

    _log(f"\n=== P2 Executable Repricing ({agg['horizon_main']}, "
         f"n={agg['n_signals']}) ===")
    _log("exec_stats: " + json.dumps(agg["exec_stats"]))
    _log("skipped-entry profile (unattainable trades): "
         + json.dumps(agg["skipped_entry_profile"]))
    for mkt, vm in agg["variant_means_all"].items():
        _log(f"means[{mkt}] " + " ".join(f"{v}={vm[v]}" for v in VARIANTS))
    for ph, d in agg["phase_family_variants"].items():
        for fam in FAMILIES:
            line = " | ".join(f"{v}:{_fmt(d[fam][v])}" for v in VARIANTS)
            _log(f"{ph:<20} {fam:<9} {line}")
    _log("adverse-open: " + json.dumps(agg["adverse_open_check"]))
    _log(f"\nVERDICT: {json.dumps(checks, ensure_ascii=False)}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({"generated_at": datetime.now().isoformat(timespec="seconds"),
                   "elapsed_s": round(time.time() - t0, 1),
                   "args": vars(args), "universe_rule": rule,
                   "fee_table": FEE_TABLE, "checks": checks,
                   "aggregate": agg,
                   "per_stock": [{k: v for k, v in r.items() if k != "rows"}
                                 for r in records]}, f,
                  ensure_ascii=False, indent=1)
    _log(f"[p2] written -> {args.out}")


if __name__ == "__main__":
    main()
