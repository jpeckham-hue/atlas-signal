"""Command-line entry point for Atlas Signal."""

import argparse

from atlas_signal import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas_signal",
        description=(
            "Atlas Signal: experimental news intelligence and event tracking. "
            "No commands are implemented yet."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
