#!/usr/bin/env python3
"""Run the signal-store refresh and paper-trading replay pipeline.

The script intentionally delegates data and trading logic to the existing
P4 command-line tools. It only assembles their arguments and propagates
failures, so scheduled runs have the same behavior as manual runs.
"""

import argparse
import os
import subprocess
import sys


def _command(script, *args):
    return [sys.executable, os.path.join(os.path.dirname(__file__), script), *args]


def build_commands(args):
    """Build the subprocess commands without touching the network or store."""
    commands = []
    if not args.replay_only:
        refresh = ["--save-store", args.db, "--out", args.signals_out,
                   "--days", str(args.days), "--parallel", str(args.parallel)]
        if args.codes:
            refresh += ["--codes", args.codes]
        if args.limit:
            refresh += ["--limit", str(args.limit)]
        if args.no_cache:
            refresh.append("--no-cache")
        commands.append(("refresh signals", _command("p4_combo_backtest.py", *refresh)))

    if not args.signals_only:
        replay = ["--db", args.db, "--start", args.start,
                  "--out", args.replay_out, "--days", str(args.days),
                  "--persist"]
        if args.end:
            replay += ["--end", args.end]
        if args.no_cache:
            replay.append("--no-cache")
        commands.append(("replay paper trader", _command("paper_trader.py", *replay)))
    return commands


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh signals and replay the paper account")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--signals-only", action="store_true",
                      help="refresh the SQLite signal store without replay")
    mode.add_argument("--replay-only", action="store_true",
                      help="replay the existing signal store without refreshing")
    parser.add_argument("--db", default="reports/signals.db")
    parser.add_argument("--signals-out", default="reports/p4_combo_backtest.json")
    parser.add_argument("--replay-out", default="reports/p4_paper_trader.json")
    parser.add_argument("--start", default="2025-09-01",
                        help="replay signal date lower bound")
    parser.add_argument("--end", default=None,
                        help="optional replay signal date upper bound")
    parser.add_argument("--codes", default="",
                        help="comma-separated stock codes for the refresh")
    parser.add_argument("--limit", type=int, default=0,
                        help="limit the refresh universe after selection")
    parser.add_argument("--days", type=int, default=900,
                        help="history bars passed to both existing tools")
    parser.add_argument("--parallel", type=int, default=6,
                        help="refresh worker count")
    parser.add_argument("--no-cache", action="store_true",
                        help="disable both tools' local data caches")
    args = parser.parse_args(argv)

    for label, command in build_commands(args):
        print(f"[daily] {label}: {' '.join(command)}", flush=True)
        subprocess.run(command, check=True)
    print("[daily] update complete", flush=True)


if __name__ == "__main__":
    main()