#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "numpy>=2,<3",
#   "openpyxl>=3.1.5,<4",
#   "pandas>=2.2,<4",
#   "plotly>=6.0,<7",
#   "yfinance[repair]>=1.4,<2",
# ]
# ///
"""ASX market-breadth command-line entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from asx_breadth.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
