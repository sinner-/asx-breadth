"""Trend state for the cached S&P/ASX 200 VIX market series."""

from __future__ import annotations

from .series_level import PublishedSeriesTrend


class VolatilityTrend(PublishedSeriesTrend):
    """Reconstruct the actual AXVI level and calculate its 200-session EMA."""

    key = "volatility_trend"
    title = "S&P/ASX 200 VIX (AXVI)"
    series_role = "volatility"
    default_symbol = "^AXVI"
    level_column = "axvi"
    ema_spans = (200,)
