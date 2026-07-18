"""Shared calculation for cached, non-composite published market series."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import IndicatorContext, IndicatorResult


class PublishedSeriesTrend:
    """Reconstruct one published level and apply configured EMA overlays."""

    key: str
    title: str
    series_role: str
    default_symbol: str
    level_column: str
    ema_spans: tuple[int, ...]
    dependencies: tuple[str, ...] = ()

    @property
    def columns(self) -> list[str]:
        return [
            self.level_column,
            *(f"{self.level_column}_ema{span}" for span in self.ema_spans),
        ]

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult:
        del results
        instrument = context.snapshot.instrument_for_role(self.series_role)
        symbol = (
            instrument.provider_symbol
            if instrument is not None
            else self.default_symbol
        )
        factors = context.series_factors.get(self.series_role, pd.DataFrame())
        anchor = context.series_anchor_prices.get(self.series_role)
        levels = reconstruct_actual_level(factors, anchor)
        if levels.empty:
            return self._empty_result(symbol)

        frame = levels.rename(self.level_column).to_frame()
        for span in self.ema_spans:
            frame[f"{self.level_column}_ema{span}"] = (
                frame[self.level_column]
                .ewm(span=span, adjust=False, min_periods=span)
                .mean()
            )
        return IndicatorResult(
            key=self.key,
            title=self.title,
            frame=frame[self.columns],
            metadata={
                "series_role": self.series_role,
                "provider_symbol": symbol,
                "latest_adjusted_close_anchor": float(anchor),
                "warmup_sessions": max(self.ema_spans, default=0),
                "return_basis": "Published index-level close ratios",
            },
        )

    def _empty_result(self, symbol: str) -> IndicatorResult:
        return IndicatorResult(
            key=self.key,
            title=self.title,
            frame=pd.DataFrame(columns=self.columns),
            metadata={
                "series_role": self.series_role,
                "provider_symbol": symbol,
                "warmup_sessions": max(self.ema_spans, default=0),
            },
        )


def reconstruct_actual_level(
    factors: pd.DataFrame,
    anchor: object,
) -> pd.Series:
    """Rebuild an actual published level backwards from its latest observation.

    The cache stores append-stable daily ratios. Anchoring the latest factor row
    to its observed adjusted close recovers the provider's published scale. A
    broken chain remains unavailable before the break rather than being bridged.
    """
    required = {"trade_date", "previous_trade_date", "total_return_factor"}
    anchor_value = positive_number(anchor)
    if factors.empty or anchor_value is None or not required.issubset(factors.columns):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], name="trade_date"))

    ordered = ordered_factor_frame(factors)
    levels = np.full(len(ordered), np.nan, dtype=float)
    levels[-1] = anchor_value
    for position in range(len(ordered) - 1, 0, -1):
        factor = positive_number(ordered.iloc[position]["total_return_factor"])
        if (
            factor is None
            or not links_previous(ordered, position)
            or not np.isfinite(levels[position])
        ):
            break
        levels[position - 1] = levels[position] / factor
    return pd.Series(
        levels,
        index=pd.DatetimeIndex(ordered["trade_date"], name="trade_date"),
        dtype=float,
    )


def ordered_factor_frame(factors: pd.DataFrame) -> pd.DataFrame:
    ordered = factors.copy()
    ordered["trade_date"] = pd.to_datetime(ordered["trade_date"])
    ordered["previous_trade_date"] = pd.to_datetime(
        ordered["previous_trade_date"], errors="coerce"
    )
    return (
        ordered.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )


def links_previous(factors: pd.DataFrame, position: int) -> bool:
    if position <= 0:
        return False
    reported = factors.iloc[position]["previous_trade_date"]
    return pd.notna(reported) and pd.Timestamp(reported) == pd.Timestamp(
        factors.iloc[position - 1]["trade_date"]
    )


def positive_number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if np.isfinite(number) and number > 0 else None
