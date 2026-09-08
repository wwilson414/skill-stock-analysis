#!/usr/bin/env python3
"""
Standalone mean-reversion signal (ROADMAP P1-5) + momentum-complement
candidate components (P1-6). numpy only.

Design rules
------------
* The MR signal is STANDALONE — it is never mixed into the momentum
  total score (ROADMAP: "不要混入动量总分").
* Components are a-priori transforms; NO weights are fitted on the
  evaluation sample. Equal-weight sum, thresholds chosen ex-ante from
  indicator conventions (RSI2<=10 oversold, 10% below MA60, 2x volume).
* Every feature at index t uses bars <= t only (no look-ahead); forward
  returns are measured from t's close, same convention as the momentum
  backtest.

MR components (higher = stronger rebound expectation)
    c_rsi  = clip((50 - RSI2) / 50, -1, 1.5)      oversold -> positive
    c_dev  = clip(-dev_MA60 / 0.20, -1, 2)        deep below MA60 -> positive
    c_stop = clip(vol_ratio, 0, 3) if stabilization day after >= 2 down
             closes else 0                        放量止跌
    mr_score = c_rsi + c_dev + c_stop

MR event rule (discrete, for event studies):
    rsi2 <= 10 AND dev60 <= -10% AND stabilization day (close >= prev close)

P1-6 candidate components (must earn their place by segmented IC):
    comp_vol: volume surge vs 20d average          (换手/量能异动 proxy)
    comp_pv : -(z(px20) * z(obv20))                (量价背离)
    comp_brk: breakout above prior 60d high, scaled by platform narrowness
    comp_gap: overnight gap vs 2% reference        (跳空)
"""

import numpy as np


def wilder_rsi(closes: np.ndarray, period: int = 2) -> np.ndarray:
    """Wilder-smoothed RSI; NaN before the seed window."""
    n = len(closes)
    out = np.full(n, np.nan)
    if n <= period:
        return out
    deltas = np.diff(closes, prepend=closes[:1])
    gains = np.clip(deltas, 0, None)
    losses = np.clip(-deltas, 0, None)
    ag = float(gains[1:period + 1].mean())
    al = float(losses[1:period + 1].mean())
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i] = 100.0 if al <= 1e-12 else 100.0 - 100.0 / (1.0 + ag / al)
    return out



def wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
               period: int = 14) -> np.ndarray:
    """Wilder-smoothed ATR; NaN before the seed window."""
    n = len(closes)
    out = np.full(n, np.nan)
    if n <= period:
        return out
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    out[period] = float(np.nanmean(tr[1:period + 1]))
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def sma(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if len(a) < n:
        return out
    c = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def rolling_z(a: np.ndarray, win: int = 120, min_obs: int = 30) -> np.ndarray:
    """Trailing z-score; 0.0 when the window is degenerate (sd ~ 0)."""
    out = np.full(len(a), np.nan)
    for i in range(len(a)):
        w = a[max(0, i - win + 1):i + 1]
        w = w[~np.isnan(w)]
        if len(w) < min_obs:
            continue
        sd = float(w.std())
        out[i] = 0.0 if sd <= 1e-12 else (a[i] - float(w.mean())) / sd
    return out



def compute_components(ohlcv: list) -> dict:
    """All P1-5/P1-6 features aligned to ohlcv index (NaN where undefined).

    ohlcv rows: {date, open, close, high, low, volume} (volume may be None).
    """
    n = len(ohlcv)
    closes = np.array([float(ohlcv[i]["close"]) for i in range(n)])
    highs = np.array([float(ohlcv[i]["high"]) for i in range(n)])
    lows = np.array([float(ohlcv[i]["low"]) for i in range(n)])
    opens = np.array([float(ohlcv[i]["open"]) for i in range(n)])
    vols = np.array([float(ohlcv[i]["volume"] or 0.0) for i in range(n)])

    rsi2 = wilder_rsi(closes, 2)
    atr = wilder_atr(highs, lows, closes, 14)
    ma60 = sma(closes, 60)
    vsma20 = sma(vols, 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        dev60 = closes / ma60 - 1.0
        atr_pct = atr / closes
    vol_ratio = np.where(vsma20 > 0, vols / np.where(vsma20 > 0, vsma20, 1.0),
                         1.0)

    # down-streak & stabilization (止跌)
    down_streak = np.zeros(n)
    stabilize = np.zeros(n)
    for i in range(1, n):
        down_streak[i] = down_streak[i - 1] + 1 if closes[i] < closes[i - 1] else 0
        stabilize[i] = 1.0 if closes[i] >= closes[i - 1] else 0.0

    # OBV and 20d momentum for price-volume divergence
    obv = np.cumsum(np.where(np.r_[0.0, np.diff(closes)] >= 0, vols, -vols))
    px20 = np.full(n, np.nan)
    obv20 = np.full(n, np.nan)
    px20[20:] = closes[20:] / closes[:-20] - 1.0
    obv20[20:] = obv[20:] - obv[:-20]
    z_px20 = rolling_z(px20, 120)
    z_obv20 = rolling_z(obv20, 120)

    # breakout above prior 60d high (excluding today) + platform narrowness
    hh_prev = np.full(n, np.nan)
    rng_prev = np.full(n, np.nan)
    for i in range(61, n):
        w = closes[i - 60:i]
        hh_prev[i] = w.max()
        rng_prev[i] = (w.max() - w.min()) / closes[i - 1]

    c_rsi = np.clip((50.0 - rsi2) / 50.0, -1.0, 1.5)
    c_dev = np.clip(-dev60 / 0.20, -1.0, 2.0)
    c_stop = np.where((stabilize > 0) & (down_streak >= 2),
                      np.clip(vol_ratio, 0.0, 3.0), 0.0)

    return {
        "rsi2": rsi2, "atr_pct": atr_pct, "dev60": dev60,
        "vol_ratio": vol_ratio, "down_streak": down_streak,
        "stabilize": stabilize, "hh_prev": hh_prev,
        "c_rsi": c_rsi, "c_dev": c_dev, "c_stop": c_stop,
        "mr_score": c_rsi + c_dev + c_stop,
        "comp_vol": np.clip(vol_ratio - 1.0, -1.0, 3.0),
        "comp_pv": -(z_px20 * z_obv20),
        "comp_brk": np.clip((closes / hh_prev - 1.0) / 0.02, -1.0, 3.0)
                    * np.clip(rng_prev / 0.10, 0.2, 1.5),
        "comp_gap": np.clip((opens / np.r_[opens[:1], closes[:-1]] - 1.0)
                            / 0.02, -1.0, 3.0),
        "mr_event": (rsi2 <= 10.0) & (dev60 <= -0.10) & (stabilize > 0),
    }


MR_COMPONENTS = ["c_rsi", "c_dev", "c_stop", "mr_score", "mr_event"]
P1_6_COMPONENTS = ["comp_vol", "comp_pv", "comp_brk", "comp_gap"]
