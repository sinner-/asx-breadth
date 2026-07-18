"""Trend overlays for the cached Australian Dollar Currency Index series."""

from __future__ import annotations

from .series_level import PublishedSeriesTrend


class CurrencyIndexTrend(PublishedSeriesTrend):
    key = "currency_index_trend"
    title = "Australian Dollar Currency Index (XDA)"
    series_role = "currency_index"
    default_symbol = "^XDA"
    level_column = "currency_index"
    ema_spans = (19, 39, 200)
