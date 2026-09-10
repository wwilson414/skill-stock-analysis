"""P4-2: analyze_stock() combo field integration tests (mechanics, no network).

Verifies that the production entry point carries the phase-aware combo signal
(4-2 acceptance: field complete), the combo phase agrees with the momentum
backtest phase (same calc_pullback_context classifier), and the primary
driver follows the P4-1 division-of-labour table.
"""
import numpy as np
import pytest

import stock_data_fetcher as sdf


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


# 已在信号组合验证中确认的 phase 分类构造：
# uptrend: 长缓升 + 尾部回落；downtrend: 急坠后持平日（mr_event 触发）
UP = _seg(156, 90, 110) + _seg(41, 110, 111.5) + [111.5, 111.3, 111.0]
DT = _seg(80, 125, 85) + _seg(30, 85, 60) + [58, 58]


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


def test_analyze_stock_combo_field_complete(env, monkeypatch):
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw(UP))
    r = sdf.analyze_stock("600519", days=200, fetch_news=False)
    assert "combo" in r
    c = r["combo"]
    for k in ("phase", "primary", "primary_score", "weight",
              "secondary", "secondary_score", "gates",
              "gate_results", "gate_blocked", "combo_score", "context"):
        assert k in c, f"combo missing field {k}"
    assert c["phase"] == "uptrend_pullback"
    assert c["primary"] == "comp_vol"
    assert c["weight"] == 1.0
    assert c["gates"] == ["momentum_confirm"]


def test_analyze_stock_combo_phase_matches_context(env, monkeypatch):
    """combo 与 indicators.context.phase 出自同一分类器，必须一致。"""
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw(UP))
    r = sdf.analyze_stock("600519", days=200, fetch_news=False)
    assert r["combo"]["phase"] == r["indicators"]["context"]["phase"]


def test_analyze_stock_combo_downtrend_primary(env, monkeypatch):
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw(DT))
    r = sdf.analyze_stock("600519", days=200, fetch_news=False)
    c = r["combo"]
    assert c["phase"] == "downtrend_decline"
    assert c["primary"] == "mr_score"
    assert c["weight"] == 0.3
    assert c["gates"] == ["extreme_only"]


def test_analyze_stock_combo_nonfatal(env, monkeypatch):
    """combo 计算失败不阻塞整体分析（非致命）：外层仍返回完整 result。"""
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: _raw(DT))
    import signal_combo as sc
    monkeypatch.setattr(sc, "compute_combo_signal",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom")))
    # compute_combo_signal 在 analyze 内以延迟导入获取 → 补丁要作用于其模块 attr
    r = sdf.analyze_stock("600519", days=200, fetch_news=False)
    assert r["combo"] is None
    assert r["trend_score"] is not None
    assert r["indicators"]["context"]["phase"] == "downtrend_decline"