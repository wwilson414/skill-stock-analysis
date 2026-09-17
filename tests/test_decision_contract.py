"""N-01 / N-02 / N-03 contract tests: output, errors, data quality, decisions.

Covers the three P0 items of HANDOFF §17:
- N-03 market normalization (`HK00700` / `0700.HK` -> one internal id), currency
  and the per-market trading-rule object;
- N-01 structured envelope (`success`/`status`), `{code, message, retryable}`
  errors, and the per-stock data-quality grade with its confidence impact;
- N-02 decision-support states with evidence, plus the N-10 hard rules
  (RSI > 80 / MA5 bias > 5% can never yield BUY_CANDIDATE).

Gate logic is tested directly against build_decision() with hand-built
indicator dicts (deterministic); wiring is tested through analyze_stock() with
the data-source layer monkeypatched.
"""
import numpy as np
import pytest

import stock_data_fetcher as sdf


# --- helpers ---------------------------------------------------------------

def _seg(n, a, b):
    return [float(x) for x in np.linspace(a, b, n)]


def _ohlcv(closes):
    return [{"date": f"2025-{i // 22 + 1:02d}-{i % 22 + 1:02d}",
             "open": c, "close": c, "high": c * 1.001, "low": c * 0.999,
             "volume": 1e6} for i, c in enumerate(closes)]


UP = _seg(156, 90, 110) + _seg(41, 110, 111.5) + [111.5, 111.3, 111.0]
DT = _seg(80, 125, 85) + _seg(30, 85, 60) + [58, 58]


def _raw(closes, market="cn_a", source="tencent", realtime=None):
    rt = {"price": closes[-1], "name": "TEST", "realtime_source": "tencent",
          "pe_ratio": 10.0, "pb_ratio": 1.2}
    if realtime:
        rt.update(realtime)
    return {"ohlcv": _ohlcv(closes), "name": "TEST", "source": source,
            "errors": [], "realtime": rt,
            "currency": sdf.market_currency(market),
            "market_rules": sdf.market_rules(market),
            "adjustment": "qfq"}


@pytest.fixture
def env(monkeypatch):
    """Cut the network: benchmark and unlock lookups become no-ops."""
    monkeypatch.setattr(sdf, "_benchmark_closes", lambda market, days=140: None)
    monkeypatch.setattr(sdf, "fetch_upcoming_unlocks",
                        lambda code, within_days=60: [])


def _stub_fetch(monkeypatch, market, raw):
    fn = {"cn_a": "fetch_cn_a", "cn_hk": "fetch_hk", "us": "fetch_us"}[market]
    monkeypatch.setattr(sdf, fn, lambda code, days: raw)


def _analyze(monkeypatch, market, closes=None, source="tencent", realtime=None):
    raw = _raw(closes or UP, market, source, realtime)
    _stub_fetch(monkeypatch, market, raw)
    code = {"cn_a": "600519", "cn_hk": "HK00700", "us": "AAPL"}[market]
    return sdf.analyze_stock(code, days=200, fetch_news=False)


def _indicators(**over):
    """Fully-populated indicator view with per-test overrides."""
    base = {
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
        "relative_strength": {"benchmark": "CSI 300", "rs_60d": 4.0},
        "events": {"upcoming_unlocks": [], "unlock_pct_30d": None,
                   "unlock_gate_status": "not_enabled"},
    }
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            base[key].update(val)
        else:
            base[key] = val
    return base


def _quality(level="high", impact="none", caveats=None):
    return {"level": level, "score": 100, "confidence_impact": impact,
            "caveats": caveats or [], "missing_fields": []}


def _combo(score=1.0, weight=1.0, blocked=False, phase="uptrend_pullback"):
    return {"phase": phase, "primary": "comp_vol", "primary_score": score,
            "weight": weight, "secondary": "mr_score", "secondary_score": 0.0,
            "gates": ["momentum_confirm"], "gate_results": {},
            "gate_blocked": blocked, "combo_score": None if blocked else score,
            "context": {}}


# ============================================================
# N-03: market normalization, currency, trading rules
# ============================================================

@pytest.mark.parametrize("raw_code", ["HK00700", "0700.HK", "00700.HK", "hk00700"])
def test_hk_ticker_formats_normalize_to_one_internal_id(raw_code):
    market, normalized, _display = sdf.classify_stock(raw_code)

    assert market == "cn_hk"
    assert normalized == "00700"


def test_a_share_ticker_formats_normalize_to_one_internal_id():
    for raw_code in ("600519", "600519.SH", "600519.SS", "SH600519"):
        assert sdf.classify_stock(raw_code) == ("cn_a", "600519", "600519")


def test_us_and_unknown_tickers():
    assert sdf.classify_stock("AAPL") == ("us", "AAPL", "AAPL")
    assert sdf.classify_stock("???")[0] == "unknown"


def test_market_currency_covers_all_markets():
    assert sdf.market_currency("cn_a") == "CNY"
    assert sdf.market_currency("cn_hk") == "HKD"
    assert sdf.market_currency("us") == "USD"


def test_market_rules_object_shape_per_market():
    required = {"exchange", "currency", "t_plus_1", "lot_size",
                "has_limit_up_down", "trading_calendar_ref", "execution_notes"}
    for market in ("cn_a", "cn_hk", "us"):
        rules = sdf.market_rules(market)
        assert required <= set(rules)
        assert rules["currency"] == sdf.market_currency(market)

    cn = sdf.market_rules("cn_a")
    assert cn["t_plus_1"] is True and cn["has_limit_up_down"] is True
    assert cn["limit_up_pct"] == 10.0 and cn["lot_size"] == 100

    us = sdf.market_rules("us")
    assert us["t_plus_1"] is False and us["has_limit_up_down"] is False
    assert us["limit_up_pct"] is None and us["lot_size"] == 1


def test_market_rules_returns_a_copy():
    rules = sdf.market_rules("cn_a")
    rules["lot_size"] = 999

    assert sdf.market_rules("cn_a")["lot_size"] == 100


@pytest.mark.parametrize("market,currency", [("cn_a", "CNY"),
                                             ("cn_hk", "HKD"),
                                             ("us", "USD")])
def test_analyze_stock_exposes_currency_and_market_rules(env, monkeypatch,
                                                         market, currency):
    result = _analyze(monkeypatch, market)

    assert result["currency"] == currency
    assert result["market_rules"]["currency"] == currency
    assert result["decision"]["market_rules"]["currency"] == currency


def test_hk_dot_format_runs_through_analyze_stock(env, monkeypatch):
    """`0700.HK` must reach the HK fetch path, not fall through as unknown."""
    _stub_fetch(monkeypatch, "cn_hk", _raw(UP, "cn_hk"))
    result = sdf.analyze_stock("0700.HK", days=200, fetch_news=False)

    assert result["market"] == "cn_hk"
    assert result["currency"] == "HKD"


@pytest.mark.parametrize("market,fetch_name,realtime_name,code,kline,currency,"
                         "has_bar_fallback", [
    ("cn_a", "fetch_cn_a", "_fetch_realtime_a", "600519", "_fetch_qq_a",
     "CNY", False),
    ("cn_hk", "fetch_hk", "_fetch_realtime_hk", "00700", "_fetch_qq_hk",
     "HKD", True),
    ("us", "fetch_us", "_fetch_realtime_us", "AAPL", "_fetch_qq_us",
     "USD", True),
])
def test_real_fetch_paths_return_currency_and_market_rules(
        monkeypatch, market, fetch_name, realtime_name, code, kline, currency,
        has_bar_fallback):
    """Regression: the real fetch_* return paths (incl. the no-quote fallback)
    must carry name/currency/market_rules and never raise UnboundLocalError."""
    bars = _ohlcv(UP)
    monkeypatch.setattr(sdf, kline, lambda c, d: (bars, "tencent"))
    # No live quote -> exercises the last-daily-bar fallback branch where present.
    monkeypatch.setattr(sdf, realtime_name, lambda c: {})

    out = getattr(sdf, fetch_name)(code, 120)

    assert out["currency"] == currency
    assert out["market_rules"]["currency"] == currency
    assert out["name"]
    assert out["ohlcv"]
    got_fallback = any("last daily bar" in e for e in out["errors"])
    assert got_fallback is has_bar_fallback


# ============================================================
# N-01: data quality, structured errors, envelope
# ============================================================

def test_data_quality_contract_shape(env, monkeypatch):
    result = _analyze(monkeypatch, "cn_a")
    dq = result["data_quality"]

    for key in ("level", "score", "missing_fields", "caveats",
                "confidence_impact", "fallback_used", "sources", "as_of",
                "adjustment", "fetch_time"):
        assert key in dq, f"data_quality missing {key}"
    assert dq["level"] in sdf.QUALITY_LEVELS
    assert dq["confidence_impact"] in ("none", "minor", "moderate", "major")
    assert set(dq["sources"]) >= {"ohlcv", "ohlcv_preferred", "ohlcv_fallback",
                                  "realtime", "valuation", "errors"}
    assert isinstance(dq["fallback_used"], bool)


def test_data_quality_high_when_primary_source_serves(env, monkeypatch):
    result = _analyze(monkeypatch, "cn_a", source="tencent")
    dq = result["data_quality"]

    assert dq["sources"]["ohlcv"] == "tencent"
    assert dq["sources"]["ohlcv_fallback"] is False
    assert dq["level"] == "high"
    assert dq["confidence_impact"] == "none"


def test_data_quality_flags_fallback_source(env, monkeypatch):
    """A non-preferred K-line source must be reported, not silently accepted."""
    result = _analyze(monkeypatch, "cn_a", source="akshare")
    dq = result["data_quality"]

    assert dq["fallback_used"] is True
    assert dq["sources"]["ohlcv_fallback"] is True
    assert dq["sources"]["ohlcv_preferred"] == "tencent"
    assert any("fallback source" in c for c in dq["caveats"])
    assert dq["confidence_impact"] in ("minor", "moderate", "major")


def test_data_quality_records_missing_fields(env, monkeypatch):
    """P/E + P/B absent -> listed as missing and confidence impact raised."""
    result = _analyze(monkeypatch, "cn_a",
                      realtime={"pe_ratio": None, "pb_ratio": None})
    dq = result["data_quality"]

    assert "realtime.pe_ratio" in dq["missing_fields"]
    assert "realtime.pb_ratio" in dq["missing_fields"]
    assert any("missing fields" in c for c in dq["caveats"])


def test_data_quality_reports_valuation_fallback_source(env, monkeypatch):
    result = _analyze(monkeypatch, "cn_a",
                      realtime={"valuation_source": "yfinance"})
    dq = result["data_quality"]

    assert dq["fallback_used"] is True
    assert dq["sources"]["valuation"] == "yfinance"
    assert any("valuation source" in c for c in dq["caveats"])


def test_empty_result_is_insufficient_quality():
    """No price / no MA60 -> insufficient, major impact, fields listed."""
    dq = sdf.assess_data_quality({"realtime": {}, "data_source": "tencent",
                                  "fetch_errors": [], "indicators": {}}, "cn_a")

    assert dq["level"] == "insufficient"
    assert dq["confidence_impact"] == "major"
    assert "realtime.price" in dq["missing_fields"]


def _quality_probe(combo):
    return {
        "realtime": {"price": 100.0, "name": "TEST", "pe_ratio": 10.0,
                     "pb_ratio": 1.2, "realtime_source": "tencent"},
        "data_source": "tencent", "fetch_errors": [],
        "indicators": {"ma": {"MA60": 95.0}, "risk": {"rr_ratio": 2.0},
                       "volume": {"vol_ratio": 1.0},
                       "relative_strength": {"rs_60d": 1.0}},
        "combo": combo,
        "events": {"unlock_gate_status": "active"},
    }


def test_data_quality_ignores_gate_blocked_combo():
    """A combo blocked by its own gate is by design, not a data gap."""
    dq = sdf.assess_data_quality(
        _quality_probe({"gate_blocked": True, "combo_score": None}), "cn_a")

    assert "combo.combo_score" not in dq["missing_fields"]
    assert dq["level"] == "high"


def test_data_quality_flags_absent_combo_object():
    dq = sdf.assess_data_quality(_quality_probe(None), "cn_a")

    assert "combo.combo_score" in dq["missing_fields"]
    assert dq["score"] < 100


def test_data_quality_does_not_penalise_clean_a_share_for_no_unlocks():
    """unlock_gate_status=active means the gate ran, even with no events."""
    dq = sdf.assess_data_quality(_quality_probe({"combo_score": 1.0}), "cn_a")

    assert "events.unlock_pct_30d" not in dq["missing_fields"]
    assert dq["level"] == "high"


@pytest.mark.parametrize("message,expected,retryable", [
    ("Cannot classify stock code: ???", "INVALID_TICKER", False),
    ("unknown_market: XX", "UNSUPPORTED_MARKET", False),
    ("All data sources failed for A-share 600519: 429", "DATA_SOURCE_UNAVAILABLE", True),
    ("tencent returned no data for sh600519", "DATA_NOT_FOUND", False),
    ("Insufficient data for 600519: only 3 bars", "INSUFFICIENT_HISTORY", False),
    ("HTTP 429 too many requests", "RATE_LIMITED", True),
    ("request timed out", "TIMEOUT", True),
])
def test_error_payload_maps_code_and_retryable(message, expected, retryable):
    payload = sdf.error_payload("600519", ValueError(message), message=message)

    assert payload["error_code"] == expected
    assert payload["retryable"] is retryable
    assert payload["code"] == "600519"
    assert payload["message"] == message
    assert payload["error_code"] in sdf.ERROR_CODES


def test_error_payload_keeps_legacy_fields():
    payload = sdf.error_payload("AAPL", RuntimeError("boom"))

    assert payload["error"] == "RuntimeError: boom"
    assert payload["type"] == "RuntimeError"
    assert payload["error_code"] == "UNKNOWN_ERROR"


def test_error_payload_timeout_error_type():
    payload = sdf.error_payload("AAPL", TimeoutError("something odd"))

    assert payload["error_code"] == "TIMEOUT"
    assert payload["retryable"] is True


@pytest.mark.parametrize("requested,success,expected", [
    (3, 3, (True, "success")),
    (3, 1, (False, "partial")),
    (3, 0, (False, "failure")),
    (0, 0, (False, "no_data")),
])
def test_envelope_status_states(requested, success, expected):
    assert sdf.envelope_status(requested, success) == expected


def test_main_envelope_reports_partial_failure(env, monkeypatch, capsys):
    """N-01: JSON stdout must carry success/status + structured errors."""
    import json

    def fake_analyze(code, days=120, fetch_news=False, **kw):
        if code == "BAD":
            raise ValueError("Cannot classify stock code: BAD")
        return {"code": code, "market": "cn_a", "currency": "CNY"}

    monkeypatch.setattr(sdf, "analyze_stock", fake_analyze)
    monkeypatch.setattr(sdf.sys, "argv",
                        ["stock_data_fetcher.py", "--stocks", "600519,BAD"])
    sdf.main()
    out = json.loads(capsys.readouterr().out)

    assert out["success"] is False
    assert out["status"] == "partial"
    assert out["total_requested"] == 2 and out["total_success"] == 1
    assert len(out["errors"]) == 1
    err = out["errors"][0]
    assert err["code"] == "BAD"
    assert err["error_code"] == "INVALID_TICKER"
    assert err["retryable"] is False
    assert err["error"] == "ValueError: Cannot classify stock code: BAD"


def test_main_envelope_reports_full_success(env, monkeypatch, capsys):
    import json

    monkeypatch.setattr(sdf, "analyze_stock",
                        lambda code, days=120, fetch_news=False, **kw:
                        {"code": code, "market": "cn_a", "currency": "CNY"})
    monkeypatch.setattr(sdf.sys, "argv",
                        ["stock_data_fetcher.py", "--stocks", "600519"])
    sdf.main()
    out = json.loads(capsys.readouterr().out)

    assert out["success"] is True
    assert out["status"] == "success"
    assert out["errors"] == []


# ============================================================
# N-02 / N-10: decision states and hard rules
# ============================================================

def _decide(indicators, total=80.0, combo=None, quality=None, market="cn_a"):
    score = {"total": total, "signal": "buy", "signal_cn": "Buy",
             "buy_gates": [], "warnings": []}
    return sdf.build_decision(market, score, combo if combo is not None
                              else _combo(), indicators,
                              quality or _quality())


def test_decision_payload_contract_complete(env, monkeypatch):
    result = _analyze(monkeypatch, "cn_a")
    decision = result["decision"]

    for key in ("state", "horizon", "intent", "confidence",
                "supporting_evidence", "opposing_evidence", "key_risks",
                "hard_gates", "suggested_action_range",
                "position_size_suggestion", "re_evaluation_triggers",
                "data_quality_warning", "assumptions", "market_rules",
                "disclaimer"):
        assert key in decision, f"decision missing {key}"
    assert decision["state"] in sdf.DECISION_STATES
    assert decision["confidence"] in ("high", "medium", "low",
                                      "insufficient_data")
    assert decision["disclaimer"] == sdf.DECISION_DISCLAIMER
    assert decision["intent"] == "technical"
    assert isinstance(decision["supporting_evidence"], list)
    assert isinstance(decision["opposing_evidence"], list)
    assert isinstance(decision["re_evaluation_triggers"], list)


def test_decision_state_is_always_in_allowed_enum():
    for total in (0, 20, 40, 55, 65, 90):
        for alignment in ("strong_bullish", "bullish", "consolidation",
                          "bearish", "strong_bearish"):
            for phase in ("uptrend_pullback", "range_swing", "downtrend_decline"):
                d = _decide(_indicators(ma={"alignment": alignment},
                                        context={"phase": phase}), total=total)
                assert d["state"] in sdf.DECISION_STATES


def test_decision_rsi_overbought_never_buy_candidate():
    """N-10: RSI > 80 must block BUY_CANDIDATE regardless of the score."""
    d = _decide(_indicators(rsi={"RSI6": 88.0, "RSI12": 84.0}), total=95.0)

    assert d["state"] != "BUY_CANDIDATE"
    assert any("overbought" in g for g in d["hard_gates"])


def test_decision_bias_ma5_never_buy_candidate():
    """N-10: MA5 bias > 5% must block BUY_CANDIDATE regardless of the score."""
    d = _decide(_indicators(bias={"bias_ma5": 6.5}), total=95.0)

    assert d["state"] != "BUY_CANDIDATE"
    assert any("overextended" in g for g in d["hard_gates"])


def test_decision_downtrend_never_buy_candidate():
    d = _decide(_indicators(ma={"alignment": "bearish"},
                            context={"phase": "downtrend_decline"}))

    assert d["state"] != "BUY_CANDIDATE"
    assert any("downtrend" in g for g in d["hard_gates"])


def test_decision_insufficient_quality_forces_recheck():
    d = _decide(_indicators(), total=95.0,
                quality=_quality("insufficient", "major", ["no price"]))

    assert d["state"] == "RECHECK_REQUIRED"
    assert d["confidence"] == "insufficient_data"
    assert d["data_quality_warning"] is not None


def test_decision_buy_candidate_requires_combo_confirmation():
    """A buy-grade score without a passing combo signal is not a candidate."""
    blocked = _decide(_indicators(), total=80.0,
                      combo=_combo(blocked=True))
    ok = _decide(_indicators(), total=80.0, combo=_combo())

    assert blocked["state"] == "SMALL_POSITION_ONLY"
    assert ok["state"] == "BUY_CANDIDATE"


def test_decision_position_size_follows_paper_trader_sizing():
    """Cap must mirror paper_trader: (1/max_positions) x phase weight."""
    d = _decide(_indicators(), total=80.0, combo=_combo(weight=0.5))
    base = 1.0 / sdf.DEFAULT_MAX_POSITIONS

    assert d["position_size_suggestion"]["suggested_max_weight"] == base * 0.5
    assert "combo phase weight 0.5" in d["position_size_suggestion"]["basis"]
    assert "RISK.max_per_stock" in d["position_size_suggestion"]["basis"]


def test_decision_position_size_never_exceeds_risk_ceiling():
    """A full phase weight is still capped by score_config.RISK.max_per_stock."""
    from score_config import RISK
    d = _decide(_indicators(), total=80.0, combo=_combo(weight=1.0))

    assert d["position_size_suggestion"]["suggested_max_weight"] <= \
        RISK["max_per_stock"]


def test_decision_has_no_position_size_for_non_entry_states():
    d = _decide(_indicators(bias={"bias_ma5": 9.0}), total=95.0)

    assert d["state"] == "WATCHLIST"
    assert d["position_size_suggestion"] is None


def test_decision_does_not_fabricate_price_levels():
    """Stop/target must be echoed from indicators.risk, never invented."""
    d = _decide(_indicators())
    band = d["suggested_action_range"]

    assert band["stop_suggested"] == 95.0
    assert band["target_suggested"] == 125.0
    assert "indicators.risk" in band["price_levels_source"]


def test_decision_reports_missing_price_levels_as_none():
    """No risk levels -> None, never a made-up number."""
    indicators = _indicators()
    indicators["risk"] = {"rr_ratio": None, "stop_suggested": None,
                          "target_suggested": None}
    d = _decide(indicators)
    band = d["suggested_action_range"]

    assert band["stop_suggested"] is None
    assert band["target_suggested"] is None


def test_decision_key_risks_carry_required_keys():
    d = _decide(_indicators(context={"phase": "downtrend_decline"},
                            ma={"alignment": "bearish"}),
                quality=_quality("low", "moderate", ["fallback source used"]))

    assert d["key_risks"], "at least one risk flag required"
    for flag in d["key_risks"]:
        assert {"category", "severity", "evidence", "impact",
                "monitoring_indicator"} <= set(flag)
        assert flag["severity"] in ("low", "medium", "high", "critical")


def test_decision_currency_risk_for_non_a_share():
    d = _decide(_indicators(), market="us")

    assert any(r["category"] == "currency" for r in d["key_risks"])


def test_decision_long_term_boundary_is_explicit():
    """A technical state must not be presented as a long-term conclusion."""
    d = _decide(_indicators())

    assert any("Fundamentals and full valuation" in a for a in d["assumptions"])
    assert any("portfolio" in a.lower() for a in d["assumptions"])