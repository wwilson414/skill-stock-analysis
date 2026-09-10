#!/usr/bin/env python3
"""P4-3: SQLite persistence layer (ROADMAP P4-3, reports/signals.db).

Tables
------
  signals(date, code, market, phase, mom, mr_score, comp_vol,
          combo_signal, combo_phase, combo_weight, gate_blocked, source,
          created_at)   PRIMARY KEY (date, code)
  trades(id, date, code, direction, price, size, pnl, status, created_at)
  portfolio(date, cash, positions, total_value, daily_return)

Conventions
-----------
* Incremental append: `save_signals` upserts on (date, code) — re-running a
  day never duplicates rows nor rewrites history (created_at is preserved on
  conflict; signal columns are refreshed).
* sqlite3 stdlib only (no ORM dependency); Row factory for dict access.
* Data flow (P4-3 onwards): analyze_stock() `combo` field / p4 harness rows
  -> row_to_signal() -> Store.save_signals() -> paper_trader.py (P4-4) replay.

Usage
  python3 references/store.py --db reports/signals.db --demo   # smoke
"""

import argparse
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    date          TEXT NOT NULL,
    code          TEXT NOT NULL,
    market        TEXT,
    phase         TEXT,
    mom           REAL,
    mr_score      REAL,
    comp_vol      REAL,
    combo_signal  REAL,
    combo_phase   TEXT,
    combo_weight  REAL,
    gate_blocked  INTEGER DEFAULT 0,
    source        TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (date, code)
);
CREATE INDEX IF NOT EXISTS idx_signals_code  ON signals(code);
CREATE INDEX IF NOT EXISTS idx_signals_phase ON signals(phase);
CREATE INDEX IF NOT EXISTS idx_signals_date  ON signals(date);

CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    code       TEXT NOT NULL,
    direction  TEXT NOT NULL,          -- BUY / SELL
    price      REAL NOT NULL,
    size       REAL NOT NULL,
    pnl        REAL,
    status     TEXT DEFAULT 'open',    -- open / closed / cancelled
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);

CREATE TABLE IF NOT EXISTS portfolio (
    date         TEXT PRIMARY KEY,
    cash         REAL,
    positions    INTEGER,
    total_value  REAL,
    daily_return REAL
);
"""

_SIGNAL_COLS = ("date", "code", "market", "phase", "mom", "mr_score",
                "comp_vol", "combo_signal", "combo_phase", "combo_weight",
                "gate_blocked", "source")


def row_to_signal(row: dict) -> dict:
    """Map a p2/p4 execution row (run_stock_combo output) to a signals row.

    None-safe: absent keys become NULL; gate_blocked stored as 0/1.
    """
    blocked = row.get("combo_blocked")
    return {
        "date": row.get("date"),
        "code": row.get("code"),
        "market": row.get("market"),
        "phase": row.get("phase"),
        "mom": row.get("mom"),
        "mr_score": row.get("mr_score"),
        "comp_vol": row.get("comp_vol"),
        "combo_signal": row.get("combo"),
        "combo_phase": row.get("combo_phase"),
        "combo_weight": row.get("combo_weight"),
        "gate_blocked": 1 if blocked else 0,
        "source": row.get("source"),
    }


class Store:
    """Thin sqlite3 wrapper over the P4-3 schema (CRUD + queries)."""

    def __init__(self, path: str = ":memory:"):
        if path != ":memory:" and os.path.dirname(os.path.abspath(path)):
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- signals ------------------------------------------------------------
    def save_signals(self, rows: list) -> int:
        """Upsert signal rows (dict or p2/p4 row); returns rows written."""
        n = 0
        for r in rows:
            sig = r if "combo_signal" in r else row_to_signal(r)
            vals = [sig.get(c) for c in _SIGNAL_COLS]
            self.conn.execute(
                f"INSERT INTO signals ({', '.join(_SIGNAL_COLS)}) "
                f"VALUES ({', '.join('?' * len(_SIGNAL_COLS))}) "
                f"ON CONFLICT(date, code) DO UPDATE SET "
                + ", ".join(f"{c} = excluded.{c}" for c in _SIGNAL_COLS[2:]),
                vals)
            n += 1
        self.conn.commit()
        return n

    def query_signals(self, code: str = None, phase: str = None,
                      date_from: str = None, date_to: str = None,
                      limit: int = None) -> list:
        """Signals ordered by date/code; filters are AND-combined."""
        q, args = ["SELECT * FROM signals WHERE 1=1"], []
        for col, val in (("code", code), ("phase", phase)):
            if val:
                q.append(f" AND {col} = ?")
                args.append(val)
        if date_from:
            q.append(" AND date >= ?")
            args.append(date_from)
        if date_to:
            q.append(" AND date <= ?")
            args.append(date_to)
        q.append(" ORDER BY date, code")
        if limit:
            q.append(" LIMIT ?")
            args.append(limit)
        return [dict(r) for r in self.conn.execute("".join(q), args)]

    def latest_signal_date(self):
        row = self.conn.execute("SELECT MAX(date) AS d FROM signals").fetchone()
        return row["d"] if row else None

    # -- trades -------------------------------------------------------------
    def save_trade(self, date: str, code: str, direction: str, price: float,
                   size: float, status: str = "open") -> int:
        cur = self.conn.execute(
            "INSERT INTO trades (date, code, direction, price, size, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date, code, direction.upper(), price, size, status))
        self.conn.commit()
        return cur.lastrowid

    def close_trade(self, trade_id: int, pnl: float,
                    status: str = "closed") -> bool:
        cur = self.conn.execute(
            "UPDATE trades SET pnl = ?, status = ? WHERE id = ?",
            (pnl, status, trade_id))
        self.conn.commit()
        return cur.rowcount > 0

    def query_trades(self, code: str = None, status: str = None) -> list:
        q, args = ["SELECT * FROM trades WHERE 1=1"], []
        for col, val in (("code", code), ("status", status)):
            if val:
                q.append(f" AND {col} = ?")
                args.append(val)
        q.append(" ORDER BY id")
        return [dict(r) for r in self.conn.execute("".join(q), args)]

    # -- portfolio ----------------------------------------------------------
    def save_portfolio_snapshot(self, date: str, cash: float,
                                positions: int, total_value: float,
                                daily_return: float = None) -> None:
        self.conn.execute(
            "INSERT INTO portfolio (date, cash, positions, total_value, "
            "daily_return) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET cash = excluded.cash, "
            "positions = excluded.positions, "
            "total_value = excluded.total_value, "
            "daily_return = excluded.daily_return",
            (date, cash, positions, total_value, daily_return))
        self.conn.commit()

    def get_portfolio_series(self) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM portfolio ORDER BY date")]


def _demo(db_path: str):
    """Smoke: upsert 2 signals (1 duplicate round), trade lifecycle, portfolio."""
    with Store(db_path) as st:
        demo = [
            {"date": "2026-09-01", "code": "600519", "market": "cn_a",
             "phase": "uptrend_pullback", "mom": 72.0, "mr_score": None,
             "comp_vol": 0.8, "combo": 0.8, "combo_phase": "uptrend_pullback",
             "combo_weight": 1.0, "combo_blocked": False, "source": "demo"},
            {"date": "2026-09-02", "code": "600519", "market": "cn_a",
             "phase": "downtrend_decline", "mom": 31.0, "mr_score": 2.9,
             "comp_vol": None, "combo": 0.87,
             "combo_phase": "downtrend_decline", "combo_weight": 0.3,
             "combo_blocked": False, "source": "demo"},
        ]
        st.save_signals(demo)
        st.save_signals(demo)          # duplicate round -> upsert, still 2 rows
        q = st.query_signals(code="600519")
        assert len(q) == 2, q
        assert q[1]["combo_signal"] == 0.87
        assert q[1]["gate_blocked"] == 0
        tid = st.save_trade("2026-09-01", "600519", "buy", 110.0, 100)
        st.close_trade(tid, pnl=1240.0)
        trades = st.query_trades(code="600519")
        assert trades[0]["pnl"] == 1240.0 and trades[0]["status"] == "closed"
        st.save_portfolio_snapshot("2026-09-01", 90000.0, 1, 111000.0, 0.0)
        st.save_portfolio_snapshot("2026-09-02", 90000.0, 1, 112240.0, 0.0112)
        series = st.get_portfolio_series()
        assert len(series) == 2 and series[-1]["total_value"] == 112240.0
        print(f"[store] demo OK -> {db_path}")
        print(f"[store] latest_signal_date: {st.latest_signal_date()}")
        print(f"[store] signals: {len(q)} | trades: {len(trades)} | "
              f"portfolio: {len(series)}")


def main():
    ap = argparse.ArgumentParser(description="P4-3 SQLite store smoke")
    ap.add_argument("--db", default="reports/signals.db")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        _demo(args.db)
    else:
        print(f"db: {args.db} (use --demo for a smoke run)")


if __name__ == "__main__":
    main()
