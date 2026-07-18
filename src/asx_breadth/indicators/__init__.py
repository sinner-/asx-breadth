"""Built-in indicator plugins."""

from .advance_decline import AdvanceDecline
from .base import IndicatorContext, IndicatorResult, run_indicators
from .benchmark_trend import BenchmarkTrend
from .currency_index_trend import CurrencyIndexTrend
from .geometric_index import GeometricIndex
from .mcclellan import RatioAdjustedMcClellan
from .new_highs_lows import NewHighLow
from .volatility_trend import VolatilityTrend

BUILT_IN_INDICATORS = (
    BenchmarkTrend(),
    GeometricIndex(),
    CurrencyIndexTrend(),
    VolatilityTrend(),
    AdvanceDecline(),
    RatioAdjustedMcClellan(),
    NewHighLow(),
)

__all__ = [
    "BUILT_IN_INDICATORS",
    "AdvanceDecline",
    "BenchmarkTrend",
    "CurrencyIndexTrend",
    "GeometricIndex",
    "IndicatorContext",
    "IndicatorResult",
    "RatioAdjustedMcClellan",
    "NewHighLow",
    "VolatilityTrend",
    "run_indicators",
]
