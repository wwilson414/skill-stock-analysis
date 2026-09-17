#!/usr/bin/env python3
"""Out-of-time probability calibration for validated signal components.

This is a research harness only. It does not change combo ranking, position
sizing, or the production analysis path. Each feature gets its own isotonic
mapping fitted on the early time segment and evaluated on the later segment.
Combo rows with a blocked gate are excluded from combo calibration and their
coverage is reported explicitly.
"""

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import p0_backtest as p0
from mr_signal import compute_components
from score_calibration import brier, pava_fit, pava_predict, spearman, time_split
from signal_combo import combo_signal_series
from stock_data_fetcher import _MIN_BARS_FOR_INDICATORS, compute_signal_from_ohlcv


FEATURES = ("comp_vol", "mr_score", "score_total", "combo_score")


def _finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _forward_return(closes, index, horizon):
    if index + horizon >= len(closes) or not closes[index] or closes[index] <= 0:
        return None
    future = closes[index + horizon]
    return round((future / closes[index] - 1.0) * 100.0, 4) if future else None


def collect_stock_rows(entry, days, horizon, bench_rows, use_cache=True):
    """Build no-lookahead feature/forward-return rows for one stock."""
    ohlcv, _, _ = p0._fetch_stock_ohlcv(entry, days, use_cache)
    bench, _ = p0._align_bench_to_stock(ohlcv, bench_rows)
    min_bars = _MIN_BARS_FOR_INDICATORS + horizon
    if len(ohlcv) < min_bars:
        return []

    closes = [bar["close"] for bar in ohlcv]
    rows = []
    score_by_index = [None] * len(ohlcv)
    for index in range(_MIN_BARS_FOR_INDICATORS, len(ohlcv) - horizon):
        window = ohlcv[:index + 1]
        score = compute_signal_from_ohlcv(window, bench[:index + 1])
        if not score:
            continue
        score_total = _finite(score.get("total"))
        score_by_index[index] = score_total

    # Component arrays are trailing-only, so computing them on the full series
    # is equivalent to slicing at each bar and avoids an O(n^2) recomputation.
    components = compute_components(ohlcv)
    combo_series = combo_signal_series(ohlcv, mom_scores=score_by_index)
    for index in range(_MIN_BARS_FOR_INDICATORS, len(ohlcv) - horizon):
        score_total = score_by_index[index]
        if score_total is None:
            continue
        component_values = {
            "comp_vol": _finite(components["comp_vol"][index]),
            "mr_score": _finite(components["mr_score"][index]),
        }
        combo = combo_series[index]
        forward = _forward_return(closes, index, horizon)
        if forward is None:
            continue
        rows.append({
            "date": ohlcv[index].get("date"),
            "code": entry["code"],
            "market": entry["market"],
            "phase": score.get("phase", "unknown"),
            "comp_vol": component_values["comp_vol"],
            "mr_score": component_values["mr_score"],
            "score_total": score_total,
            "combo_score": _finite(combo.get("combo_score")) if combo else None,
            "combo_gate_blocked": bool(combo and combo.get("gate_blocked")),
            "forward_return": forward,
        })
    return rows


def calibrate_feature(rows, feature, train_frac=0.7):
    """Fit train-only isotonic calibration and evaluate on the OOT tail."""
    pairs = [(row["date"], row[feature], row["phase"], row["forward_return"])
             for row in rows if _finite(row.get(feature)) is not None
             and _finite(row.get("forward_return")) is not None]
    if len(pairs) < 10:
        return {"feature": feature, "status": "insufficient_data", "n": len(pairs)}
    train, oot, cutoff = time_split(pairs, train_frac)
    if not train or not oot:
        return {"feature": feature, "status": "insufficient_time_split",
                "n": len(pairs)}

    train_x = np.asarray([row[1] for row in train], dtype=float)
    train_y = np.asarray([row[3] > 0 for row in train], dtype=float)
    oot_x = np.asarray([row[1] for row in oot], dtype=float)
    oot_y = np.asarray([row[3] > 0 for row in oot], dtype=float)
    rho = spearman(train_x, train_y)
    flipped = rho < 0
    fit_x = -train_x if flipped else train_x
    points = pava_fit(fit_x, train_y)
    train_pred = [pava_predict(points, -x if flipped else x) for x in train_x]
    oot_pred = [pava_predict(points, -x if flipped else x) for x in oot_x]
    base_rate = float(oot_y.mean())
    base_brier = brier([base_rate] * len(oot_y), oot_y)
    oot_brier = brier(oot_pred, oot_y)
    return {
        "feature": feature,
        "status": "ok",
        "n": len(pairs),
        "train_n": len(train),
        "oot_n": len(oot),
        "cutoff_date": cutoff,
        "oot_date_start": min(row[0] for row in oot),
        "oot_date_end": max(row[0] for row in oot),
        "train_spearman": round(rho, 4),
        "sign_flipped": bool(flipped),
        "isotonic_blocks": len(points),
        "oot_base_rate": round(base_rate, 4),
        "brier_train_isotonic": brier(train_pred, train_y),
        "brier_oot_isotonic": oot_brier,
        "brier_oot_baseline": base_brier,
        "oot_brier_better_than_baseline": oot_brier < base_brier,
        "oot_brier_improvement": round(base_brier - oot_brier, 5),
    }


def run(args):
    class P0Args:
        codes = args.codes
        universe = args.universe
        pool_size = args.pool_size
        sample_n = args.sample_n
        seed = args.seed
        no_cache = args.no_cache

    entries, universe_rule, universe_meta = p0.build_universe(P0Args())
    if args.limit:
        entries = entries[:args.limit]
    markets = sorted({entry["market"] for entry in entries})
    benchmarks = {}
    for market in markets:
        benchmarks[market], _ = p0._load_or_fetch_bench(
            market, args.bench_bars, not args.no_cache)

    rows = []
    errors = []
    for entry in entries:
        try:
            rows.extend(collect_stock_rows(
                entry, args.days, args.horizon, benchmarks[entry["market"]],
                use_cache=not args.no_cache))
        except Exception as exc:
            errors.append({"code": entry["code"], "error": f"{type(exc).__name__}: {exc}"})

    results = {}
    for feature in FEATURES:
        results[feature] = calibrate_feature(rows, feature, args.train_frac)
    combo_candidates = sum(1 for row in rows if row.get("combo_score") is not None)
    output = {
        "experiment": "O-8 component/combo isotonic calibration",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "horizon_days": args.horizon,
        "train_frac": args.train_frac,
        "features": list(FEATURES),
        "universe": {"rule": universe_rule, "meta": universe_meta,
                     "n_requested": len(entries), "n_errors": len(errors)},
        "n_rows": len(rows),
        "combo_candidate_rows": combo_candidates,
        "combo_coverage_pct": round(100.0 * combo_candidates / len(rows), 2) if rows else 0.0,
        "errors": errors,
        "calibration": results,
        "verdict": {
            feature: results[feature].get("oot_brier_better_than_baseline", False)
            for feature in FEATURES
        },
    }
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="O-8 component probability calibration")
    parser.add_argument("--universe", choices=("fixed", "random"), default="fixed")
    parser.add_argument("--codes", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pool-size", type=int, default=300)
    parser.add_argument("--sample-n", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=900)
    parser.add_argument("--bench-bars", type=int, default=1200)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--out", default="reports/o8_component_calibration.json")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)
    output = run(args)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=1)
    print(json.dumps({"n_rows": output["n_rows"],
                      "combo_coverage_pct": output["combo_coverage_pct"],
                      "verdict": output["verdict"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()