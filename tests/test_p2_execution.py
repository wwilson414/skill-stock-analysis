"""P2 execution-repricing unit tests (mechanics, not network)."""
import p2_execution as p2
import pytest

import numpy as np

N = 130
_CLOS = [100.0] * N
_CLOS[104] = 90.0
_CLOS[105] = 92.0
_OPENS = list(_CLOS)
_OPENS[101] = 110.0
_OHLCV = [{"date": f"2026-{i:04d}", "open": _OPENS[i], "close": _CLOS[i],
           "high": max(_OPENS[i], _CLOS[i]) * 1.001,
           "low": min(_OPENS[i], _CLOS[i]) * 0.999,
           "volume": 1e6} for i in range(N)]
_BENCH = [{"date": f"2026-{i:04d}", "close": 100.0} for i in range(N)]


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(p2, "_fetch_stock_ohlcv",
                        lambda e, d, uc: (_OHLCV, "X", "syn"))
    monkeypatch.setattr(p2, "_log", lambda *a, **k: None)


def _cn_a_entry():
    return {"code": "600519", "market": "cn_a", "normalized": "600519",
            "name": "x", "sector": "y"}


def test_a_share_limit_up_skips_entry(patched):
    rec = p2.run_stock((_cn_a_entry(), N, [5], _BENCH, False, 10.0))
    rows = {r["date"]: r for r in rec["rows"]}
    assert rows["2026-0100"]["entry_skipped"] is True
    assert rows["2026-0100"]["fwd5_open"] is None
    assert rows["2026-0100"]["fwd5_base"] is not None  # base unaffected


def test_a_share_limit_down_delays_exit(patched):
    rec = p2.run_stock((_cn_a_entry(), N, [5], _BENCH, False, 10.0))
    r = {r["date"]: r for r in rec["rows"]}["2026-0099"]
    assert r["exit_delayed"] is True
    assert abs(r["fwd5_open"] - (-8.0)) < 1e-6
    assert abs(r["fwd5_base"] - (-10.0)) < 1e-6


def test_cost_table_values():
    assert p2.FEE_TABLE["cn_a"][0] == pytest.approx(0.025)
    assert p2.FEE_TABLE["cn_a"][1] == pytest.approx(0.075)  # 2.5bp + 5bp stamp
    assert p2.FEE_TABLE["us"] == (0.02, 0.02)


def test_net_fee_calculation(patched):
    rec = p2.run_stock((_cn_a_entry(), N, [5], _BENCH, False, 10.0))
    r = {r["date"]: r for r in rec["rows"]}["2026-0099"]
    # net = open(-8.0) - fees(10bp=0.1) - 2*slip(10bp) = -8.300
    assert abs(r["fwd5_net_fee"] - (-8.1)) < 1e-6
    assert abs(r["fwd5_net"] - (-8.3)) < 1e-6


def test_hk_no_limit_mechanics(patched):
    hk = {"code": "HK00700", "market": "cn_hk", "normalized": "00700",
          "name": "x", "sector": "y"}
    rec = p2.run_stock((hk, N, [5], _BENCH, False, 10.0))
    r = {r["date"]: r for r in rec["rows"]}["2026-0100"]
    assert r["entry_skipped"] is False and r["exit_delayed"] is False
    assert abs(r["fwd5_open"] - (-8.0)) < 1e-6


def test_aggregate_aggregate_and_verdict(patched):
    rec = p2.run_stock((_cn_a_entry(), N, [5], _BENCH, False, 10.0))
    agg = p2.aggregate([rec], [5], 0)
    assert agg["exec_stats"]["cn_a"]["skipped"] >= 1
    v = p2.verdict(agg)
    assert "cn_a_open_entry_drag" in v
