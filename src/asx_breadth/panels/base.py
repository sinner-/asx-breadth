"""Dashboard panel extension point."""

from __future__ import annotations

from typing import Protocol

import plotly.graph_objects as go

from ..indicators.base import IndicatorResult


class DashboardPanel(Protocol):
    key: str
    indicator_key: str
    title: str

    def figure(self, result: IndicatorResult) -> go.Figure: ...
