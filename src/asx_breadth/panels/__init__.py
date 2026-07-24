"""Built-in dashboard panel plugins."""

from .base import DashboardPanel, PanelSummary
from .charts import (
    AdvanceDeclinePanel,
    BenchmarkTrendPanel,
    CurrencyIndexTrendPanel,
    GeometricIndexPanel,
    McClellanOscillatorPanel,
    NetNewHighsPanel,
    NewHighsPanel,
    NewLowsPanel,
    PercentAboveSmaPanel,
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
    *(PercentAboveSmaPanel(window) for window in (5, 20, 50, 200)),
    VolatilityTrendPanel(),
    CurrencyIndexTrendPanel(),
)

__all__ = [
    "BUILT_IN_PANELS",
    "AdvanceDeclinePanel",
    "BenchmarkTrendPanel",
    "CurrencyIndexTrendPanel",
    "DashboardPanel",
    "GeometricIndexPanel",
    "McClellanOscillatorPanel",
    "NetNewHighsPanel",
    "NewHighsPanel",
    "NewLowsPanel",
    "PanelSummary",
    "PercentAboveSmaPanel",
    "RasiPanel",
    "VolatilityTrendPanel",
]
