"""Command-line entry point for Atlas Signal."""

import argparse
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

from atlas_signal import __version__
from atlas_signal.collector import RunSummary, run_collection
from atlas_signal.config import ConfigError, load_sources, sync_sources
from atlas_signal.db import SchemaError, open_database
from atlas_signal.download import Downloader, fetch_url

DEFAULT_DB = "data/atlas_signal.sqlite3"
DEFAULT_SOURCES = "config/sources.toml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas_signal",
        description="Atlas Signal: experimental news intelligence and event tracking.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    collect = commands.add_parser(
        "collect",
        help="fetch the configured feeds once and store what they contain",
        description="Fetch each enabled feed once, sequentially, and store articles and sightings.",
    )
    collect.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path (default: {DEFAULT_DB})")
    collect.add_argument(
        "--sources", default=DEFAULT_SOURCES, help=f"source config file (default: {DEFAULT_SOURCES})"
    )
    collect.add_argument(
        "--source", action="append", dest="source_ids", metavar="ID",
        help="collect only this source id (repeatable)",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    downloader: Downloader = fetch_url,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "collect":
        return collect(args, downloader, sleep)
    parser.print_help()
    return 0


def collect(args: argparse.Namespace, downloader: Downloader, sleep: Callable[[float], None]) -> int:
    try:
        sources = load_sources(args.sources)
    except ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    selected = [s for s in sources if s.enabled]
    if args.source_ids:
        by_id = {s.id: s for s in sources}
        unknown = [i for i in args.source_ids if i not in by_id]
        if unknown:
            print(f"error: unknown source id(s): {', '.join(unknown)}. "
                  f"Known: {', '.join(by_id)}", file=sys.stderr)
            return 2
        disabled = [i for i in args.source_ids if not by_id[i].enabled]
        if disabled:
            print(f"error: source id(s) disabled in config: {', '.join(disabled)}", file=sys.stderr)
            return 2
        wanted = set(args.source_ids)
        selected = [s for s in sources if s.id in wanted]

    try:
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        conn = open_database(args.db)
    except (OSError, sqlite3.Error, SchemaError) as err:
        print(f"error: cannot open database {args.db!r}: {err}", file=sys.stderr)
        return 2

    try:
        sync_sources(conn, sources)
        summary = run_collection(conn, selected, downloader=downloader, sleep=sleep)
    except Exception as err:
        print(f"error: collection failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    print_summary(summary)
    return summary.exit_code


def print_summary(summary: RunSummary) -> None:
    print(f"Run {summary.run_id}: {summary.status} "
          f"({summary.started_at} to {summary.finished_at})")
    if not summary.sources:
        print("  no enabled sources selected")
    width = max([len(s.source_id) for s in summary.sources] + [6])
    for s in summary.sources:
        status = str(s.http_status) if s.http_status is not None else "-"
        line = f"  {s.source_id:<{width}}  {s.outcome:<17} {status:>3}"
        if s.outcome == "ok":
            line += (f"  {s.entry_count} entries, {s.new_article_count} new,"
                     f" {s.entry_error_count} entry errors")
            if s.bozo:
                line += "  [malformed feed, entries recovered]"
        elif s.message:
            line += f"  {s.message}"
        print(line)
    entries = sum(s.entry_count or 0 for s in summary.sources if s.outcome == "ok")
    new = sum(s.new_article_count for s in summary.sources)
    entry_errors = sum(s.entry_error_count for s in summary.sources)
    failed = sum(1 for s in summary.sources if s.outcome not in ("ok", "not_modified"))
    print(f"Totals: {entries} entries, {new} new articles, {entry_errors} entry errors, "
          f"{failed} source(s) with errors")
