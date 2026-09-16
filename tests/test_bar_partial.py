"""O-2: intraday partial-bar flag + vol_ratio session pro-rating.

When the newest OHLCV bar is still forming, its raw volume is a partial-day
figure; comparing it to full-day 5-day averages understates vol_ratio (the
14:24 华能国际 0.60 / 苏泊尔 0.16 artifacts). The live analyze path now
pro-rates the current bar by the elapsed session fraction and flags
`bar_partial`; the backtest path (`compute_signal_from_ohlcv`) keeps the
historical, unadjusted semantics.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import stock_data_fetcher as sdf


def _dt(market_tz, hour, minute, day=16):
    return datetime(2026, 9, day, hour, minute, tzinfo=ZoneInfo(market_tz))


# --- _session_elapsed_frac ---

def test_session_frac_cn_a_morning():
    # 10:30 -> 60 of 240 session minutes
    assert sdf._session_elapsed_frac("cn_a", _dt("Asia/Shanghai", 10, 30)) == 0.25


def test_session_frac_cn_a_1424():
    # 14:24 -> 120 morning + 84 afternoon = 204/240
    assert sdf._session_elapsed_frac("cn_a", _dt("Asia/Shanghai", 14, 24)) == 0.85


def test_session_frac_cn_a_lunch_break_counts_morning_only():
    assert sdf._session_elapsed_frac("cn_a", _dt("Asia/Shanghai", 12, 0)) == 0.5


def test_session_frac_after_close_is_complete():
    assert sdf._session_elapsed_frac("cn_a", _dt("Asia/Shanghai", 16, 0)) == 1.0


def test_session_frac_before_open_clamped():
    # nothing traded yet; clamped to 5% to avoid division blowups
    assert sdf._session_elapsed_frac("cn_a", _dt("Asia/Shanghai", 8, 0)) == 0.05


def test_session_frac_hk_afternoon():
    # 14:30 -> 150 morning + 90 afternoon = 240/330
    assert abs(sdf._session_elapsed_frac(
        "cn_hk", _dt("Asia/Hong_Kong", 14, 30)) - 240 / 330) < 1e-9


def test_session_frac_us_converts_to_exchange_tz():
    # 10:00 New York -> 30 of 390 minutes
    assert abs(sdf._session_elapsed_frac(
        "us", _dt("America/New_York", 10, 0)) - 30 / 390) < 1e-9


def test_session_frac_aware_time_converts_to_market_tz():
    # 22:00 Shanghai == 10:00 New York (EDT, UTC-4) -> still a US session
    shanghai_10am_ny = datetime(2026, 9, 16, 22, 0,
                                tzinfo=ZoneInfo("Asia/Shanghai"))
    assert abs(sdf._session_elapsed_frac("us", shanghai_10am_ny) - 30 / 390) < 1e-9


def test_session_frac_unknown_market_never_prorates():
    assert sdf._session_elapsed_frac("jp", _dt("Asia/Shanghai", 10, 30)) == 1.0


# --- _last_bar_partial ---

def test_last_bar_partial_true_mid_session():
    partial, frac = sdf._last_bar_partial(
        "cn_a", "2026-09-16", now=_dt("Asia/Shanghai", 14, 24))
    assert partial is True
    assert frac == 0.85


def test_last_bar_partial_false_after_close():
    partial, frac = sdf._last_bar_partial(
        "cn_a", "2026-09-16", now=_dt("Asia/Shanghai", 15, 30))
    assert (partial, frac) == (False, 1.0)


def test_last_bar_partial_false_for_past_bars():
    partial, frac = sdf._last_bar_partial(
        "cn_a", "2026-09-15", now=_dt("Asia/Shanghai", 14, 24))
    assert (partial, frac) == (False, 1.0)


def test_last_bar_partial_false_without_date_or_market():
    assert sdf._last_bar_partial("cn_a", None) == (False, 1.0)
    assert sdf._last_bar_partial("jp", "2026-09-16") == (False, 1.0)


# --- calc_volume_analysis ---

def test_default_call_matches_legacy_shape_and_values():
    """No frac -> exactly the historical contract: no extra keys, same math."""
    out = sdf.calc_volume_analysis([1e6] * 10, [10.0] * 10)
    assert out == {"bar_partial": False, "vol_ratio": 1.0, "trend": "normal"}


def test_partial_bar_prorates_vol_ratio():
    # raw 0.6 at half session -> full-day equivalent 1.2
    vols = [2e6] * 5 + [1.2e6]
    out = sdf.calc_volume_analysis(vols, [10.0] * 6, session_elapsed_frac=0.5)
    assert out["bar_partial"] is True
    assert out["session_elapsed_pct"] == 50.0
    assert out["vol_ratio_raw"] == 0.6
    assert out["vol_ratio"] == 1.2
    assert out["trend"] == "normal"


def test_partial_bar_can_flip_trend_classification():
    # the 华能国际 artifact in reverse: a 'shrinking' raw 0.7 is actually heavy volume
    vols = [2e6] * 5 + [1.4e6]  # raw 0.7
    out = sdf.calc_volume_analysis(vols, [10.0, 10.5], session_elapsed_frac=0.4)
    assert out["vol_ratio_raw"] == 0.7
    assert out["vol_ratio"] == 1.75
    assert out["trend"] == "heavy_volume_up"  # closes rising


def test_partial_bar_fraction_one_is_not_partial():
    out = sdf.calc_volume_analysis([1e6] * 6, [10.0] * 6,
                                   session_elapsed_frac=1.0)
    assert out == {"bar_partial": False, "vol_ratio": 1.0, "trend": "normal"}


def test_partial_bar_with_insufficient_data_still_flags():
    out = sdf.calc_volume_analysis([1e6, 1e6], [10.0, 10.0],
                                   session_elapsed_frac=0.5)
    assert out["bar_partial"] is True
    assert out["vol_ratio"] is None
    assert out["trend"] == "insufficient_data"


def test_partial_bar_with_none_volume_stays_none():
    vols = [2e6] * 5 + [None]
    out = sdf.calc_volume_analysis(vols, [10.0] * 6, session_elapsed_frac=0.5)
    assert out["bar_partial"] is True
    assert out["vol_ratio_raw"] is None
    assert out["vol_ratio"] is None
    assert out["trend"] == "unknown"


# --- live path wiring (analyze_stock), modeled on test_analyze_combo fixtures ---

def _ohlcv(n=130, vol=1e6):
    rows = []
    for i in range(n):
        c = 90 + i * 0.15
        rows.append({"date": f"2025-{i // 28 + 1:02d}-{i % 28 + 1:02d}",
                     "open": c, "close": c, "high": c * 1.001,
                     "low": c * 0.999, "volume": vol})
    return rows


def _pipeline_env(monkeypatch):
    monkeypatch.setattr(sdf, "_benchmark_closes", lambda market, days=140: None)
    monkeypatch.setattr(sdf, "calc_relative_strength",
                        lambda market, closes, bench: {"rs_60d": None,
                                                       "rs_20d": None})
    monkeypatch.setattr(sdf, "fetch_upcoming_unlocks",
                        lambda code, within_days=60: [])
    monkeypatch.setattr(sdf, "calc_tradability",
                        lambda realtime, code, name="": {"limit_status":
                                                         "normal"})


def _raw():
    return {"ohlcv": _ohlcv(), "name": "TEST", "source": "syn",
            "errors": [], "realtime": {}, "adjustment": "qfq"}


def test_analyze_volume_flags_partial_bar_when_session_open(monkeypatch):
    _pipeline_env(monkeypatch)
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw())
    monkeypatch.setattr(sdf, "_last_bar_partial",
                        lambda market, date: (True, 0.5))
    r = sdf.analyze_stock("600519", days=120)
    v = r["indicators"]["volume"]
    assert v["bar_partial"] is True
    assert v["session_elapsed_pct"] == 50.0
    assert v["vol_ratio_raw"] == 1.0
    assert v["vol_ratio"] == 2.0  # constant volume pro-rated by half session


def test_analyze_volume_complete_bar_when_market_closed(monkeypatch):
    _pipeline_env(monkeypatch)
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw())
    r = sdf.analyze_stock("600519", days=120)  # past dates -> complete bar
    v = r["indicators"]["volume"]
    assert v["bar_partial"] is False
    assert "session_elapsed_pct" not in v
    assert v["vol_ratio"] == 1.0


def test_backtest_path_keeps_historical_semantics():
    """compute_signal_from_ohlcv must not see pro-rated volumes: with default
    args calc_volume_analysis is byte-for-byte the legacy contract, and the
    score it feeds is computed the same as before O-2."""
    bars = _ohlcv()
    score = sdf.compute_signal_from_ohlcv(bars)
    assert score is not None
    assert "total" in score and "signal" in score
    # default-path equality (no partial keys, unadjusted ratio)
    direct = sdf.calc_volume_analysis([b["volume"] for b in bars],
                                      [b["close"] for b in bars])
    assert direct == {"bar_partial": False, "vol_ratio": 1.0, "trend": "normal"}