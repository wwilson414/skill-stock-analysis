#!/usr/bin/env python3
"""
Score -> probability calibration (ROADMAP P1-7).

Input: raw per-signal dumps produced by p0_backtest.py --dump-signals
       (rows: [date, score_total, phase, fwd5, fwd10, fwd20, ...]).

What it builds (train split only; evaluation on out-of-time tail):
  * binned empirical calibration table (any shape, primary artifact)
  * isotonic regression (PAVA, sign chosen on train) as a compact mapping
  * logistic regression on [1, s, s^2] (hand-written full-batch GD)

Evaluation: Brier score train vs out-of-time, reliability table by
predicted-probability quintile, and an actionable threshold table
(75+/60-75/... x phase) with empirical P(20d up) and mean forward return.

Usage:
  python3 references/score_calibration.py \
      --signals reports/signals_p0_fixed.json --horizon 20
Output: reports/calibration_20d.json
"""

import argparse
import json
import math
import os
from datetime import datetime

import numpy as np

MIN_BIN_N = 30


def load_pairs(path, horizon):
    """-> list of (date, score, phase, fwd%)."""
    data = json.load(open(path))
    col = 3 + horizon_index(data, horizon)
    pairs = []
    for rec in data:
        for s in rec.get("signals", []):
            if s[col] is not None and s[1] is not None:
                pairs.append((s[0], float(s[1]), s[2], float(s[col])))
    return pairs


def horizon_index(data, horizon):
    rec = next(r for r in data if r.get("signals"))
    sig = rec["signals"][0]
    n_slots = len(sig) - 3
    # slots are ordered as produced (5,10,20); map horizon to nearest slot
    slots = [5 + 5 * i for i in range(n_slots)] if n_slots <= 3 else \
            list(range(5, 5 + 5 * n_slots, 5))
    best = min(range(n_slots), key=lambda i: abs(slots[i] - horizon))
    return best


def time_split(pairs, train_frac):
    dates = sorted({p[0] for p in pairs})
    cut = dates[int(len(dates) * train_frac)]
    train = [p for p in pairs if p[0] < cut]
    oot = [p for p in pairs if p[0] >= cut]
    return train, oot, cut


def binned_table(pairs, bin_w=5.0):
    bins = {}
    for _, s, ph, y in pairs:
        key = int(math.floor(s / bin_w) * bin_w)
        bins.setdefault(key, []).append((ph, y))
    out = []
    for lo in sorted(bins):
        rows = bins[lo]
        if len(rows) < MIN_BIN_N:
            out.append({"lo": lo, "hi": lo + bin_w, "n": len(rows),
                        "p_up": None, "mean_fwd": None})
            continue
        ys = [y for _, y in rows]
        out.append({"lo": lo, "hi": lo + bin_w, "n": len(ys),
                    "p_up": round(float(np.mean(np.array(ys) > 0)), 4),
                    "mean_fwd": round(float(np.mean(ys)), 3),
                    "win_lb95": round(float(np.mean(np.array(ys) > 0)
                                            - 1.96 * math.sqrt(
                          np.mean(np.array(ys) > 0) * (1 - np.mean(
                              np.array(ys) > 0)) / len(ys))), 4)})
    return out


def bin_lookup(table, s):
    best = None
    for b in table:
        if b["lo"] <= s < b["hi"]:
            best = b
            break
    if best is None:
        best = table[0] if s < table[0]["lo"] else table[-1]
    return best


def pava_fit(xs, ys):
    """Isotonic regression (non-decreasing) via pool-adjacent-violators.
    Returns step-function points [(x_threshold, value), ...]."""
    order = np.argsort(np.asarray(xs), kind="stable")
    blocks = []
    for i in order:
        blocks.append([float(ys[i]), 1.0, float(xs[i])])
        while (len(blocks) >= 2
               and blocks[-2][0] / blocks[-2][1]
               > blocks[-1][0] / blocks[-1][1] + 1e-15):
            s2, n2, x2 = blocks.pop()
            s1, n1, x1 = blocks.pop()
            blocks.append([s1 + s2, n1 + n2, x2])
    return [(x, s / n) for s, n, x in blocks]


def pava_predict(points, x):
    v = points[0][1]
    for xl, val in points:
        if x >= xl:
            v = val
        else:
            break
    return float(v)


def spearman(a, b):
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])



def logistic_fit(xs, ys, epochs=4000, lr=0.5):
    """Full-batch gradient descent on [1, z, z^2], z = standardized score.
    Returns (w, z_mean, z_sd)."""
    x = np.asarray(xs, dtype=float)
    mu, sd = float(x.mean()), float(x.std()) or 1.0
    z = (x - mu) / sd
    X = np.column_stack([np.ones_like(z), z, z * z])
    y = np.asarray(ys, dtype=float)
    w = np.zeros(3)
    for _ in range(epochs):
        p = 1.0 / (1.0 + np.exp(-np.clip(X @ w, -30, 30)))
        w -= lr * (X.T @ (p - y)) / len(y)
    return w, mu, sd


def logistic_predict(model, x):
    w, mu, sd = model
    z = (np.asarray(x, dtype=float) - mu) / sd
    X = np.column_stack([np.ones_like(z), z, z * z])
    return 1.0 / (1.0 + np.exp(-np.clip(X @ w, -30, 30)))


def brier(preds, ys):
    p = np.asarray(preds, dtype=float)
    y = (np.asarray(ys, dtype=float) > 0).astype(float)
    return round(float(np.mean((p - y) ** 2)), 5)


def reliability(preds, ys, k=5):
    """Bucket by predicted-probability rank; compare mean pred vs realized."""
    p = np.asarray(preds, dtype=float)
    y = (np.asarray(ys, dtype=float) > 0).astype(float)
    order = np.argsort(p)
    out = []
    for chunk in np.array_split(order, k):
        if len(chunk) == 0:
            continue
        out.append({"n": int(len(chunk)),
                    "mean_pred": round(float(p[chunk].mean()), 4),
                    "realized_p_up": round(float(y[chunk].mean()), 4)})
    return out



def threshold_table(pairs):
    """Empirical P(up)/mean fwd by score bucket, overall + by phase group."""
    def bucket(s):
        if s >= 75: return "75+"
        if s >= 60: return "60-75"
        if s >= 45: return "45-60"
        if s >= 30: return "30-45"
        return "0-30"
    groups = {"all": {}, "downtrend": {}, "non_downtrend": {}}
    for _, s, ph, y in pairs:
        key = bucket(s)
        grp = "downtrend" if ph == "downtrend_decline" else "non_downtrend"
        for g in ("all", grp):
            groups[g].setdefault(key, []).append(y)
    out = {}
    for g, buckets in groups.items():
        out[g] = {}
        for key in ("75+", "60-75", "45-60", "30-45", "0-30"):
            ys = buckets.get(key, [])
            if not ys:
                out[g][key] = {"n": 0}
                continue
            arr = np.array(ys)
            out[g][key] = {
                "n": len(ys),
                "p_up": round(float((arr > 0).mean()), 4),
                "mean_fwd": round(float(arr.mean()), 3),
                "median_fwd": round(float(np.median(arr)), 3),
            }
    return out


def main():
    ap = argparse.ArgumentParser(
        description="P1-7 score->probability calibration")
    ap.add_argument("--signals", default="reports/signals_p0_fixed.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--bin-w", type=float, default=5.0)
    args = ap.parse_args()
    if not args.out:
        args.out = f"reports/calibration_{args.horizon}d.json"

    pairs = load_pairs(args.signals, args.horizon)
    train, oot, cut = time_split(pairs, args.train_frac)
    print(f"[cal] pairs={len(pairs)} train={len(train)} oot={len(oot)} "
          f"(cutoff date {cut})")

    table = binned_table(train, args.bin_w)
    filled = sum(1 for b in table if b["p_up"] is not None)

    ts = np.array([p[1] for p in train])
    ty = np.array([p[3] for p in train])
    ty01 = (ty > 0).astype(float)
    rho = spearman(ts, ty)
    flip = rho < 0
    pts = pava_fit(-ts if flip else ts, ty01)
    print(f"[cal] train spearman={rho:.3f} -> isotonic on "
          f"{'-score' if flip else 'score'} ({len(pts)} blocks)")

    model = logistic_fit(ts, ty01)

    os_ = np.array([p[1] for p in oot])
    oy = np.array([p[3] for p in oot])
    os01 = (oy > 0).astype(float)
    base_rate = float(os01.mean())

    def bin_pred(s):
        b = bin_lookup(table, s)
        return b["p_up"] if b["p_up"] is not None else base_rate

    ev = {
        "oot_base_rate_p_up": round(base_rate, 4),
        "brier_train_binned": brier([bin_pred(s) for s in ts], ty),
        "brier_oot_binned": brier([bin_pred(s) for s in os_], oy),
        "brier_train_isotonic": brier(
            [pava_predict(pts, -s if flip else s) for s in ts], ty),
        "brier_oot_isotonic": brier(
            [pava_predict(pts, -s if flip else s) for s in os_], oy),
        "brier_train_logistic": brier(logistic_predict(model, ts), ty01),
        "brier_oot_logistic": brier(logistic_predict(model, os_), os01),
        "brier_baseline_constant_base_rate": round(
            float(np.mean((base_rate - os01) ** 2)), 5),
        "reliability_oot_isotonic": reliability(
            [pava_predict(pts, -s if flip else s) for s in os_], oy),
        "reliability_oot_logistic": reliability(
            logistic_predict(model, os_), os01),
    }


    output = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input": args.signals, "horizon_days": args.horizon,
        "n_pairs": len(pairs),
        "train": {"n": len(train), "date_end": cut},
        "out_of_time": {"n": len(oot),
                        "date_start": min(p[0] for p in oot),
                        "date_end": max(p[0] for p in oot)},
        "binned_table": {"bin_w": args.bin_w, "min_bin_n": MIN_BIN_N,
                         "bins_filled": filled, "rows": table},
        "isotonic": {"sign_flipped": bool(flip),
                     "train_spearman": round(rho, 4),
                     "points": [[round(x, 2), round(v, 4)] for x, v in pts]},
        "logistic": {"w": [round(float(w), 5) for w in model[0]],
                     "z_mean": round(model[1], 3),
                     "z_sd": round(model[2], 3),
                     "form": "p = sigmoid(w0 + w1*z + w2*z^2), z=(score-mu)/sd"},
        "evaluation": ev,
        "threshold_table": threshold_table(pairs),
        "usage_note": (
            "binned_table maps score -> empirical P(fwd>0)/mean fwd on the "
            "train window (calibration, NOT a strategy return forecast); "
            "isotonic/logistic are compact monotone/quadratic variants. "
            "Evaluation is on the out-of-time tail. Flat reliability slopes "
            "mean the score carries no probability information at this "
            "horizon — do not trade off it."),
    }
    with open(args.out, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)
    print(f"[cal] written -> {args.out}")
    print(f"[cal] Brier oot: binned={ev['brier_oot_binned']} "
          f"iso={ev['brier_oot_isotonic']} logit={ev['brier_oot_logistic']} "
          f"baseline={ev['brier_baseline_constant_base_rate']}")


if __name__ == "__main__":
    main()
