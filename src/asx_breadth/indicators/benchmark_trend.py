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
        factors = factors.sort_values("trade_date").reset_index(drop=True)
        total_levels = _total_return_levels(
            factors,
            anchor=context.benchmark_anchor_price,
        )
        rows: list[dict[str, float | pd.Timestamp]] = []
        for position, row in enumerate(factors.itertuples(index=False)):
            total_factor = _positive(row.total_return_factor)
            close_factor = _positive(row.close_to_previous_close)
            high_factor = _positive(row.high_to_previous_close)
            low_factor = _positive(row.low_to_previous_close)
            total_index = total_levels[position]
            adjusted_high = np.nan
            adjusted_low = np.nan
            previous_total = (
                total_levels[position - 1]
                if position > 0 and _links_previous(factors, position)
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
        anchor_price = context.benchmark_anchor_price
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


def _positive(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if np.isfinite(number) and number > 0 else None


def _total_return_levels(factors: pd.DataFrame, *, anchor: object) -> np.ndarray:
    """Reconstruct one honest factor-chain segment without bridging a bad row."""
    levels = np.full(len(factors), np.nan, dtype=float)
    if not len(factors):
        return levels

    anchor_value = _positive(anchor)
    if anchor_value is not None:
        # A real latest adjusted close identifies the scale of the newest
        # connected segment. Work backwards until an explicit broken link; older
        # values cannot be placed on that scale and remain unavailable.
        levels[-1] = anchor_value
        for position in range(len(factors) - 1, 0, -1):
            factor = _positive(factors.iloc[position]["total_return_factor"])
            if factor is None or not _links_previous(factors, position):
                break
            levels[position - 1] = levels[position] / factor
        return levels

    # Without a real price anchor, the initial row can supply an arbitrary base
    # only for its original connected segment. Never silently rebase after a
    # broken interior factor because that would make later values incomparable.
    levels[0] = 100.0
    for position in range(1, len(factors)):
        factor = _positive(factors.iloc[position]["total_return_factor"])
        if factor is None or not _links_previous(factors, position):
            break
        levels[position] = levels[position - 1] * factor
    return levels


def _links_previous(factors: pd.DataFrame, position: int) -> bool:
    if position <= 0:
        return False
    if "previous_trade_date" not in factors:
        return True
    reported = factors.iloc[position]["previous_trade_date"]
    if pd.isna(reported):
        return False
    return pd.Timestamp(reported) == pd.Timestamp(
        factors.iloc[position - 1]["trade_date"]
    )
