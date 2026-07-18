"""Shared reconstruction for cached, non-composite market index levels."""

from __future__ import annotations

import numpy as np
import pandas as pd


def reconstruct_actual_level(
    factors: pd.DataFrame,
    anchor: object,
) -> pd.Series:
    """Rebuild an actual published level backwards from its latest observation.

    The cache stores append-stable daily ratios. Anchoring the latest factor row
    to its observed adjusted close recovers the provider's published scale. A
    broken chain remains unavailable before the break rather than being bridged.
    """
    required = {"trade_date", "previous_trade_date", "total_return_factor"}
    anchor_value = positive_number(anchor)
    if factors.empty or anchor_value is None or not required.issubset(factors.columns):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], name="trade_date"))

    ordered = factors.copy()
    ordered["trade_date"] = pd.to_datetime(ordered["trade_date"])
    ordered["previous_trade_date"] = pd.to_datetime(
        ordered["previous_trade_date"], errors="coerce"
    )
    ordered = (
        ordered.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )
    levels = np.full(len(ordered), np.nan, dtype=float)
    levels[-1] = anchor_value
    for position in range(len(ordered) - 1, 0, -1):
        factor = positive_number(ordered.iloc[position]["total_return_factor"])
        expected_previous = ordered.iloc[position - 1]["trade_date"]
        reported_previous = ordered.iloc[position]["previous_trade_date"]
        if (
            factor is None
            or pd.isna(reported_previous)
            or reported_previous != expected_previous
            or not np.isfinite(levels[position])
        ):
            break
        levels[position - 1] = levels[position] / factor
    return pd.Series(
        levels,
        index=pd.DatetimeIndex(ordered["trade_date"], name="trade_date"),
        dtype=float,
    )


def positive_number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if np.isfinite(number) and number > 0 else None
