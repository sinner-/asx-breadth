"""Shared return preparation for realized cross-sectional indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd

ANNUALIZATION_SESSIONS = 252


def consecutive_log_returns(
    factors: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    *,
    include_instrument: bool,
) -> pd.DataFrame:
    """Return valid one-market-session total returns on the canonical calendar."""
    factors = _consecutive_factors(
        factors,
        sessions,
        include_instrument=include_instrument,
    )
    result_columns = ["trade_date"]
    if include_instrument:
        result_columns.insert(0, "instrument_id")
    result = factors[result_columns].copy()
    result["log_return"] = np.log(factors["total_return_factor"].astype(float))
    return result


def consecutive_simple_returns(
    factors: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    *,
    include_instrument: bool,
) -> pd.DataFrame:
    """Return valid one-session percentage changes for correlation estimates."""
    factors = _consecutive_factors(
        factors,
        sessions,
        include_instrument=include_instrument,
    )
    result_columns = ["trade_date"]
    if include_instrument:
        result_columns.insert(0, "instrument_id")
    result = factors[result_columns].copy()
    result["return"] = factors["total_return_factor"].astype(float) - 1.0
    return result


def _consecutive_factors(
    factors: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    *,
    include_instrument: bool,
) -> pd.DataFrame:
    columns = ["trade_date", "previous_trade_date", "total_return_factor"]
    if include_instrument:
        columns.insert(0, "instrument_id")
    if factors.empty or not set(columns).issubset(factors.columns):
        return pd.DataFrame(
            columns=[
                *(("instrument_id",) if include_instrument else ()),
                "trade_date",
                "total_return_factor",
            ]
        )

    ordered = factors[columns].copy()
    ordered["trade_date"] = pd.to_datetime(ordered["trade_date"])
    ordered["previous_trade_date"] = pd.to_datetime(
        ordered["previous_trade_date"],
        errors="coerce",
    )
    ordered["total_return_factor"] = pd.to_numeric(
        ordered["total_return_factor"],
        errors="coerce",
    )
    keys = ["trade_date"]
    if include_instrument:
        keys.insert(0, "instrument_id")
    ordered = ordered.sort_values(keys).drop_duplicates(keys, keep="last")
    expected_previous = pd.Series(sessions, index=sessions).shift(1)
    expected = ordered["trade_date"].map(expected_previous)
    factor = ordered["total_return_factor"]
    valid = (
        ordered["trade_date"].isin(sessions)
        & factor.notna()
        & np.isfinite(factor)
        & factor.gt(0)
        & ordered["previous_trade_date"].eq(expected)
    )
    result_columns = ["trade_date", "total_return_factor"]
    if include_instrument:
        result_columns.insert(0, "instrument_id")
    return ordered.loc[valid, result_columns].reset_index(drop=True)


def annualized_volatility(returns: pd.Series, window: int) -> pd.Series:
    """Annualized sample volatility, expressed in percentage points."""
    return (
        returns.rolling(window=window, min_periods=window).std(ddof=1)
        * np.sqrt(ANNUALIZATION_SESSIONS)
        * 100.0
    )
