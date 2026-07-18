"""Data-transfer objects shared across the ingestion boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Holding:
    local_symbol: str
    provider_symbol: str
    name: str
    sector: str | None
    country_code: str | None
    weight: float | None
    market_value: float | None
    units: float | None


@dataclass(frozen=True, slots=True)
class HoldingsFile:
    path: Path
    as_of_date: date
    fund_name: str
    holdings: tuple[Holding, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class SnapshotInstrument:
    instrument_id: int
    local_symbol: str
    provider_symbol: str
    name: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    snapshot_id: int
    universe_code: str
    universe_name: str
    as_of_date: date
    source_path: str
    instruments: tuple[SnapshotInstrument, ...]
    benchmark: SnapshotInstrument | None = None

    @property
    def all_instruments(self) -> tuple[SnapshotInstrument, ...]:
        items = list(self.instruments)
        known = {instrument.instrument_id for instrument in items}
        if self.benchmark is not None and self.benchmark.instrument_id not in known:
            items.append(self.benchmark)
        return tuple(items)


@dataclass(frozen=True, slots=True)
class SyncTarget:
    """An instrument plus the date boundary its cache must cover."""

    instrument: SnapshotInstrument
    active: bool
    # Inclusive final active date for an outgoing constituent. Active
    # constituents and the benchmark use the run's current session boundary.
    required_end: date | None = None


@dataclass(frozen=True, slots=True)
class SyncOptions:
    lookback_sessions: int = 1000
    batch_size: int = 20
    threads: int = 2
    retries: int = 4
    timeout: float = 30.0
    batch_pause: float = 1.25
    base_backoff: float = 5.0
