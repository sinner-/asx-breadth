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
    """Run plugins in dependency order, independent of registration order."""
    registered: dict[str, Indicator] = {}
    for indicator in indicators:
        if indicator.key in registered:
            raise ValueError(f"Duplicate indicator key: {indicator.key!r}")
        registered[indicator.key] = indicator

    missing = {
        key: tuple(
            dependency
            for dependency in indicator.dependencies
            if dependency not in registered
        )
        for key, indicator in registered.items()
    }
    missing = {
        key: dependencies for key, dependencies in missing.items() if dependencies
    }
    if missing:
        details = "; ".join(
            f"{key!r} requires {list(dependencies)!r}"
            for key, dependencies in missing.items()
        )
        raise ValueError(f"Missing indicator dependencies: {details}")

    results: dict[str, IndicatorResult] = {}
    pending = dict(registered)
    while pending:
        ready = [
            key
            for key, indicator in pending.items()
            if all(dependency in results for dependency in indicator.dependencies)
        ]
        if not ready:
            details = "; ".join(
                f"{key!r} -> {list(indicator.dependencies)!r}"
                for key, indicator in pending.items()
            )
            raise ValueError(f"Cyclic indicator dependencies: {details}")
        for key in ready:
            indicator = pending.pop(key)
            results[key] = indicator.calculate(context, results)
    return results
