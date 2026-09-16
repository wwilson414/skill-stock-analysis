"""O-3: realtime.name unified to the resolved Chinese display name.

The THS price snapshot carries no name (only its valuation supplement does),
so a 429 there used to leave realtime.name as the bare code ('002032');
qt.gtimg occasionally emits spaced CJK names ('苏 泊 尔'). Real names pass
through; degraded (missing / code-like) names are backfilled via the THS
ticker search -> akshare code-name table -> display, best-effort.
"""
import pytest

import stock_data_fetcher as sdf


# --- _clean_cjk_name ---

def test_clean_cjk_collapses_spaced_cjk():
    assert sdf._clean_cjk_name("苏 泊 尔") == "苏泊尔"


def test_clean_cjk_keeps_clean_names():
    assert sdf._clean_cjk_name("腾讯控股") == "腾讯控股"


def test_clean_cjk_keeps_non_cjk_spaces():
    assert sdf._clean_cjk_name("Apple Inc") == "Apple Inc"


def test_clean_cjk_tolerates_none_and_empty():
    assert sdf._clean_cjk_name(None) is None
    assert sdf._clean_cjk_name("") == ""


# --- _unified_realtime_name passthrough ---

def test_real_chinese_name_passes_through(monkeypatch):
    monkeypatch.setattr(sdf, "_ths_api_key",
                        lambda: pytest.fail("backfill must not trigger"))
    assert sdf._unified_realtime_name(
        "苏泊尔", "002032", "002032", "cn_a") == "苏泊尔"


def test_spaced_cjk_name_is_collapsed(monkeypatch):
    monkeypatch.setattr(sdf, "_ths_api_key",
                        lambda: pytest.fail("backfill must not trigger"))
    assert sdf._unified_realtime_name(
        "苏 泊 尔", "002032", "002032", "cn_a") == "苏泊尔"


def test_english_name_passes_through(monkeypatch):
    monkeypatch.setattr(sdf, "_ths_api_key",
                        lambda: pytest.fail("backfill must not trigger"))
    assert sdf._unified_realtime_name(
        "Apple Inc", "AAPL", "AAPL", "us") == "Apple Inc"


def test_hk_code_like_name_falls_back_to_display(monkeypatch):
    monkeypatch.setattr(sdf, "_ths_api_key",
                        lambda: pytest.fail("backfill must not trigger"))
    monkeypatch.setattr(sdf, "_resolve_code_name_akshare",
                        lambda code: pytest.fail("hk must not use akshare"))
    assert sdf._unified_realtime_name(
        "HK00700", "00700", "HK00700", "cn_hk") == "HK00700"


# --- degraded names: backfill chain ---

def test_code_like_name_backfills_via_ths_search(monkeypatch):
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "fake_key")
    monkeypatch.setattr(sdf, "_search_fuyao", lambda q: [
        {"thscode": "002032.SZ", "ticker": "002032", "name": "苏泊尔",
         "market": "A-share"}])
    assert sdf._unified_realtime_name(
        "002032", "002032", "002032", "cn_a") == "苏泊尔"


def test_ths_search_miss_falls_back_to_akshare_table(monkeypatch):
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "fake_key")
    monkeypatch.setattr(sdf, "_search_fuyao", lambda q: [])
    monkeypatch.setattr(sdf, "_resolve_code_name_akshare",
                        lambda code: "华能国际")
    assert sdf._unified_realtime_name(
        "600011", "600011", "600011", "cn_a") == "华能国际"


def test_backfill_failure_keeps_code_without_raising(monkeypatch):
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "")
    monkeypatch.setattr(sdf, "_resolve_code_name_akshare", lambda code: None)
    assert sdf._unified_realtime_name(
        "002032", "002032", "002032", "cn_a") == "002032"


def test_backfill_survives_ths_search_exception(monkeypatch):
    def boom(q):
        raise RuntimeError("ths down")
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "fake_key")
    monkeypatch.setattr(sdf, "_search_fuyao", boom)
    monkeypatch.setattr(sdf, "_resolve_code_name_akshare",
                        lambda code: "苏泊尔")
    assert sdf._unified_realtime_name(
        "002032", "002032", "002032", "cn_a") == "苏泊尔"


def test_empty_name_backfills_via_ths_search(monkeypatch):
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "fake_key")
    monkeypatch.setattr(sdf, "_search_fuyao", lambda q: [
        {"thscode": "600519.SH", "name": "贵州茅台"}])
    assert sdf._unified_realtime_name(
        None, "600519", "600519", "cn_a") == "贵州茅台"


# --- analyze_stock wiring ---

def _ohlcv(n=130):
    rows = []
    for i in range(n):
        c = 90 + i * 0.15
        rows.append({"date": f"2025-{i // 28 + 1:02d}-{i % 28 + 1:02d}",
                     "open": c, "close": c, "high": c * 1.001,
                     "low": c * 0.999, "volume": 1e6})
    return rows


def _raw(realtime_name):
    return {"ohlcv": _ohlcv(), "name": realtime_name or "002032",
            "source": "syn", "errors": [],
            "realtime": {"name": realtime_name, "price": 39.43},
            "adjustment": "qfq"}


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


def test_analyze_backfills_degraded_realtime_name(monkeypatch):
    _pipeline_env(monkeypatch)
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw("002032"))
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "")
    monkeypatch.setattr(sdf, "_resolve_code_name_akshare",
                        lambda code: "苏泊尔")
    r = sdf.analyze_stock("002032", days=120)
    assert r["name"] == "苏泊尔"
    assert r["realtime"]["name"] == "苏泊尔"


def test_analyze_cleans_spaced_name_without_backfill(monkeypatch):
    _pipeline_env(monkeypatch)
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw("苏 泊 尔"))
    monkeypatch.setattr(sdf, "_ths_api_key",
                        lambda: pytest.fail("backfill must not trigger"))
    r = sdf.analyze_stock("002032", days=120)
    assert r["name"] == "苏泊尔"
    assert r["realtime"]["name"] == "苏泊尔"


def test_analyze_keeps_real_name_untouched(monkeypatch):
    _pipeline_env(monkeypatch)
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw("苏泊尔"))
    monkeypatch.setattr(sdf, "_ths_api_key",
                        lambda: pytest.fail("backfill must not trigger"))
    r = sdf.analyze_stock("002032", days=120)
    assert r["name"] == "苏泊尔"
    assert r["realtime"]["name"] == "苏泊尔"