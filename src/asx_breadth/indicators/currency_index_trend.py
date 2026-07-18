"""Trend overlays for the cached Australian Dollar Currency Index."""

from __future__ import annotations

import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .series_level import reconstruct_actual_level


COLUMNS = [
    "currency_index",
    "currency_index_ema19",
    "currency_index_ema39",
    "currency_index_ema200",
]


class CurrencyIndexTrend:
    key = "currency_index_trend"
    dependencies: tuple[str, ...] = ()
    series_role = "currency_index"

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult:
        del results
        instrument = context.snapshot.instrument_for_role(self.series_role)
        symbol = instrument.provider_symbol if instrument is not None else "^XDA"
        factors = context.series_factors.get(self.series_role, pd.DataFrame())
        anchor = context.series_anchor_prices.get(self.series_role)
        levels = reconstruct_actual_level(factors, anchor)
        if levels.empty:
            return _empty_result(symbol)

        frame = levels.rename("currency_index").to_frame()
        for span in (19, 39, 200):
            frame[f"currency_index_ema{span}"] = (
                frame["currency_index"]
                .ewm(span=span, adjust=False, min_periods=span)
                .mean()
            )
        return IndicatorResult(
            key=self.key,
            title="Australian Dollar Currency Index (XDA)",
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
        key=CurrencyIndexTrend.key,
        title="Australian Dollar Currency Index (XDA)",
        frame=pd.DataFrame(columns=COLUMNS),
        metadata={
            "series_role": CurrencyIndexTrend.series_role,
            "provider_symbol": symbol,
            "warmup_sessions": 200,
        },
    )
