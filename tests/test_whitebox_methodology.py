#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "numpy>=2,<3",
#   "pandas>=2.2,<4",
# ]
# ///
"""Focused regressions for path-state and corporate-action methodology."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from asx_breadth.indicators.base import IndicatorContext, IndicatorResult
from asx_breadth.indicators.mcclellan import RatioAdjustedMcClellan
from asx_breadth.indicators.new_highs_lows import NewHighLow, _price_extremes
from asx_breadth.models import Snapshot, SnapshotInstrument


class WhiteboxMethodologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.instrument = SnapshotInstrument(1, "AAA", "AAA.AX", "AAA")
        self.snapshot = Snapshot(
            snapshot_id=7,
            universe_code="TEST",
            universe_name="Test universe",
            as_of_date=date(2026, 7, 3),
            source_path="holdings.xlsx",
            instruments=(self.instrument,),
        )

    def test_price_extremes_apply_dividend_and_split_adjustment(self) -> None:
        dates = pd.date_range("2026-07-01", periods=4, freq="D")
        factors = pd.DataFrame(
            {
                "trade_date": dates,
                "close_to_previous_close": [None, 1.0, 0.95, 0.50],
                "total_return_factor": [None, 1.0, 1.0, 1.0],
                "high_to_previous_close": [None, 1.01, 0.96, 0.52],
                "low_to_previous_close": [None, 0.99, 0.95, 0.48],
            }
        )

        highs, lows = _price_extremes(factors)

        self.assertAlmostEqual(highs.loc[dates[2]], 100.0 * 0.96 / 0.95)
        self.assertAlmostEqual(lows.loc[dates[2]], 100.0)
        self.assertAlmostEqual(highs.loc[dates[3]], 104.0)
        self.assertAlmostEqual(lows.loc[dates[3]], 96.0)

    def test_dividend_price_drop_does_not_create_a_false_new_low(self) -> None:
        dates = pd.date_range("2026-07-01", periods=4, freq="D")
        factors = pd.DataFrame(
            {
                "instrument_id": [1] * 4,
                "trade_date": dates,
                "close_to_previous_close": [None, 1.0, 1.0, 0.95],
                "total_return_factor": [None, 1.0, 1.0, 1.0],
                "high_to_previous_close": [None, 1.01, 1.01, 0.96],
                "low_to_previous_close": [None, 0.99, 0.99, 0.95],
            }
        )
        result = NewHighLow(lookback_sessions=3).calculate(
            IndicatorContext(
                snapshot=self.snapshot,
                factors=factors,
                benchmark_factors=pd.DataFrame({"trade_date": dates}),
            ),
            {},
        )

        self.assertEqual(result.frame.index[-1], dates[-1])
        self.assertEqual(result.frame.iloc[-1]["new_lows"], 0)
        self.assertIn("total-return-adjusted", result.metadata["price_basis"])

    def test_mcclellan_exposes_held_state_and_last_accepted_session(self) -> None:
        dates = pd.to_datetime(["2026-07-01", "2026-07-02"])
        ad = pd.DataFrame(
            {
                "advances_plus_declines": [5, 0],
                "net_advances": [3.0, float("nan")],
                "quality_ok": [True, False],
            },
            index=dates,
        )
        result = RatioAdjustedMcClellan().calculate(
            IndicatorContext(self.snapshot, pd.DataFrame()),
            {
                "advance_decline": IndicatorResult(
                    key="advance_decline",
                    title="A/D",
                    frame=ad,
                )
            },
        )

        self.assertFalse(result.frame.iloc[-1]["input_quality_ok"])
        self.assertFalse(result.frame.iloc[-1]["signal_updated"])
        self.assertEqual(result.frame.iloc[-1]["last_accepted_session"], dates[0])
        self.assertFalse(result.metadata["latest_signal_updated"])
        self.assertEqual(result.metadata["last_accepted_session"], "2026-07-01")


if __name__ == "__main__":
    unittest.main()
