# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "openpyxl>=3.1.5,<4",
#   "pandas>=2.2,<4",
#   "plotly>=6.0,<7",
#   "yfinance>=1.4,<2",
# ]
# ///
"""White-box persistence and synchronisation regressions."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from asx_breadth import cli  # noqa: E402
from asx_breadth.db import Database  # noqa: E402
from asx_breadth.models import (  # noqa: E402
    Holding,
    HoldingsFile,
    SnapshotInstrument,
    SyncOptions,
    SyncTarget,
)
from asx_breadth.providers.yahoo import YahooProvider, _normalise  # noqa: E402
from asx_breadth.sync import synchronise  # noqa: E402


class MigrationAndAdmissionTests(unittest.TestCase):
    def test_repairs_v2_cache_and_splits_legacy_sync_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.sqlite3"
            database = Database(path)
            database.connection.execute(
                """
                INSERT INTO instruments
                    (exchange, local_symbol, provider, provider_symbol, name)
                VALUES ('ASX', 'AAA', 'yahoo', 'AAA.AX', 'AAA')
                """
            )
            instrument_id = database.connection.execute(
                "SELECT id FROM instruments"
            ).fetchone()["id"]
            database.connection.execute(
                """
                CREATE TABLE sync_state (
                    instrument_id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL,
                    backfill_attempted INTEGER NOT NULL DEFAULT 0,
                    checked_through TEXT,
                    latest_trade_date TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    retry_after TEXT,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            database.connection.execute(
                """
                INSERT INTO sync_state
                    (instrument_id, provider, backfill_attempted, checked_through,
                     latest_trade_date, consecutive_failures, retry_after,
                     last_error, updated_at)
                VALUES (?, 'yahoo', 1, '2026-07-10', '2026-07-09', 2,
                        '2026-07-20T00:00:00+00:00', 'old failure',
                        '2026-07-10T00:00:00+00:00')
                """,
                (instrument_id,),
            )
            database.connection.execute(
                "DELETE FROM sync_obligation_state WHERE instrument_id = ?",
                (instrument_id,),
            )
            database.connection.execute(
                "DELETE FROM instrument_sync_state WHERE instrument_id = ?",
                (instrument_id,),
            )
            database.connection.execute(
                """
                INSERT INTO universes (code, name, exchange, created_at)
                VALUES ('TEST', 'TEST', 'ASX', '2026-07-01T00:00:00+00:00')
                """
            )
            universe_id = database.connection.execute(
                "SELECT id FROM universes"
            ).fetchone()["id"]
            database.connection.execute(
                """
                INSERT INTO universe_snapshots
                    (universe_id, as_of_date, source_path, source_sha256, imported_at)
                VALUES (?, '2026-07-10', 'old.xlsx', 'old',
                        '2026-07-10T00:00:00+00:00')
                """,
                (universe_id,),
            )
            snapshot_id = database.connection.execute(
                "SELECT id FROM universe_snapshots"
            ).fetchone()["id"]
            database.connection.execute(
                """
                INSERT INTO sync_requirements
                    (universe_id, instrument_id, snapshot_id, required_end,
                     status, created_at, satisfied_at)
                VALUES (?, ?, ?, '2026-07-10', 'satisfied',
                        '2026-07-10T00:00:00+00:00',
                        '2026-07-11T00:00:00+00:00')
                """,
                (universe_id, instrument_id, snapshot_id),
            )
            database.connection.commit()
            database.close()

            raw = sqlite3.connect(path)
            raw.execute("ALTER TABLE sync_requirements DROP COLUMN history_start")
            raw.execute("PRAGMA user_version = 2")
            raw.commit()
            raw.close()

            repaired = Database(path)
            try:
                columns = {
                    row["name"]
                    for row in repaired.connection.execute(
                        "PRAGMA table_info(sync_requirements)"
                    )
                }
                state = repaired.obligation_states([instrument_id])[
                    (instrument_id, "forward")
                ]
                self.assertIn("history_start", columns)
                self.assertEqual(state["checked_through"], "2026-07-10")
                self.assertEqual(state["consecutive_failures"], 2)
                instrument_state = repaired.instrument_sync_states([instrument_id])[
                    instrument_id
                ]
                self.assertEqual(instrument_state["latest_trade_date"], "2026-07-09")
                self.assertEqual(instrument_state["backfill_attempted"], 1)
                legacy_table = repaired.connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'sync_state'
                    """
                ).fetchone()
                self.assertIsNone(legacy_table)
                requirement = repaired.connection.execute(
                    "SELECT status FROM sync_requirements"
                ).fetchone()["status"]
                self.assertEqual(requirement, "pending")
            finally:
                repaired.close()

            # Repairs for a partially upgraded database must survive close;
            # checking only the migration connection can hide an uncommitted
            # ALTER/INSERT sequence.
            reopened = Database(path)
            try:
                columns = {
                    row["name"]
                    for row in reopened.connection.execute(
                        "PRAGMA table_info(sync_requirements)"
                    )
                }
                state = reopened.obligation_states([instrument_id])[
                    (instrument_id, "forward")
                ]
                requirement = reopened.connection.execute(
                    "SELECT status FROM sync_requirements"
                ).fetchone()["status"]
                self.assertIn("history_start", columns)
                self.assertEqual(state["checked_through"], "2026-07-10")
                self.assertEqual(requirement, "pending")
            finally:
                reopened.close()

    def test_duplicate_source_is_immutable_and_date_reinterpretation_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "cache.sqlite3")
            try:
                original = _holdings(date(2026, 7, 1), "same-hash", ("AAA",))
                first = database.import_snapshot(
                    original,
                    universe_code="TEST",
                    universe_name="TEST",
                )
                changed_parse = _holdings(date(2026, 7, 1), "same-hash", ("AAA", "BBB"))
                duplicate = database.import_snapshot(
                    changed_parse,
                    universe_code="TEST",
                    universe_name="TEST",
                )
                self.assertEqual(duplicate.snapshot_id, first.snapshot_id)
                self.assertEqual(
                    [item.local_symbol for item in duplicate.instruments], ["AAA"]
                )
                with self.assertRaisesRegex(ValueError, "already imported"):
                    database.import_snapshot(
                        _holdings(date(2026, 7, 2), "same-hash", ("AAA",)),
                        universe_code="TEST",
                        universe_name="TEST",
                    )
            finally:
                database.close()

    def test_hard_cli_guards_ignore_allow_suspicious(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache.sqlite3"
            unseen = _holdings(date.today(), "unseen", ("AAA",))
            with patch.object(cli, "parse_holdings", return_value=unseen):
                self.assertEqual(
                    cli.main(
                        [
                            str(root / "anything.xlsx"),
                            "--db",
                            str(cache),
                            "--no-download",
                            "--allow-suspicious-holdings",
                        ]
                    ),
                    2,
                )

            existing_db = Database(cache)
            existing_db.import_snapshot(
                _holdings(date.today() - timedelta(days=2), "known", ("AAA",)),
                universe_code="VAS",
                universe_name="VAS",
            )
            existing_db.close()
            changed_date = _holdings(
                date.today() - timedelta(days=1), "known", ("AAA",)
            )
            with patch.object(cli, "parse_holdings", return_value=changed_date):
                self.assertEqual(
                    cli.main(
                        [
                            str(root / "anything.xlsx"),
                            "--db",
                            str(cache),
                            "--allow-suspicious-holdings",
                        ]
                    ),
                    2,
                )

            future = _holdings(date.today() + timedelta(days=1), "future", ("AAA",))
            with patch.object(cli, "parse_holdings", return_value=future):
                self.assertEqual(
                    cli.main(
                        [
                            str(root / "anything.xlsx"),
                            "--db",
                            str(cache),
                            "--allow-suspicious-holdings",
                        ]
                    ),
                    2,
                )

    def test_future_snapshot_is_hard_rejected_by_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "cache.sqlite3")
            try:
                with self.assertRaisesRegex(ValueError, "future"):
                    database.import_snapshot(
                        _holdings(
                            date.today() + timedelta(days=1),
                            "future",
                            ("AAA",),
                        ),
                        universe_code="TEST",
                        universe_name="TEST",
                    )
            finally:
                database.close()


class FactorAndProviderTests(unittest.TestCase):
    def test_provider_marks_only_per_symbol_empty_in_a_usable_batch(self) -> None:
        fields = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
        columns = pd.MultiIndex.from_product([["AAA.AX", "BBB.AX"], fields])
        values = [[10.0] * len(fields) + [float("nan")] * len(fields)]
        raw = pd.DataFrame(
            values,
            index=pd.to_datetime(["2026-07-01"]),
            columns=columns,
        )
        provider = YahooProvider()
        with patch("asx_breadth.providers.yahoo.yf.download", return_value=raw):
            result = provider.fetch(
                ["AAA.AX", "BBB.AX"],
                start=date(2026, 7, 1),
                end=date(2026, 7, 2),
                threads=1,
                timeout=1,
            )
        self.assertEqual(len(result["AAA.AX"]), 1)
        self.assertTrue(result["BBB.AX"].empty)

        with patch(
            "asx_breadth.providers.yahoo.yf.download",
            return_value=pd.DataFrame(),
        ):
            self.assertEqual(
                provider.fetch(
                    ["AAA.AX"],
                    start=date(2026, 7, 1),
                    end=date(2026, 7, 2),
                    threads=1,
                    timeout=1,
                ),
                {},
            )

    def test_missing_optional_market_fields_remain_null(self) -> None:
        raw = pd.DataFrame(
            {"Close": [10.0], "Adj Close": [9.5]},
            index=pd.to_datetime(["2026-07-01"]),
        )
        frame = _normalise(raw, start=date(2026, 7, 1), end=date(2026, 7, 2))
        self.assertEqual(len(frame), 1)
        self.assertTrue(
            frame.loc[pd.Timestamp("2026-07-01"), "open"] is pd.NA
            or pd.isna(frame.iloc[0]["open"])
        )
        self.assertTrue(pd.isna(frame.iloc[0]["high"]))
        self.assertTrue(pd.isna(frame.iloc[0]["low"]))
        self.assertTrue(pd.isna(frame.iloc[0]["volume"]))
        self.assertEqual(frame.iloc[0]["dividend"], 0.0)
        self.assertEqual(frame.iloc[0]["split_ratio"], 0.0)

    def test_truncated_leading_candidates_cannot_create_dangling_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, instrument_id = _database_with_instrument(Path(directory))
            try:
                first = database.start_fetch_run(date(2026, 7, 1), date(2026, 7, 2))
                database.append_series(
                    run_id=first,
                    instrument_id=instrument_id,
                    frame=_frame(["2026-07-01"], [100.0]),
                )
                truncated = database.start_fetch_run(date(2026, 7, 2), date(2026, 7, 4))
                database.append_series(
                    run_id=truncated,
                    instrument_id=instrument_id,
                    frame=_frame(["2026-07-02", "2026-07-03"], [101.0, 102.0]),
                )
                dates = database.connection.execute(
                    "SELECT trade_date FROM daily_factors ORDER BY trade_date"
                ).fetchall()
                dangling = database.connection.execute(
                    """
                    SELECT COUNT(*) FROM daily_factors f
                    WHERE f.previous_trade_date IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM daily_factors p
                          WHERE p.instrument_id = f.instrument_id
                            AND p.trade_date = f.previous_trade_date
                      )
                    """
                ).fetchone()[0]
                self.assertEqual([row["trade_date"] for row in dates], ["2026-07-01"])
                self.assertEqual(dangling, 0)
            finally:
                database.close()

    def test_correction_requires_cached_successor_in_same_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, instrument_id = _database_with_instrument(Path(directory))
            try:
                first = database.start_fetch_run(date(2026, 7, 1), date(2026, 7, 4))
                database.append_series(
                    run_id=first,
                    instrument_id=instrument_id,
                    frame=_frame(
                        ["2026-07-01", "2026-07-02", "2026-07-03"],
                        [100.0, 101.0, 102.0],
                    ),
                )
                correction = database.start_fetch_run(
                    date(2026, 7, 1), date(2026, 7, 3)
                )
                revised = _frame(["2026-07-01", "2026-07-02"], [100.0, 99.0])
                database.append_series(
                    run_id=correction,
                    instrument_id=instrument_id,
                    frame=revised,
                )
                factor = database.connection.execute(
                    """
                    SELECT total_return_factor FROM daily_factors
                    WHERE instrument_id = ? AND trade_date = '2026-07-02'
                    """,
                    (instrument_id,),
                ).fetchone()[0]
                revisions = database.connection.execute(
                    "SELECT COUNT(*) FROM factor_revisions"
                ).fetchone()[0]
                self.assertAlmostEqual(factor, 1.01)
                self.assertEqual(revisions, 0)
            finally:
                database.close()


class ObligationTests(unittest.TestCase):
    def test_history_failure_survives_forward_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, instrument_id = _database_with_instrument(Path(directory))
            try:
                database.update_sync_failure(
                    instrument_id=instrument_id,
                    checked_through=date(2026, 7, 1),
                    backfill_attempted=False,
                    error="history failed",
                    retry_after=datetime(2026, 7, 20, tzinfo=UTC),
                    obligation_type="history",
                )
                database.update_sync_success(
                    instrument_id=instrument_id,
                    checked_through=date(2026, 7, 18),
                    latest_trade_date=date(2026, 7, 17),
                    backfill_attempted=False,
                    obligation_type="forward",
                )
                states = database.obligation_states([instrument_id])
                self.assertEqual(
                    states[(instrument_id, "history")]["consecutive_failures"], 1
                )
                self.assertIsNotNone(states[(instrument_id, "history")]["retry_after"])
                self.assertEqual(
                    states[(instrument_id, "forward")]["consecutive_failures"], 0
                )
                instrument_state = database.instrument_sync_states([instrument_id])[
                    instrument_id
                ]
                self.assertNotIn("last_error", instrument_state.keys())
            finally:
                database.close()

    def test_outgoing_waits_for_verified_history_after_forward_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "cache.sqlite3")
            try:
                first = database.import_snapshot(
                    _holdings(date(2026, 7, 1), "first", ("AAA",)),
                    universe_code="TEST",
                    universe_name="TEST",
                )
                second = database.import_snapshot(
                    _holdings(date(2026, 7, 8), "second", ("BBB",)),
                    universe_code="TEST",
                    universe_name="TEST",
                )
                target = next(
                    item
                    for item in database.sync_targets_for_snapshot(second.snapshot_id)
                    if item.instrument.local_symbol == "AAA"
                )
                options = _options()
                first_provider = _Provider(
                    [_frame(["2026-06-20", "2026-07-08"], [90.0, 91.0])]
                )
                synchronise(
                    database,
                    (target,),
                    options,
                    now=datetime(2026, 7, 17, 8, tzinfo=UTC),
                    provider=first_provider,
                )
                self.assertTrue(
                    database.has_pending_sync_requirements(
                        first.instruments[0].instrument_id
                    )
                )
                forward = database.obligation_states(
                    [first.instruments[0].instrument_id]
                )[(first.instruments[0].instrument_id, "forward")]
                self.assertEqual(forward["checked_through"], "2026-07-09")

                second_provider = _Provider([_frame(["2026-06-20"], [90.0])])
                synchronise(
                    database,
                    (target,),
                    options,
                    now=datetime(2026, 7, 18, 8, tzinfo=UTC),
                    provider=second_provider,
                )
                self.assertFalse(
                    database.has_pending_sync_requirements(
                        first.instruments[0].instrument_id
                    )
                )
            finally:
                database.close()

    def test_explicit_empty_outgoing_retires_only_after_both_confirmations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "cache.sqlite3")
            try:
                first = database.import_snapshot(
                    _holdings(date(2026, 7, 1), "first-empty", ("AAA",)),
                    universe_code="TEST",
                    universe_name="TEST",
                )
                second = database.import_snapshot(
                    _holdings(date(2026, 7, 8), "second-empty", ("BBB",)),
                    universe_code="TEST",
                    universe_name="TEST",
                )
                target = next(
                    item
                    for item in database.sync_targets_for_snapshot(second.snapshot_id)
                    if item.instrument.local_symbol == "AAA"
                )
                first_report = synchronise(
                    database,
                    (target,),
                    _options(),
                    now=datetime(2026, 7, 17, 8, tzinfo=UTC),
                    provider=_Provider([pd.DataFrame()]),
                )
                self.assertEqual(first_report.failed, 1)
                self.assertTrue(
                    database.has_pending_sync_requirements(
                        first.instruments[0].instrument_id
                    )
                )

                second_report = synchronise(
                    database,
                    (target,),
                    _options(),
                    now=datetime(2026, 7, 18, 8, tzinfo=UTC),
                    provider=_Provider([pd.DataFrame()]),
                )
                self.assertEqual(second_report.successful, 1)
                self.assertFalse(
                    database.has_pending_sync_requirements(
                        first.instruments[0].instrument_id
                    )
                )
            finally:
                database.close()

    def test_truncated_leading_overlap_cannot_advance_forward_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, instrument_id = _database_with_instrument(Path(directory))
            try:
                run_id = database.start_fetch_run(date(2026, 7, 1), date(2026, 7, 5))
                database.append_series(
                    run_id=run_id,
                    instrument_id=instrument_id,
                    frame=_frame(
                        ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"],
                        [98.0, 99.0, 100.0, 101.0],
                    ),
                )
                database.update_sync_success(
                    instrument_id=instrument_id,
                    checked_through=date(2026, 7, 11),
                    latest_trade_date=date(2026, 7, 4),
                    backfill_attempted=True,
                )
                database.mark_history_checked(instrument_id, date(2026, 5, 1))
                response = _frame(
                    ["2026-07-03", "2026-07-04", "2026-07-17"],
                    [100.0, 101.0, 104.0],
                )
                report = synchronise(
                    database,
                    (
                        SyncTarget(
                            SnapshotInstrument(instrument_id, "AAA", "AAA.AX", "AAA"),
                            active=True,
                        ),
                    ),
                    _options(),
                    now=datetime(2026, 7, 17, 8, tzinfo=UTC),
                    provider=_Provider([response]),
                )
                checked = database.obligation_states([instrument_id])[
                    (instrument_id, "forward")
                ]["checked_through"]
                self.assertEqual(report.failed, 1)
                self.assertEqual(checked, "2026-07-11")
            finally:
                database.close()

    def test_sparse_and_empty_forward_ranges_need_independent_confirmation(
        self,
    ) -> None:
        for empty in (False, True):
            with self.subTest(empty=empty), tempfile.TemporaryDirectory() as directory:
                database, instrument_id = _database_with_instrument(Path(directory))
                try:
                    initial = database.start_fetch_run(
                        date(2026, 7, 3), date(2026, 7, 5)
                    )
                    database.append_series(
                        run_id=initial,
                        instrument_id=instrument_id,
                        frame=_frame(["2026-07-03", "2026-07-04"], [100.0, 101.0]),
                    )
                    database.update_sync_success(
                        instrument_id=instrument_id,
                        checked_through=date(2026, 7, 11),
                        latest_trade_date=date(2026, 7, 4),
                        backfill_attempted=True,
                    )
                    database.mark_history_checked(instrument_id, date(2026, 5, 1))
                    target = SyncTarget(
                        SnapshotInstrument(instrument_id, "AAA", "AAA.AX", "AAA"),
                        active=True,
                    )
                    response = (
                        pd.DataFrame()
                        if empty
                        else _frame(
                            ["2026-07-03", "2026-07-04", "2026-07-17"],
                            [100.0, 101.0, 104.0],
                        )
                    )
                    first = synchronise(
                        database,
                        (target,),
                        _options(),
                        now=datetime(2026, 7, 17, 8, tzinfo=UTC),
                        provider=_Provider([response]),
                    )
                    checked = database.obligation_states([instrument_id])[
                        (instrument_id, "forward")
                    ]["checked_through"]
                    self.assertEqual(first.failed, 1)
                    self.assertEqual(checked, "2026-07-11")

                    second = synchronise(
                        database,
                        (target,),
                        _options(),
                        now=datetime(2026, 7, 18, 8, tzinfo=UTC),
                        provider=_Provider([response]),
                    )
                    checked = database.obligation_states([instrument_id])[
                        (instrument_id, "forward")
                    ]["checked_through"]
                    self.assertEqual(second.successful, 1)
                    self.assertGreater(checked, "2026-07-11")
                finally:
                    database.close()


class _Provider:
    def __init__(self, frames: list[pd.DataFrame]):
        self.frames = frames
        self.calls = 0

    def fetch(self, symbols: list[str], **_: object) -> dict[str, pd.DataFrame]:
        frame = self.frames[min(self.calls, len(self.frames) - 1)]
        self.calls += 1
        return {symbol: frame.copy() for symbol in symbols}


def _options() -> SyncOptions:
    return SyncOptions(
        lookback_sessions=10,
        batch_size=10,
        retries=1,
        batch_pause=0,
        base_backoff=0,
    )


def _database_with_instrument(directory: Path) -> tuple[Database, int]:
    database = Database(directory / "cache.sqlite3")
    database.connection.execute(
        """
        INSERT INTO instruments
            (exchange, local_symbol, provider, provider_symbol, name)
        VALUES ('ASX', 'AAA', 'yahoo', 'AAA.AX', 'AAA')
        """
    )
    instrument_id = database.connection.execute(
        "SELECT id FROM instruments"
    ).fetchone()["id"]
    database.connection.commit()
    return database, int(instrument_id)


def _holdings(
    as_of_date: date,
    sha256: str,
    symbols: tuple[str, ...],
) -> HoldingsFile:
    return HoldingsFile(
        path=Path(f"{sha256}.xlsx"),
        as_of_date=as_of_date,
        fund_name="TEST",
        holdings=tuple(
            Holding(
                local_symbol=symbol,
                provider_symbol=f"{symbol}.AX",
                name=symbol,
                sector=None,
                country_code="AU",
                weight=None,
                market_value=None,
                units=None,
            )
            for symbol in symbols
        ),
        sha256=sha256,
    )


def _frame(dates: list[str], prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "adjusted_close": prices,
            "volume": [100.0] * len(dates),
            "dividend": [0.0] * len(dates),
            "split_ratio": [0.0] * len(dates),
            "repaired": [False] * len(dates),
        },
        index=pd.to_datetime(dates),
    )


if __name__ == "__main__":
    unittest.main()
