"""Total-return trend and high/low EMA hysteresis for the universe ETF."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import IndicatorContext, IndicatorResult
from .series_level import (
    links_previous,
    ordered_factor_frame,
    positive_number,
    reconstruct_actual_level,
)


COLUMNS = [
    "total_return_index",
    "adjusted_high_index",
    "adjusted_low_index",
    "total_return_ema19",
    "total_return_ema39",
    "high_ema200",
    "low_ema200",
]


class BenchmarkTrend:
    key = "benchmark_trend"
    dependencies: tuple[str, ...] = ()

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult:
        del results
        instrument = context.snapshot.instrument_for_role("benchmark")
        symbol = (
            instrument.provider_symbol
            if instrument is not None
            else context.snapshot.universe_code
        )
        raw_factors = context.series_factors.get("benchmark", pd.DataFrame())
        anchor_price = context.series_anchor_prices.get("benchmark")
        required = {
            "trade_date",
            "previous_trade_date",
            "total_return_factor",
            "close_to_previous_close",
            "high_to_previous_close",
            "low_to_previous_close",
        }
        if raw_factors.empty or not required.issubset(raw_factors.columns):
            return _empty_result(symbol)

        factors = ordered_factor_frame(raw_factors)
        total_levels = reconstruct_actual_level(factors, anchor_price).to_numpy()
        if not len(total_levels):
            return _empty_result(symbol)
        rows: list[dict[str, float | pd.Timestamp]] = []
        for position, row in enumerate(factors.itertuples(index=False)):
            total_factor = positive_number(row.total_return_factor)
            close_factor = positive_number(row.close_to_previous_close)
            high_factor = positive_number(row.high_to_previous_close)
            low_factor = positive_number(row.low_to_previous_close)
            total_index = total_levels[position]
            adjusted_high = np.nan
            adjusted_low = np.nan
            previous_total = (
                total_levels[position - 1]
                if position > 0 and links_previous(factors, position)
                else np.nan
            )
            if (
                np.isfinite(previous_total)
                and total_factor is not None
                and close_factor is not None
            ):
                adjustment = total_factor / close_factor
                if high_factor is not None:
                    adjusted_high = previous_total * high_factor * adjustment
                if low_factor is not None:
                    adjusted_low = previous_total * low_factor * adjustment
            rows.append(
                {
                    "trade_date": row.trade_date,
                    "total_return_index": total_index,
                    "adjusted_high_index": adjusted_high,
                    "adjusted_low_index": adjusted_low,
                }
            )

        frame = pd.DataFrame(rows).set_index("trade_date")
        frame["total_return_ema19"] = (
            frame["total_return_index"]
            .ewm(span=19, adjust=False, min_periods=19)
            .mean()
        )
        frame["total_return_ema39"] = (
            frame["total_return_index"]
            .ewm(span=39, adjust=False, min_periods=39)
            .mean()
        )
        frame["high_ema200"] = (
            frame["adjusted_high_index"]
            .ewm(span=200, adjust=False, min_periods=200)
            .mean()
        )
        frame["low_ema200"] = (
            frame["adjusted_low_index"]
            .ewm(span=200, adjust=False, min_periods=200)
            .mean()
        )
        return IndicatorResult(
            key=self.key,
            title=f"{symbol} Total Return & Trend",
            frame=frame[COLUMNS],
            metadata={
                "benchmark_symbol": symbol,
                "latest_adjusted_close_anchor": anchor_price,
                "high_low_adjustment": "Dividend/split-adjusted OHLC factors",
                "broken_chain_policy": (
                    "Show only the segment connected to the latest price anchor"
                ),
                "warmup_sessions": 200,
            },
        )


def _empty_result(symbol: str) -> IndicatorResult:
    return IndicatorResult(
        key=BenchmarkTrend.key,
        title=f"{symbol} Total Return & Trend",
        frame=pd.DataFrame(columns=COLUMNS),
        metadata={"benchmark_symbol": symbol, "warmup_sessions": 200},
    )
