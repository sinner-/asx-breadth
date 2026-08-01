"""Average constituent realized volatility versus the VAS benchmark."""

from __future__ import annotations

import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .membership import active_membership_for_sessions, market_sessions
from .quality import QUALITY_POLICY, session_quality
from .realized import (
    ANNUALIZATION_SESSIONS,
    annualized_volatility,
    consecutive_log_returns,
)

DISPERSION_WINDOWS = (21, 63, 120)
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
        for window in DISPERSION_WINDOWS
        for column in (
            f"dispersion_{window}",
            f"average_stock_vol_{window}",
            f"index_vol_{window}",
            f"eligible_issues_{window}",
        )
    ),
    *QUALITY_COLUMNS,
]


class RealizedDispersion:
    """Compare average constituent volatility with VAS index volatility."""

    key = "realized_dispersion"
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
        if len(sessions) < min(DISPERSION_WINDOWS) + 1:
            return _empty_result(context)
        membership = active_membership_for_sessions(context, sessions)
        if membership.empty:
            return _empty_result(context)

        stock_returns = consecutive_log_returns(
            context.factors,
            sessions,
            include_instrument=True,
        )
        if stock_returns.empty:
            return _empty_result(context)
        stock_metrics = stock_returns[["trade_date", "instrument_id"]].copy()
        stock_metrics["has_return"] = True
        grouped_returns = stock_returns.groupby("instrument_id", sort=False)[
            "log_return"
        ]
        for window in DISPERSION_WINDOWS:
            volatility = grouped_returns.transform(
                lambda series, span=window: annualized_volatility(series, span)
            )
            stock_metrics[f"volatility_{window}"] = volatility

        metric_columns = [
            "trade_date",
            "instrument_id",
            "has_return",
            *(f"volatility_{window}" for window in DISPERSION_WINDOWS),
        ]
        active = membership.merge(
            stock_metrics[metric_columns],
            on=["trade_date", "instrument_id"],
            how="left",
            validate="one_to_one",
        )
        active["has_return"] = active["has_return"].fillna(False).astype(bool)
        aggregations: dict[str, tuple[str, str]] = {
            "quoted_issues": ("has_return", "sum"),
            "active_issues": ("instrument_id", "nunique"),
        }
        for window in DISPERSION_WINDOWS:
            aggregations[f"average_stock_vol_{window}"] = (
                f"volatility_{window}",
                "mean",
            )
            aggregations[f"eligible_issues_{window}"] = (
                f"volatility_{window}",
                "count",
            )
        frame = active.groupby("trade_date").agg(**aggregations)
        frame.index.name = "trade_date"

        benchmark = context.series_factors.get("benchmark", pd.DataFrame())
        benchmark_returns = consecutive_log_returns(
            benchmark,
            sessions,
            include_instrument=False,
        )
        benchmark_returns = benchmark_returns.set_index("trade_date")
        for window in DISPERSION_WINDOWS:
            frame[f"index_vol_{window}"] = annualized_volatility(
                benchmark_returns["log_return"],
                window,
            ).reindex(frame.index)

        quality = session_quality(frame["quoted_issues"], frame["active_issues"])
        frame[["coverage", "coverage_floor", "quality_ok"]] = quality
        for window in DISPERSION_WINDOWS:
            average_stock_vol = frame[f"average_stock_vol_{window}"]
            index_vol = frame[f"index_vol_{window}"]
            available = (
                frame["quality_ok"]
                & frame[f"eligible_issues_{window}"].gt(0)
                & average_stock_vol.notna()
                & index_vol.notna()
            )
            frame[f"average_stock_vol_{window}"] = average_stock_vol.where(available)
            frame[f"dispersion_{window}"] = (average_stock_vol - index_vol).where(
                available
            )

        accepted = frame.index[frame["quality_ok"]]
        last_accepted = accepted[-1] if len(accepted) else None
        return IndicatorResult(
            key=self.key,
            title="Realized Dispersion",
            frame=frame[OUTPUT_COLUMNS],
            metadata={
                "windows": DISPERSION_WINDOWS,
                "annualization_sessions": ANNUALIZATION_SESSIONS,
                "return_basis": "Consecutive-session adjusted-close log returns",
                "constituent_weighting": "Equal-weight arithmetic mean",
                "formula": "mean constituent volatility - VAS volatility",
                "membership": "Point-in-time holdings snapshots by effective date",
                "quality_policy": QUALITY_POLICY,
                "gap_policy": (
                    "Missing and multi-session returns are excluded; prior valid "
                    "one-session returns remain available to later rolling windows"
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
        key=RealizedDispersion.key,
        title="Realized Dispersion",
        frame=pd.DataFrame(columns=OUTPUT_COLUMNS),
        metadata={
            "windows": DISPERSION_WINDOWS,
            "universe_size": len(context.snapshot.instruments),
        },
    )
