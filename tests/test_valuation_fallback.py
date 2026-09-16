"""O-1: Valuation P/E, P/B fallback chain (tencent / akshare / efinance / yfinance).

Verifies that when the primary realtime source serves a price but not
valuation fields (e.g. THS Official API valuation endpoint HTTP 429),
pe_ratio / pb_ratio are enriched from the market-specific fallback chain
(single-code Tencent quote → akshare spot → efinance base info → yfinance),
so the dashboard never shows N/A for P/E and P/B.
"""
import pytest

import stock_data_fetcher as sdf


# --- _enrich_valuation unit tests (no network, mocked spot fetch) ---

def test_enrich_noop_when_pe_pb_present(monkeypatch):
    """When pe_ratio + pb_ratio already present, no fallback fetch is triggered."""
    called = {"val": False}
    monkeypatch.setattr(sdf, "_fetch_valuation_spot",
                        lambda m, c: (called.__setitem__("val", True), {})[1])
    rt = {"price": 100.0, "pe_ratio": 15.0, "pb_ratio": 2.5}
    result = sdf._enrich_valuation(rt, "cn_a", "600519")
    assert result is rt
    assert not called["val"]


def test_enrich_fills_missing_pe_and_pb(monkeypatch):
    """pe_ratio/pb_ratio missing => fetched from _fetch_valuation_spot."""
    monkeypatch.setattr(sdf, "_fetch_valuation_spot",
                        lambda m, c: {"pe_ratio": 28.5, "pb_ratio": 5.2})
    rt = {"price": 1880.0, "name": "600519", "pe_ratio": None, "pb_ratio": None}
    result = sdf._enrich_valuation(rt, "cn_a", "600519")
    assert result["pe_ratio"] == 28.5
    assert result["pb_ratio"] == 5.2
    assert result["pe_ttm"] == 28.5  # backward-compat alias


def test_enrich_noop_when_no_price(monkeypatch):
    """No price => nowhere to enrich."""
    called = {"val": False}
    monkeypatch.setattr(sdf, "_fetch_valuation_spot",
                        lambda m, c: (called.__setitem__("val", True), {})[1])
    rt = {"pe_ratio": None, "pb_ratio": None}
    result = sdf._enrich_valuation(rt, "cn_a", "600519")
    assert not called["val"]


def test_enrich_graceful_on_spot_failure(monkeypatch):
    """When _fetch_valuation_spot returns empty, fields stay None."""
    monkeypatch.setattr(sdf, "_fetch_valuation_spot", lambda m, c: {})
    rt = {"price": 100.0, "pe_ratio": None, "pb_ratio": None}
    result = sdf._enrich_valuation(rt, "cn_a", "600519")
    assert result["pe_ratio"] is None
    assert result["pb_ratio"] is None


def test_enrich_only_fills_missing_fields(monkeypatch):
    """Only missing fields are filled; existing ones are preserved."""
    monkeypatch.setattr(sdf, "_fetch_valuation_spot",
                        lambda m, c: {"pe_ratio": 28.5, "pb_ratio": 5.2})
    rt = {"price": 100.0, "pe_ratio": 20.0, "pb_ratio": None}
    result = sdf._enrich_valuation(rt, "cn_a", "600519")
    assert result["pe_ratio"] == 20.0  # preserved
    assert result["pb_ratio"] == 5.2   # filled


def test_enrich_mirrors_pe_ttm_alias_into_pe(monkeypatch):
    """rt carrying only the legacy pe_ttm still ends up with the template's pe_ratio."""
    monkeypatch.setattr(sdf, "_fetch_valuation_spot", lambda m, c: {})
    rt = {"price": 100.0, "pe_ttm": 15.0, "pb_ratio": None}
    result = sdf._enrich_valuation(rt, "cn_a", "600519")
    # pe known via pe_ttm alias => no spot fetch, but pe_ratio must be populated
    assert result["pe_ratio"] == 15.0
    assert result["pe_ttm"] == 15.0


# --- _fetch_realtime_a integration (mocked sources) ---

def test_fetch_realtime_a_enriches_when_fuyao_price_no_valuation(monkeypatch):
    """fuyao serves price but no pe/pb -> _fetch_valuation_spot fills both."""
    monkeypatch.setattr(sdf, "_fetch_realtime_qt", lambda symbol: {})  # tencent unavailable
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "fake_key")
    monkeypatch.setattr(sdf, "_fetch_realtime_fuyao",
                        lambda code: {"price": 1880.0, "name": "600519"})
    monkeypatch.setattr(sdf, "_fetch_valuation_spot",
                        lambda m, c: {"pe_ratio": 28.5, "pb_ratio": 5.2})
    rt = sdf._fetch_realtime_a("600519")
    assert rt["price"] == 1880.0
    assert rt["pe_ratio"] == 28.5
    assert rt["pb_ratio"] == 5.2


def test_fetch_realtime_a_akshare_has_valuation_no_enrich(monkeypatch):
    """When akshare spot is primary (has pe/pb), enrichment is no-op."""
    called = {"val": False}
    monkeypatch.setattr(sdf, "_fetch_realtime_qt", lambda symbol: {})  # tencent unavailable
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "")
    monkeypatch.setattr(sdf, "_check_source", lambda name: name == "akshare")
    import akshare as ak
    monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: __import__("pandas").DataFrame([{
        "code": "600519", "name": "Moutai", "latest price": 1880.0,
        "change percent": 1.0, "P/E ratio - dynamic": 28.5, "P/B ratio": 5.2,
        "total market cap": 1e9, "circulating market cap": 8e8,
        "high": 1890.0, "low": 1870.0, "today open": 1875.0,
        "previous close": 1865.0, "volume ratio": 1.2,
        "volume": 1e6, "turnover": 1.8e9, "amplitude": 1.0,
    }]))
    monkeypatch.setattr(sdf, "_fetch_valuation_spot",
                        lambda m, c: (called.__setitem__("val", True), {})[1])
    rt = sdf._fetch_realtime_a("600519")
    assert not called["val"]
    assert rt["pe_ratio"] == 28.5
    assert rt["pb_ratio"] == 5.2


def test_fetch_realtime_a_efinance_enriches(monkeypatch):
    """efinance serves price but no pe/pb -> enrichment fills them."""
    monkeypatch.setattr(sdf, "_fetch_realtime_qt", lambda symbol: {})  # tencent unavailable
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "")
    monkeypatch.setattr(sdf, "_check_source", lambda name: name == "efinance")
    import efinance as ef
    monkeypatch.setattr(ef.stock, "get_realtime_quotes", lambda codes:
                        __import__("pandas").DataFrame([{
                            "stock name": "Moutai", "latest price": 1880.0,
                            "change percent": 1.0,
                        }]))
    monkeypatch.setattr(sdf, "_fetch_valuation_spot",
                        lambda m, c: {"pe_ratio": 28.5, "pb_ratio": 5.2})
    rt = sdf._fetch_realtime_a("600519")
    assert rt["price"] == 1880.0
    assert rt["pe_ratio"] == 28.5
    assert rt["pb_ratio"] == 5.2
# --- fallback chain (_fetch_valuation_spot) ---

def test_qq_a_symbol_exchange_prefix():
    """A-share code -> Tencent quote symbol via the shared thscode classifier."""
    assert sdf._qq_a_symbol("600519") == "sh600519"
    assert sdf._qq_a_symbol("688111") == "sh688111"
    assert sdf._qq_a_symbol("002032") == "sz002032"
    assert sdf._qq_a_symbol("300750") == "sz300750"
    assert sdf._qq_a_symbol("920008") == "bj920008"


def test_valuation_spot_prefers_tencent(monkeypatch):
    """cn_a chain: Tencent wins and later sources are never called."""
    called = []
    monkeypatch.setattr(sdf, "_valuation_tencent",
                        lambda m, c: (called.append("tencent"),
                                      {"pe_ratio": 15.58, "pb_ratio": 6.25})[1])
    for name in ("akshare", "efinance", "yfinance"):
        monkeypatch.setattr(sdf, f"_valuation_{name}",
                            lambda m, c, n=name: called.append(n) or {})
    val = sdf._fetch_valuation_spot("cn_a", "002032")
    assert val == {"pe_ratio": 15.58, "pb_ratio": 6.25, "source": "tencent"}
    assert called == ["tencent"]


def test_valuation_spot_skips_failing_source_and_uses_next(monkeypatch):
    """A raising source is skipped without aborting the chain."""
    def boom(m, c):
        raise RuntimeError("eastmoney blocked")

    monkeypatch.setattr(sdf, "_valuation_tencent", boom)
    monkeypatch.setattr(sdf, "_valuation_akshare", lambda m, c: {})
    monkeypatch.setattr(sdf, "_valuation_efinance",
                        lambda m, c: {"pe_ratio": 9.19, "pb_ratio": None})
    monkeypatch.setattr(sdf, "_valuation_yfinance",
                        lambda m, c: pytest.fail("yfinance should not be reached"))
    val = sdf._fetch_valuation_spot("cn_a", "600011")
    assert val == {"pe_ratio": 9.19, "pb_ratio": None, "source": "efinance"}


def test_valuation_spot_empty_when_every_source_returns_nulls(monkeypatch):
    """All-null responses (e.g. loss-making stock) yield {} instead of a stub."""
    null = lambda m, c: {"pe_ratio": None, "pb_ratio": None}
    for name in ("tencent", "akshare", "efinance", "yfinance"):
        monkeypatch.setattr(sdf, f"_valuation_{name}", null)
    assert sdf._fetch_valuation_spot("cn_a", "600519") == {}


def test_valuation_spot_hk_chain_starts_with_yfinance(monkeypatch):
    """cn_hk chain: yfinance first (only cheap source carrying HK P/B)."""
    called = []
    monkeypatch.setattr(sdf, "_valuation_yfinance",
                        lambda m, c: (called.append("yfinance"),
                                      {"pe_ratio": 14.62, "pb_ratio": 2.95})[1])
    for name in ("tencent", "akshare"):
        monkeypatch.setattr(sdf, f"_valuation_{name}",
                            lambda m, c, n=name: called.append(n) or {})
    val = sdf._fetch_valuation_spot("cn_hk", "00700")
    assert val == {"pe_ratio": 14.62, "pb_ratio": 2.95, "source": "yfinance"}
    assert called == ["yfinance"]


def test_valuation_spot_unknown_market_returns_empty():
    assert sdf._fetch_valuation_spot("jp", "7203") == {}


# --- provenance + HK tencent path ---

def test_enrich_records_valuation_source(monkeypatch):
    """Filled fields are tagged with the source that served them."""
    monkeypatch.setattr(sdf, "_fetch_valuation_spot",
                        lambda m, c: {"pe_ratio": 15.58, "pb_ratio": 6.25,
                                      "source": "tencent"})
    rt = {"price": 39.43}
    result = sdf._enrich_valuation(rt, "cn_a", "002032")
    assert result["valuation_source"] == "tencent"


def test_enrich_does_not_tag_source_when_nothing_filled(monkeypatch):
    """An all-null response fills nothing and adds no provenance tag."""
    monkeypatch.setattr(sdf, "_fetch_valuation_spot",
                        lambda m, c: {"pe_ratio": None, "pb_ratio": None,
                                      "source": "tencent"})
    rt = {"price": 39.43, "pe_ratio": None, "pb_ratio": None}
    result = sdf._enrich_valuation(rt, "cn_a", "002032")
    assert "valuation_source" not in result


# --- priority order: Tencent first for A-share quote + K-line ---

def test_fetch_realtime_a_tencent_is_priority_1(monkeypatch):
    """Tencent single quote wins and the key-gated/other sources are never called."""
    monkeypatch.setattr(sdf, "_ths_api_key",
                        lambda: pytest.fail("THS must not be reached"))
    monkeypatch.setattr(sdf, "_check_source",
                        lambda name: pytest.fail(f"{name} must not be reached"))
    monkeypatch.setattr(sdf, "_fetch_realtime_qt",
                        lambda symbol: {"name": "华能国际", "price": 6.87,
                                        "pe_ratio": 9.19, "pb_ratio": 1.67,
                                        "realtime_source": "tencent"})
    rt = sdf._fetch_realtime_a("600011")
    assert rt["realtime_source"] == "tencent"
    assert rt["price"] == 6.87
    assert rt["pe_ratio"] == 9.19   # already present -> no fallback call
    assert "valuation_source" not in rt


def test_fetch_realtime_a_falls_through_when_tencent_empty(monkeypatch):
    """An empty Tencent response (unknown symbol) must not abort the chain."""
    monkeypatch.setattr(sdf, "_fetch_realtime_qt", lambda symbol: {})
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "fake_key")
    monkeypatch.setattr(sdf, "_fetch_realtime_fuyao",
                        lambda code: {"name": "华能国际", "price": 6.88})
    monkeypatch.setattr(sdf, "_fetch_valuation_spot", lambda m, c: {})
    rt = sdf._fetch_realtime_a("600011")
    assert rt["price"] == 6.88


def test_qq_a_symbol_used_by_realtime_and_kline(monkeypatch):
    """Both A-share entry points resolve the exchange prefix through _qq_a_symbol."""
    seen = []
    monkeypatch.setattr(sdf, "_fetch_realtime_qt",
                        lambda symbol: seen.append(("quote", symbol)) or {})
    monkeypatch.setattr(sdf, "_check_source", lambda name: False)
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "")
    monkeypatch.setattr(sdf, "_fetch_realtime_ths", lambda code: {})
    assert sdf._fetch_realtime_a("920008") == {}
    assert ("quote", "bj920008") in seen

    kline_seen = []
    closes = [10.0, 10.5, 10.5, 11.0, 11.0]
    monkeypatch.setattr(sdf, "_qq_kline",
                        lambda symbol, days: kline_seen.append(symbol) or
                        [{"date": f"2026-09-{i + 1:02d}", "open": c, "high": c,
                          "low": c, "close": c, "volume": 100, "amount": None,
                          "pct_chg": None} for i, c in enumerate(closes)])
    ohlcv, source = sdf._fetch_qq_a("002032", 5)
    assert kline_seen == ["sz002032"]
    assert source == "tencent"
    assert len(ohlcv) == 5
    assert ohlcv[1]["pct_chg"] == 5.0   # 10.0 -> 10.5
    assert ohlcv[3]["pct_chg"] == 4.76  # 10.5 -> 11.0
    assert ohlcv[0]["volume"] == 10000.0  # lots -> shares conversion


def test_fetch_cn_a_kline_tencent_is_priority_1(monkeypatch):
    """fetch_cn_a takes Tencent K-line first, skipping THS/efinance/akshare/yfinance."""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(sdf, "_fetch_qq_a",
                        lambda code, days: ([{"date": "2026-09-16", "open": 1.0,
                                              "high": 1.0, "low": 1.0, "close": 1.0,
                                              "volume": 1.0, "amount": None,
                                              "pct_chg": None}], "tencent"))
    monkeypatch.setattr(sdf, "_ths_api_key",
                        lambda: pytest.fail("THS must not be reached"))
    monkeypatch.setattr(sdf, "_check_source",
                        lambda name: pytest.fail(f"{name} must not be reached"))
    monkeypatch.setattr(sdf, "_fetch_realtime_a", lambda code: {"price": 1.0})
    out = sdf.fetch_cn_a("002032", 1)
    assert out["source"] == "tencent"
    assert out["errors"] == []


def test_fetch_cn_a_falls_back_when_tencent_kline_fails(monkeypatch):
    """A failing Tencent K-line is recorded and THS Official API takes over."""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(sdf, "_fetch_qq_a",
                        lambda code, days: (_ for _ in ()).throw(ValueError("no data")))
    monkeypatch.setattr(sdf, "_ths_api_key", lambda: "fake_key")
    monkeypatch.setattr(sdf, "_fetch_fuyao_a",
                        lambda code, days: ([{"date": "2026-09-16", "open": 1.0,
                                              "high": 1.0, "low": 1.0, "close": 1.0,
                                              "volume": 1.0, "amount": None,
                                              "pct_chg": None}], "ths_api"))
    monkeypatch.setattr(sdf, "_fetch_realtime_a", lambda code: {"price": 1.0})
    out = sdf.fetch_cn_a("002032", 1)
    assert out["source"] == "ths_api"
    assert any(e.startswith("tencent:") for e in out["errors"])


def test_fetch_realtime_hk_tencent_enriches_missing_pb(monkeypatch):
    """Tencent HK serves P/E but no P/B -> P/B comes from the fallback chain."""
    monkeypatch.setattr(sdf, "_check_source", lambda name: False)
    monkeypatch.setattr(sdf, "_fetch_realtime_qt",
                        lambda symbol: {"name": "腾讯控股", "price": 434.8,
                                        "pe_ratio": 15.89, "pb_ratio": None})
    monkeypatch.setattr(sdf, "_valuation_yfinance",
                        lambda m, c: {"pe_ratio": 14.62, "pb_ratio": 2.95})
    rt = sdf._fetch_realtime_hk("00700")
    assert rt["price"] == 434.8
    assert rt["pe_ratio"] == 15.89   # tencent value preserved
    assert rt["pb_ratio"] == 2.95    # filled by yfinance
    assert rt["valuation_source"] == "yfinance"


def test_fetch_realtime_hk_tencent_is_priority_1(monkeypatch):
    """HK realtime: tencent leads; efinance/akshare (35s EastMoney timeouts on
    rate-limited networks) and yfinance must not even be reached."""
    monkeypatch.setattr(sdf, "_check_source",
                        lambda name: pytest.fail(f"{name} must not be reached"))
    monkeypatch.setattr(sdf, "_fetch_realtime_qt",
                        lambda symbol: {"name": "腾讯控股", "price": 434.8,
                                        "pe_ratio": 15.89, "pb_ratio": None,
                                        "realtime_source": "tencent"})
    monkeypatch.setattr(sdf, "_valuation_yfinance",
                        lambda m, c: {"pe_ratio": 14.62, "pb_ratio": 2.95})
    rt = sdf._fetch_realtime_hk("00700")
    assert rt["realtime_source"] == "tencent"
    assert rt["pb_ratio"] == 2.95    # only missing field filled
    assert rt["pe_ratio"] == 15.89   # existing value kept
