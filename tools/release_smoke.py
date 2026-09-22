#!/usr/bin/env python3
"""Maintainer release gate: one locked read-only hardware smoke."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from g502x_onboard.cli import cmd_smoke_readonly


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the final read-only hardware/recovery/report release smoke. "
            "This performs no persistent mouse write."
        )
    )
    parser.add_argument(
        "report",
        nargs="?",
        default="device-report-smoke.json",
        help="privacy-minimized device report output path",
    )
    parser.add_argument("--label", default="release-smoke", help="private checkpoint label")
    args = parser.parse_args()
    cmd_smoke_readonly(SimpleNamespace(report=args.report, label=args.label))


if __name__ == "__main__":
    main()
