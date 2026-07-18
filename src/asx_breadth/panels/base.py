"""Dashboard panel extension point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import plotly.graph_objects as go

from ..indicators.base import IndicatorResult


@dataclass(frozen=True)
class PanelSummary:
    label: str
    value: str
    detail: str
    tone: Literal["positive", "negative", "neutral", "ink"] = "neutral"


class DashboardPanel(Protocol):
    key: str
    indicator_key: str
    title: str

    def figure(self, result: IndicatorResult) -> go.Figure: ...

    def summary(self, result: IndicatorResult) -> PanelSummary: ...
