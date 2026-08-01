"""Built-in dashboard panel plugins."""

from .base import DashboardPanel, PanelSummary
from .charts import (
    AdvanceDeclinePanel,
    AverageCorrelationPanel,
    BenchmarkTrendPanel,
    CurrencyIndexTrendPanel,
    GeometricIndexPanel,
    McClellanOscillatorPanel,
    NetNewHighsPanel,
    NewHighsPanel,
    NewLowsPanel,
    PercentAboveSmaPanel,
    RasiPanel,
    RealizedDispersionPanel,
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
    *(RealizedDispersionPanel(window) for window in (21, 63, 120)),
    *(AverageCorrelationPanel(window) for window in (21, 63, 120)),
    VolatilityTrendPanel(),
    CurrencyIndexTrendPanel(),
)

__all__ = [
    "BUILT_IN_PANELS",
    "AdvanceDeclinePanel",
    "AverageCorrelationPanel",
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
    "RealizedDispersionPanel",
    "VolatilityTrendPanel",
]
