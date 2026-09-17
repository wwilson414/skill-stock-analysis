"""Offline tests for daily_update command orchestration."""
import sys
from types import SimpleNamespace

import daily_update


def _args(**overrides):
    values = {
        "replay_only": False,
        "signals_only": False,
        "db": "reports/signals.db",
        "signals_out": "reports/p4_combo_backtest.json",
        "replay_out": "reports/p4_paper_trader.json",
        "start": "2025-09-01",
        "end": None,
        "codes": "",
        "limit": 0,
        "days": 900,
        "parallel": 6,
        "no_cache": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_commands_runs_refresh_then_replay():
    commands = daily_update.build_commands(_args(codes="600519,002032", limit=2,
                                                 end="2026-08-31"))

    assert [label for label, _ in commands] == ["refresh signals", "replay paper trader"]
    refresh = commands[0][1]
    replay = commands[1][1]
    assert refresh[:2] == [sys.executable, daily_update.os.path.join(
        daily_update.os.path.dirname(daily_update.__file__), "p4_combo_backtest.py")]
    assert "--save-store" in refresh and "reports/signals.db" in refresh
    assert "--codes" in refresh and "600519,002032" in refresh
    assert "--limit" in refresh and "2" in refresh
    assert "--persist" in replay and "--end" in replay


def test_build_commands_supports_cron_stages():
    assert len(daily_update.build_commands(_args(signals_only=True))) == 1
    assert daily_update.build_commands(_args(signals_only=True))[0][0] == "refresh signals"
    assert len(daily_update.build_commands(_args(replay_only=True))) == 1
    assert daily_update.build_commands(_args(replay_only=True))[0][0] == "replay paper trader"


def test_build_commands_forwards_no_cache():
    commands = daily_update.build_commands(_args(no_cache=True))

    assert "--no-cache" in commands[0][1]
    assert "--no-cache" in commands[1][1]