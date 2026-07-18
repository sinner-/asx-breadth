"""Built-in indicator plugins."""

from .advance_decline import AdvanceDecline
from .base import IndicatorContext, IndicatorResult, run_indicators
from .benchmark_trend import BenchmarkTrend
from .mcclellan import RatioAdjustedMcClellan
from .new_highs_lows import NewHighLow

BUILT_IN_INDICATORS = (
    BenchmarkTrend(),
    AdvanceDecline(),
    RatioAdjustedMcClellan(),
    NewHighLow(),
)

__all__ = [
    "BUILT_IN_INDICATORS",
    "AdvanceDecline",
    "BenchmarkTrend",
    "IndicatorContext",
    "IndicatorResult",
    "RatioAdjustedMcClellan",
    "NewHighLow",
    "run_indicators",
]
