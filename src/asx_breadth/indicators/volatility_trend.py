"""Trend state for the cached S&P/ASX 200 VIX auxiliary series."""

from __future__ import annotations

import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .series_level import reconstruct_actual_level


COLUMNS = ["axvi", "axvi_ema200", "above_ema200"]


class VolatilityTrend:
    """Reconstruct the actual AXVI level and calculate its 200-session EMA."""

    key = "volatility_trend"
    dependencies: tuple[str, ...] = ()
    series_role = "volatility"

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult:
        del results
        instrument = context.snapshot.instrument_for_role(self.series_role)
        symbol = instrument.provider_symbol if instrument is not None else "^AXVI"
        factors = context.series_factors.get(self.series_role, pd.DataFrame())
        anchor = context.series_anchor_prices.get(self.series_role)
        levels = reconstruct_actual_level(factors, anchor)
        if levels.empty:
            return _empty_result(symbol)
        frame = levels.rename("axvi").to_frame()
        frame["axvi_ema200"] = (
            frame["axvi"]
            .ewm(
                span=200,
                adjust=False,
                min_periods=200,
            )
            .mean()
        )
        frame["above_ema200"] = frame["axvi"] > frame["axvi_ema200"]
        return IndicatorResult(
            key=self.key,
            title="S&P/ASX 200 VIX (AXVI)",
            frame=frame[COLUMNS],
            metadata={
                "series_role": self.series_role,
                "provider_symbol": symbol,
                "latest_adjusted_close_anchor": float(anchor),
                "warmup_sessions": 200,
                "return_basis": "Published index-level close ratios",
            },
        )


def _empty_result(symbol: str) -> IndicatorResult:
    return IndicatorResult(
        key=VolatilityTrend.key,
        title="S&P/ASX 200 VIX (AXVI)",
        frame=pd.DataFrame(columns=COLUMNS),
        metadata={
            "series_role": VolatilityTrend.series_role,
            "provider_symbol": symbol,
            "warmup_sessions": 200,
        },
    )
