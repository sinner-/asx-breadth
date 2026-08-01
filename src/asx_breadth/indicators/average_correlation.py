"""Equal-weight average pairwise correlation of active constituents."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .membership import active_membership_for_sessions, market_sessions
from .quality import QUALITY_POLICY, session_quality
from .realized import consecutive_simple_returns

CORRELATION_WINDOWS = (21, 63, 120)
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
        for window in CORRELATION_WINDOWS
        for column in (
            f"average_correlation_{window}",
            f"eligible_issues_{window}",
            f"eligible_pairs_{window}",
        )
    ),
    *QUALITY_COLUMNS,
]


class AverageCorrelation:
    """Average rolling Pearson correlation across all eligible active pairs."""

    key = "average_correlation"
    dependencies: tuple[str, ...] = ()

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult:
        del results
        required = {
            "instrument_id",
            "trade_date",
            "previous_trade_date",
            "total_return_factor",
        }
        if context.factors.empty or not required.issubset(context.factors.columns):
            return _empty_result(context)

        sessions = market_sessions(context)
        if len(sessions) < min(CORRELATION_WINDOWS) + 1:
            return _empty_result(context)
        membership = active_membership_for_sessions(context, sessions)
        if membership.empty:
            return _empty_result(context)

        returns = consecutive_simple_returns(
            context.factors,
            sessions,
            include_instrument=True,
        )
        if returns.empty:
            return _empty_result(context)

        instrument_ids = pd.Index(
            sorted(membership["instrument_id"].astype(int).unique()),
            dtype="int64",
        )
        return_matrix = (
            returns.pivot(
                index="trade_date",
                columns="instrument_id",
                values="return",
            )
            .reindex(index=sessions, columns=instrument_ids)
            .to_numpy(dtype=float)
        )
        active = _active_mask(membership, sessions, instrument_ids)
        finite_returns = np.isfinite(return_matrix)

        frame = pd.DataFrame(index=sessions)
        frame.index.name = "trade_date"
        frame["quoted_issues"] = (finite_returns & active).sum(axis=1)
        frame["active_issues"] = active.sum(axis=1)
        quality = session_quality(frame["quoted_issues"], frame["active_issues"])
        frame[["coverage", "coverage_floor", "quality_ok"]] = quality

        correlation_sums, pair_counts, participants = _rolling_pair_correlations(
            return_matrix,
            active,
            CORRELATION_WINDOWS,
        )
        for window in CORRELATION_WINDOWS:
            count = pair_counts[window]
            average = np.full(len(sessions), np.nan, dtype=float)
            np.divide(
                correlation_sums[window],
                count,
                out=average,
                where=count > 0,
            )
            available = frame["quality_ok"].to_numpy(dtype=bool) & (count > 0)
            frame[f"average_correlation_{window}"] = np.where(
                available,
                100.0 * average,
                np.nan,
            )
            frame[f"eligible_issues_{window}"] = participants[window].sum(axis=1)
            frame[f"eligible_pairs_{window}"] = count

        accepted = frame.index[frame["quality_ok"]]
        last_accepted = accepted[-1] if len(accepted) else None
        return IndicatorResult(
            key=self.key,
            title="Average Stock Correlation",
            frame=frame[OUTPUT_COLUMNS],
            metadata={
                "windows": CORRELATION_WINDOWS,
                "coefficient": "Pearson product-moment correlation",
                "aggregation": "Equal-weight arithmetic mean of eligible pairs",
                "return_basis": "Consecutive-session adjusted-close simple returns",
                "membership": "Point-in-time holdings snapshots by effective date",
                "quality_policy": QUALITY_POLICY,
                "gap_policy": (
                    "Each pair uses its latest N synchronous valid one-session "
                    "returns; missing and multi-session returns are excluded without "
                    "breaking later estimates"
                ),
                "last_accepted_session": (
                    pd.Timestamp(last_accepted).date().isoformat()
                    if last_accepted is not None
                    else None
                ),
            },
        )


def _active_mask(
    membership: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    instrument_ids: pd.Index,
) -> np.ndarray:
    active = np.zeros((len(sessions), len(instrument_ids)), dtype=bool)
    dates = pd.DatetimeIndex(pd.to_datetime(membership["trade_date"]))
    date_positions = sessions.get_indexer(dates)
    instrument_positions = instrument_ids.get_indexer(
        membership["instrument_id"].astype(int)
    )
    valid = (date_positions >= 0) & (instrument_positions >= 0)
    active[date_positions[valid], instrument_positions[valid]] = True
    return active


def _rolling_pair_correlations(
    returns: np.ndarray,
    active: np.ndarray,
    windows: tuple[int, ...],
) -> tuple[
    dict[int, np.ndarray],
    dict[int, np.ndarray],
    dict[int, np.ndarray],
]:
    """Aggregate rolling Pearson coefficients without materializing pair cubes."""
    session_count, instrument_count = returns.shape
    correlation_sums = {
        window: np.zeros(session_count, dtype=float) for window in windows
    }
    pair_counts = {
        window: np.zeros(session_count, dtype=np.int64) for window in windows
    }
    participants = {
        window: np.zeros((session_count, instrument_count), dtype=bool)
        for window in windows
    }
    finite = np.isfinite(returns)

    for left in range(instrument_count - 1):
        for right in range(left + 1, instrument_count):
            if not np.any(active[:, left] & active[:, right]):
                continue
            joint_dates = np.flatnonzero(finite[:, left] & finite[:, right])
            if len(joint_dates) < min(windows):
                continue

            left_values = returns[joint_dates, left]
            right_values = returns[joint_dates, right]
            left_sum = _cumulative(left_values)
            right_sum = _cumulative(right_values)
            left_square_sum = _cumulative(np.square(left_values))
            right_square_sum = _cumulative(np.square(right_values))
            product_sum = _cumulative(left_values * right_values)

            for window in windows:
                if len(joint_dates) < window:
                    continue
                sx = left_sum[window:] - left_sum[:-window]
                sy = right_sum[window:] - right_sum[:-window]
                sx2 = left_square_sum[window:] - left_square_sum[:-window]
                sy2 = right_square_sum[window:] - right_square_sum[:-window]
                sxy = product_sum[window:] - product_sum[:-window]
                covariance = sxy - (sx * sy / window)
                variance_left = sx2 - (np.square(sx) / window)
                variance_right = sy2 - (np.square(sy) / window)
                tolerance_left = _variance_tolerance(sx2, sx, window)
                tolerance_right = _variance_tolerance(sy2, sy, window)
                end_dates = joint_dates[window - 1 :]
                usable = (
                    active[end_dates, left]
                    & active[end_dates, right]
                    & (variance_left > tolerance_left)
                    & (variance_right > tolerance_right)
                )
                if not np.any(usable):
                    continue

                denominator = np.sqrt(variance_left[usable] * variance_right[usable])
                correlations = np.clip(covariance[usable] / denominator, -1.0, 1.0)
                dates = end_dates[usable]
                correlation_sums[window][dates] += correlations
                pair_counts[window][dates] += 1
                participants[window][dates, left] = True
                participants[window][dates, right] = True

    return correlation_sums, pair_counts, participants


def _cumulative(values: np.ndarray) -> np.ndarray:
    return np.concatenate(([0.0], np.cumsum(values, dtype=float)))


def _variance_tolerance(
    square_sum: np.ndarray,
    value_sum: np.ndarray,
    window: int,
) -> np.ndarray:
    scale = np.maximum(np.abs(square_sum), np.abs(np.square(value_sum) / window))
    return 128.0 * np.finfo(float).eps * np.maximum(scale, np.finfo(float).tiny)


def _empty_result(context: IndicatorContext) -> IndicatorResult:
    return IndicatorResult(
        key=AverageCorrelation.key,
        title="Average Stock Correlation",
        frame=pd.DataFrame(columns=OUTPUT_COLUMNS),
        metadata={
            "windows": CORRELATION_WINDOWS,
            "universe_size": len(context.snapshot.instruments),
        },
    )
