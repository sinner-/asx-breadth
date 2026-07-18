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

from asx_breadth.indicators.advance_decline import AdvanceDecline
from asx_breadth.indicators.base import IndicatorContext, IndicatorResult
from asx_breadth.indicators.benchmark_trend import BenchmarkTrend
from asx_breadth.indicators.geometric_index import GeometricIndex
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

    def test_price_extremes_do_not_resume_from_stale_level_after_broken_row(
        self,
    ) -> None:
        dates = pd.bdate_range("2026-07-01", periods=5)
        factors = pd.DataFrame(
            {
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
                "close_to_previous_close": [None, 1.10, None, 1.20, 1.10],
                "total_return_factor": [None, 1.10, None, 1.20, 1.10],
                "high_to_previous_close": [None, 1.10, None, 1.20, 1.10],
                "low_to_previous_close": [None, 1.00, None, 1.10, 1.00],
            }
        )

        highs, lows = _price_extremes(factors)

        self.assertAlmostEqual(highs.iloc[1], 110.0)
        self.assertAlmostEqual(lows.iloc[1], 100.0)
        self.assertTrue(highs.iloc[2:].isna().all())
        self.assertTrue(lows.iloc[2:].isna().all())

    def test_missing_calendar_session_without_factor_row_remains_linkable(self) -> None:
        dates = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-06"])
        factors = pd.DataFrame(
            {
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, dates[0], dates[1]],
                "close_to_previous_close": [None, 1.10, 1.20],
                "total_return_factor": [None, 1.10, 1.20],
                "high_to_previous_close": [None, 1.10, 1.20],
                "low_to_previous_close": [None, 1.00, 1.10],
            }
        )

        highs, _ = _price_extremes(factors)

        self.assertAlmostEqual(highs.iloc[-1], 132.0)

    def test_benchmark_uses_latest_anchor_without_bridging_broken_factor(self) -> None:
        dates = pd.bdate_range("2026-07-01", periods=5)
        factors = pd.DataFrame(
            {
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
                "total_return_factor": [None, 1.10, None, 1.20, 1.10],
                "close_to_previous_close": [None, 1.10, None, 1.20, 1.10],
                "high_to_previous_close": [None, 1.10, None, 1.25, 1.15],
                "low_to_previous_close": [None, 1.00, None, 1.10, 1.00],
            }
        )

        result = BenchmarkTrend().calculate(
            IndicatorContext(
                snapshot=self.snapshot,
                factors=pd.DataFrame(),
                benchmark_factors=factors,
                benchmark_anchor_price=132.0,
            ),
            {},
        )

        levels = result.frame["total_return_index"]
        self.assertTrue(levels.iloc[:2].isna().all())
        self.assertAlmostEqual(levels.iloc[2], 100.0)
        self.assertAlmostEqual(levels.iloc[3], 120.0)
        self.assertAlmostEqual(levels.iloc[4], 132.0)
        self.assertTrue(pd.isna(result.frame.iloc[2]["adjusted_high_index"]))
        self.assertAlmostEqual(result.frame.iloc[3]["adjusted_high_index"], 125.0)

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

    def test_earliest_snapshot_supplies_approximate_prior_membership(self) -> None:
        dates = pd.bdate_range("2026-07-01", periods=6)
        factors = pd.DataFrame(
            {
                "instrument_id": [1] * len(dates),
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
                "total_return_factor": [None, *([1.01] * (len(dates) - 1))],
            }
        )
        membership = pd.DataFrame(
            {
                "snapshot_id": [7],
                "effective_from": [dates[2]],
                "instrument_id": [1],
            }
        )
        context = IndicatorContext(
            snapshot=self.snapshot,
            factors=factors,
            benchmark_factors=pd.DataFrame({"trade_date": dates}),
            membership=membership,
        )

        ad = AdvanceDecline().calculate(context, {}).frame
        geometric = GeometricIndex().calculate(context, {}).frame

        expected = dates[1:]
        self.assertTrue(ad.index.equals(expected))
        self.assertTrue(geometric.index.equals(expected))
        self.assertEqual(ad.iloc[0]["advances"], 1)
        self.assertAlmostEqual(geometric.iloc[0]["geometric_index"], 101.0)

    def test_rejected_session_holds_breadth_ema_state(self) -> None:
        dates = pd.bdate_range("2025-09-01", periods=205)
        instruments = tuple(
            SnapshotInstrument(index, f"S{index}", f"S{index}.AX", f"Stock {index}")
            for index in range(1, 26)
        )
        snapshot = Snapshot(
            snapshot_id=8,
            universe_code="TEST",
            universe_name="Test universe",
            as_of_date=date(2025, 8, 29),
            source_path="holdings.xlsx",
            instruments=instruments,
        )
        rejected_date = dates[-2]
        rows: list[dict[str, object]] = []
        for instrument in instruments:
            for position, trade_date in enumerate(dates):
                factor = None if position == 0 else 1.01
                if trade_date == rejected_date and instrument.instrument_id > 5:
                    factor = None
                rows.append(
                    {
                        "instrument_id": instrument.instrument_id,
                        "trade_date": trade_date,
                        "previous_trade_date": (
                            dates[position - 1] if position else pd.NaT
                        ),
                        "total_return_factor": factor,
                    }
                )
        context = IndicatorContext(
            snapshot=snapshot,
            factors=pd.DataFrame(rows),
            benchmark_factors=pd.DataFrame({"trade_date": dates}),
        )

        ad = AdvanceDecline().calculate(context, {}).frame
        geometric = GeometricIndex().calculate(context, {}).frame

        self.assertFalse(ad.loc[rejected_date, "quality_ok"])
        self.assertFalse(geometric.loc[rejected_date, "quality_ok"])
        previous_date = ad.index[ad.index.get_loc(rejected_date) - 1]
        for column in (
            "cumulative_ad_ema19",
            "cumulative_ad_ema39",
            "cumulative_ad_ema200",
            "net_advances_ema19",
            "net_advances_ema39",
        ):
            self.assertEqual(
                ad.loc[rejected_date, column], ad.loc[previous_date, column]
            )
        for column in (
            "geometric_ema19",
            "geometric_ema39",
            "geometric_ema200",
        ):
            self.assertEqual(
                geometric.loc[rejected_date, column],
                geometric.loc[previous_date, column],
            )


if __name__ == "__main__":
    unittest.main()
