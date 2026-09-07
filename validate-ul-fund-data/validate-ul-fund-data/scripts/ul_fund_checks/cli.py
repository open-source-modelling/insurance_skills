"""The hook itself.

Deliberately thin: take paths, print a report, exit non-zero if anything
failed. That single entry point serves as a pre-commit hook, a CI step, or a
manual check before analysis, so the trigger mechanism is not baked in.

Every rule is evaluated before the exit code is decided — hard fail, but not
fail fast.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .report import render_json, render_text
from .runner import validate_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check-ul-fund-data",
        description="Validate the format of UL_FUND_DATA workbooks before analysis.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="one or more .xlsx files")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--verbose", "-v", action="store_true", help="show passing checks too")
    args = parser.parse_args(argv)

    exit_code = 0
    for path in args.paths:
        if not path.exists():
            print(f"{path}: file not found", file=sys.stderr)
            exit_code = 1
            continue
        report = validate_file(path)
        if args.json:
            print(render_json(report, str(path)))
        else:
            print(render_text(report, str(path), verbose=args.verbose))
        if not report.ok:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
