"""Advance/decline breadth from consecutive-session total returns."""

from __future__ import annotations

import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .membership import (
    active_membership_for_sessions,
    market_sessions,
)
from .quality import QUALITY_POLICY, accepted_session_ema, session_quality


OUTPUT_COLUMNS = [
    "advances",
    "declines",
    "unchanged",
    "issues",
    "advances_plus_declines",
    "coverage",
    "coverage_floor",
    "quality_ok",
    "net_advances",
    "cumulative_ad",
    "cumulative_ad_ema19",
    "cumulative_ad_ema39",
    "cumulative_ad_ema200",
    "net_advances_ema19",
    "net_advances_ema39",
]


class AdvanceDecline:
    key = "advance_decline"
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

        factors["trade_date"] = pd.to_datetime(factors["trade_date"])
        factors["previous_trade_date"] = pd.to_datetime(
            factors["previous_trade_date"], errors="coerce"
        )
        sessions = market_sessions(context)
        if len(sessions) < 2:
            return _empty_result(context)
        membership = active_membership_for_sessions(context, sessions)
        factors = factors.merge(
            membership,
            on=["trade_date", "instrument_id"],
            how="inner",
            validate="many_to_one",
        )
        # Shift values, not the DatetimeIndex itself (an exchange calendar has
        # irregular holiday gaps and therefore intentionally has no fixed freq).
        expected_previous = pd.Series(sessions, index=sessions).shift(1)
        factors["expected_previous"] = factors["trade_date"].map(expected_previous)

        # A/D counts only stocks with a quote on two consecutive universe sessions.
        valid = factors[
            factors["total_return_factor"].notna()
            & (factors["previous_trade_date"] == factors["expected_previous"])
        ].copy()
        epsilon = 1e-10
        valid["advance"] = valid["total_return_factor"] > 1.0 + epsilon
        valid["decline"] = valid["total_return_factor"] < 1.0 - epsilon
        valid["unchanged"] = ~(valid["advance"] | valid["decline"])
        grouped = valid.groupby("trade_date").agg(
            advances=("advance", "sum"),
            declines=("decline", "sum"),
            unchanged=("unchanged", "sum"),
        )
        active_sessions = pd.DatetimeIndex(
            membership["trade_date"].drop_duplicates().sort_values()
        )
        eligible_sessions = sessions[1:][sessions[1:].isin(active_sessions)]
        frame = grouped.reindex(eligible_sessions, fill_value=0).astype(
            {"advances": int, "declines": int, "unchanged": int}
        )
        frame.index.name = "trade_date"
        frame["issues"] = frame[["advances", "declines", "unchanged"]].sum(axis=1)
        frame["advances_plus_declines"] = frame["advances"] + frame["declines"]
        universe_sizes = (
            membership.groupby("trade_date")["instrument_id"]
            .nunique()
            .reindex(frame.index)
        )
        quality = session_quality(frame["issues"], universe_sizes)
        frame[["coverage", "coverage_floor", "quality_ok"]] = quality
        raw_net_advances = frame["advances"] - frame["declines"]
        frame["net_advances"] = raw_net_advances.where(frame["quality_ok"])
        frame["cumulative_ad"] = frame["net_advances"].fillna(0).cumsum()
        accepted = frame["quality_ok"] & frame["net_advances"].notna()
        for span in (19, 39, 200):
            frame[f"cumulative_ad_ema{span}"] = accepted_session_ema(
                frame["cumulative_ad"], accepted, span=span
            )
        frame["net_advances_ema19"] = accepted_session_ema(
            frame["net_advances"], accepted, span=19
        )
        frame["net_advances_ema39"] = accepted_session_ema(
            frame["net_advances"], accepted, span=39
        )
        return IndicatorResult(
            key=self.key,
            title="Advance–Decline",
            frame=frame[OUTPUT_COLUMNS],
            metadata={
                "universe_size": len(context.snapshot.instruments),
                "membership": "Point-in-time holdings snapshots by effective date",
                "quality_policy": QUALITY_POLICY,
                "classification": "Consecutive-session adjusted-close total return",
                "gap_policy": "A missing or halted issue is unclassified for that session",
            },
        )


def _empty_result(context: IndicatorContext) -> IndicatorResult:
    return IndicatorResult(
        key=AdvanceDecline.key,
        title="Advance–Decline",
        frame=pd.DataFrame(columns=OUTPUT_COLUMNS),
        metadata={"universe_size": len(context.snapshot.instruments)},
    )
