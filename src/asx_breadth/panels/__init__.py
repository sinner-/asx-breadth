"""Built-in dashboard panel plugins."""

from .charts import (
    AdvanceDeclinePanel,
    BenchmarkTrendPanel,
    CurrencyIndexTrendPanel,
    GeometricIndexPanel,
    McClellanOscillatorPanel,
    NetNewHighsPanel,
    NewHighsPanel,
    NewLowsPanel,
    RasiPanel,
    VolatilityTrendPanel,
)

BUILT_IN_PANELS = (
    BenchmarkTrendPanel(),
    GeometricIndexPanel(),
    AdvanceDeclinePanel(),
    RasiPanel(),
    McClellanOscillatorPanel(),
    NewHighsPanel(),
    NewLowsPanel(),
    NetNewHighsPanel(),
    VolatilityTrendPanel(),
    CurrencyIndexTrendPanel(),
)

__all__ = [
    "BUILT_IN_PANELS",
    "AdvanceDeclinePanel",
    "BenchmarkTrendPanel",
    "CurrencyIndexTrendPanel",
    "GeometricIndexPanel",
    "McClellanOscillatorPanel",
    "NetNewHighsPanel",
    "NewHighsPanel",
    "NewLowsPanel",
    "RasiPanel",
    "VolatilityTrendPanel",
]
