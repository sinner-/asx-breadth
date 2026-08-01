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

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from asx_breadth.indicators.advance_decline import AdvanceDecline
from asx_breadth.indicators.average_correlation import AverageCorrelation
from asx_breadth.indicators.base import IndicatorContext, IndicatorResult
from asx_breadth.indicators.benchmark_trend import BenchmarkTrend
from asx_breadth.indicators.geometric_index import GeometricIndex
from asx_breadth.indicators.mcclellan import RatioAdjustedMcClellan
from asx_breadth.indicators.new_highs_lows import NewHighLow, _price_extremes
from asx_breadth.indicators.percent_above_sma import (
    PercentAboveMovingAverages,
    _total_return_levels,
)
from asx_breadth.indicators.quality import session_quality
from asx_breadth.indicators.realized_dispersion import RealizedDispersion
from asx_breadth.models import Snapshot, SnapshotInstrument, SnapshotSeries


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
            market_series=(SnapshotSeries("benchmark", self.instrument),),
        )

    def test_quality_tolerates_one_halt_but_not_a_small_universe_outage(self) -> None:
        index = pd.to_datetime(["2026-07-01", "2026-07-02"])
        quality = session_quality(
            pd.Series([2, 0], index=index),
            pd.Series([3, 3], index=index),
        )

        self.assertTrue(quality.iloc[0]["quality_ok"])
        self.assertFalse(quality.iloc[1]["quality_ok"])

    def test_price_extremes_apply_dividend_and_split_adjustment(self) -> None:
        dates = pd.date_range("2026-07-01", periods=4, freq="D")
        factors = pd.DataFrame(
            {
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
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
                series_factors={"benchmark": factors},
                series_anchor_prices={"benchmark": 132.0},
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

    def test_benchmark_without_a_real_price_anchor_is_unavailable(self) -> None:
        dates = pd.bdate_range("2026-07-01", periods=3)
        factors = pd.DataFrame(
            {
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
                "total_return_factor": [None, 1.01, 1.02],
                "close_to_previous_close": [None, 1.01, 1.02],
                "high_to_previous_close": [None, 1.02, 1.03],
                "low_to_previous_close": [None, 0.99, 1.00],
            }
        )

        result = BenchmarkTrend().calculate(
            IndicatorContext(
                snapshot=self.snapshot,
                factors=pd.DataFrame(),
                series_factors={"benchmark": factors},
            ),
            {},
        )

        self.assertTrue(result.frame.empty)

    def test_dividend_price_drop_does_not_create_a_false_new_low(self) -> None:
        dates = pd.date_range("2026-07-01", periods=4, freq="D")
        factors = pd.DataFrame(
            {
                "instrument_id": [1] * 4,
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
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
                series_factors={"benchmark": pd.DataFrame({"trade_date": dates})},
            ),
            {},
        )

        self.assertEqual(result.frame.index[-1], dates[-1])
        self.assertEqual(result.frame.iloc[-1]["new_lows"], 0)
        self.assertIn("total-return-adjusted", result.metadata["price_basis"])

    def test_dividend_price_drop_does_not_create_a_false_sma_break(self) -> None:
        dates = pd.bdate_range("2026-07-01", periods=6)
        factors = pd.DataFrame(
            {
                "instrument_id": [1] * len(dates),
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
                "close_to_previous_close": [None, 1.01, 1.01, 1.01, 1.01, 0.95],
                "total_return_factor": [None, 1.01, 1.01, 1.01, 1.01, 1.0],
            }
        )

        result = PercentAboveMovingAverages().calculate(
            IndicatorContext(
                snapshot=self.snapshot,
                factors=factors,
                series_factors={"benchmark": pd.DataFrame({"trade_date": dates})},
            ),
            {},
        )

        latest = result.frame.iloc[-1]
        self.assertEqual(latest["eligible_sma_5"], 1)
        self.assertEqual(latest["above_sma_5"], 1)
        self.assertEqual(latest["percent_above_sma_5"], 100.0)
        self.assertIn("dividends", result.metadata["price_basis"])

    def test_sma_chain_restarts_and_requires_a_fresh_full_window(self) -> None:
        dates = pd.bdate_range("2026-07-01", periods=11)
        factors = pd.DataFrame(
            {
                "instrument_id": [1] * len(dates),
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
                "total_return_factor": [
                    None,
                    1.01,
                    1.01,
                    1.01,
                    1.01,
                    None,
                    1.01,
                    1.01,
                    1.01,
                    1.01,
                    1.01,
                ],
            }
        )

        levels = _total_return_levels(factors)
        result = PercentAboveMovingAverages().calculate(
            IndicatorContext(
                snapshot=self.snapshot,
                factors=factors,
                series_factors={"benchmark": pd.DataFrame({"trade_date": dates})},
            ),
            {},
        )

        self.assertTrue(pd.isna(levels.loc[5, "total_return_level"]))
        self.assertEqual(levels.loc[6, "total_return_level"], 100.0)
        self.assertEqual(levels.loc[5, "chain_id"] + 1, levels.loc[6, "chain_id"])
        self.assertEqual(result.frame.loc[dates[9], "eligible_sma_5"], 0)
        self.assertEqual(result.frame.loc[dates[10], "eligible_sma_5"], 1)

    def test_sma_breadth_withholds_a_broad_quote_outage(self) -> None:
        dates = pd.bdate_range("2026-07-01", periods=6)
        instruments = tuple(
            SnapshotInstrument(index, f"S{index}", f"S{index}.AX", f"Stock {index}")
            for index in range(1, 26)
        )
        snapshot = Snapshot(
            snapshot_id=8,
            universe_code="TEST",
            universe_name="Test universe",
            as_of_date=date(2026, 6, 30),
            source_path="holdings.xlsx",
            instruments=instruments,
        )
        rows: list[dict[str, object]] = []
        for instrument in instruments:
            instrument_dates = dates if instrument.instrument_id <= 5 else dates[:-1]
            for position, trade_date in enumerate(instrument_dates):
                rows.append(
                    {
                        "instrument_id": instrument.instrument_id,
                        "trade_date": trade_date,
                        "previous_trade_date": (
                            instrument_dates[position - 1] if position else pd.NaT
                        ),
                        "total_return_factor": 1.01 if position else None,
                    }
                )

        frame = (
            PercentAboveMovingAverages()
            .calculate(
                IndicatorContext(
                    snapshot=snapshot,
                    factors=pd.DataFrame(rows),
                    series_factors={"benchmark": pd.DataFrame({"trade_date": dates})},
                ),
                {},
            )
            .frame
        )

        self.assertTrue(frame.loc[dates[-2], "quality_ok"])
        self.assertEqual(frame.loc[dates[-2], "percent_above_sma_5"], 100.0)
        self.assertFalse(frame.loc[dates[-1], "quality_ok"])
        self.assertTrue(pd.isna(frame.loc[dates[-1], "percent_above_sma_5"]))

    def test_identical_stock_and_index_returns_have_zero_dispersion(self) -> None:
        dates = pd.bdate_range("2026-01-01", periods=121)
        factors = [None, *(1.01 if index % 2 else 0.99 for index in range(1, 121))]
        rows = pd.DataFrame(
            {
                "instrument_id": [1] * len(dates),
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
                "total_return_factor": factors,
            }
        )
        benchmark = rows.drop(columns="instrument_id")

        frame = (
            RealizedDispersion()
            .calculate(
                IndicatorContext(
                    snapshot=self.snapshot,
                    factors=rows,
                    series_factors={"benchmark": benchmark},
                ),
                {},
            )
            .frame
        )

        for window in (21, 63, 120):
            latest = frame.iloc[-1]
            self.assertAlmostEqual(
                latest[f"average_stock_vol_{window}"],
                latest[f"index_vol_{window}"],
            )
            self.assertAlmostEqual(latest[f"dispersion_{window}"], 0.0)

    def test_dispersion_is_average_stock_vol_minus_index_vol(self) -> None:
        dates = pd.bdate_range("2026-06-01", periods=22)
        stock_factors = {
            1: [None, *(1.03 if index % 2 else 0.97 for index in range(1, 22))],
            2: [None, *(0.99 if index % 2 else 1.01 for index in range(1, 22))],
        }
        rows = [
            {
                "instrument_id": instrument_id,
                "trade_date": trade_date,
                "previous_trade_date": dates[position - 1] if position else pd.NaT,
                "total_return_factor": factors[position],
            }
            for instrument_id, factors in stock_factors.items()
            for position, trade_date in enumerate(dates)
        ]
        index_factors = [
            None,
            *(1.002 if index % 2 else 0.998 for index in range(1, 22)),
        ]
        benchmark = pd.DataFrame(
            {
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
                "total_return_factor": index_factors,
            }
        )
        snapshot = Snapshot(
            snapshot_id=9,
            universe_code="TEST",
            universe_name="Test universe",
            as_of_date=date(2026, 5, 29),
            source_path="holdings.xlsx",
            instruments=(
                self.instrument,
                SnapshotInstrument(2, "BBB", "BBB.AX", "BBB"),
            ),
        )

        latest = (
            RealizedDispersion()
            .calculate(
                IndicatorContext(
                    snapshot=snapshot,
                    factors=pd.DataFrame(rows),
                    series_factors={"benchmark": benchmark},
                ),
                {},
            )
            .frame.iloc[-1]
        )

        stock_vols = [
            pd.Series(np.log(factors[1:])).std(ddof=1) * np.sqrt(252) * 100
            for factors in stock_factors.values()
        ]
        index_vol = (
            pd.Series(np.log(index_factors[1:])).std(ddof=1) * np.sqrt(252) * 100
        )
        average_stock_vol = np.mean(stock_vols)
        expected_dispersion = average_stock_vol - index_vol
        self.assertAlmostEqual(latest["average_stock_vol_21"], average_stock_vol)
        self.assertAlmostEqual(latest["index_vol_21"], index_vol)
        self.assertAlmostEqual(latest["dispersion_21"], expected_dispersion)
        self.assertEqual(latest["eligible_issues_21"], 2)

    def test_halt_does_not_poison_later_dispersion_windows(self) -> None:
        dates = pd.bdate_range("2026-05-01", periods=25)
        rows: list[dict[str, object]] = []
        for instrument_id in (1, 2):
            instrument_dates = dates if instrument_id == 1 else dates.delete(10)
            for position, trade_date in enumerate(instrument_dates):
                rows.append(
                    {
                        "instrument_id": instrument_id,
                        "trade_date": trade_date,
                        "previous_trade_date": (
                            instrument_dates[position - 1] if position else pd.NaT
                        ),
                        "total_return_factor": (
                            None if position == 0 else 1.01 + 0.001 * (position % 2)
                        ),
                    }
                )
        benchmark = pd.DataFrame(
            {
                "trade_date": dates,
                "previous_trade_date": [pd.NaT, *dates[:-1]],
                "total_return_factor": [
                    None,
                    *(1.001 + 0.0001 * (index % 2) for index in range(1, 25)),
                ],
            }
        )
        snapshot = Snapshot(
            snapshot_id=10,
            universe_code="TEST",
            universe_name="Test universe",
            as_of_date=date(2026, 4, 30),
            source_path="holdings.xlsx",
            instruments=(
                self.instrument,
                SnapshotInstrument(2, "BBB", "BBB.AX", "BBB"),
            ),
        )

        frame = (
            RealizedDispersion()
            .calculate(
                IndicatorContext(
                    snapshot=snapshot,
                    factors=pd.DataFrame(rows),
                    series_factors={"benchmark": benchmark},
                ),
                {},
            )
            .frame
        )

        self.assertEqual(frame.loc[dates[10], "quoted_issues"], 1)
        self.assertEqual(frame.loc[dates[11], "quoted_issues"], 1)
        self.assertTrue(frame.loc[dates[11], "quality_ok"])
        self.assertEqual(frame.iloc[-1]["eligible_issues_21"], 2)
        self.assertTrue(pd.notna(frame.iloc[-1]["dispersion_21"]))

    def test_average_correlation_is_mean_of_pairwise_pearson_coefficients(
        self,
    ) -> None:
        dates = pd.bdate_range("2026-06-01", periods=22)
        base_returns = np.array([0.01, -0.015, 0.005, 0.02, -0.01, 0.012, -0.004] * 3)
        log_returns = {
            1: base_returns,
            2: base_returns,
            3: -base_returns,
        }
        rows = [
            {
                "instrument_id": instrument_id,
                "trade_date": trade_date,
                "previous_trade_date": dates[position - 1] if position else pd.NaT,
                "total_return_factor": (
                    None if position == 0 else 1.0 + returns[position - 1]
                ),
            }
            for instrument_id, returns in log_returns.items()
            for position, trade_date in enumerate(dates)
        ]
        snapshot = Snapshot(
            snapshot_id=11,
            universe_code="TEST",
            universe_name="Test universe",
            as_of_date=date(2026, 5, 29),
            source_path="holdings.xlsx",
            instruments=(
                self.instrument,
                SnapshotInstrument(2, "BBB", "BBB.AX", "BBB"),
                SnapshotInstrument(3, "CCC", "CCC.AX", "CCC"),
            ),
        )

        latest = (
            AverageCorrelation()
            .calculate(
                IndicatorContext(
                    snapshot=snapshot,
                    factors=pd.DataFrame(rows),
                    series_factors={"benchmark": pd.DataFrame({"trade_date": dates})},
                ),
                {},
            )
            .frame.iloc[-1]
        )

        # The three pairs are +1, -1, and -1: their arithmetic mean is -1/3.
        self.assertAlmostEqual(latest["average_correlation_21"], -100.0 / 3.0)
        self.assertEqual(latest["eligible_issues_21"], 3)
        self.assertEqual(latest["eligible_pairs_21"], 3)

    def test_halt_does_not_poison_later_average_correlation(self) -> None:
        dates = pd.bdate_range("2026-05-01", periods=26)
        rows: list[dict[str, object]] = []
        for instrument_id in (1, 2):
            instrument_dates = dates if instrument_id == 1 else dates.delete(10)
            for position, trade_date in enumerate(instrument_dates):
                simple_return = 0.005 * ((position % 5) - 2)
                rows.append(
                    {
                        "instrument_id": instrument_id,
                        "trade_date": trade_date,
                        "previous_trade_date": (
                            instrument_dates[position - 1] if position else pd.NaT
                        ),
                        "total_return_factor": (
                            None if position == 0 else 1.0 + simple_return
                        ),
                    }
                )
        snapshot = Snapshot(
            snapshot_id=12,
            universe_code="TEST",
            universe_name="Test universe",
            as_of_date=date(2026, 4, 30),
            source_path="holdings.xlsx",
            instruments=(
                self.instrument,
                SnapshotInstrument(2, "BBB", "BBB.AX", "BBB"),
            ),
        )

        frame = (
            AverageCorrelation()
            .calculate(
                IndicatorContext(
                    snapshot=snapshot,
                    factors=pd.DataFrame(rows),
                    series_factors={"benchmark": pd.DataFrame({"trade_date": dates})},
                ),
                {},
            )
            .frame
        )

        self.assertEqual(frame.loc[dates[10], "quoted_issues"], 1)
        self.assertEqual(frame.loc[dates[11], "quoted_issues"], 1)
        self.assertEqual(frame.iloc[-1]["eligible_issues_21"], 2)
        self.assertEqual(frame.iloc[-1]["eligible_pairs_21"], 1)
        self.assertTrue(pd.notna(frame.iloc[-1]["average_correlation_21"]))

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
            membership=membership,
            series_factors={"benchmark": pd.DataFrame({"trade_date": dates})},
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
            series_factors={"benchmark": pd.DataFrame({"trade_date": dates})},
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
