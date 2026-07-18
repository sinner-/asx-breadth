"""Advance/decline breadth from consecutive-session total returns."""

from __future__ import annotations

import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .membership import (
    active_membership_for_sessions,
    market_sessions,
)
from .quality import QUALITY_POLICY, session_quality


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
        if factors.empty:
            return IndicatorResult(
                key=self.key,
                title="Advance–Decline",
                frame=pd.DataFrame(columns=OUTPUT_COLUMNS),
                metadata={"universe_size": len(context.snapshot.instruments)},
            )

        factors["trade_date"] = pd.to_datetime(factors["trade_date"])
        factors["previous_trade_date"] = pd.to_datetime(
            factors["previous_trade_date"], errors="coerce"
        )
        sessions = market_sessions(context)
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
        frame = grouped.reindex(sessions[1:], fill_value=0).astype(
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
        for span in (19, 39, 200):
            frame[f"cumulative_ad_ema{span}"] = (
                frame["cumulative_ad"]
                .ewm(span=span, adjust=False, min_periods=span)
                .mean()
            )
        frame["net_advances_ema19"] = (
            frame["net_advances"].ewm(span=19, adjust=False, min_periods=19).mean()
        )
        frame["net_advances_ema39"] = (
            frame["net_advances"].ewm(span=39, adjust=False, min_periods=39).mean()
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
