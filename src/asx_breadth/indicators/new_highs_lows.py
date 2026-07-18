"""New 52-week highs, lows, and net new highs breadth."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .membership import (
    active_membership_for_sessions,
    market_sessions,
)
from .quality import QUALITY_POLICY, session_quality


class NewHighLow:
    key = "new_high_low"
    dependencies: tuple[str, ...] = ()

    def __init__(
        self,
        lookback_sessions: int = 252,
        minimum_history_coverage: float = 0.90,
    ):
        self.lookback_sessions = lookback_sessions
        self.minimum_history_coverage = minimum_history_coverage

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult:
        del results
        columns = [
            "new_highs",
            "new_lows",
            "nh_nl",
            "eligible_issues",
            "quoted_issues",
            "active_issues",
            "coverage",
            "coverage_floor",
            "quality_ok",
        ]
        factors = context.factors.copy()
        if factors.empty:
            return IndicatorResult(
                key=self.key,
                title="New 52-Week Highs and Lows",
                frame=pd.DataFrame(columns=columns),
            )

        factors["trade_date"] = pd.to_datetime(factors["trade_date"])
        high_series: dict[int, pd.Series] = {}
        low_series: dict[int, pd.Series] = {}
        for instrument_id, group in factors.groupby("instrument_id", sort=False):
            highs, lows = _price_extremes(group.sort_values("trade_date"))
            high_series[int(instrument_id)] = highs
            low_series[int(instrument_id)] = lows

        highs = pd.DataFrame(high_series).sort_index()
        lows = pd.DataFrame(low_series).sort_index()
        sessions = market_sessions(context)
        highs = highs.reindex(sessions)
        lows = lows.reindex(sessions)
        highs.index.name = lows.index.name = "trade_date"
        highs.columns.name = lows.columns.name = "instrument_id"
        prior_window = max(self.lookback_sessions - 1, 1)
        required_observations = max(
            1,
            math.ceil(prior_window * self.minimum_history_coverage),
        )
        prior_high = (
            highs.shift(1)
            .rolling(window=prior_window, min_periods=required_observations)
            .max()
        )
        prior_low = (
            lows.shift(1)
            .rolling(window=prior_window, min_periods=required_observations)
            .min()
        )
        history_age = pd.DataFrame(False, index=highs.index, columns=highs.columns)
        observed = highs.notna() | lows.notna()
        for instrument_id in observed.columns:
            positions = np.flatnonzero(observed[instrument_id].to_numpy())
            if positions.size:
                eligible_from = positions[0] + self.lookback_sessions - 1
                if eligible_from < len(history_age):
                    history_age.loc[
                        history_age.index[eligible_from] :, instrument_id
                    ] = True
        eligible_high = highs.notna() & prior_high.notna() & history_age
        eligible_low = lows.notna() & prior_low.notna() & history_age
        metrics = pd.DataFrame(
            {
                "is_new_high": ((highs >= prior_high) & eligible_high).stack(
                    future_stack=True
                ),
                "is_new_low": ((lows <= prior_low) & eligible_low).stack(
                    future_stack=True
                ),
                "is_eligible": (eligible_high | eligible_low).stack(future_stack=True),
                "has_quote": (highs.notna() & lows.notna()).stack(future_stack=True),
            }
        ).reset_index()
        active = active_membership_for_sessions(context, highs.index)
        active_metrics = active.merge(
            metrics,
            on=["trade_date", "instrument_id"],
            how="left",
            validate="one_to_one",
        )
        for column in ("is_new_high", "is_new_low", "is_eligible", "has_quote"):
            active_metrics[column] = active_metrics[column].fillna(False).astype(bool)
        frame = active_metrics.groupby("trade_date").agg(
            new_highs=("is_new_high", "sum"),
            new_lows=("is_new_low", "sum"),
            eligible_issues=("is_eligible", "sum"),
            quoted_issues=("has_quote", "sum"),
            active_issues=("instrument_id", "nunique"),
        )
        quality = session_quality(frame["quoted_issues"], frame["active_issues"])
        frame[["coverage", "coverage_floor", "quality_ok"]] = quality
        frame.loc[frame["eligible_issues"] == 0, ["new_highs", "new_lows"]] = np.nan
        frame.loc[~frame["quality_ok"], ["new_highs", "new_lows"]] = np.nan
        frame["nh_nl"] = frame["new_highs"] - frame["new_lows"]
        eligible_dates = frame.index[frame["eligible_issues"] > 0]
        if len(eligible_dates):
            frame = frame.loc[eligible_dates.min() :]
        else:
            frame = frame.iloc[0:0]
        frame.index.name = "trade_date"
        return IndicatorResult(
            key=self.key,
            title="New 52-Week Highs and Lows",
            frame=frame[columns],
            metadata={
                "lookback_sessions": self.lookback_sessions,
                "minimum_history_coverage": self.minimum_history_coverage,
                "quality_policy": QUALITY_POLICY,
                "membership": "Point-in-time holdings snapshots by effective date",
                "price_basis": (
                    "Continuous total-return-adjusted daily high/low index"
                ),
            },
        )


def _price_extremes(group: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    previous_total_return_index = 100.0
    previous_date: pd.Timestamp | None = None
    chain_available = True
    has_previous_dates = "previous_trade_date" in group
    dates: list[pd.Timestamp] = []
    highs: list[float] = []
    lows: list[float] = []
    for position, row in enumerate(group.itertuples(index=False)):
        trade_date = pd.Timestamp(row.trade_date)
        close_factor = _positive(row.close_to_previous_close)
        # Production factor frames always contain total_return_factor. Falling
        # back to close preserves the small synthetic plugin interface used by
        # third-party indicators and older tests.
        total_return_factor = _positive(
            getattr(row, "total_return_factor", row.close_to_previous_close)
        )
        high_factor = _positive(row.high_to_previous_close)
        low_factor = _positive(row.low_to_previous_close)
        dates.append(trade_date)

        if position == 0:
            # The first cached row is the arbitrary scale anchor; its own factor
            # has no predecessor and therefore cannot supply an intraday range.
            highs.append(np.nan)
            lows.append(np.nan)
            previous_date = trade_date
            continue

        links_previous = True
        if has_previous_dates:
            reported_previous = getattr(row, "previous_trade_date")
            links_previous = (
                pd.notna(reported_previous)
                and previous_date is not None
                and pd.Timestamp(reported_previous) == previous_date
            )
        if total_return_factor is None or not links_previous:
            chain_available = False

        if chain_available and close_factor is not None:
            # AdjClose return / raw-close return is the change in Yahoo's
            # adjustment factor. Applying it to the raw intraday factors puts
            # highs, lows, and the close on one append-stable total-return
            # scale across both distributions and splits.
            adjustment = total_return_factor / close_factor
            highs.append(
                previous_total_return_index * high_factor * adjustment
                if high_factor is not None
                else np.nan
            )
            lows.append(
                previous_total_return_index * low_factor * adjustment
                if low_factor is not None
                else np.nan
            )
        else:
            highs.append(np.nan)
            lows.append(np.nan)
        if chain_available:
            previous_total_return_index *= total_return_factor
        previous_date = trade_date
    return (
        pd.Series(highs, index=dates, dtype=float),
        pd.Series(lows, index=dates, dtype=float),
    )


def _positive(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if np.isfinite(number) and number > 0 else None
