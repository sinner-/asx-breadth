"""Total-return trend and high/low EMA hysteresis for the universe ETF."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import IndicatorContext, IndicatorResult


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
        factors = context.benchmark_factors.copy()
        symbol = (
            context.snapshot.benchmark.provider_symbol
            if context.snapshot.benchmark is not None
            else context.snapshot.universe_code
        )
        if factors.empty:
            return IndicatorResult(
                key=self.key,
                title=f"{symbol} Total Return & Trend",
                frame=pd.DataFrame(columns=COLUMNS),
                metadata={"benchmark_symbol": symbol},
            )

        factors["trade_date"] = pd.to_datetime(factors["trade_date"])
        factors = factors.sort_values("trade_date")
        previous_total = 100.0
        rows: list[dict[str, float | pd.Timestamp]] = []
        for position, row in enumerate(factors.itertuples(index=False)):
            total_factor = _positive(row.total_return_factor)
            close_factor = _positive(row.close_to_previous_close)
            high_factor = _positive(row.high_to_previous_close)
            low_factor = _positive(row.low_to_previous_close)
            adjusted_high = np.nan
            adjusted_low = np.nan
            if total_factor is not None and close_factor is not None:
                adjustment = total_factor / close_factor
                if high_factor is not None:
                    adjusted_high = previous_total * high_factor * adjustment
                if low_factor is not None:
                    adjusted_low = previous_total * low_factor * adjustment
                total_index = previous_total * total_factor
            elif position == 0:
                total_index = previous_total
            else:
                total_index = np.nan
            rows.append(
                {
                    "trade_date": row.trade_date,
                    "total_return_index": total_index,
                    "adjusted_high_index": adjusted_high,
                    "adjusted_low_index": adjusted_low,
                }
            )
            if np.isfinite(total_index):
                previous_total = float(total_index)

        frame = pd.DataFrame(rows).set_index("trade_date")
        latest_index = frame["total_return_index"].dropna().iloc[-1]
        anchor_price = context.benchmark_anchor_price
        if anchor_price is not None and np.isfinite(anchor_price) and latest_index > 0:
            scale = float(anchor_price) / float(latest_index)
            frame[
                ["total_return_index", "adjusted_high_index", "adjusted_low_index"]
            ] *= scale
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
                "warmup_sessions": 200,
            },
        )


def _positive(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if np.isfinite(number) and number > 0 else None
