"""Percentage of active constituents above selected moving averages."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .membership import active_membership_for_sessions, market_sessions
from .quality import QUALITY_POLICY, session_quality

SMA_WINDOWS = (5, 20, 50, 200)
QUALITY_COLUMNS = [
    "quoted_issues",
    "active_issues",
    "coverage",
    "coverage_floor",
    "quality_ok",
]
OUTPUT_COLUMNS = [
    *(
        column
        for window in SMA_WINDOWS
        for column in (
            f"above_sma_{window}",
            f"eligible_sma_{window}",
            f"percent_above_sma_{window}",
        )
    ),
    *QUALITY_COLUMNS,
]


class PercentAboveMovingAverages:
    """Measure equal-weight participation above 5/20/50/200-session SMAs."""

    key = "percent_above_sma"
    dependencies: tuple[str, ...] = ()

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult:
        del results
        factors = context.factors.copy()
        required = {
            "instrument_id",
            "trade_date",
            "previous_trade_date",
            "total_return_factor",
        }
        if factors.empty or not required.issubset(factors.columns):
            return _empty_result(context)

        sessions = market_sessions(context)
        if sessions.empty:
            return _empty_result(context)
        membership = active_membership_for_sessions(context, sessions)
        if membership.empty:
            return _empty_result(context)

        levels = _total_return_levels(factors)
        metrics = levels[levels["trade_date"].isin(sessions)].copy()
        grouped_levels = metrics.groupby(
            ["instrument_id", "chain_id"],
            sort=False,
        )["total_return_level"]
        for window in SMA_WINDOWS:
            average = grouped_levels.transform(
                lambda series, span=window: series.rolling(
                    window=span,
                    min_periods=span,
                ).mean()
            )
            eligible = metrics["total_return_level"].notna() & average.notna()
            metrics[f"eligible_sma_{window}"] = eligible
            metrics[f"above_sma_{window}"] = eligible & (
                metrics["total_return_level"] > average
            )
        metrics["has_quote"] = metrics["total_return_level"].notna()

        metric_columns = [
            "trade_date",
            "instrument_id",
            "has_quote",
            *(
                column
                for window in SMA_WINDOWS
                for column in (
                    f"above_sma_{window}",
                    f"eligible_sma_{window}",
                )
            ),
        ]
        active = membership.merge(
            metrics[metric_columns],
            on=["trade_date", "instrument_id"],
            how="left",
            validate="one_to_one",
        )
        boolean_columns = [
            column
            for column in metric_columns
            if column
            not in {
                "trade_date",
                "instrument_id",
            }
        ]
        active[boolean_columns] = active[boolean_columns].fillna(False).astype(bool)

        aggregations: dict[str, tuple[str, str]] = {
            "quoted_issues": ("has_quote", "sum"),
            "active_issues": ("instrument_id", "nunique"),
        }
        for window in SMA_WINDOWS:
            aggregations[f"above_sma_{window}"] = (
                f"above_sma_{window}",
                "sum",
            )
            aggregations[f"eligible_sma_{window}"] = (
                f"eligible_sma_{window}",
                "sum",
            )
        frame = active.groupby("trade_date").agg(**aggregations)
        frame.index.name = "trade_date"
        quality = session_quality(frame["quoted_issues"], frame["active_issues"])
        frame[["coverage", "coverage_floor", "quality_ok"]] = quality
        for window in SMA_WINDOWS:
            eligible = frame[f"eligible_sma_{window}"].replace(0, np.nan)
            frame[f"percent_above_sma_{window}"] = (
                100.0 * frame[f"above_sma_{window}"] / eligible
            ).where(frame["quality_ok"])

        accepted = frame.index[frame["quality_ok"]]
        last_accepted = accepted[-1] if len(accepted) else None
        return IndicatorResult(
            key=self.key,
            title="Stocks Above Moving Averages",
            frame=frame[OUTPUT_COLUMNS],
            metadata={
                "windows": SMA_WINDOWS,
                "membership": "Point-in-time holdings snapshots by effective date",
                "quality_policy": QUALITY_POLICY,
                "price_basis": (
                    "Continuous adjusted-close total-return index; dividends and "
                    "splits are adjustment-neutral"
                ),
                "denominator": (
                    "Active constituents with a current quote and the full SMA history"
                ),
                "last_accepted_session": (
                    pd.Timestamp(last_accepted).date().isoformat()
                    if last_accepted is not None
                    else None
                ),
            },
        )


def _total_return_levels(factors: pd.DataFrame) -> pd.DataFrame:
    """Build arbitrary-scale, append-stable price chains for every instrument."""
    if factors.empty:
        return pd.DataFrame(
            columns=[
                "instrument_id",
                "trade_date",
                "chain_id",
                "total_return_level",
            ]
        )

    ordered = factors[
        [
            "instrument_id",
            "trade_date",
            "previous_trade_date",
            "total_return_factor",
        ]
    ].copy()
    ordered["trade_date"] = pd.to_datetime(ordered["trade_date"])
    ordered["previous_trade_date"] = pd.to_datetime(
        ordered["previous_trade_date"],
        errors="coerce",
    )
    ordered["total_return_factor"] = pd.to_numeric(
        ordered["total_return_factor"],
        errors="coerce",
    )
    ordered = (
        ordered.sort_values(["instrument_id", "trade_date"])
        .drop_duplicates(["instrument_id", "trade_date"], keep="last")
        .reset_index(drop=True)
    )
    instrument = ordered["instrument_id"]
    prior_date = ordered.groupby("instrument_id", sort=False)["trade_date"].shift()
    linked = ordered["previous_trade_date"].eq(prior_date)
    factor = ordered["total_return_factor"]
    valid_factor = factor.notna() & np.isfinite(factor) & factor.gt(0)

    # An unlinked row is a legitimate arbitrary-scale anchor. A linked row
    # without a usable factor is unavailable; the first later valid row starts
    # a new chain and must build its own full SMA history.
    level_valid = ~linked | valid_factor
    prior_level_valid = (
        level_valid.groupby(instrument, sort=False).shift(fill_value=False).astype(bool)
    )
    anchor = level_valid & (~linked | ~prior_level_valid)
    chain_id = (anchor | ~level_valid).groupby(instrument, sort=False).cumsum()
    components = factor.where(linked, 1.0).where(level_valid).mask(anchor, 1.0)
    levels = components.groupby([instrument, chain_id], sort=False).cumprod() * 100.0
    return pd.DataFrame(
        {
            "instrument_id": instrument.astype(int),
            "trade_date": ordered["trade_date"],
            "chain_id": chain_id.astype(int),
            "total_return_level": levels,
        }
    )


def _empty_result(context: IndicatorContext) -> IndicatorResult:
    return IndicatorResult(
        key=PercentAboveMovingAverages.key,
        title="Stocks Above Moving Averages",
        frame=pd.DataFrame(columns=OUTPUT_COLUMNS),
        metadata={
            "windows": SMA_WINDOWS,
            "universe_size": len(context.snapshot.instruments),
        },
    )
