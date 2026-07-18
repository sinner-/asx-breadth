"""SQLite schema and repository operations."""

from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterator, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from .models import (
    HoldingsFile,
    Snapshot,
    SnapshotInstrument,
    SnapshotSeries,
    SyncTarget,
)


SCHEMA_VERSION = 2
SYDNEY = ZoneInfo("Australia/Sydney")


SCHEMA = """
CREATE TABLE IF NOT EXISTS universes (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    exchange TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_snapshots (
    id INTEGER PRIMARY KEY,
    universe_id INTEGER NOT NULL REFERENCES universes(id),
    as_of_date TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    UNIQUE (universe_id, source_sha256)
);

CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL,
    local_symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    sector TEXT,
    country_code TEXT,
    UNIQUE (provider, provider_symbol)
);

CREATE TABLE IF NOT EXISTS snapshot_holdings (
    snapshot_id INTEGER NOT NULL REFERENCES universe_snapshots(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    weight REAL,
    market_value REAL,
    units REAL,
    PRIMARY KEY (snapshot_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS universe_series (
    universe_id INTEGER NOT NULL REFERENCES universes(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    role TEXT NOT NULL,
    PRIMARY KEY (universe_id, role)
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    requested_start TEXT NOT NULL,
    requested_end TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);

-- Every provider response is retained. Repeated anchor rows are intentional:
-- they preserve exactly what Yahoo reported at each adjustment epoch.
CREATE TABLE IF NOT EXISTS provider_observations (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    adjusted_close REAL,
    volume REAL,
    dividend REAL,
    split_ratio REAL,
    repaired INTEGER NOT NULL DEFAULT 0,
    observed_at TEXT NOT NULL,
    UNIQUE (run_id, instrument_id, trade_date)
);

-- Factors are scale-invariant canonical inputs. Corrections are applied only
-- from internally consistent overlaps and every replaced version is audited.
CREATE TABLE IF NOT EXISTS daily_factors (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    trade_date TEXT NOT NULL,
    previous_trade_date TEXT,
    open_to_previous_close REAL,
    high_to_previous_close REAL,
    low_to_previous_close REAL,
    close_to_previous_close REAL,
    total_return_factor REAL,
    volume REAL,
    source_run_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (instrument_id, trade_date)
);

CREATE TABLE IF NOT EXISTS factor_revisions (
    id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    trade_date TEXT NOT NULL,
    previous_trade_date TEXT,
    open_to_previous_close REAL,
    high_to_previous_close REAL,
    low_to_previous_close REAL,
    close_to_previous_close REAL,
    total_return_factor REAL,
    volume REAL,
    source_run_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    replaced_by_run_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    reason TEXT NOT NULL,
    revised_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id),
    provider TEXT NOT NULL,
    backfill_attempted INTEGER NOT NULL DEFAULT 0,
    checked_through TEXT,
    latest_trade_date TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    retry_after TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history_watermarks (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id),
    checked_from TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- A late listing legitimately has no rows near the requested history target,
-- but a truncated provider response can look identical. Require the same
-- observed beginning on two separate fetch runs before accepting it as the
-- instrument's natural history bound.
CREATE TABLE IF NOT EXISTS history_probe_evidence (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id),
    target_start TEXT NOT NULL,
    earliest_returned TEXT NOT NULL,
    confirmations INTEGER NOT NULL,
    last_run_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS empty_history_probe_evidence (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id),
    target_start TEXT NOT NULL,
    confirmations INTEGER NOT NULL,
    last_run_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_requirements (
    id INTEGER PRIMARY KEY,
    universe_id INTEGER NOT NULL REFERENCES universes(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    snapshot_id INTEGER NOT NULL REFERENCES universe_snapshots(id),
    required_end TEXT NOT NULL,
    history_start TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    satisfied_at TEXT,
    UNIQUE (snapshot_id, instrument_id)
);

-- History depth and forward freshness are separate obligations. A successful
-- request for one must not clear the retry/cooldown state of the other.
CREATE TABLE IF NOT EXISTS sync_obligation_state (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    obligation_type TEXT NOT NULL CHECK (obligation_type IN ('history', 'forward')),
    checked_through TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    retry_after TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (instrument_id, obligation_type)
);

-- Sparse and empty forward responses are useful evidence for a halt/delisting,
-- but cannot advance a watermark until independently repeated.
CREATE TABLE IF NOT EXISTS forward_probe_evidence (
    instrument_id INTEGER PRIMARY KEY REFERENCES instruments(id),
    target_end TEXT NOT NULL,
    response_signature TEXT NOT NULL,
    confirmations INTEGER NOT NULL,
    last_run_id INTEGER NOT NULL REFERENCES fetch_runs(id),
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observations_instrument_date
    ON provider_observations (instrument_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_factors_date
    ON daily_factors (trade_date);
CREATE INDEX IF NOT EXISTS idx_factor_revisions_instrument_date
    ON factor_revisions (instrument_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_snapshot_holdings_instrument
    ON snapshot_holdings (instrument_id);
CREATE INDEX IF NOT EXISTS idx_sync_requirements_pending
    ON sync_requirements (status, instrument_id);
CREATE INDEX IF NOT EXISTS idx_sync_obligation_retry
    ON sync_obligation_state (obligation_type, retry_after);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        schema_version = int(
            self.connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if schema_version > SCHEMA_VERSION:
            self.connection.close()
            raise RuntimeError(
                f"Cache schema {schema_version} is newer than this program "
                f"(supports {SCHEMA_VERSION})"
            )
        # Version 0 covers both a new database and caches made before explicit
        # schema versioning. Migrations remain additive so an interrupted
        # upgrade can safely be opened again.
        self.connection.executescript(SCHEMA)
        # Verify additive invariants even when user_version was already bumped:
        # a process can be interrupted between individual DDL statements.
        self._migrate(schema_version)
        if schema_version < SCHEMA_VERSION:
            self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        # `_migrate` is deliberately run for an apparently-current database so
        # a partially applied v2 is self-healing. Persist those repairs even
        # when the version number itself did not need changing.
        self.connection.commit()

    def _migrate(self, schema_version: int) -> None:
        del schema_version
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(sync_requirements)"
            ).fetchall()
        }
        if "history_start" not in columns:
            self.connection.execute(
                "ALTER TABLE sync_requirements ADD COLUMN history_start TEXT"
            )
        # A v1 removal may have been marked satisfied using forward coverage
        # alone. Reopen it once so v2 can attach and verify its history bound.
        self.connection.execute(
            """
            UPDATE sync_requirements
            SET status = 'pending', satisfied_at = NULL
            WHERE status = 'satisfied' AND history_start IS NULL
            """
        )
        # Preserve the meaning of the old monolithic state as the initial
        # forward state. History watermarks already persist completed depth.
        self.connection.execute(
            """
            INSERT OR IGNORE INTO sync_obligation_state
                (instrument_id, obligation_type, checked_through,
                 consecutive_failures, retry_after, last_error, updated_at)
            SELECT instrument_id, 'forward', checked_through,
                   consecutive_failures, retry_after, last_error, updated_at
            FROM sync_state
            """
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def snapshot_exists(
        self,
        universe_code: str,
        source_sha256: str,
        *,
        as_of_date: date | None = None,
    ) -> bool:
        date_clause = " AND s.as_of_date = ?" if as_of_date is not None else ""
        parameters: tuple[object, ...] = (universe_code, source_sha256)
        if as_of_date is not None:
            parameters += (as_of_date.isoformat(),)
        row = self.connection.execute(
            f"""
            SELECT 1
            FROM universe_snapshots s
            JOIN universes u ON u.id = s.universe_id
            WHERE u.code = ? AND s.source_sha256 = ?
            {date_clause}
            """,
            parameters,
        ).fetchone()
        return row is not None

    def snapshot_admission_issues(
        self,
        holdings_file: HoldingsFile,
        *,
        universe_code: str,
    ) -> list[str]:
        issues: list[str] = []
        holdings = holdings_file.holdings
        symbols = {holding.provider_symbol for holding in holdings}
        if holdings_file.as_of_date > datetime.now(SYDNEY).date():
            issues.append(f"holdings date {holdings_file.as_of_date} is in the future")
        source_date = self.connection.execute(
            """
            SELECT s.as_of_date
            FROM universe_snapshots s
            JOIN universes u ON u.id = s.universe_id
            WHERE u.code = ? AND s.source_sha256 = ?
            LIMIT 1
            """,
            (universe_code, holdings_file.sha256),
        ).fetchone()
        if (
            source_date is not None
            and source_date["as_of_date"] != holdings_file.as_of_date.isoformat()
        ):
            issues.append(
                "the same workbook bytes were already imported with effective date "
                f"{source_date['as_of_date']}, not {holdings_file.as_of_date}"
            )
        minimum_count = 200 if universe_code == "VAS" else 2
        if len(symbols) < minimum_count:
            issues.append(
                f"only {len(symbols)} distinct holdings; expected at least {minimum_count}"
            )
        if len(symbols) != len(holdings):
            issues.append("multiple workbook rows map to the same provider symbol")

        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            known_names = {
                row["provider_symbol"]: row["name"]
                for row in self.connection.execute(
                    f"""
                    SELECT provider_symbol, name FROM instruments
                    WHERE provider = 'yahoo'
                      AND provider_symbol IN ({placeholders})
                    """,
                    tuple(symbols),
                ).fetchall()
            }
            for holding in holdings:
                known = known_names.get(holding.provider_symbol)
                if (
                    known
                    and SequenceMatcher(
                        None,
                        known.casefold(),
                        holding.name.casefold(),
                    ).ratio()
                    < 0.35
                ):
                    issues.append(
                        f"{holding.provider_symbol} changed identity from "
                        f"{known!r} to {holding.name!r}"
                    )

        weights = [holding.weight for holding in holdings if holding.weight is not None]
        if len(weights) >= len(holdings) * 0.9:
            total_weight = sum(weights)
            if not 0.97 <= total_weight <= 1.03:
                issues.append(
                    f"portfolio weights sum to {total_weight:.2%}, not about 100%"
                )

        latest = self.connection.execute(
            """
            SELECT s.id, s.as_of_date
            FROM universe_snapshots s
            JOIN universes u ON u.id = s.universe_id
            WHERE u.code = ?
            ORDER BY s.as_of_date DESC, s.id DESC
            LIMIT 1
            """,
            (universe_code,),
        ).fetchone()
        if latest is None:
            return issues
        latest_date = date.fromisoformat(latest["as_of_date"])
        if holdings_file.as_of_date < latest_date:
            issues.append(
                f"holdings date {holdings_file.as_of_date} predates latest snapshot {latest_date}"
            )
        previous_symbols = {
            row["provider_symbol"]
            for row in self.connection.execute(
                """
                SELECT i.provider_symbol
                FROM snapshot_holdings h
                JOIN instruments i ON i.id = h.instrument_id
                WHERE h.snapshot_id = ?
                """,
                (latest["id"],),
            ).fetchall()
        }
        changed = len(symbols.symmetric_difference(previous_symbols))
        denominator = max(len(symbols), len(previous_symbols), 1)
        if changed / denominator > 0.25:
            issues.append(
                f"composition changes {changed}/{denominator} symbols versus latest snapshot"
            )
        return issues

    def import_snapshot(
        self,
        holdings_file: HoldingsFile,
        *,
        universe_code: str,
        universe_name: str,
        benchmark_symbol: str | None = None,
    ) -> Snapshot:
        if holdings_file.as_of_date > datetime.now(SYDNEY).date():
            raise ValueError(
                f"Holdings date {holdings_file.as_of_date} is in the future"
            )
        existing = self.connection.execute(
            """
            SELECT s.id, s.as_of_date
            FROM universe_snapshots s
            JOIN universes u ON u.id = s.universe_id
            WHERE u.code = ? AND s.source_sha256 = ?
            LIMIT 1
            """,
            (universe_code, holdings_file.sha256),
        ).fetchone()
        if existing is not None:
            existing_date = date.fromisoformat(existing["as_of_date"])
            if existing_date != holdings_file.as_of_date:
                raise ValueError(
                    "The same workbook was already imported with effective date "
                    f"{existing_date}; refusing to reinterpret it as "
                    f"{holdings_file.as_of_date}"
                )
            # Snapshots are immutable evidence. Re-importing the exact source
            # cannot silently mutate membership after parser changes.
            return self.snapshot(int(existing["id"]))
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO universes (code, name, exchange, created_at)
                VALUES (?, ?, 'ASX', ?)
                ON CONFLICT (code) DO UPDATE SET name = excluded.name
                """,
                (universe_code, universe_name, now),
            )
            universe_id = connection.execute(
                "SELECT id FROM universes WHERE code = ?", (universe_code,)
            ).fetchone()["id"]
            snapshot_insert = connection.execute(
                """
                INSERT INTO universe_snapshots
                    (universe_id, as_of_date, source_path, source_sha256, imported_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (universe_id, source_sha256) DO NOTHING
                """,
                (
                    universe_id,
                    holdings_file.as_of_date.isoformat(),
                    str(holdings_file.path),
                    holdings_file.sha256,
                    now,
                ),
            )
            created_snapshot = snapshot_insert.rowcount == 1
            snapshot_id = connection.execute(
                """
                SELECT id FROM universe_snapshots
                WHERE universe_id = ? AND source_sha256 = ?
                """,
                (universe_id, holdings_file.sha256),
            ).fetchone()["id"]

            qualified_benchmark = (benchmark_symbol or universe_code).strip().upper()
            if "." not in qualified_benchmark:
                qualified_benchmark = f"{qualified_benchmark}.AX"
            local_benchmark = qualified_benchmark.removesuffix(".AX")
            connection.execute(
                """
                INSERT INTO instruments
                    (exchange, local_symbol, provider, provider_symbol, name)
                VALUES ('ASX', ?, 'yahoo', ?, ?)
                ON CONFLICT (provider, provider_symbol) DO UPDATE SET
                    local_symbol = excluded.local_symbol,
                    name = excluded.name
                """,
                (local_benchmark, qualified_benchmark, universe_name),
            )
            benchmark_id = connection.execute(
                """
                SELECT id FROM instruments
                WHERE provider = 'yahoo' AND provider_symbol = ?
                """,
                (qualified_benchmark,),
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO universe_series (universe_id, instrument_id, role)
                VALUES (?, ?, 'benchmark')
                ON CONFLICT (universe_id, role) DO UPDATE SET
                    instrument_id = excluded.instrument_id
                """,
                (universe_id, benchmark_id),
            )

            for holding in holdings_file.holdings:
                connection.execute(
                    """
                    INSERT INTO instruments
                        (exchange, local_symbol, provider, provider_symbol, name,
                         sector, country_code)
                    VALUES ('ASX', ?, 'yahoo', ?, ?, ?, ?)
                    ON CONFLICT (provider, provider_symbol) DO UPDATE SET
                        local_symbol = excluded.local_symbol,
                        name = excluded.name,
                        sector = excluded.sector,
                        country_code = excluded.country_code
                    """,
                    (
                        holding.local_symbol,
                        holding.provider_symbol,
                        holding.name,
                        holding.sector,
                        holding.country_code,
                    ),
                )
                instrument_id = connection.execute(
                    """
                    SELECT id FROM instruments
                    WHERE provider = 'yahoo' AND provider_symbol = ?
                    """,
                    (holding.provider_symbol,),
                ).fetchone()["id"]
                connection.execute(
                    """
                    INSERT INTO snapshot_holdings
                        (snapshot_id, instrument_id, weight, market_value, units)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (snapshot_id, instrument_id) DO UPDATE SET
                        weight = excluded.weight,
                        market_value = excluded.market_value,
                        units = excluded.units
                    """,
                    (
                        snapshot_id,
                        instrument_id,
                        holding.weight,
                        holding.market_value,
                        holding.units,
                    ),
                )
            if created_snapshot:
                predecessor = connection.execute(
                    """
                    SELECT id
                    FROM universe_snapshots
                    WHERE universe_id = ?
                      AND (as_of_date < ? OR (as_of_date = ? AND id < ?))
                    ORDER BY as_of_date DESC, id DESC
                    LIMIT 1
                    """,
                    (
                        universe_id,
                        holdings_file.as_of_date.isoformat(),
                        holdings_file.as_of_date.isoformat(),
                        snapshot_id,
                    ),
                ).fetchone()
                if predecessor is not None:
                    outgoing = connection.execute(
                        """
                        SELECT instrument_id FROM snapshot_holdings
                        WHERE snapshot_id = ?
                        EXCEPT
                        SELECT instrument_id FROM snapshot_holdings
                        WHERE snapshot_id = ?
                        """,
                        (predecessor["id"], snapshot_id),
                    ).fetchall()
                    connection.executemany(
                        """
                        INSERT OR IGNORE INTO sync_requirements
                            (universe_id, instrument_id, snapshot_id,
                             required_end, status, created_at)
                        VALUES (?, ?, ?, ?, 'pending', ?)
                        """,
                        [
                            (
                                universe_id,
                                row["instrument_id"],
                                snapshot_id,
                                holdings_file.as_of_date.isoformat(),
                                now,
                            )
                            for row in outgoing
                        ],
                    )
        return self.snapshot(snapshot_id)

    def snapshot(self, snapshot_id: int) -> Snapshot:
        row = self.connection.execute(
            """
            SELECT s.id, s.as_of_date, s.source_path, u.id AS universe_id,
                   u.code, u.name
            FROM universe_snapshots s
            JOIN universes u ON u.id = s.universe_id
            WHERE s.id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown universe snapshot: {snapshot_id}")
        instruments = self.connection.execute(
            """
            SELECT i.id, i.local_symbol, i.provider_symbol, i.name
            FROM snapshot_holdings h
            JOIN instruments i ON i.id = h.instrument_id
            WHERE h.snapshot_id = ?
            ORDER BY i.provider_symbol
            """,
            (snapshot_id,),
        ).fetchall()
        benchmark_row = self.connection.execute(
            """
            SELECT i.id, i.local_symbol, i.provider_symbol, i.name
            FROM universe_series us
            JOIN instruments i ON i.id = us.instrument_id
            WHERE us.universe_id = ? AND us.role = 'benchmark'
            """,
            (row["universe_id"],),
        ).fetchone()
        benchmark = (
            SnapshotInstrument(
                instrument_id=benchmark_row["id"],
                local_symbol=benchmark_row["local_symbol"],
                provider_symbol=benchmark_row["provider_symbol"],
                name=benchmark_row["name"],
            )
            if benchmark_row is not None
            else None
        )
        auxiliary_rows = self.connection.execute(
            """
            SELECT us.role, i.id, i.local_symbol, i.provider_symbol, i.name
            FROM universe_series us
            JOIN instruments i ON i.id = us.instrument_id
            WHERE us.universe_id = ? AND us.role <> 'benchmark'
            ORDER BY us.role
            """,
            (row["universe_id"],),
        ).fetchall()
        return Snapshot(
            snapshot_id=row["id"],
            universe_code=row["code"],
            universe_name=row["name"],
            as_of_date=date.fromisoformat(row["as_of_date"]),
            source_path=row["source_path"],
            instruments=tuple(
                SnapshotInstrument(
                    instrument_id=item["id"],
                    local_symbol=item["local_symbol"],
                    provider_symbol=item["provider_symbol"],
                    name=item["name"],
                )
                for item in instruments
            ),
            benchmark=benchmark,
            auxiliary_series=tuple(
                SnapshotSeries(
                    role=item["role"],
                    instrument=SnapshotInstrument(
                        instrument_id=item["id"],
                        local_symbol=item["local_symbol"],
                        provider_symbol=item["provider_symbol"],
                        name=item["name"],
                    ),
                )
                for item in auxiliary_rows
            ),
        )

    def register_universe_series(
        self,
        snapshot_id: int,
        *,
        role: str,
        provider_symbol: str,
        local_symbol: str,
        name: str,
    ) -> Snapshot:
        """Attach a cached non-constituent series to a snapshot's universe.

        Series configuration belongs to the universe rather than a holdings
        snapshot. Re-registering a role is idempotent and makes a symbol change
        explicit without altering any historical composition evidence.
        """
        normalised_role = role.strip().lower()
        if (
            not normalised_role
            or not normalised_role[0].isalpha()
            or not normalised_role.replace("_", "").isalnum()
        ):
            raise ValueError(f"Invalid universe-series role: {role!r}")
        symbol = provider_symbol.strip().upper()
        local = local_symbol.strip().upper()
        series_name = name.strip()
        if not symbol or not local or not series_name:
            raise ValueError(
                "Universe-series symbol, local symbol, and name are required"
            )

        with self.transaction() as connection:
            universe = connection.execute(
                """
                SELECT s.universe_id
                FROM universe_snapshots s
                WHERE s.id = ?
                """,
                (snapshot_id,),
            ).fetchone()
            if universe is None:
                raise ValueError(f"Unknown universe snapshot: {snapshot_id}")
            connection.execute(
                """
                INSERT INTO instruments
                    (exchange, local_symbol, provider, provider_symbol, name)
                VALUES ('ASX', ?, 'yahoo', ?, ?)
                ON CONFLICT (provider, provider_symbol) DO UPDATE SET
                    local_symbol = excluded.local_symbol,
                    name = excluded.name
                """,
                (local, symbol, series_name),
            )
            instrument_id = connection.execute(
                """
                SELECT id FROM instruments
                WHERE provider = 'yahoo' AND provider_symbol = ?
                """,
                (symbol,),
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO universe_series (universe_id, instrument_id, role)
                VALUES (?, ?, ?)
                ON CONFLICT (universe_id, role) DO UPDATE SET
                    instrument_id = excluded.instrument_id
                """,
                (universe["universe_id"], instrument_id, normalised_role),
            )
        return self.snapshot(snapshot_id)

    def membership_for_snapshot(self, snapshot_id: int) -> pd.DataFrame:
        """Return effective-dated compositions known by the selected snapshot."""
        return pd.read_sql_query(
            """
            WITH target AS (
                SELECT id, universe_id, as_of_date
                FROM universe_snapshots
                WHERE id = ?
            ),
            ranked AS (
                SELECT s.id, s.as_of_date,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.as_of_date ORDER BY s.id DESC
                       ) AS date_rank
                FROM universe_snapshots s
                JOIN target t ON t.universe_id = s.universe_id
                WHERE s.as_of_date < t.as_of_date
                   OR (s.as_of_date = t.as_of_date AND s.id <= t.id)
            ),
            chosen AS (
                SELECT id, as_of_date FROM ranked WHERE date_rank = 1
            )
            SELECT c.id AS snapshot_id, c.as_of_date AS effective_from,
                   h.instrument_id
            FROM chosen c
            JOIN snapshot_holdings h ON h.snapshot_id = c.id
            ORDER BY c.as_of_date, c.id, h.instrument_id
            """,
            self.connection,
            params=(snapshot_id,),
        )

    def sync_targets_for_snapshot(self, snapshot_id: int) -> tuple[SyncTarget, ...]:
        """Return active series and unfinished outgoing date obligations."""
        rows = self.connection.execute(
            """
            WITH target AS (
                SELECT id, universe_id FROM universe_snapshots WHERE id = ?
            ), candidates AS (
                SELECT h.instrument_id, 1 AS active, NULL AS required_end
                FROM snapshot_holdings h
                JOIN target t ON t.id = h.snapshot_id
                UNION ALL
                SELECT us.instrument_id, 1 AS active, NULL AS required_end
                FROM universe_series us
                JOIN target t ON t.universe_id = us.universe_id
                UNION ALL
                SELECT r.instrument_id, 0 AS active, r.required_end
                FROM sync_requirements r
                JOIN target t ON t.universe_id = r.universe_id
                WHERE r.status = 'pending'
            )
            SELECT instrument_id, MAX(active) AS active,
                   MAX(required_end) AS required_end
            FROM candidates
            GROUP BY instrument_id
            """,
            (snapshot_id,),
        ).fetchall()
        instruments = {
            instrument.instrument_id: instrument
            for instrument in self.instruments_by_ids(
                [int(row["instrument_id"]) for row in rows]
            )
        }
        return tuple(
            SyncTarget(
                instrument=instruments[int(row["instrument_id"])],
                active=bool(row["active"]),
                required_end=(
                    date.fromisoformat(row["required_end"])
                    if not row["active"] and row["required_end"]
                    else None
                ),
            )
            for row in sorted(
                rows,
                key=lambda item: (
                    instruments[int(item["instrument_id"])].provider_symbol
                ),
            )
        )

    def satisfy_sync_requirements(
        self,
        instrument_id: int,
        *,
        checked_through: date,
        history_checked_from: date | None = None,
    ) -> None:
        """Close obligations covered by both date boundaries.

        ``history_checked_from=None`` retains the old repository helper's
        forward-only behaviour for callers outside the synchroniser. Production
        sync uses :meth:`satisfy_ready_sync_requirements`.
        """
        history_clause = ""
        parameters: tuple[object, ...] = (
            utc_now(),
            instrument_id,
            checked_through.isoformat(),
        )
        if history_checked_from is not None:
            history_clause = " AND history_start IS NOT NULL AND history_start >= ?"
            parameters += (history_checked_from.isoformat(),)
        self.connection.execute(
            f"""
            UPDATE sync_requirements
            SET status = 'satisfied', satisfied_at = ?
            WHERE instrument_id = ? AND status = 'pending'
              AND required_end < ?
              {history_clause}
            """,
            parameters,
        )
        self.connection.commit()

    def prepare_sync_requirements(
        self,
        instrument_id: int,
        *,
        calendar_days: int,
    ) -> date | None:
        """Persist and return the oldest history boundary for pending removals."""
        modifier = f"-{max(calendar_days, 1)} days"
        self.connection.execute(
            """
            UPDATE sync_requirements
            SET history_start = date(required_end, ?)
            WHERE instrument_id = ? AND status = 'pending'
              AND (history_start IS NULL
                   OR history_start > date(required_end, ?))
            """,
            (modifier, instrument_id, modifier),
        )
        row = self.connection.execute(
            """
            SELECT MIN(history_start) AS history_start
            FROM sync_requirements
            WHERE instrument_id = ? AND status = 'pending'
            """,
            (instrument_id,),
        ).fetchone()
        self.connection.commit()
        return (
            date.fromisoformat(row["history_start"])
            if row is not None and row["history_start"]
            else None
        )

    def satisfy_ready_sync_requirements(self, instrument_id: int) -> None:
        """Retire removals only after forward and history obligations are done."""
        self.connection.execute(
            """
            UPDATE sync_requirements
            SET status = 'satisfied', satisfied_at = ?
            WHERE instrument_id = ? AND status = 'pending'
              AND history_start IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM sync_obligation_state o
                  WHERE o.instrument_id = sync_requirements.instrument_id
                    AND o.obligation_type = 'forward'
                    AND o.checked_through IS NOT NULL
                    AND sync_requirements.required_end < o.checked_through
              )
              AND EXISTS (
                  SELECT 1 FROM history_watermarks h
                  WHERE h.instrument_id = sync_requirements.instrument_id
                    AND h.checked_from <= sync_requirements.history_start
              )
            """,
            (utc_now(), instrument_id),
        )
        self.connection.commit()

    def has_pending_sync_requirements(self, instrument_id: int) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM sync_requirements
            WHERE instrument_id = ? AND status = 'pending'
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()
        return row is not None

    def cached_trade_dates(
        self,
        instrument_id: int,
        *,
        start: date,
        end: date,
    ) -> frozenset[date]:
        rows = self.connection.execute(
            """
            SELECT trade_date FROM daily_factors
            WHERE instrument_id = ? AND trade_date >= ? AND trade_date < ?
            """,
            (instrument_id, start.isoformat(), end.isoformat()),
        ).fetchall()
        return frozenset(date.fromisoformat(row["trade_date"]) for row in rows)

    def instruments_by_ids(
        self, instrument_ids: Sequence[int]
    ) -> tuple[SnapshotInstrument, ...]:
        if not instrument_ids:
            return ()
        rows: list[sqlite3.Row] = []
        for start in range(0, len(instrument_ids), 900):
            chunk = instrument_ids[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                self.connection.execute(
                    f"""
                    SELECT id, local_symbol, provider_symbol, name
                    FROM instruments
                    WHERE id IN ({placeholders})
                    ORDER BY provider_symbol
                    """,
                    tuple(chunk),
                ).fetchall()
            )
        return tuple(
            SnapshotInstrument(
                instrument_id=row["id"],
                local_symbol=row["local_symbol"],
                provider_symbol=row["provider_symbol"],
                name=row["name"],
            )
            for row in sorted(rows, key=lambda row: row["provider_symbol"])
        )

    def sync_states(self, instrument_ids: Sequence[int]) -> dict[int, sqlite3.Row]:
        if not instrument_ids:
            return {}
        placeholders = ",".join("?" for _ in instrument_ids)
        rows = self.connection.execute(
            f"SELECT * FROM sync_state WHERE instrument_id IN ({placeholders})",
            tuple(instrument_ids),
        ).fetchall()
        return {row["instrument_id"]: row for row in rows}

    def obligation_states(
        self,
        instrument_ids: Sequence[int],
    ) -> dict[tuple[int, str], sqlite3.Row]:
        if not instrument_ids:
            return {}
        rows: list[sqlite3.Row] = []
        for start in range(0, len(instrument_ids), 900):
            chunk = instrument_ids[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                self.connection.execute(
                    f"""
                    SELECT * FROM sync_obligation_state
                    WHERE instrument_id IN ({placeholders})
                    """,
                    tuple(chunk),
                ).fetchall()
            )
        return {
            (int(row["instrument_id"]), str(row["obligation_type"])): row
            for row in rows
        }

    def earliest_trade_dates(self, instrument_ids: Sequence[int]) -> dict[int, date]:
        if not instrument_ids:
            return {}
        placeholders = ",".join("?" for _ in instrument_ids)
        rows = self.connection.execute(
            f"""
            SELECT instrument_id, MIN(trade_date) AS earliest
            FROM daily_factors
            WHERE instrument_id IN ({placeholders})
            GROUP BY instrument_id
            """,
            tuple(instrument_ids),
        ).fetchall()
        return {
            row["instrument_id"]: date.fromisoformat(row["earliest"]) for row in rows
        }

    def history_watermarks(self, instrument_ids: Sequence[int]) -> dict[int, date]:
        if not instrument_ids:
            return {}
        placeholders = ",".join("?" for _ in instrument_ids)
        rows = self.connection.execute(
            f"""
            SELECT instrument_id, checked_from
            FROM history_watermarks
            WHERE instrument_id IN ({placeholders})
            """,
            tuple(instrument_ids),
        ).fetchall()
        return {
            row["instrument_id"]: date.fromisoformat(row["checked_from"])
            for row in rows
        }

    def mark_history_checked(self, instrument_id: int, checked_from: date) -> None:
        self.connection.execute(
            """
            INSERT INTO history_watermarks (instrument_id, checked_from, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (instrument_id) DO UPDATE SET
                checked_from = MIN(history_watermarks.checked_from,
                                   excluded.checked_from),
                updated_at = excluded.updated_at
            """,
            (instrument_id, checked_from.isoformat(), utc_now()),
        )
        self.connection.commit()

    def record_history_probe(
        self,
        *,
        instrument_id: int,
        target_start: date,
        earliest_returned: date,
        run_id: int,
    ) -> date | None:
        """Return the verified history boundary, if this probe establishes one."""
        self.connection.execute(
            "DELETE FROM empty_history_probe_evidence WHERE instrument_id = ?",
            (instrument_id,),
        )
        if earliest_returned <= target_start + timedelta(days=7):
            self.connection.execute(
                "DELETE FROM history_probe_evidence WHERE instrument_id = ?",
                (instrument_id,),
            )
            self.connection.commit()
            return target_start

        row = self.connection.execute(
            """
            SELECT target_start, earliest_returned, confirmations, last_run_id
            FROM history_probe_evidence
            WHERE instrument_id = ?
            """,
            (instrument_id,),
        ).fetchone()
        previous_target = (
            date.fromisoformat(row["target_start"]) if row is not None else None
        )
        same_natural_bound = (
            row is not None
            and row["earliest_returned"] == earliest_returned.isoformat()
            and previous_target is not None
            and previous_target < earliest_returned
            and target_start < earliest_returned
        )
        confirmations = int(row["confirmations"]) + 1 if same_natural_bound else 1
        if row is not None and int(row["last_run_id"]) == run_id:
            confirmations = int(row["confirmations"])
        retained_target = (
            min(previous_target, target_start)
            if same_natural_bound and previous_target is not None
            else target_start
        )
        self.connection.execute(
            """
            INSERT INTO history_probe_evidence
                (instrument_id, target_start, earliest_returned, confirmations,
                 last_run_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (instrument_id) DO UPDATE SET
                target_start = excluded.target_start,
                earliest_returned = excluded.earliest_returned,
                confirmations = excluded.confirmations,
                last_run_id = excluded.last_run_id,
                updated_at = excluded.updated_at
            """,
            (
                instrument_id,
                retained_target.isoformat(),
                earliest_returned.isoformat(),
                confirmations,
                run_id,
                utc_now(),
            ),
        )
        self.connection.commit()
        return retained_target if confirmations >= 2 else None

    def record_empty_history_probe(
        self,
        *,
        instrument_id: int,
        target_start: date,
        run_id: int,
    ) -> date | None:
        """Confirm that Yahoo explicitly has no history for this instrument."""
        row = self.connection.execute(
            """
            SELECT target_start, confirmations, last_run_id
            FROM empty_history_probe_evidence
            WHERE instrument_id = ?
            """,
            (instrument_id,),
        ).fetchone()
        previous_target = (
            date.fromisoformat(row["target_start"]) if row is not None else None
        )
        confirmations = int(row["confirmations"]) + 1 if row is not None else 1
        if row is not None and int(row["last_run_id"]) == run_id:
            confirmations = int(row["confirmations"])
        retained_target = (
            min(previous_target, target_start)
            if previous_target is not None
            else target_start
        )
        self.connection.execute(
            """
            INSERT INTO empty_history_probe_evidence
                (instrument_id, target_start, confirmations, last_run_id,
                 updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (instrument_id) DO UPDATE SET
                target_start = excluded.target_start,
                confirmations = excluded.confirmations,
                last_run_id = excluded.last_run_id,
                updated_at = excluded.updated_at
            """,
            (
                instrument_id,
                retained_target.isoformat(),
                confirmations,
                run_id,
                utc_now(),
            ),
        )
        self.connection.commit()
        return retained_target if confirmations >= 2 else None

    def reopen_empty_history_if_data_arrived(self, instrument_id: int) -> None:
        """A later real quote invalidates an earlier confirmed no-data bound."""
        evidence = self.connection.execute(
            """
            SELECT confirmations FROM empty_history_probe_evidence
            WHERE instrument_id = ?
            """,
            (instrument_id,),
        ).fetchone()
        if evidence is None:
            return
        if int(evidence["confirmations"]) >= 2:
            self.connection.execute(
                "DELETE FROM history_watermarks WHERE instrument_id = ?",
                (instrument_id,),
            )
        self.connection.execute(
            "DELETE FROM empty_history_probe_evidence WHERE instrument_id = ?",
            (instrument_id,),
        )
        self.connection.commit()

    def record_forward_probe(
        self,
        *,
        instrument_id: int,
        target_end: date,
        response_signature: str,
        run_id: int,
    ) -> bool:
        """Return true after a sparse/empty horizon is independently repeated."""
        row = self.connection.execute(
            """
            SELECT target_end, response_signature, confirmations, last_run_id
            FROM forward_probe_evidence
            WHERE instrument_id = ?
            """,
            (instrument_id,),
        ).fetchone()
        same_evidence = (
            row is not None
            and row["response_signature"] == response_signature
            and date.fromisoformat(row["target_end"]) <= target_end
        )
        confirmations = int(row["confirmations"]) + 1 if same_evidence else 1
        if row is not None and int(row["last_run_id"]) == run_id:
            confirmations = int(row["confirmations"])
        self.connection.execute(
            """
            INSERT INTO forward_probe_evidence
                (instrument_id, target_end, response_signature, confirmations,
                 last_run_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (instrument_id) DO UPDATE SET
                target_end = excluded.target_end,
                response_signature = excluded.response_signature,
                confirmations = excluded.confirmations,
                last_run_id = excluded.last_run_id,
                updated_at = excluded.updated_at
            """,
            (
                instrument_id,
                target_end.isoformat(),
                response_signature,
                confirmations,
                run_id,
                utc_now(),
            ),
        )
        self.connection.commit()
        return confirmations >= 2

    def clear_forward_probe(self, instrument_id: int) -> None:
        self.connection.execute(
            "DELETE FROM forward_probe_evidence WHERE instrument_id = ?",
            (instrument_id,),
        )
        self.connection.commit()

    def start_fetch_run(self, start: date, end: date) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO fetch_runs
                (provider, started_at, requested_start, requested_end, status)
            VALUES ('yahoo', ?, ?, ?, 'running')
            """,
            (utc_now(), start.isoformat(), end.isoformat()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_fetch_run(
        self, run_id: int, status: str, error: str | None = None
    ) -> None:
        self.connection.execute(
            """
            UPDATE fetch_runs SET completed_at = ?, status = ?, error = ?
            WHERE id = ?
            """,
            (utc_now(), status, error, run_id),
        )
        self.connection.commit()

    def append_series(
        self,
        *,
        run_id: int,
        instrument_id: int,
        frame: pd.DataFrame,
    ) -> int:
        """Append observations and reconcile canonical factors from overlaps."""
        now = utc_now()
        observations: list[tuple[Any, ...]] = []
        factors: list[tuple[Any, ...]] = []
        ordered = frame.sort_index()
        previous: pd.Series | None = None
        previous_date: str | None = None
        for timestamp, row in ordered.iterrows():
            trade_date = pd.Timestamp(timestamp).date().isoformat()
            observations.append(
                (
                    run_id,
                    instrument_id,
                    trade_date,
                    _finite(row.get("open")),
                    _finite(row.get("high")),
                    _finite(row.get("low")),
                    _finite(row.get("close")),
                    _finite(row.get("adjusted_close")),
                    _finite(row.get("volume")),
                    _finite(row.get("dividend")) or 0.0,
                    _finite(row.get("split_ratio")) or 0.0,
                    int(bool(row.get("repaired", False))),
                    now,
                )
            )
            previous_close = (
                _finite(previous.get("close")) if previous is not None else None
            )
            previous_adjusted = (
                _finite(previous.get("adjusted_close"))
                if previous is not None
                else None
            )
            factors.append(
                (
                    instrument_id,
                    trade_date,
                    previous_date,
                    _ratio(row.get("open"), previous_close),
                    _ratio(row.get("high"), previous_close),
                    _ratio(row.get("low"), previous_close),
                    _ratio(row.get("close"), previous_close),
                    _ratio(row.get("adjusted_close"), previous_adjusted),
                    _finite(row.get("volume")),
                    run_id,
                    now,
                )
            )
            previous = row
            previous_date = trade_date

        with self.transaction() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO provider_observations
                    (run_id, instrument_id, trade_date, open, high, low, close,
                     adjusted_close, volume, dividend, split_ratio, repaired,
                     observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                observations,
            )
            existing = self._factor_rows(
                connection,
                instrument_id,
                [factor[1] for factor in factors],
            )
            cached_dates = {
                row["trade_date"]
                for row in connection.execute(
                    """
                    SELECT trade_date FROM daily_factors
                    WHERE instrument_id = ?
                    """,
                    (instrument_id,),
                ).fetchall()
            }
            candidate_dates = {factor[1] for factor in factors}
            timeline = sorted(cached_dates | candidate_dates)
            timeline_positions = {
                trade_date: position for position, trade_date in enumerate(timeline)
            }
            chronological_previous = {
                trade_date: timeline[position - 1] if position else None
                for position, trade_date in enumerate(timeline)
            }
            invalid_candidates = {
                factor[1]
                for factor in factors
                if factor[2] != chronological_previous[factor[1]]
            }
            candidate_by_date = {factor[1]: factor for factor in factors}
            # A leading response row has no pair. If it is not already a
            # cached anchor, rows derived from it cannot be committed with a
            # dangling previous_trade_date. Cascade that rejection until a
            # real cached anchor is reached.
            changed = True
            while changed:
                changed = False
                for factor in factors:
                    predecessor = factor[2]
                    if (
                        predecessor in invalid_candidates
                        and predecessor not in cached_dates
                        and factor[1] not in invalid_candidates
                    ):
                        invalid_candidates.add(factor[1])
                        changed = True
            # Inserting an interior date is safe only when the same response
            # also supplies its successor factor. Otherwise the stored
            # successor would still point around the new row.
            mutation_dates = set(candidate_dates - cached_dates)
            for trade_date in candidate_dates & cached_dates:
                current = existing.get(trade_date)
                candidate = candidate_by_date[trade_date]
                if (
                    current is not None
                    and current["total_return_factor"] is not None
                    and candidate[7] is not None
                    and _factor_changed(current, candidate)
                ):
                    mutation_dates.add(trade_date)
            for trade_date in mutation_dates:
                position = timeline_positions[trade_date]
                successor = (
                    timeline[position + 1] if position + 1 < len(timeline) else None
                )
                if successor in cached_dates and successor not in candidate_dates:
                    invalid_candidates.add(trade_date)
            # If the supplied successor is itself unusable, changing its
            # predecessor would still leave a half-repaired chain.
            changed = True
            while changed:
                changed = False
                for trade_date in mutation_dates - invalid_candidates:
                    position = timeline_positions[trade_date]
                    successor = (
                        timeline[position + 1] if position + 1 < len(timeline) else None
                    )
                    if successor in invalid_candidates:
                        invalid_candidates.add(trade_date)
                        changed = True
            mutations = 0
            for factor in factors:
                if factor[1] in invalid_candidates:
                    continue
                current = existing.get(factor[1])
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO daily_factors
                            (instrument_id, trade_date, previous_trade_date,
                             open_to_previous_close, high_to_previous_close,
                             low_to_previous_close, close_to_previous_close,
                             total_return_factor, volume, source_run_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        factor,
                    )
                    mutations += 1
                    continue
                # The first row of a provider response has no predecessor. It
                # must never erase an established factor. All later overlap
                # rows are internally consistent pairs and can safely repair a
                # null seam, inserted interior date, or genuine provider fix.
                if factor[7] is None or not _factor_changed(current, factor):
                    continue
                connection.execute(
                    """
                    INSERT INTO factor_revisions
                        (instrument_id, trade_date, previous_trade_date,
                         open_to_previous_close, high_to_previous_close,
                         low_to_previous_close, close_to_previous_close,
                         total_return_factor, volume, source_run_id,
                         replaced_by_run_id, reason, revised_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        instrument_id,
                        current["trade_date"],
                        current["previous_trade_date"],
                        current["open_to_previous_close"],
                        current["high_to_previous_close"],
                        current["low_to_previous_close"],
                        current["close_to_previous_close"],
                        current["total_return_factor"],
                        current["volume"],
                        current["source_run_id"],
                        run_id,
                        "null seam"
                        if current["total_return_factor"] is None
                        else "overlap correction",
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE daily_factors SET
                        previous_trade_date = ?, open_to_previous_close = ?,
                        high_to_previous_close = ?, low_to_previous_close = ?,
                        close_to_previous_close = ?, total_return_factor = ?,
                        volume = ?, source_run_id = ?, created_at = ?
                    WHERE instrument_id = ? AND trade_date = ?
                    """,
                    (
                        factor[2],
                        factor[3],
                        factor[4],
                        factor[5],
                        factor[6],
                        factor[7],
                        factor[8],
                        factor[9],
                        factor[10],
                        factor[0],
                        factor[1],
                    ),
                )
                mutations += 1
        return mutations

    def _factor_rows(
        self,
        connection: sqlite3.Connection,
        instrument_id: int,
        trade_dates: Sequence[str],
    ) -> dict[str, sqlite3.Row]:
        rows: list[sqlite3.Row] = []
        for start in range(0, len(trade_dates), 900):
            chunk = trade_dates[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                connection.execute(
                    f"""
                    SELECT * FROM daily_factors
                    WHERE instrument_id = ? AND trade_date IN ({placeholders})
                    """,
                    (instrument_id, *chunk),
                ).fetchall()
            )
        return {row["trade_date"]: row for row in rows}

    def update_sync_success(
        self,
        *,
        instrument_id: int,
        checked_through: date,
        latest_trade_date: date | None,
        backfill_attempted: bool,
        obligation_type: str = "forward",
    ) -> None:
        if obligation_type not in {"history", "forward"}:
            raise ValueError(f"Unknown sync obligation: {obligation_type}")
        now = utc_now()
        forward_checked = (
            checked_through.isoformat() if obligation_type == "forward" else None
        )
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sync_state
                    (instrument_id, provider, backfill_attempted, checked_through,
                     latest_trade_date, consecutive_failures, retry_after,
                     last_error, updated_at)
                VALUES (?, 'yahoo', ?, ?, ?, 0, NULL, NULL, ?)
                ON CONFLICT (instrument_id) DO UPDATE SET
                    backfill_attempted = MAX(sync_state.backfill_attempted,
                                             excluded.backfill_attempted),
                    checked_through = CASE
                        WHEN excluded.checked_through IS NULL
                            THEN sync_state.checked_through
                        WHEN sync_state.checked_through IS NULL
                            THEN excluded.checked_through
                        ELSE MAX(sync_state.checked_through,
                                 excluded.checked_through)
                    END,
                    latest_trade_date = CASE
                        WHEN excluded.latest_trade_date IS NULL
                            THEN sync_state.latest_trade_date
                        WHEN sync_state.latest_trade_date IS NULL
                            THEN excluded.latest_trade_date
                        ELSE MAX(sync_state.latest_trade_date,
                                 excluded.latest_trade_date)
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    instrument_id,
                    int(backfill_attempted),
                    forward_checked,
                    latest_trade_date.isoformat() if latest_trade_date else None,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO sync_obligation_state
                    (instrument_id, obligation_type, checked_through,
                     consecutive_failures, retry_after, last_error, updated_at)
                VALUES (?, ?, ?, 0, NULL, NULL, ?)
                ON CONFLICT (instrument_id, obligation_type) DO UPDATE SET
                    checked_through = CASE
                        WHEN excluded.checked_through IS NULL
                            THEN sync_obligation_state.checked_through
                        WHEN sync_obligation_state.checked_through IS NULL
                            THEN excluded.checked_through
                        ELSE MAX(sync_obligation_state.checked_through,
                                 excluded.checked_through)
                    END,
                    consecutive_failures = 0,
                    retry_after = NULL,
                    last_error = NULL,
                    updated_at = excluded.updated_at
                """,
                (instrument_id, obligation_type, forward_checked, now),
            )
            self._refresh_legacy_sync_status(connection, instrument_id, now)

    def update_sync_failure(
        self,
        *,
        instrument_id: int,
        checked_through: date,
        backfill_attempted: bool,
        error: str,
        retry_after: datetime,
        obligation_type: str = "forward",
    ) -> None:
        if obligation_type not in {"history", "forward"}:
            raise ValueError(f"Unknown sync obligation: {obligation_type}")
        now = utc_now()
        forward_checked = (
            checked_through.isoformat() if obligation_type == "forward" else None
        )
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sync_state
                    (instrument_id, provider, backfill_attempted, checked_through,
                     consecutive_failures, retry_after, last_error, updated_at)
                VALUES (?, 'yahoo', ?, ?, 0, NULL, NULL, ?)
                ON CONFLICT (instrument_id) DO UPDATE SET
                    backfill_attempted = MAX(sync_state.backfill_attempted,
                                             excluded.backfill_attempted),
                    checked_through = CASE
                        WHEN excluded.checked_through IS NULL
                            THEN sync_state.checked_through
                        WHEN sync_state.checked_through IS NULL
                            THEN excluded.checked_through
                        ELSE MAX(sync_state.checked_through,
                                 excluded.checked_through)
                    END,
                    updated_at = excluded.updated_at
                """,
                (instrument_id, int(backfill_attempted), forward_checked, now),
            )
            connection.execute(
                """
                INSERT INTO sync_obligation_state
                    (instrument_id, obligation_type, checked_through,
                     consecutive_failures, retry_after, last_error, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT (instrument_id, obligation_type) DO UPDATE SET
                    checked_through = CASE
                        WHEN excluded.checked_through IS NULL
                            THEN sync_obligation_state.checked_through
                        WHEN sync_obligation_state.checked_through IS NULL
                            THEN excluded.checked_through
                        ELSE MAX(sync_obligation_state.checked_through,
                                 excluded.checked_through)
                    END,
                    consecutive_failures =
                        sync_obligation_state.consecutive_failures + 1,
                    retry_after = excluded.retry_after,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    instrument_id,
                    obligation_type,
                    forward_checked,
                    retry_after.isoformat(timespec="seconds"),
                    error[:1000],
                    now,
                ),
            )
            self._refresh_legacy_sync_status(connection, instrument_id, now)

    @staticmethod
    def _refresh_legacy_sync_status(
        connection: sqlite3.Connection,
        instrument_id: int,
        now: str,
    ) -> None:
        outstanding = connection.execute(
            """
            SELECT consecutive_failures, retry_after, last_error
            FROM sync_obligation_state
            WHERE instrument_id = ? AND consecutive_failures > 0
            ORDER BY updated_at DESC, obligation_type
            """,
            (instrument_id,),
        ).fetchall()
        failures = max(
            (int(row["consecutive_failures"]) for row in outstanding),
            default=0,
        )
        retry_after = max(
            (row["retry_after"] for row in outstanding if row["retry_after"]),
            default=None,
        )
        last_error = outstanding[0]["last_error"] if outstanding else None
        connection.execute(
            """
            UPDATE sync_state SET consecutive_failures = ?, retry_after = ?,
                                  last_error = ?, updated_at = ?
            WHERE instrument_id = ?
            """,
            (failures, retry_after, last_error, now, instrument_id),
        )

    def factors_for_snapshot(self, snapshot_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            """
            SELECT f.instrument_id, f.trade_date, f.previous_trade_date,
                   f.open_to_previous_close, f.high_to_previous_close,
                   f.low_to_previous_close, f.close_to_previous_close,
                   f.total_return_factor, f.volume, i.provider_symbol
            FROM daily_factors f
            JOIN snapshot_holdings h ON h.instrument_id = f.instrument_id
            JOIN instruments i ON i.id = f.instrument_id
            WHERE h.snapshot_id = ?
            ORDER BY f.trade_date, f.instrument_id
            """,
            self.connection,
            params=(snapshot_id,),
        )

    def factors_for_membership(self, membership: pd.DataFrame) -> pd.DataFrame:
        if membership.empty:
            return pd.DataFrame()
        instrument_ids = (
            membership["instrument_id"].astype(int).drop_duplicates().tolist()
        )
        frames: list[pd.DataFrame] = []
        for start in range(0, len(instrument_ids), 900):
            chunk = instrument_ids[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            frames.append(
                pd.read_sql_query(
                    f"""
                    SELECT f.instrument_id, f.trade_date, f.previous_trade_date,
                           f.open_to_previous_close, f.high_to_previous_close,
                           f.low_to_previous_close, f.close_to_previous_close,
                           f.total_return_factor, f.volume, i.provider_symbol
                    FROM daily_factors f
                    JOIN instruments i ON i.id = f.instrument_id
                    WHERE f.instrument_id IN ({placeholders})
                    """,
                    self.connection,
                    params=tuple(chunk),
                )
            )
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values(["trade_date", "instrument_id"])
            .reset_index(drop=True)
        )

    def factors_for_instrument(self, instrument_id: int | None) -> pd.DataFrame:
        if instrument_id is None:
            return pd.DataFrame()
        return pd.read_sql_query(
            """
            SELECT f.instrument_id, f.trade_date, f.previous_trade_date,
                   f.open_to_previous_close, f.high_to_previous_close,
                   f.low_to_previous_close, f.close_to_previous_close,
                   f.total_return_factor, f.volume, i.provider_symbol
            FROM daily_factors f
            JOIN instruments i ON i.id = f.instrument_id
            WHERE f.instrument_id = ?
            ORDER BY f.trade_date
            """,
            self.connection,
            params=(instrument_id,),
        )

    def latest_adjusted_close(self, instrument_id: int | None) -> float | None:
        if instrument_id is None:
            return None
        row = self.connection.execute(
            """
            SELECT o.adjusted_close
            FROM provider_observations o
            JOIN daily_factors f
              ON f.instrument_id = o.instrument_id
             AND f.trade_date = o.trade_date
            WHERE o.instrument_id = ? AND o.adjusted_close IS NOT NULL
            ORDER BY f.trade_date DESC, o.observed_at DESC, o.id DESC
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()
        return float(row["adjusted_close"]) if row is not None else None

    def sync_summary(self, snapshot_id: int) -> dict[str, Any]:
        latest = self.connection.execute(
            """
            SELECT MAX(f.trade_date) AS trade_date
            FROM universe_snapshots s
            JOIN universe_series us
              ON us.universe_id = s.universe_id AND us.role = 'benchmark'
            JOIN daily_factors f ON f.instrument_id = us.instrument_id
            WHERE s.id = ?
            """,
            (snapshot_id,),
        ).fetchone()["trade_date"]
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS holdings,
                   SUM(CASE WHEN hw.checked_from IS NOT NULL THEN 1 ELSE 0 END)
                       AS with_history,
                   SUM(CASE WHEN s.last_error IS NOT NULL THEN 1 ELSE 0 END)
                       AS provider_failures
            FROM snapshot_holdings h
            LEFT JOIN sync_state s ON s.instrument_id = h.instrument_id
            LEFT JOIN history_watermarks hw
              ON hw.instrument_id = h.instrument_id
            WHERE h.snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        unavailable = self.connection.execute(
            """
            SELECT i.local_symbol
            FROM snapshot_holdings h
            JOIN instruments i ON i.id = h.instrument_id
            LEFT JOIN daily_factors f
              ON f.instrument_id = h.instrument_id
             AND f.trade_date = ?
             AND f.total_return_factor IS NOT NULL
            WHERE h.snapshot_id = ? AND f.instrument_id IS NULL
            ORDER BY i.local_symbol
            """,
            (latest, snapshot_id),
        ).fetchall()
        result: dict[str, Any] = {
            key: int(row[key] or 0)
            for key in ("holdings", "with_history", "provider_failures")
        }
        result.update(
            {
                "latest_session": latest,
                "quoted_latest": result["holdings"] - len(unavailable),
                "unavailable_symbols": tuple(
                    item["local_symbol"] for item in unavailable
                ),
            }
        )
        return result


def _finite(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return (
        number
        if pd.notna(number) and number not in (float("inf"), float("-inf"))
        else None
    )


def _ratio(value: Any, denominator: float | None) -> float | None:
    numerator = _finite(value)
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _factor_changed(current: sqlite3.Row, candidate: tuple[Any, ...]) -> bool:
    if current["previous_trade_date"] != candidate[2]:
        return True
    for column, position in (
        ("open_to_previous_close", 3),
        ("high_to_previous_close", 4),
        ("low_to_previous_close", 5),
        ("close_to_previous_close", 6),
        ("total_return_factor", 7),
        ("volume", 8),
    ):
        old = current[column]
        new = candidate[position]
        if old is None or new is None:
            if old is not new:
                return True
            continue
        if not math.isclose(float(old), float(new), rel_tol=1e-9, abs_tol=1e-12):
            return True
    return False
