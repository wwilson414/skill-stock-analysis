#!/usr/bin/env python3
"""
P0 evidence-expansion harness (ROADMAP.md P0-1 .. P0-4).

What it answers
---------------
Is the negative score IC found in the original 5-stock backtest a window
artifact or a structural property of the scoring system?

P0-1 expand sample : 36 frozen cross-market stocks (or a seeded random
                     whole-A-market liquidity sample), ~3y lookback, IC
                     evaluated per half-year segment (multi-start view).
P0-2 market regime : CSI300 / HSI / SPY bull-bear-sideways labels per date;
                     IC cross-tabulated by (stock phase x market regime).
P0-3 IC confidence : bootstrap percentile CI + t-stat + normal-approx
                     p-value on every pooled IC; per-stock IC cross-section.
P0-4 selection bias: universe construction is explicit and recorded verbatim
                     in the output JSON (rule text, codes, seed, snapshot).

Usage
-----
python3 references/p0_backtest.py                    # fixed universe, full run
python3 references/p0_backtest.py --universe random  # seeded liquidity sample
python3 references/p0_backtest.py --limit 2          # smoke test

Summary JSON -> reports/p0_expansion.json (raw signals only with
--dump-signals). Data cached under references/.p0_cache/ (gitignored).
"""

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock_data_fetcher import (
    _MIN_BARS_FOR_INDICATORS,
    _SCORE_BUCKETS,
    _fetch_qq_hk,
    _log,
    backtest_stock,
    classify_stock,
    fetch_cn_a,
    fetch_us,
)

BOOTSTRAP_SEED = 20260908
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".p0_cache")
BENCH_BARS_DEFAULT = 1200

# ---------------------------------------------------------------------------
# P0-4: universe construction (selection-bias control)
# ---------------------------------------------------------------------------
# Fixed-list rule (recorded verbatim in every output JSON):
#   * listed on/before 2023-01-01 -> guarantees ~3y of history
#   * liquid large/mid caps (top-quintile turnover of their market)
#   * spans >= 10 sectors across A-main / ChiNext / STAR / HK / US
#   * frozen BEFORE the run; never filtered on realized 2024-2026 paths
# Known residual bias: "famous names" survivorship — exactly why
# --universe random exists (seeded whole-market liquidity sample).
FIXED_UNIVERSE = [
    ("600519", "Kweichow Moutai", "consumer"),
    ("000858", "Wuliangye", "consumer"),
    ("601318", "Ping An", "financials"),
    ("600036", "China Merchants Bank", "financials"),
    ("600030", "CITIC Securities", "financials"),
    ("601899", "Zijin Mining", "materials"),
    ("600900", "Yangtze Power", "utilities"),
    ("601088", "China Shenhua", "energy"),
    ("600028", "Sinopec", "energy"),
    ("600276", "Hengrui Pharma", "healthcare"),
    ("000333", "Midea Group", "industrials"),
    ("601988", "Bank of China", "financials"),
    ("300750", "CATL", "batteries"),
    ("300059", "East Money", "fintech"),
    ("300760", "Mindray", "healthcare"),
    ("300014", "EVE Energy", "batteries"),
    ("300124", "Inovance", "industrials"),
    ("300628", "Yealink", "tech"),
    ("688981", "SMIC", "semiconductors"),
    ("688111", "Kingsoft Office", "software"),
    ("688012", "AMEC", "semiconductors"),
    ("688599", "Trina Solar", "solar"),
    ("HK00700", "Tencent", "tech"),
    ("HK00941", "China Mobile", "telecom"),
    ("HK01211", "BYD", "autos"),
    ("HK03690", "Meituan", "internet"),
    ("HK00005", "HSBC", "financials"),
    ("HK00388", "HKEX", "financials"),
    ("AAPL", "Apple", "tech"),
    ("MSFT", "Microsoft", "tech"),
    ("NVDA", "Nvidia", "semiconductors"),
    ("AMZN", "Amazon", "consumer_disc"),
    ("JPM", "JPMorgan", "financials"),
    ("JNJ", "Johnson&Johnson", "healthcare"),
    ("XOM", "ExxonMobil", "energy"),
    ("KO", "Coca-Cola", "consumer_staples"),
]
UNIVERSE_RULE_FIXED = (
    "fixed frozen list: 36 liquid large/mid caps listed <= 2023-01-01, "
    "12 A-main + 6 ChiNext + 4 STAR + 6 HK + 8 US, >= 10 sectors; selected "
    "from major index constituents before the run, never filtered on "
    "realized 2024-2026 price paths (residual famous-name survivorship bias "
    "acknowledged; cross-check with --universe random)"
)
UNIVERSE_RULE_RANDOM = (
    "seeded whole-A-market liquidity sample: eastmoney clist snapshot sorted "
    "by turnover (f6) desc, ST/*ST/delisting filtered, top --pool-size kept "
    "as pool, random.sample(seed=--seed, k=--sample-n); snapshot date + seed "
    "recorded; HK/US not covered in this mode"
)

# P0-2: deterministic benchmark-trend labeling rule (recorded in output).
REGIME_RULE = (
    "bull: bench 60d return > +5% AND close > 60d MA; "
    "bear: bench 60d return < -5% AND close < 60d MA; "
    "sideways: everything else; warmup: first 60 bench bars"
)


# ---------------------------------------------------------------------------
# Cache + data layer
# ---------------------------------------------------------------------------
def _cache_path(kind, key):
    return os.path.join(CACHE_DIR, f"{kind}_{key}.json")


def _cache_get(kind, key, use_cache):
    if not use_cache:
        return None
    path = _cache_path(kind, key)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _cache_put(kind, key, payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(kind, key), "w") as f:
        json.dump(payload, f, ensure_ascii=False)


def _fetch_bench_rows(market: str, bars: int):
    """Benchmark daily closes WITH dates for market-regime labeling.

    cn_a -> CSI300 (tencent sh000300); cn_hk -> HSI (tencent hkHSI);
    us -> SPY (yfinance; tencent usSPY returns 1 bar — verified unusable).
    Raises on total failure (harness needs regime labels to be meaningful).
    """
    rows, source = None, None
    if market in ("cn_a", "cn_hk"):
        sym = "sh000300" if market == "cn_a" else "hkHSI"
        try:
            from stock_data_fetcher import _qq_kline

            rows = [{"date": b["date"], "close": b["close"]}
                    for b in _qq_kline(sym, bars) if b.get("close")]
            source = f"tencent:{sym}"
        except Exception as e:
            _log(f"[bench:{market}] tencent failed: {e}")
    if rows is None:
        import yfinance as yf

        yf_sym = {"cn_a": "000300.SS", "cn_hk": "^HSI", "us": "SPY"}[market]
        hist = yf.Ticker(yf_sym).history(period=f"{bars}d", auto_adjust=True)
        rows = [{"date": idx.strftime("%Y-%m-%d"), "close": float(c)}
                for idx, c in zip(hist.index, hist["Close"].dropna())]
        source = f"yfinance:{yf_sym}"
    if len(rows) < 200:
        raise ValueError(f"bench {market}: only {len(rows)} bars")
    return rows, source


def _load_or_fetch_bench(market: str, bars: int, use_cache: bool):
    cached = _cache_get("bench", f"{market}_{bars}", use_cache)
    if cached and cached.get("rows"):
        return cached["rows"], cached.get("source", "cache")
    rows, source = _fetch_bench_rows(market, bars)
    _cache_put("bench", f"{market}_{bars}",
               {"fetched_at": datetime.now().isoformat(timespec="seconds"),
                "source": source, "rows": rows})
    return rows, source



def label_market_regimes(bench_rows: list) -> dict:
    """Date -> {regime, chg60, chg20, dist_ma60} using REGIME_RULE."""
    rows = [(b["date"], b["close"]) for b in bench_rows if b.get("close")]
    closes = [c for _, c in rows]
    out = {}
    for i, (d, c) in enumerate(rows):
        if i < 60:
            out[d] = {"regime": "warmup", "chg60": None, "chg20": None,
                      "dist_ma60": None}
            continue
        ma60 = sum(closes[i - 59:i + 1]) / 60.0
        chg60 = (c / closes[i - 60] - 1) * 100.0
        chg20 = (c / closes[i - 20] - 1) * 100.0
        dist = (c - ma60) / ma60 * 100.0
        if chg60 > 5 and dist > 0:
            regime = "bull"
        elif chg60 < -5 and dist < 0:
            regime = "bear"
        else:
            regime = "sideways"
        out[d] = {"regime": regime, "chg60": round(chg60, 2),
                  "chg20": round(chg20, 2), "dist_ma60": round(dist, 2)}
    return out


def _align_bench_to_stock(stock_ohlcv: list, bench_rows: list):
    """Bench closes re-indexed to the stock's bar dates (forward-filled).

    Keeps _calc_rs_simple's trailing windows date-aligned even when the
    stock series and bench series have different lengths/sources.
    Returns (aligned_closes, n_leading_misses).
    """
    bmap = {r["date"]: r["close"] for r in bench_rows if r.get("close")}
    out, last, misses = [], None, 0
    for b in stock_ohlcv:
        if b.get("date") in bmap:
            last = bmap[b["date"]]
        if last is None:
            misses += 1
        out.append(last)
    return out, misses


def _eastmoney_liquid_pool(pool_size: int, use_cache: bool = True):
    """Whole-A-market snapshot sorted by turnover (f6) via eastmoney clist.

    Retries transient HTTP errors (observed 502s) with backoff and caches
    the full snapshot under .p0_cache so re-runs use a stable pool.
    Returns (rows, snapshot_date); rows = [{code, name, amount}, ...].
    """
    import time as _time
    import urllib.request

    cached = _cache_get("pool", f"em_{pool_size}", use_cache)
    if cached and cached.get("rows"):
        return cached["rows"], cached.get("snapshot_date")

    rows, pn = [], 1
    # push2delay mirror first: main push2 host had a full 502 outage while
    # the delay host stayed up; delayed quotes are fine for a turnover snapshot.
    hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com"]
    while len(rows) < 6000 and pn <= 12:
        data = None
        last_err = None
        for host in hosts:
            url = (f"https://{host}/api/qt/clist/get"
                   f"?pn={pn}&pz=500&po=1&np=1&fltt=2&invt=2&fid=f6"
                   "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f6")
            for attempt in range(4):
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "Mozilla/5.0"})
                    data = json.loads(urllib.request.urlopen(
                        req, timeout=15).read())
                    break
                except Exception as e:
                    last_err = e
                    if attempt == 3:
                        break
                    _log(f"[p0] clist {host} pn={pn} attempt {attempt + 1} "
                         f"failed: {e}; retrying in {2 * (attempt + 1)}s")
                    _time.sleep(2 * (attempt + 1))
            if data is not None:
                break
        if data is None:
            raise RuntimeError(f"clist all hosts failed: {last_err}")
        diff = (data.get("data") or {}).get("diff") or []
        if not diff:
            break
        for r in diff:
            name = r.get("f14") or ""
            if "ST" in name.upper() or "退" in name or r.get("f6") is None:
                continue
            rows.append({"code": str(r["f12"]), "name": name,
                         "amount": float(r["f6"])})
        pn += 1
    rows.sort(key=lambda r: r["amount"], reverse=True)
    snap_date = datetime.now().strftime("%Y-%m-%d")
    _cache_put("pool", f"em_{pool_size}",
               {"snapshot_date": snap_date, "rows": rows})
    return rows[:pool_size], snap_date



def build_universe(args):
    """Returns (entries, rule, meta). entries: {code, market, normalized, name, sector}."""
    if args.codes:
        entries = []
        for c in args.codes.split(","):
            c = c.strip()
            if not c:
                continue
            market, normalized, display = classify_stock(c)
            if market == "unknown":
                raise SystemExit(f"unrecognized code: {c}")
            entries.append({"code": c, "market": market,
                            "normalized": normalized, "name": c,
                            "sector": "unspecified"})
        return entries, ("explicit --codes list: "
                         + ",".join(c.strip() for c in args.codes.split(",")
                                    if c.strip())), {}

    if args.universe == "random":
        pool, snap_date = _eastmoney_liquid_pool(args.pool_size,
                                                 use_cache=not args.no_cache)
        rng = random.Random(args.seed)
        sampled = rng.sample(pool, min(args.sample_n, len(pool)))
        entries = []
        for r in sampled:
            market, normalized, display = classify_stock(r["code"])
            entries.append({"code": r["code"], "market": market,
                            "normalized": normalized, "name": r["name"],
                            "sector": "unspecified"})
        meta = {"snapshot_date": snap_date, "pool_size": args.pool_size,
                "sample_n": len(entries), "seed": args.seed,
                "pool_head": [r["code"] for r in pool[:20]]}
        return entries, UNIVERSE_RULE_RANDOM, meta

    entries = []
    for code, name, sector in FIXED_UNIVERSE:
        market, normalized, display = classify_stock(code)
        entries.append({"code": code, "market": market,
                        "normalized": normalized, "name": name,
                        "sector": sector})
    return entries, UNIVERSE_RULE_FIXED, {}


def _yf_us_ohlcv(symbol: str, days: int) -> list:
    """Direct yfinance fallback — tencent US kline covers NASDAQ (.OQ) well
    but returns 1 bar for some NYSE symbols (JPM/JNJ/KO/XOM, measured).
    yfinance occasionally returns empty frames (rate limit); retry once."""
    import yfinance as yf

    for attempt in range(2):
        hist = yf.Ticker(symbol).history(period=f"{days}d", auto_adjust=True)
        if len(hist):
            break
        time.sleep(3)
    cols = {c.lower(): c for c in hist.columns}
    need = ["open", "close", "high", "low", "volume"]
    if len(hist) == 0 or any(c not in cols for c in need):
        raise ValueError(f"yfinance {symbol}: no usable OHLCV")
    df = hist[[cols[c] for c in need]].dropna()
    return [{"date": idx.strftime("%Y-%m-%d"), "open": float(r.iloc[0]),
             "close": float(r.iloc[1]), "high": float(r.iloc[2]),
             "low": float(r.iloc[3]), "volume": float(r.iloc[4])}
            for idx, r in df.iterrows()]


def _fetch_stock_ohlcv(entry: dict, days: int, use_cache: bool):
    """OHLCV via the canonical degradation chain; HK skips the slow realtime
    path (measured: fetch_hk chain spent 83s on failed akshare realtime).
    US adds a yfinance fallback; cache keys versioned (_v2) and entries
    with <200 bars are treated as poisoned (tencent 1-bar US responses)."""
    ckey = f"{entry['code']}_{days}_v2"
    cached = _cache_get("ohlcv", ckey, use_cache)
    if cached and cached.get("ohlcv") and len(cached["ohlcv"]) >= 200:
        ohlcv = [dict(zip(("date", "open", "close", "high", "low", "volume"), row))
                 for row in cached["ohlcv"]]
        return ohlcv, cached.get("name", entry["code"]), "cache"

    market, normalized = entry["market"], entry["normalized"]
    source = "live"
    if market == "cn_a":
        raw = fetch_cn_a(normalized, days)
        ohlcv, name = raw["ohlcv"], raw.get("name", entry["code"])
    elif market == "cn_hk":
        ohlcv, _src = _fetch_qq_hk(normalized, days)
        name = entry["code"]
    elif market == "us":
        raw = fetch_us(normalized, days)
        ohlcv, name = raw["ohlcv"], raw.get("name", entry["code"])
        if len(ohlcv) < 200:
            _log(f"[p0] {entry['code']}: chain returned {len(ohlcv)} bars "
                 f"-> yfinance fallback")
            ohlcv, source = _yf_us_ohlcv(normalized, days), "yfinance"
    else:
        raise ValueError(f"unknown market {market}")
    ohlcv = ohlcv[-days:]  # uniform window across stocks for comparability
    _cache_put("ohlcv", ckey,
               {"fetched_at": datetime.now().isoformat(timespec="seconds"),
                "name": name, "source": source,
                "ohlcv": [[b.get("date"), b.get("open"), b.get("close"),
                           b.get("high"), b.get("low"), b.get("volume")]
                          for b in ohlcv]})
    return ohlcv, name, source


# ---------------------------------------------------------------------------
# P0-3: IC statistics (bootstrap CI + t-stat, no scipy needed)
# ---------------------------------------------------------------------------
def _pearson(xs, ys):
    if len(xs) < 3:
        return None
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    xm, ym = xs - xs.mean(), ys - ys.mean()
    den = math.sqrt(float((xm * xm).sum()) * float((ym * ym).sum()))
    if den <= 0:
        return None
    return round(float((xm * ym).sum()) / den, 4)


def _bootstrap_ci(xs, ys, n_boot):
    """Percentile bootstrap CI for Pearson IC (numpy, chunked, seeded)."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    n = len(xs)
    boots = np.empty(n_boot, dtype=float)
    done = 0
    while done < n_boot:
        k = min(64, n_boot - done)
        idx = rng.integers(0, n, size=(k, n))
        xb, yb = xs[idx], ys[idx]
        xb = xb - xb.mean(axis=1, keepdims=True)
        yb = yb - yb.mean(axis=1, keepdims=True)
        num = (xb * yb).sum(axis=1)
        den = np.sqrt((xb * xb).sum(axis=1) * (yb * yb).sum(axis=1))
        ok = den > 0
        vals = num[ok] / den[ok]
        take = min(len(vals), n_boot - done)
        if take == 0:  # fully degenerate chunk
            boots[done] = np.nan
            done += 1
            continue
        boots[done:done + take] = vals[:take]
        done += take
    boots = boots[~np.isnan(boots)]
    if len(boots) == 0:
        return None, None
    boots.sort()
    lo = float(boots[int(0.025 * len(boots))])
    hi = float(boots[min(len(boots) - 1, int(math.ceil(0.975 * len(boots))))])
    return round(lo, 4), round(hi, 4)


def _ic_stats(pairs, n_boot):
    """pairs: non-empty list of (score, fwd_return). Returns IC + uncertainty."""
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    out = {"n": len(pairs), "ic": None}
    if len(pairs) < 3:
        return out
    xs = np.array([p[0] for p in pairs], dtype=float)
    ys = np.array([p[1] for p in pairs], dtype=float)
    ic = _pearson(xs, ys)
    out["ic"] = ic
    if ic is None:
        return out
    n = len(xs)
    denom = 1.0 - ic * ic
    if denom > 0 and n > 2:
        t = ic * math.sqrt((n - 2) / denom)
        out["t_stat"] = round(t, 3)
        out["p_value"] = round(math.erfc(abs(t) / math.sqrt(2.0)), 5)
    if n_boot and n_boot > 0:
        lo, hi = _bootstrap_ci(xs, ys, n_boot)
        if lo is not None:
            out["ci95"] = [lo, hi]
            out["ci_includes_zero"] = bool(lo <= 0 <= hi)
    out["mean_fwd"] = round(float(ys.mean()), 3)
    out["win_rate"] = round(float((ys > 0).mean()) * 100, 1)
    return out


def _cross_section(stock_ics):
    """Per-stock IC distribution: is the sign consistent across stocks?"""
    vals = [v for v in stock_ics if v is not None]
    out = {"n_stocks": len(stock_ics), "n_valid": len(vals)}
    if len(vals) < 2:
        return out
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    se = math.sqrt(var / len(vals)) if var > 0 else 0.0
    out.update({
        "mean": round(mean, 4),
        "median": round(sorted(vals)[len(vals) // 2], 4),
        "pct_negative": round(100.0 * sum(1 for v in vals if v < 0) / len(vals), 1),
        "se": round(se, 4),
        "t_stat": round(mean / se, 3) if se > 0 else None,
    })
    if out.get("t_stat") is not None:
        out["p_value"] = round(math.erfc(abs(out["t_stat"]) / math.sqrt(2.0)), 5)
    return out


def _run_stock(task):
    """Worker: fetch/cache OHLCV, align bench, walk-forward backtest,
    return compact per-stock record (signals as [date, score, phase, fwd...])."""
    entry, days, forward_days, bench_rows, use_cache = task
    code = entry["code"]
    rec = {"code": code, "name": entry["name"], "sector": entry["sector"],
           "market": entry["market"]}
    try:
        ohlcv, name, src = _fetch_stock_ohlcv(entry, days, use_cache)
        rec["name"], rec["source"] = name, src
        min_bars = _MIN_BARS_FOR_INDICATORS + max(forward_days) + 1
        if len(ohlcv) < min_bars:
            rec["error"] = f"insufficient_data ({len(ohlcv)} < {min_bars})"
            return rec
        bench_aligned, misses = _align_bench_to_stock(ohlcv, bench_rows)
        rec["bars"] = len(ohlcv)
        rec["date_start"] = ohlcv[0].get("date")
        rec["date_end"] = ohlcv[-1].get("date")
        rec["bench_unmapped_head_bars"] = misses
        calib = backtest_stock(code, days=days, forward_days=forward_days,
                               ohlcv_data=ohlcv, bench_closes=bench_aligned,
                               return_signals=True)
        if "error" in calib:
            rec["error"] = calib["error"]
            return rec
        sigs = []
        for s in calib.get("signals", []):
            fr = s.get("forward_returns") or {}
            sigs.append([s.get("date"), s.get("score_total"),
                         s.get("phase", "unknown")]
                        + [fr.get(f"{d}d") for d in forward_days])
        rec["fwd_slots"] = [f"{d}d" for d in forward_days]
        rec["n_signals"] = len(sigs)
        rec["signals"] = sigs

        idx = {f"{d}d": 3 + i for i, d in enumerate(forward_days)}
        rec["ic"] = {}
        for hk, col in idx.items():
            pairs = [(s[1], s[col]) for s in sigs if s[col] is not None]
            rec["ic"][hk] = _ic_stats(pairs, 0)
        main_h = f"{max(forward_days)}d"
        main_col = idx[main_h]
        by_phase = {}
        for s in sigs:
            if s[main_col] is not None:
                by_phase.setdefault(s[2], []).append((s[1], s[main_col]))
        rec["ic_by_phase"] = {ph: {"ic": _ic_stats(p, 0)["ic"], "n": len(p)}
                              for ph, p in by_phase.items()}
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


# ---------------------------------------------------------------------------
# P0-1/P0-2: pooled segmentation views + decision-tree verdict
# ---------------------------------------------------------------------------
def _halfyear(d):
    return f"{d[:4]}-H{1 if int(d[5:7]) <= 6 else 2}"


def _bucket_of(score):
    for lo, hi, label in _SCORE_BUCKETS:
        if lo <= score < hi:
            return label
    return "unknown"


def analyze(stock_records: list, regime_maps: dict, args, forward_days: list) -> dict:
    """Pool all signals; segment IC (main horizon) by every P0-relevant cut."""
    main_h = f"{max(forward_days)}d"
    main_col = 3 + forward_days.index(max(forward_days))

    flat = []
    for rec in stock_records:
        if rec.get("error") or not rec.get("signals"):
            continue
        rmap = regime_maps.get(rec["market"], {})
        for s in rec["signals"]:
            info = rmap.get(s[0]) or {}
            flat.append({"code": rec["code"], "market": rec["market"],
                         "date": s[0], "score": s[1], "phase": s[2],
                         "regime": info.get("regime", "unmapped"),
                         "hy": _halfyear(s[0]) if s[0] else "unknown",
                         "fwd": s[main_col]})

    def cells(key_fn):
        groups = defaultdict(list)
        for f in flat:
            if f["fwd"] is not None:
                groups[key_fn(f)].append((f["score"], f["fwd"]))
        return {k: _ic_stats(v, args.bootstrap) for k, v in sorted(groups.items())}

    views = {"horizon": main_h, "n_signals": len(flat)}
    views["overall"] = cells(lambda f: "all")
    views["by_market"] = cells(lambda f: f["market"])
    views["by_phase"] = cells(lambda f: f["phase"])
    views["by_market_regime"] = cells(lambda f: f["regime"])
    views["by_phase_x_regime"] = cells(lambda f: f"{f['phase']}|{f['regime']}")
    views["by_halfyear"] = cells(lambda f: f["hy"])
    views["by_halfyear_x_phase"] = cells(lambda f: f"{f['hy']}|{f['phase']}")
    views["by_score_bucket"] = cells(lambda f: _bucket_of(f["score"]))
    views["regime_signal_counts"] = dict(Counter(f["regime"] for f in flat))

    # per-stock IC cross-sections: is a sign consistent across stocks?
    xs = {"overall": _cross_section(
        [r.get("ic", {}).get(main_h, {}).get("ic") for r in stock_records
         if not r.get("error")])}
    phase_ics = defaultdict(list)
    for r in stock_records:
        if r.get("error"):
            continue
        for ph, st in (r.get("ic_by_phase") or {}).items():
            phase_ics[ph].append(st.get("ic"))
    for ph, ics in sorted(phase_ics.items()):
        xs[f"phase:{ph}"] = _cross_section(ics)
    views["cross_section"] = xs
    return views


def decide(views: dict) -> dict:
    """ROADMAP decision tree: which P1/P2 branch does the expanded evidence pick?"""
    bp = views.get("by_phase", {})
    dn = bp.get("downtrend_decline") or {}
    dn_ic = dn.get("ic")
    ci = dn.get("ci95") or [None, None]
    dn_sig_neg = bool(ci[1] is not None and ci[1] < 0)
    others = [v.get("ic") for k, v in bp.items()
              if k != "downtrend_decline" and isinstance(v, dict)
              and v.get("ic") is not None]
    other_mean = sum(others) / len(others) if others else None
    overall_ic = (views.get("overall") or {}).get("all", {}).get("ic")

    pxr = views.get("by_phase_x_regime", {})
    dn_bull = pxr.get("downtrend_decline|bull", {})
    dn_bear = pxr.get("downtrend_decline|bear", {})
    hyxp = views.get("by_halfyear_x_phase", {})
    dn_hy = [v.get("ic") for k, v in hyxp.items()
             if k.endswith("|downtrend_decline") and isinstance(v, dict)
             and v.get("ic") is not None]
    evidence = {
        "downtrend_ic": dn_ic, "downtrend_ci95": ci,
        "downtrend_significant_negative": dn_sig_neg,
        "non_downtrend_mean_ic": None if other_mean is None else round(other_mean, 4),
        "overall_ic": overall_ic,
        "downtrend_in_bull_ic": dn_bull.get("ic"),
        "downtrend_in_bear_ic": dn_bear.get("ic"),
        "downtrend_negative_halfyears":
            f"{sum(1 for v in dn_hy if v < 0)}/{len(dn_hy)}",
        "cross_section_downtrend": (views.get("cross_section") or {})
            .get("phase:downtrend_decline"),
    }

    if dn_ic is None:
        branch, desc = "insufficient_data", "no downtrend_decline cell in expanded sample"
        actions = ["re-run with a wider universe or longer window"]
    elif (dn_ic < -0.05 and dn_sig_neg and other_mean is not None
          and other_mean > dn_ic + 0.03):
        branch = "regime_structural"
        desc = ("downtrend IC significantly negative across the expanded sample "
                "and clearly worse than non-downtrend phases — original finding "
                "is structural (phase-dependent), not a window artifact")
        actions = ["P1-5: standalone mean-reversion signal for decline phases",
                   "P1-7: score->probability calibration"]
    elif dn_ic >= 0.02 and other_mean is not None and other_mean >= 0.02:
        branch = "window_specific"
        desc = "all phase ICs turned positive on the expanded sample"
        actions = ["P2 execution realism + data cache; keep scoring framework"]
    elif (overall_ic is not None and abs(overall_ic) < 0.02
          and not dn_sig_neg and (other_mean is None or abs(other_mean) < 0.05)):
        branch = "no_signal"
        desc = "expanded-sample ICs cluster around zero with wide CIs"
        actions = ["P1-6: component experiments before trusting the framework"]
    else:
        branch = "mixed"
        desc = ("evidence is mixed — inspect by_phase / by_phase_x_regime / "
                "by_halfyear tables before choosing a branch")
        actions = ["likely P1-5 + P1-7 if the downtrend cell dominates the "
                   "negative mass; P1-6 otherwise"]
    return {"branch": branch, "description": desc,
            "next_actions": actions, "evidence": evidence}


def _fmt_cell(st):
    if not st or st.get("ic") is None:
        return "ic=n/a"
    s = f"ic={st['ic']}"
    if st.get("ci95"):
        s += f" ci[{st['ci95'][0]},{st['ci95'][1]}]"
    if st.get("p_value") is not None:
        s += f" p={st['p_value']}"
    return f"{s} n={st.get('n', 0)}"


def main():
    ap = argparse.ArgumentParser(
        description="P0 evidence-expansion harness (ROADMAP P0-1..P0-4)")
    ap.add_argument("--universe", choices=["fixed", "random"], default="fixed")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pool-size", type=int, default=300)
    ap.add_argument("--sample-n", type=int, default=30)
    ap.add_argument("--codes", type=str, default="",
                    help="comma-separated explicit universe override")
    ap.add_argument("--limit", type=int, default=0,
                    help="debug: run only the first N universe entries")
    ap.add_argument("--days", type=int, default=900,
                    help="per-stock lookback in trading bars (~3y at 900)")
    ap.add_argument("--bench-bars", type=int, default=BENCH_BARS_DEFAULT)
    ap.add_argument("--forward-days", type=str, default="5,10,20")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--out", type=str, default="reports/p0_expansion.json")
    ap.add_argument("--dump-signals", type=str, default="",
                    help="optional path to also dump raw per-signal records")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    forward_days = [int(x) for x in args.forward_days.split(",") if x.strip()]
    entries, rule, uni_meta = build_universe(args)
    if args.limit:
        entries = entries[:args.limit]
    _log(f"[p0] universe: {len(entries)} stocks | {rule[:100]}...")

    markets = sorted({e["market"] for e in entries})
    bench_rows_map, regime_maps, regime_meta = {}, {}, {}
    for m in markets:
        rows, src = _load_or_fetch_bench(m, args.bench_bars, not args.no_cache)
        bench_rows_map[m] = rows
        regime_maps[m] = label_market_regimes(rows)
        labels = Counter(v["regime"] for v in regime_maps[m].values())
        regime_meta[m] = {"source": src, "bars": len(rows),
                          "date_start": rows[0]["date"],
                          "date_end": rows[-1]["date"],
                          "rule": REGIME_RULE, "label_days": dict(labels)}
        _log(f"[p0] bench {m}: {len(rows)} bars via {src} labels={dict(labels)}")

    tasks = [(e, args.days, forward_days, bench_rows_map[e["market"]],
              not args.no_cache) for e in entries]
    records = []
    if args.parallel > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=args.parallel) as ex:
            futs = {ex.submit(_run_stock, t): t[0]["code"] for t in tasks}
            for fut in as_completed(futs):
                rec = fut.result()
                if rec.get("error"):
                    _log(f"[p0] {rec['code']}: ERROR {rec['error']}")
                else:
                    _log(f"[p0] {rec['code']}: {rec['n_signals']} signals "
                         f"({rec['date_start']}..{rec['date_end']})")
                records.append(rec)
    else:
        for t in tasks:
            records.append(_run_stock(t))
    records.sort(key=lambda r: r["code"])
    ok = [r for r in records if not r.get("error")]
    _log(f"[p0] {len(ok)}/{len(records)} stocks backtested "
         f"in {time.time() - t0:.0f}s")

    views = analyze(records, regime_maps, args, forward_days)
    verdict = decide(views)

    _log("\n=== P0 Expanded-Sample Evidence ===")
    _log(f"signals: {views['n_signals']} | horizon: {views['horizon']}")
    _log("overall:            " + _fmt_cell(views["overall"].get("all")))
    for k, v in views["by_market"].items():
        _log(f"market {k:<6}      " + _fmt_cell(v))
    for k, v in views["by_phase"].items():
        _log(f"phase {k:<20} " + _fmt_cell(v))
    for k, v in views["by_market_regime"].items():
        _log(f"regime {k:<10}     " + _fmt_cell(v))
    for k, v in views["by_halfyear"].items():
        _log(f"halfyear {k:<10}   " + _fmt_cell(v))
    xs = views.get("cross_section", {})
    for k in ("overall", "phase:downtrend_decline", "phase:uptrend_pullback",
              "phase:range_swing"):
        if k in xs and xs[k].get("mean") is not None:
            _log(f"cross-section {k:<28} mean={xs[k]['mean']} "
                 f"neg={xs[k]['pct_negative']}% t={xs[k].get('t_stat')}")
    _log(f"\nVERDICT: [{verdict['branch']}] {verdict['description']}")
    for a in verdict["next_actions"]:
        _log(f"  -> {a}")

    per_stock = [{k: v for k, v in r.items() if k != "signals"}
                 for r in records]
    output = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 1),
        "args": {k: v for k, v in vars(args).items()},
        "universe": {"rule": rule, "meta": uni_meta,
                     "codes": [{k: e[k] for k in ("code", "name", "sector",
                                                  "market")}
                               for e in entries]},
        "market_regimes": regime_meta,
        "verdict": verdict,
        "views": views,
        "per_stock": per_stock,
        "n_stocks_ok": len(ok),
        "n_stocks_total": len(records),
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)
    _log(f"[p0] summary written -> {args.out}")
    if args.dump_signals:
        with open(args.dump_signals, "w") as f:
            json.dump([{"code": r["code"],
                        "signals": r.get("signals", [])}
                       for r in records], f, ensure_ascii=False)
        _log(f"[p0] raw signals dumped -> {args.dump_signals}")


if __name__ == "__main__":
    main()
