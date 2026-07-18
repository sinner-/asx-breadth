"""Shared active-universe session quality policy for breadth indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd


QUALITY_POLICY = (
    "At least one quote and either coverage >= max(90%, 95% of trailing "
    "60-session median) or no more than one missing issue"
)


def accepted_session_ema(
    series: pd.Series,
    accepted: pd.Series,
    *,
    span: int,
) -> pd.Series:
    """Advance EMA state only when a breadth session is accepted."""
    return (
        series.where(accepted)
        .ewm(
            span=span,
            adjust=False,
            ignore_na=True,
            min_periods=span,
        )
        .mean()
    )


def session_quality(
    available: pd.Series,
    expected: pd.Series,
) -> pd.DataFrame:
    """Measure quote coverage and decide whether a breadth session is usable."""
    expected = expected.reindex(available.index)
    coverage = available.div(expected.replace(0, np.nan))
    recent_coverage = coverage.shift(1).rolling(window=60, min_periods=10).median()
    floor = (recent_coverage * 0.95).clip(lower=0.90).fillna(0.90)
    # One isolated halt should not freeze breadth, including in a genuinely
    # small future universe. A broad outage must never pass merely because the
    # universe itself is small.
    missing = (expected - available).clip(lower=0)
    accepted = available.gt(0) & ((coverage >= floor) | (missing <= 1))
    return pd.DataFrame(
        {
            "coverage": coverage,
            "coverage_floor": floor,
            "quality_ok": accepted.fillna(False),
        },
        index=available.index,
    )
