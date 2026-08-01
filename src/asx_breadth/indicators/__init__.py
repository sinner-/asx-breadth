"""Built-in indicator plugins."""

from .advance_decline import AdvanceDecline
from .average_correlation import AverageCorrelation
from .base import IndicatorContext, IndicatorResult, run_indicators
from .benchmark_trend import BenchmarkTrend
from .currency_index_trend import CurrencyIndexTrend
from .geometric_index import GeometricIndex
from .mcclellan import RatioAdjustedMcClellan
from .new_highs_lows import NewHighLow
from .percent_above_sma import PercentAboveMovingAverages
from .realized_dispersion import RealizedDispersion
from .volatility_trend import VolatilityTrend

BUILT_IN_INDICATORS = (
    BenchmarkTrend(),
    GeometricIndex(),
    CurrencyIndexTrend(),
    VolatilityTrend(),
    AdvanceDecline(),
    RatioAdjustedMcClellan(),
    NewHighLow(),
    PercentAboveMovingAverages(),
    RealizedDispersion(),
    AverageCorrelation(),
)

__all__ = [
    "BUILT_IN_INDICATORS",
    "AdvanceDecline",
    "AverageCorrelation",
    "BenchmarkTrend",
    "CurrencyIndexTrend",
    "GeometricIndex",
    "IndicatorContext",
    "IndicatorResult",
    "NewHighLow",
    "PercentAboveMovingAverages",
    "RatioAdjustedMcClellan",
    "RealizedDispersion",
    "VolatilityTrend",
    "run_indicators",
]
