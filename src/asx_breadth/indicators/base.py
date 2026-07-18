"""Minimal dependency-aware interface for breadth indicator plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

import pandas as pd

from ..models import Snapshot


@dataclass(frozen=True, slots=True)
class IndicatorContext:
    snapshot: Snapshot
    factors: pd.DataFrame
    benchmark_factors: pd.DataFrame = field(default_factory=pd.DataFrame)
    benchmark_anchor_price: float | None = None
    membership: pd.DataFrame = field(default_factory=pd.DataFrame)
    series_factors: Mapping[str, pd.DataFrame] = field(default_factory=dict)
    series_anchor_prices: Mapping[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IndicatorResult:
    key: str
    title: str
    frame: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)


class Indicator(Protocol):
    key: str
    dependencies: tuple[str, ...]

    def calculate(
        self,
        context: IndicatorContext,
        results: dict[str, IndicatorResult],
    ) -> IndicatorResult: ...


def run_indicators(
    context: IndicatorContext,
    indicators: Sequence[Indicator],
) -> dict[str, IndicatorResult]:
    """Run plugins in declared order and enforce explicit dependencies."""
    results: dict[str, IndicatorResult] = {}
    for indicator in indicators:
        missing = [key for key in indicator.dependencies if key not in results]
        if missing:
            raise ValueError(
                f"Indicator {indicator.key!r} is missing dependencies: {missing}"
            )
        results[indicator.key] = indicator.calculate(context, results)
    return results
