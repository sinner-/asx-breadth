"""Shared active-universe session quality policy for breadth indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd


QUALITY_POLICY = "Coverage >= max(90%, 95% of trailing 60-session median)"


def session_quality(
    available: pd.Series,
    expected: pd.Series,
) -> pd.DataFrame:
    """Measure quote coverage and decide whether a breadth session is usable."""
    expected = expected.reindex(available.index)
    coverage = available.div(expected.replace(0, np.nan))
    recent_coverage = coverage.shift(1).rolling(window=60, min_periods=10).median()
    floor = (recent_coverage * 0.95).clip(lower=0.90).fillna(0.90)
    # Small plugin/test universes cannot distinguish a normal single-name halt
    # from a failed provider batch using cross-sectional coverage alone.
    accepted = (expected < 20) | (coverage >= floor)
    return pd.DataFrame(
        {
            "coverage": coverage,
            "coverage_floor": floor,
            "quality_ok": accepted.fillna(False),
        },
        index=available.index,
    )
