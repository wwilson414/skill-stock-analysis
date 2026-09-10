"""P4-3: SQLite store unit tests (CRUD / upsert idempotency / filters)."""
import pytest

from store import Store, row_to_signal


def _sig(date, code="600519", phase="uptrend_pullback", combo=0.8,
         blocked=False, mom=72.0, mr=None, vol=0.8, weight=1.0):
    return {"date": date, "code": code, "market": "cn_a", "phase": phase,
            "mom": mom, "mr_score": mr, "comp_vol": vol, "combo": combo,
            "combo_phase": phase, "combo_weight": weight,
            "combo_blocked": blocked, "source": "test"}


def test_row_to_signal_none_safe():
    """缺字段 row → NULL 列；gate_blocked → 0/1。"""
    s = row_to_signal({"date": "d1", "code": "X", "combo_blocked": True})
    assert s["market"] is None and s["mom"] is None
    assert s["gate_blocked"] == 1
    assert row_to_signal({})["gate_blocked"] == 0


def test_save_and_query_roundtrip(tmp_path):
    db = tmp_path / "s.db"
    with Store(str(db)) as st:
        n = st.save_signals([_sig("2026-09-01"), _sig("2026-09-02")])
        assert n == 2
        rows = st.query_signals()
        assert len(rows) == 2
        assert rows[0]["date"] == "2026-09-01"      # ordered by date
        assert rows[1]["combo_signal"] == 0.8
        assert rows[1]["gate_blocked"] == 0
        assert st.latest_signal_date() == "2026-09-02"


def test_upsert_idempotent(tmp_path):
    """同 (date, code) 重复写：不重复行、值刷新、created_at 保留。"""
    db = tmp_path / "s.db"
    with Store(str(db)) as st:
        st.save_signals([_sig("2026-09-01")])
        st.save_signals([_sig("2026-09-01", combo=1.5)])
        rows = st.query_signals()
        assert len(rows) == 1                        # no duplicate
        assert rows[0]["combo_signal"] == 1.5        # value refreshed
        assert rows[0]["created_at"]                 # preserved by upsert


def test_query_filters(tmp_path):
    db = tmp_path / "s.db"
    with Store(str(db)) as st:
        st.save_signals([
            _sig("2026-09-01", code="A", phase="uptrend_pullback"),
            _sig("2026-09-02", code="B", phase="downtrend_decline",
                 combo=0.87, blocked=False, mr=2.9, vol=None, weight=0.3),
            _sig("2026-09-03", code="A", phase="range_swing"),
        ])
        assert len(st.query_signals(code="A")) == 2
        assert len(st.query_signals(phase="downtrend_decline")) == 1
        assert len(st.query_signals(date_from="2026-09-02")) == 2
        assert len(st.query_signals(date_from="2026-09-01",
                                    date_to="2026-09-02")) == 2
        assert len(st.query_signals(code="B", phase="range_swing")) == 0
        assert len(st.query_signals(limit=1)) == 1


def test_trade_lifecycle(tmp_path):
    db = tmp_path / "s.db"
    with Store(str(db)) as st:
        t1 = st.save_trade("2026-09-01", "A", "buy", 110.0, 100)
        t2 = st.save_trade("2026-09-02", "A", "sell", 120.0, 100)
        assert t1 != t2
        assert st.close_trade(t1, pnl=1000.0) is True
        assert st.close_trade(999, pnl=0) is False   # nonexistent id
        trades = st.query_trades(code="A")
        assert [t["status"] for t in trades] == ["closed", "open"]
        assert st.query_trades(status="open")[0]["id"] == t2


def test_portfolio_series(tmp_path):
    db = tmp_path / "s.db"
    with Store(str(db)) as st:
        st.save_portfolio_snapshot("2026-09-01", 90000.0, 1, 111000.0, 0.0)
        st.save_portfolio_snapshot("2026-09-02", 90000.0, 1, 112240.0, 0.0112)
        st.save_portfolio_snapshot("2026-09-02", 95000.0, 2, 113000.0,
                                   0.012)       # same-date snapshot updates
        series = st.get_portfolio_series()
        assert len(series) == 2
        assert series[-1]["total_value"] == 113000.0
        assert series[-1]["positions"] == 2


def test_db_path_creates_directory(tmp_path):
    db = tmp_path / "nested" / "dir" / "s.db"
    with Store(str(db)) as st:
        st.save_signals([_sig("2026-09-01")])
    assert db.exists()