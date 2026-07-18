"""Equal-dollar geometric total-return index for the active universe."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .membership import active_membership_for_sessions, market_sessions
from .quality import QUALITY_POLICY, accepted_session_ema, session_quality


OUTPUT_COLUMNS = [
    "geometric_index",
    "geometric_ema19",
    "geometric_ema39",
    "geometric_ema200",
    "daily_geometric_factor",
    "daily_geometric_return",
    "quoted_issues",
    "active_issues",
    "coverage",
    "coverage_floor",
    "quality_ok",
]


class GeometricIndex:
    """Link the geometric mean of equal-dollar constituent total returns."""

    key = "geometric_index"
    dependencies: tuple[str, ...] = ()

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult:
        del results
        factors = context.factors.copy()
        if factors.empty:
            return _empty_result(context)

        required = {
            "instrument_id",
            "trade_date",
            "previous_trade_date",
            "total_return_factor",
        }
        if not required.issubset(factors.columns):
            return _empty_result(context)

        factors["trade_date"] = pd.to_datetime(factors["trade_date"])
        factors["previous_trade_date"] = pd.to_datetime(
            factors["previous_trade_date"], errors="coerce"
        )
        factors["total_return_factor"] = pd.to_numeric(
            factors["total_return_factor"], errors="coerce"
        )
        sessions = market_sessions(context)
        if len(sessions) < 2:
            return _empty_result(context)

        membership = active_membership_for_sessions(context, sessions)
        active = factors.merge(
            membership,
            on=["trade_date", "instrument_id"],
            how="inner",
            validate="many_to_one",
        )
        expected_previous = pd.Series(sessions, index=sessions).shift(1)
        active["expected_previous"] = active["trade_date"].map(expected_previous)
        valid = active[
            active["total_return_factor"].notna()
            & np.isfinite(active["total_return_factor"])
            & (active["total_return_factor"] > 0)
            & (active["previous_trade_date"] == active["expected_previous"])
        ].copy()
        valid["log_return_factor"] = np.log(valid["total_return_factor"])

        active_sessions = pd.DatetimeIndex(
            membership["trade_date"].drop_duplicates().sort_values()
        )
        eligible_sessions = sessions[1:][sessions[1:].isin(active_sessions)]
        frame = pd.DataFrame(index=eligible_sessions)
        frame.index.name = "trade_date"
        grouped = valid.groupby("trade_date").agg(
            mean_log_factor=("log_return_factor", "mean"),
            quoted_issues=("instrument_id", "nunique"),
        )
        frame = frame.join(grouped)
        frame["quoted_issues"] = frame["quoted_issues"].fillna(0).astype(int)
        frame["active_issues"] = (
            membership.groupby("trade_date")["instrument_id"]
            .nunique()
            .reindex(frame.index)
            .fillna(0)
            .astype(int)
        )
        quality = session_quality(frame["quoted_issues"], frame["active_issues"])
        frame[["coverage", "coverage_floor", "quality_ok"]] = quality
        frame["daily_geometric_factor"] = np.exp(frame["mean_log_factor"]).where(
            frame["quality_ok"]
        )
        frame["daily_geometric_return"] = frame["daily_geometric_factor"] - 1.0
        frame["geometric_index"] = (
            frame["daily_geometric_factor"].fillna(1.0).cumprod() * 100.0
        )
        accepted = frame["quality_ok"] & frame["daily_geometric_factor"].notna()
        for span in (19, 39, 200):
            frame[f"geometric_ema{span}"] = accepted_session_ema(
                frame["geometric_index"], accepted, span=span
            )

        accepted = frame.index[
            frame["quality_ok"] & frame["daily_geometric_factor"].notna()
        ]
        last_accepted = accepted[-1] if len(accepted) else None
        return IndicatorResult(
            key=self.key,
            title="ASX 300 Geometric Index",
            frame=frame[OUTPUT_COLUMNS],
            metadata={
                "base_value": 100.0,
                "weighting": "Equal-dollar, geometrically averaged daily",
                "membership": "Point-in-time holdings snapshots by effective date",
                "quality_policy": QUALITY_POLICY,
                "gap_policy": (
                    "Missing, halted, or non-consecutive issues are excluded; "
                    "low-coverage sessions hold the index"
                ),
                "return_basis": (
                    "Consecutive-session adjusted-close total return; cash dividends "
                    "and splits are adjustment-neutral"
                ),
                "latest_signal_updated": bool(
                    len(frame)
                    and frame.iloc[-1]["quality_ok"]
                    and pd.notna(frame.iloc[-1]["daily_geometric_factor"])
                ),
                "last_accepted_session": (
                    pd.Timestamp(last_accepted).date().isoformat()
                    if last_accepted is not None
                    else None
                ),
            },
        )


def _empty_result(context: IndicatorContext) -> IndicatorResult:
    return IndicatorResult(
        key=GeometricIndex.key,
        title="ASX 300 Geometric Index",
        frame=pd.DataFrame(columns=OUTPUT_COLUMNS),
        metadata={
            "base_value": 100.0,
            "universe_size": len(context.snapshot.instruments),
        },
    )
