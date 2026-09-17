"""Tests for multi-source upcoming unlock data and explicit gate status."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import sys

import pandas as pd

import stock_data_fetcher as sdf


def _future_date(days=7):
    return (datetime.now().date() + timedelta(days=days)).strftime("%Y-%m-%d")


def test_unlocks_use_eastmoney_queue_fields(monkeypatch):
    date = _future_date()
    ak = SimpleNamespace(
        stock_restricted_release_queue_em=lambda symbol: pd.DataFrame([
            {"解禁时间": date, "占流通市值比例": "6.25%", "解禁数量": 12345}
        ]),
        stock_restricted_release_queue_sina=lambda symbol: (_ for _ in ()).throw(
            AssertionError("Sina must not run after Eastmoney succeeds")),
    )
    monkeypatch.setattr(sdf, "_check_source", lambda name: True)
    monkeypatch.setitem(sys.modules, "akshare", ak)

    out = sdf.fetch_upcoming_unlocks("600519", within_days=30)

    assert out == [{"date": date, "pct_of_float": 6.25,
                    "detail": "12345", "source":
                    "akshare/stock_restricted_release_queue_em"}]


def test_unlocks_fall_back_to_sina_without_ratio(monkeypatch):
    date = _future_date()
    ak = SimpleNamespace(
        stock_restricted_release_queue_em=lambda symbol: (_ for _ in ()).throw(
            ConnectionError("Eastmoney unavailable")),
        stock_restricted_release_queue_sina=lambda symbol: pd.DataFrame([
            {"解禁日期": date, "解禁数量": 9876}
        ]),
    )
    monkeypatch.setattr(sdf, "_check_source", lambda name: True)
    monkeypatch.setitem(sys.modules, "akshare", ak)

    out = sdf.fetch_upcoming_unlocks("600519", within_days=30)

    assert out[0]["date"] == date
    assert out[0]["pct_of_float"] is None
    assert out[0]["source"].endswith("queue_sina")


def test_analyze_marks_unlock_gate_not_enabled(monkeypatch):
    monkeypatch.setattr(sdf, "fetch_cn_a", lambda code, days: {
        "ohlcv": [{"date": f"2025-{(i - 1) // 28 + 1:02d}-{(i - 1) % 28 + 1:02d}",
                   "open": 10, "close": 10, "high": 10.1,
                   "low": 9.9, "volume": 1000} for i in range(1, 121)],
        "name": "TEST", "source": "syn", "errors": [], "realtime": {},
        "adjustment": "qfq",
    })
    monkeypatch.setattr(sdf, "fetch_upcoming_unlocks", lambda code, within_days=60: [])
    monkeypatch.setattr(sdf, "_benchmark_closes", lambda market, days=140: None)
    monkeypatch.setattr(sdf, "calc_relative_strength",
                        lambda market, closes, bench: {"rs_60d": None, "rs_20d": None})
    monkeypatch.setattr(sdf, "calc_tradability",
                        lambda realtime, code, name="": {"limit_status": "normal"})

    result = sdf.analyze_stock("600519", days=120, fetch_news=False)

    assert result["events"]["unlock_gate_status"] == "not_enabled"
    assert result["indicators"]["context"]["unlock_gate_status"] == "not_enabled"