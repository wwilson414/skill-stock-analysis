"""N-04 fundamentals data layer + decision integration tests.

Covers the HANDOFF §17.2 N-04 acceptance points:
- unified envelope schema for A/HK/US with currency, period, source;
- missing fields are recorded with reasons (never guessed), status
  ok/partial/insufficient;
- source fallback chain: first failure -> next source; total failure ->
  "insufficient" without raising;
- five-dimension analysis grades only from metrics actually present;
- decision layer: long-term BUY_CANDIDATE requires fundamental evidence;
- analyze_stock wiring is non-fatal.

All network sources are stubbed; nothing here hits the wire.
"""
import sys
import types

import numpy as np
import pandas as pd
import pytest

import stock_data_fetcher as sdf
import fundamentals as fnd


# ============================================================
# Shared stubs for analyze_stock wiring tests (same pattern as
# test_analyze_combo.py: synthetic OHLCV, real indicator pipeline)
# ============================================================

def _seg(n, a, b):
    return [float(x) for x in np.linspace(a, b, n)]


def _ohlcv(closes):
    rows = []
    for i, c in enumerate(closes):
        rows.append({"date": f"2025-{i // 22 + 1:02d}-{i % 22 + 1:02d}",
                     "open": c, "close": c, "high": c * 1.001,
                     "low": c * 0.999, "volume": 1e6})
    return rows


def _raw(closes):
    return {"ohlcv": _ohlcv(closes), "name": "TEST", "source": "syn",
            "errors": [], "realtime": {}, "adjustment": "qfq"}


UP = _seg(156, 90, 110) + _seg(41, 110, 111.5) + [111.5, 111.3, 111.0]


@pytest.fixture
def env(monkeypatch):
    """Replace the data-source layer; indicators still run for real."""
    monkeypatch.setattr(sdf, "_benchmark_closes",
                        lambda market, days=140: None)
    monkeypatch.setattr(sdf, "calc_relative_strength",
                        lambda market, closes, bench: {"rs_60d": None,
                                                       "rs_20d": None})
    monkeypatch.setattr(sdf, "fetch_upcoming_unlocks",
                        lambda code, within_days=60: [])
    monkeypatch.setattr(sdf, "calc_tradability",
                        lambda realtime, code, name="": {"limit_status":
                                                         "normal"})


# ============================================================
# fetch_fundamentals: envelope, fallback, missing reasons
# ============================================================

def _stub_fetcher(monkeypatch, name, fn):
    monkeypatch.setitem(fnd._FETCHERS, name, fn)


def test_envelope_schema_and_status_partial(monkeypatch):
    def partial(market, code, env):
        env["metrics"]["roe_pct"] = 15.0
        env["source"] = "stub"
        env["missing"] = {}
        return fnd._fill_missing(env)

    _stub_fetcher(monkeypatch, "akshare", partial)
    out = fnd.fetch_fundamentals("cn_a", "600519")

    assert out["status"] == "partial"
    assert out["source"] == "stub"
    assert out["currency"] == "CNY"
    assert out["metrics"]["roe_pct"] == 15.0
    assert out["metrics"]["revenue"] is None
    assert "not provided" in out["missing"]["revenue"]
    assert out["period_type"] is None or out["period_type"] in ("annual", "ttm")
    assert "fetched_at" in out


def test_market_currencies_cover_all_markets():
    assert fnd._MARKET_CURRENCY == {"cn_a": "CNY", "cn_hk": "HKD", "us": "USD"}
    for market in ("cn_a", "cn_hk", "us"):
        assert fnd._envelope(market, "X")["currency"] is not None


def test_fallback_chain_first_failure_uses_second(monkeypatch):
    calls = []

    def boom(market, code, env):
        calls.append("akshare")
        raise RuntimeError("network down")

    def ok(market, code, env):
        calls.append("yfinance")
        env["metrics"]["roe_pct"] = 10.0
        env["source"] = "yfinance"
        env["missing"] = {}
        return fnd._fill_missing(env)

    _stub_fetcher(monkeypatch, "akshare", boom)
    _stub_fetcher(monkeypatch, "yfinance", ok)
    out = fnd.fetch_fundamentals("cn_a", "600519")

    assert calls == ["akshare", "yfinance"]
    assert out["source"] == "yfinance"
    assert out["status"] == "partial"


def test_all_sources_fail_is_insufficient_and_never_raises(monkeypatch):
    def boom(market, code, env):
        raise RuntimeError("network down")

    _stub_fetcher(monkeypatch, "akshare", boom)
    _stub_fetcher(monkeypatch, "yfinance", boom)
    out = fnd.fetch_fundamentals("cn_a", "600519")

    assert out["status"] == "insufficient"
    assert out["source"] is None
    assert "failed" in out["missing"]["source"]
    assert all(v is None for v in out["metrics"].values())


def test_chain_covers_all_markets():
    assert set(fnd._FUNDAMENTALS_CHAIN) == {"cn_a", "cn_hk", "us"}
    assert fnd._FUNDAMENTALS_CHAIN["cn_hk"] == ("yfinance",)
    assert fnd._FUNDAMENTALS_CHAIN["us"] == ("yfinance",)


# ============================================================
# yfinance mapping (synthetic statements — no network)
# ============================================================

def _stmt(rows, years=(2023, 2024)):
    """rows: {row_name: (older, newer)} -> a yfinance-like DataFrame."""
    return pd.DataFrame.from_dict(rows, orient="index", columns=list(years))


def _fake_yf_module(ticker_cls):
    fake = types.ModuleType("yfinance")

    class _Wrap:
        @staticmethod
        def Ticker(code):
            return ticker_cls()

    fake.Ticker = _Wrap.Ticker
    return fake


def test_yfinance_metric_mapping_from_synthetic_statements(monkeypatch):
    class FakeTicker:
        info = {"financialCurrency": "CNY", "returnOnEquity": 0.18,
                "dividendYield": 0.012}
        income_stmt = _stmt({
            "Total Revenue": (1000.0, 1200.0),
            "Net Income": (100.0, 180.0),
            "Gross Profit": (400.0, 540.0),
        })
        balance_sheet = _stmt({
            "Total Assets": (2000.0, 2200.0),
            "Total Liabilities Net Minority Total": (800.0, 880.0),
            "Current Assets": (900.0, 1000.0),
            "Current Liabilities": (450.0, 500.0),
        })
        cashflow = _stmt({
            "Operating Cash Flow": (150.0, 200.0),
            "Free Cash Flow": (120.0, 160.0),
        })

    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf_module(FakeTicker))
    env = fnd._fund_yfinance("cn_a", "600519", fnd._envelope("cn_a", "600519"))
    m = env["metrics"]

    assert env["source"] == "yfinance"
    assert env["currency"] == "CNY"
    assert env["period_type"] == "annual"
    assert env["report_period"] == "FY2024"
    assert m["revenue"] == 1200.0 and m["net_profit"] == 180.0
    assert m["gross_margin_pct"] == pytest.approx(45.0)
    assert m["net_margin_pct"] == pytest.approx(15.0)
    assert m["roe_pct"] == pytest.approx(18.0)
    assert m["revenue_growth_pct"] == pytest.approx(20.0)
    assert m["profit_growth_pct"] == pytest.approx(80.0)
    assert m["cash_conversion_ratio"] == pytest.approx(200.0 / 180.0, abs=1e-3)
    assert m["debt_ratio_pct"] == pytest.approx(40.0)
    assert m["current_ratio"] == pytest.approx(2.0)
    assert m["dividend_yield_pct"] == pytest.approx(1.2)
    assert env["status"] in ("ok", "partial")


def test_yfinance_equity_fallback_roe(monkeypatch):
    """ROE derived from balance sheet when info.returnOnEquity is absent."""
    class FakeTicker:
        info = {}
        income_stmt = _stmt({"Net Income": (100.0, 200.0),
                             "Total Revenue": (800.0, 1000.0)})
        balance_sheet = _stmt({"Stockholders Equity": (800.0, 1000.0)})
        cashflow = _stmt({})

    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf_module(FakeTicker))
    env = fnd._fund_yfinance("cn_a", "600519", fnd._envelope("cn_a", "600519"))
    assert env["metrics"]["roe_pct"] == pytest.approx(20.0)


def test_ycode_market_routing():
    assert fnd._ycode("cn_hk", "00700") == "00700.HK"
    assert fnd._ycode("cn_a", "600519") == "600519.SS"
    assert fnd._ycode("cn_a", "000001") == "000001.SZ"
    assert fnd._ycode("cn_a", "430047") == "430047.BJ"
    assert fnd._ycode("us", "AAPL") == "AAPL"


def test_growth_guards_zero_base():
    assert fnd._growth(10.0, 0) is None
    assert fnd._growth(10.0, None) is None
    assert fnd._growth(None, 5.0) is None
    assert fnd._growth(-50.0, 100.0) == pytest.approx(-150.0)


# ============================================================
# akshare mapping (synthetic F10 rows — no network)
# ============================================================

def test_akshare_a_share_row_mapping(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(
        ak, "stock_financial_analysis_indicator",
        lambda symbol, start_year: pd.DataFrame([
            {"日期": "2023-12-31", "净资产收益率(%)": 22.0,
             "销售毛利率(%)": 91.0, "销售净利率(%)": 52.0,
             "营业收入增长率(%)": 18.0, "净利润增长率(%)": 19.0,
             "资产负债率(%)": 25.0, "流动比率": 3.5},
            {"日期": "2024-12-31", "净资产收益率(%)": 24.0,
             "销售毛利率(%)": 91.5, "销售净利率(%)": 53.0,
             "营业收入增长率(%)": 15.5, "净利润增长率(%)": 15.0,
             "资产负债率(%)": 24.0, "流动比率": 3.8},
        ]))

    env = fnd._fund_akshare_a("600519", fnd._envelope("cn_a", "600519"))
    m = env["metrics"]

    assert env["source"] == "akshare"
    assert env["report_period"] == "2024-12-31"


# ============================================================
# Five-dimension analysis
# ============================================================

def _fund(**metrics):
    base = {k: None for k in fnd.METRIC_KEYS}
    base.update(metrics)
    return {"status": "partial", "metrics": base, "missing": {}}


def test_analysis_insufficient_when_no_data():
    a = fnd.analyze_fundamentals(None)
    assert a["overall"] == "insufficient"
    assert a["confidence_impact"] == "major"
    a2 = fnd.analyze_fundamentals({"status": "insufficient", "metrics": {},
                                   "missing": {"source": "akshare failed: x"}})
    assert a2["confidence_impact"] == "major"
    assert "akshare failed" in a2["warnings"]


def test_analysis_strong_company():
    a = fnd.analyze_fundamentals(_fund(
        roe_pct=25.0, net_margin_pct=30.0, revenue_growth_pct=15.0,
        profit_growth_pct=20.0, cash_conversion_ratio=1.2,
        debt_ratio_pct=30.0, current_ratio=2.5))
    assert a["overall"] == "strong"
    assert a["confidence_impact"] == "none"
    for d in ("profitability", "growth", "cash_flow_quality",
              "financial_safety"):
        assert a["dimensions"][d]["level"] == "strong", d


def test_analysis_weak_flags_lower_confidence():
    a = fnd.analyze_fundamentals(_fund(roe_pct=2.0, profit_growth_pct=-5.0,
                                       cash_conversion_ratio=-0.5,
                                       debt_ratio_pct=85.0))
    weak = [d for d, v in a["dimensions"].items() if v["level"] == "weak"]
    assert set(weak) == {"profitability", "growth", "cash_flow_quality",
                         "financial_safety"}
    assert a["confidence_impact"] == "minor"


def test_competitive_position_is_insufficient_until_n07():
    a = fnd.analyze_fundamentals(_fund(roe_pct=20.0))
    assert a["dimensions"]["competitive_position"]["level"] == "insufficient"
    assert "N-07" in a["dimensions"]["competitive_position"]["evidence"][0]
    assert a["confidence_impact"] == "none"    # not graded yet — no penalty


def test_partial_data_only_grades_what_exists():
    a = fnd.analyze_fundamentals(_fund(roe_pct=20.0))
    assert a["dimensions"]["profitability"]["level"] == "ok"
    assert a["dimensions"]["growth"]["level"] == "insufficient"
    assert a["dimensions"]["cash_flow_quality"]["level"] == "insufficient"


def test_summary_text():
    a = fnd.analyze_fundamentals(_fund(roe_pct=20.0))
    s = fnd.summary_text(a)
    assert "overall" in s and "profitability=ok" in s
    assert fnd.summary_text(None) == "no fundamentals fetched"


# ============================================================
# Decision integration: long-term gate + confidence
# ============================================================

def _good_decision_input():
    score = {"total": 80.0, "signal": "buy", "signal_cn": "买入",
             "buy_gates": [], "warnings": []}
    combo = {"phase": "uptrend_pullback", "primary": "comp_vol",
             "primary_score": 1.0, "weight": 1.0, "secondary": "mr_score",
             "secondary_score": 0.0, "gates": [], "gate_results": {},
             "gate_blocked": False, "combo_score": 1.0, "context": {}}
    indicators = {
        "ma": {"alignment": "bullish", "MA5": 100.0, "MA60": 95.0},
        "rsi": {"RSI6": 55.0, "RSI12": 58.0, "zone": "neutral"},
        "bias": {"bias_ma5": 1.0},
        "volume": {"vol_ratio": 1.2, "trend": "normal"},
        "macd": {"signal": "bullish"},
        "context": {"phase": "uptrend_pullback", "chg_20d_pct": 3.0,
                    "dist_ma60_pct": 5.0},
        "risk": {"rr_ratio": 2.5, "stop_suggested": 95.0,
                 "target_suggested": 125.0, "atr_pct": 2.0,
                 "ann_vol_pct": 25.0},
        "tradability": {"limit_status": "normal"},
        "events": {"upcoming_unlocks": [], "unlock_pct_30d": None,
                   "unlock_gate_status": "not_enabled"},
    }
    quality = {"level": "high", "score": 100, "confidence_impact": "none",
               "caveats": [], "missing_fields": []}
    return score, combo, indicators, quality


def _fund_payload(overall, impact):
    return {"data": {"status": "partial"}, "analysis": {
        "dimensions": {}, "overall": overall, "confidence_impact": impact,
        "warnings": []}}


def test_technical_buy_untouched_without_fundamentals():
    score, combo, indicators, quality = _good_decision_input()
    d = sdf.build_decision("cn_a", score, combo, indicators, quality,
                           horizon="technical")
    assert d["state"] == "BUY_CANDIDATE"


def test_long_term_buy_blocked_without_fundamentals():
    score, combo, indicators, quality = _good_decision_input()
    d = sdf.build_decision("cn_a", score, combo, indicators, quality,
                           horizon="long_term", fundamentals=None)
    assert d["state"] == "WATCHLIST"


def test_long_term_buy_blocked_with_insufficient_fundamentals():
    score, combo, indicators, quality = _good_decision_input()
    d = sdf.build_decision("cn_a", score, combo, indicators, quality,
                           horizon="long_term",
                           fundamentals=_fund_payload("insufficient", "major"))
    assert d["state"] == "WATCHLIST"


def test_long_term_buy_allowed_with_fundamental_evidence():
    score, combo, indicators, quality = _good_decision_input()
    d = sdf.build_decision("cn_a", score, combo, indicators, quality,
                           horizon="long_term",
                           fundamentals=_fund_payload("strong", "none"))
    assert d["state"] == "BUY_CANDIDATE"


def test_confidence_downgraded_by_missing_fundamentals():
    score, combo, indicators, quality = _good_decision_input()
    d = sdf.build_decision("cn_a", score, combo, indicators, quality,
                           fundamentals=_fund_payload("insufficient", "major"))
    assert d["confidence"] == "medium"         # would be high without the penalty
    assert "fundamentals" in (d["data_quality_warning"] or "")


def test_confidence_untouched_by_healthy_fundamentals():
    score, combo, indicators, quality = _good_decision_input()
    d = sdf.build_decision("cn_a", score, combo, indicators, quality,
                           fundamentals=_fund_payload("strong", "none"))
    assert d["confidence"] == "high"


# ============================================================
# analyze_stock wiring (non-fatal, off by default)
# ============================================================

def test_analyze_stock_has_no_fundamentals_by_default(env, monkeypatch):
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw(UP))
    out = sdf.analyze_stock("600519")
    assert out["fundamentals"] is None


def test_analyze_stock_fundamentals_failure_is_non_fatal(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down (test stub)")

    import fundamentals
    monkeypatch.setitem(fundamentals._FETCHERS, "akshare", boom)
    monkeypatch.setitem(fundamentals._FETCHERS, "yfinance", boom)
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw(UP))
    out = sdf.analyze_stock("600519", fetch_fundamentals=True)
    assert "fundamentals" in out
    # Whatever the network did, the technical result must survive intact.
    assert out["code"] == "600519" and "trend_score" in out


def test_analyze_stock_fundamentals_stubbed_success(env, monkeypatch):
    """With a stubbed source, analyze_stock attaches data + analysis."""
    def stub_fetch(market, code, env=None):
        e = env if env is not None else fnd._envelope(market, code)
        e["metrics"]["roe_pct"] = 20.0
        e["source"] = "stub"
        e["missing"] = {}
        return fnd._fill_missing(e)

    import fundamentals
    monkeypatch.setitem(fundamentals._FETCHERS, "akshare", stub_fetch)
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw(UP))
    out = sdf.analyze_stock("600519", fetch_fundamentals=True)

    assert out["fundamentals"]["data"]["metrics"]["roe_pct"] == 20.0
    a = out["fundamentals"]["analysis"]
    assert a["overall"] in ("strong", "ok", "weak", "insufficient")
    assert a["confidence_impact"] in ("none", "minor", "major")
