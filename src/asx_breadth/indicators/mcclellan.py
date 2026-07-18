"""Ratio-adjusted McClellan oscillator and summation index."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import IndicatorContext, IndicatorResult


class RatioAdjustedMcClellan:
    key = "mcclellan_rasi"
    dependencies = ("advance_decline",)

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult:
        del context
        ad = results["advance_decline"].frame
        columns = [
            "ratio_adjusted_net_advances",
            "ratio_ema19",
            "ratio_ema39",
            "mcclellan_oscillator",
            "rasi",
            "input_quality_ok",
            "signal_updated",
            "last_accepted_session",
        ]
        if ad.empty:
            return IndicatorResult(
                key=self.key,
                title="McClellan Ratio-Adjusted Summation Index",
                frame=pd.DataFrame(columns=columns),
                metadata={
                    "latest_input_quality_ok": None,
                    "latest_signal_updated": None,
                    "last_accepted_session": None,
                },
            )

        frame = pd.DataFrame(index=ad.index)
        # Ratio adjustment explicitly excludes unchanged issues: A + D.
        denominator = ad["advances_plus_declines"].replace(0, np.nan)
        frame["ratio_adjusted_net_advances"] = 1000.0 * ad["net_advances"] / denominator
        # The original McClellan recurrence explicitly starts both trends at
        # zero. Pandas' default EWM seed is the first observation, which leaves
        # a permanent offset in the cumulative RASI even after the EMA values
        # themselves have converged.
        frame["ratio_ema19"] = _trend_from_zero(
            frame["ratio_adjusted_net_advances"], alpha=0.10
        )
        frame["ratio_ema39"] = _trend_from_zero(
            frame["ratio_adjusted_net_advances"], alpha=0.05
        )
        available = frame["ratio_adjusted_net_advances"].notna()
        frame["mcclellan_oscillator"] = (
            frame["ratio_ema19"] - frame["ratio_ema39"]
        ).where(available)
        frame["rasi"] = frame["mcclellan_oscillator"].fillna(0).cumsum()
        input_quality = (
            (ad["quality_ok"] if "quality_ok" in ad else ad["net_advances"].notna())
            .fillna(False)
            .astype(bool)
        )
        frame["input_quality_ok"] = input_quality
        frame["signal_updated"] = input_quality & available
        accepted_sessions = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
        accepted_sessions.loc[frame["signal_updated"]] = frame.index[
            frame["signal_updated"]
        ]
        frame["last_accepted_session"] = accepted_sessions.ffill()
        last_accepted = frame["last_accepted_session"].dropna()
        return IndicatorResult(
            key=self.key,
            title="McClellan Ratio-Adjusted Summation Index",
            frame=frame[columns],
            metadata={
                "ratio_formula": "1000 × (advances − declines) / (advances + declines)",
                "oscillator_formula": "EMA19(ratio) − EMA39(ratio)",
                "trend_seeds": 0,
                "summation_seed": 0,
                "warmup_sessions": 252,
                "latest_input_quality_ok": bool(frame["input_quality_ok"].iloc[-1]),
                "latest_signal_updated": bool(frame["signal_updated"].iloc[-1]),
                "last_accepted_session": (
                    pd.Timestamp(last_accepted.iloc[-1]).date().isoformat()
                    if not last_accepted.empty
                    else None
                ),
            },
        )


def _trend_from_zero(series: pd.Series, *, alpha: float) -> pd.Series:
    """McClellan EMA recurrence, retaining state across an unavailable day."""
    trend = 0.0
    values: list[float] = []
    for observation in series:
        if pd.notna(observation):
            trend = alpha * float(observation) + (1.0 - alpha) * trend
        values.append(trend)
    return pd.Series(values, index=series.index, dtype=float)
