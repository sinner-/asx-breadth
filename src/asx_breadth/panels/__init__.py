"""Built-in dashboard panel plugins."""

from .charts import (
    AdvanceDeclinePanel,
    BenchmarkTrendPanel,
    McClellanOscillatorPanel,
    NetNewHighsPanel,
    NewHighsPanel,
    NewLowsPanel,
    RasiPanel,
)

BUILT_IN_PANELS = (
    BenchmarkTrendPanel(),
    AdvanceDeclinePanel(),
    RasiPanel(),
    McClellanOscillatorPanel(),
    NewHighsPanel(),
    NewLowsPanel(),
    NetNewHighsPanel(),
)

__all__ = [
    "BUILT_IN_PANELS",
    "AdvanceDeclinePanel",
    "BenchmarkTrendPanel",
    "McClellanOscillatorPanel",
    "NetNewHighsPanel",
    "NewHighsPanel",
    "NewLowsPanel",
    "RasiPanel",
]
