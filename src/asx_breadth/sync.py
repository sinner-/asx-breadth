"""Rate-conscious incremental synchronisation into append-only factors."""

from __future__ import annotations

import hashlib
import logging
import math
import random
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as wall_time, timedelta
from itertools import islice
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

import pandas as pd

from .db import Database
from .models import SnapshotInstrument, SyncOptions, SyncTarget
from .providers.yahoo import YahooProvider


SYDNEY = ZoneInfo("Australia/Sydney")
MIN_PROVIDER_OUTAGE_SYMBOLS = 10


@dataclass(frozen=True, slots=True)
class SyncReport:
    requested: int
    skipped_current: int
    skipped_backoff: int
    successful: int
    factor_changes: int
    failed: int
    completed_through: date
    provider_outage: bool = False
    provider_outage_requests: int = 0


@dataclass(frozen=True, slots=True)
class _Request:
    instrument: SnapshotInstrument
    start: date
    end: date
    is_backfill: bool
    covers_forward: bool = False
    history_target: date | None = None
    expected_anchor: date | None = None
    expected_cached_dates: frozenset[date] = frozenset()


@dataclass(frozen=True, slots=True)
class _PlanningContext:
    database: Database
    run_end: date
    calendar_days: int
    now_utc: datetime
    instrument_states: dict[int, sqlite3.Row]
    obligation_states: dict[tuple[int, str], sqlite3.Row]
    earliest_dates: dict[int, date]
    history_watermarks: dict[int, date]


@dataclass(frozen=True, slots=True)
class _TargetPlan:
    requests: tuple[_Request, ...]
    skipped_backoff: int = 0


@dataclass(frozen=True, slots=True)
class _SyncPlan:
    groups: dict[tuple[date, date], list[_Request]]
    instrument_states: dict[int, sqlite3.Row]
    obligation_states: dict[tuple[int, str], sqlite3.Row]
    skipped_current: int
    skipped_backoff: int


@dataclass(frozen=True, slots=True)
class _ExecutionResult:
    successful: int = 0
    factor_changes: int = 0
    failed: int = 0
    provider_outage_requests: int = 0

    def __add__(self, other: _ExecutionResult) -> _ExecutionResult:
        return _ExecutionResult(
            successful=self.successful + other.successful,
            factor_changes=self.factor_changes + other.factor_changes,
            failed=self.failed + other.failed,
            provider_outage_requests=(
                self.provider_outage_requests + other.provider_outage_requests
            ),
        )


@dataclass(frozen=True, slots=True)
class _FetchContext:
    provider: YahooProvider
    requests: dict[str, _Request]
    start: date
    end: date
    options: SyncOptions


@dataclass(slots=True)
class _FetchState:
    pending: list[str]
    successes: dict[str, pd.DataFrame]
    explicit_empty: set[str]
    errors: dict[str, str]

    @classmethod
    def for_requests(cls, requests: dict[str, _Request]) -> _FetchState:
        return cls(list(requests), {}, set(), {})

    def record_batch_error(self, symbols: list[str], error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"
        self.errors.update(dict.fromkeys(symbols, message))
        logging.warning("Yahoo batch failed without stopping the run: %s", message)

    def record_frames(
        self,
        returned: dict[str, pd.DataFrame],
        requests: dict[str, _Request],
    ) -> None:
        for symbol, frame in returned.items():
            if symbol not in self.pending:
                continue
            if frame.empty:
                self.explicit_empty.add(symbol)
                self.errors[symbol] = "Yahoo returned an empty daily range"
                continue
            validation_error = _response_validation_error(requests[symbol], frame)
            if validation_error is None:
                self.successes[symbol] = frame
                self.errors.pop(symbol, None)
            else:
                self.errors[symbol] = validation_error
                logging.warning("%s: %s", symbol, validation_error)
        self.pending = [
            symbol for symbol in self.pending if symbol not in self.successes
        ]

    def finalise(
        self,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str], frozenset[str]]:
        ambiguous = frozenset(
            symbol for symbol in self.pending if symbol not in self.explicit_empty
        )
        for symbol in self.pending:
            self.errors.setdefault(
                symbol, "Yahoo returned no usable daily rows after all retries"
            )
            if symbol in self.explicit_empty:
                self.successes[symbol] = pd.DataFrame()
        return self.successes, self.errors, ambiguous


def completed_session_end(now: datetime | None = None) -> date:
    """Return Yahoo's exclusive end date without ingesting an open ASX session."""
    local = now.astimezone(SYDNEY) if now else datetime.now(SYDNEY)
    today = local.date()
    if local.weekday() < 5 and local.time() < wall_time(17, 0):
        return today
    return today + timedelta(days=1)


def synchronise(
    database: Database,
    targets: Iterable[SyncTarget],
    options: SyncOptions,
    *,
    now: datetime | None = None,
    provider: YahooProvider | None = None,
) -> SyncReport:
    provider = provider or YahooProvider()
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    run_end = completed_session_end(now)
    targets = tuple(targets)
    plan = _build_sync_plan(
        database,
        targets,
        options=options,
        run_end=run_end,
        now_utc=now_utc,
    )
    result = _execute_sync_plan(
        database,
        provider,
        plan,
        options=options,
        now_utc=now_utc,
    )
    requested = sum(len(requests) for requests in plan.groups.values())
    outage_threshold = max(
        MIN_PROVIDER_OUTAGE_SYMBOLS,
        math.ceil(requested * 0.80),
    )
    return SyncReport(
        requested=requested,
        skipped_current=plan.skipped_current,
        skipped_backoff=plan.skipped_backoff,
        successful=result.successful,
        factor_changes=result.factor_changes,
        failed=result.failed,
        completed_through=run_end,
        provider_outage=(
            requested > 0 and result.provider_outage_requests >= outage_threshold
        ),
        provider_outage_requests=result.provider_outage_requests,
    )


def _build_sync_plan(
    database: Database,
    targets: tuple[SyncTarget, ...],
    *,
    options: SyncOptions,
    run_end: date,
    now_utc: datetime,
) -> _SyncPlan:
    calendar_days = math.ceil(options.lookback_sessions * 7 / 5) + 45
    instrument_ids = [target.instrument.instrument_id for target in targets]
    instrument_states = database.instrument_sync_states(instrument_ids)
    obligation_states = database.obligation_states(instrument_ids)
    context = _PlanningContext(
        database=database,
        run_end=run_end,
        calendar_days=calendar_days,
        now_utc=now_utc,
        instrument_states=instrument_states,
        obligation_states=obligation_states,
        earliest_dates=database.earliest_trade_dates(instrument_ids),
        history_watermarks=database.history_watermarks(instrument_ids),
    )
    groups: dict[tuple[date, date], list[_Request]] = defaultdict(list)
    skipped_current = 0
    skipped_backoff = 0

    for target in targets:
        target_plan = _plan_target(context, target)
        skipped_backoff += target_plan.skipped_backoff
        for request in target_plan.requests:
            groups[(request.start, request.end)].append(request)
        if not target_plan.requests:
            skipped_current += 1

    return _SyncPlan(
        groups=dict(groups),
        instrument_states=instrument_states,
        obligation_states=obligation_states,
        skipped_current=skipped_current,
        skipped_backoff=skipped_backoff,
    )


def _plan_target(context: _PlanningContext, target: SyncTarget) -> _TargetPlan:
    database = context.database
    instrument = target.instrument
    target_end = (
        context.run_end
        if target.active or target.required_end is None
        else min(context.run_end, target.required_end + timedelta(days=1))
    )
    initial_start = target_end - timedelta(days=context.calendar_days)
    required_history_start = database.prepare_sync_requirements(
        instrument.instrument_id,
        calendar_days=context.calendar_days,
    )
    if required_history_start is not None:
        initial_start = min(initial_start, required_history_start)
    database.satisfy_ready_sync_requirements(instrument.instrument_id)
    if (
        not target.active
        and required_history_start is not None
        and not database.has_pending_sync_requirements(instrument.instrument_id)
    ):
        return _TargetPlan(())

    instrument_state = context.instrument_states.get(instrument.instrument_id)
    forward_state = context.obligation_states.get((instrument.instrument_id, "forward"))
    history_state = context.obligation_states.get((instrument.instrument_id, "history"))
    checked = _state_date(forward_state, "checked_through")
    latest = _state_date(instrument_state, "latest_trade_date")
    earliest = context.earliest_dates.get(instrument.instrument_id)
    history_watermark = context.history_watermarks.get(instrument.instrument_id)
    needs_history = (target.active or required_history_start is not None) and (
        history_watermark is None or history_watermark > initial_start
    )
    needs_forward = checked is None or checked < target_end
    history_allowed = needs_history and not _in_backoff(history_state, context.now_utc)
    forward_allowed = needs_forward and not _in_backoff(forward_state, context.now_utc)
    skipped_backoff = int(needs_history and not history_allowed) + int(
        needs_forward and not forward_allowed
    )
    planned = _requests_for_obligations(
        instrument=instrument,
        initial_start=initial_start,
        target_end=target_end,
        earliest=earliest,
        checked=checked,
        latest=latest,
        cold_cache=(
            earliest is None
            or instrument_state is None
            or not bool(instrument_state["backfill_attempted"])
        ),
        history_allowed=history_allowed,
        forward_allowed=forward_allowed,
    )
    accepted = tuple(
        _with_cached_dates(database, request)
        for request in planned
        if request.start < request.end
    )
    return _TargetPlan(accepted, skipped_backoff)


def _requests_for_obligations(
    *,
    instrument: SnapshotInstrument,
    initial_start: date,
    target_end: date,
    earliest: date | None,
    checked: date | None,
    latest: date | None,
    cold_cache: bool,
    history_allowed: bool,
    forward_allowed: bool,
) -> list[_Request]:
    # A cold request may serve both obligations, but their completion and
    # cooldown state are persisted independently.
    if history_allowed and forward_allowed and cold_cache:
        return [
            _Request(
                instrument,
                initial_start,
                target_end,
                True,
                covers_forward=True,
                history_target=initial_start,
            )
        ]

    planned: list[_Request] = []
    if history_allowed:
        planned.append(
            _Request(
                instrument=instrument,
                start=initial_start,
                end=(
                    target_end
                    if earliest is None
                    else min(target_end, earliest + timedelta(days=1))
                ),
                is_backfill=True,
                history_target=initial_start,
                expected_anchor=earliest,
            )
        )
    if not forward_allowed:
        return planned

    forward = _plan_forward_request(
        instrument,
        checked,
        latest,
        initial_start,
        target_end,
    )
    if all(
        (forward.start, forward.end) != (request.start, request.end)
        for request in planned
    ):
        planned.append(forward)
        return planned
    return [_merge_request(request, forward) for request in planned]


def _execute_sync_plan(
    database: Database,
    provider: YahooProvider,
    plan: _SyncPlan,
    *,
    options: SyncOptions,
    now_utc: datetime,
) -> _ExecutionResult:
    result = _ExecutionResult()
    for start, request_end in sorted(plan.groups):
        result += _execute_request_group(
            database,
            provider,
            requests=plan.groups[(start, request_end)],
            instrument_states=plan.instrument_states,
            obligation_states=plan.obligation_states,
            start=start,
            end=request_end,
            options=options,
            now_utc=now_utc,
        )
    return result


def _execute_request_group(
    database: Database,
    provider: YahooProvider,
    *,
    requests: list[_Request],
    instrument_states: dict[int, sqlite3.Row],
    obligation_states: dict[tuple[int, str], sqlite3.Row],
    start: date,
    end: date,
    options: SyncOptions,
    now_utc: datetime,
) -> _ExecutionResult:
    by_symbol = {request.instrument.provider_symbol: request for request in requests}
    if len(by_symbol) != len(requests):
        raise ValueError("A fetch group contains duplicate provider symbols")
    run_id = database.start_fetch_run(start, end)
    frames, errors, ambiguous = _fetch_with_retries(
        provider,
        requests=by_symbol,
        start=start,
        end=end,
        options=options,
    )
    if _is_provider_outage(requests, frames=frames, ambiguous=ambiguous):
        dominant_error = _dominant_error(errors, frozenset(by_symbol))
        message = f"Provider-wide failure for {len(requests)} symbols: {dominant_error}"
        logging.warning("%s; preserving per-symbol retry state", message)
        database.finish_fetch_run(run_id, "failed", message)
        return _ExecutionResult(
            failed=len(requests),
            provider_outage_requests=len(requests),
        )
    result = _ExecutionResult()
    try:
        for symbol, request in by_symbol.items():
            request_result = _store_request_response(
                database,
                request=request,
                frame=frames.get(symbol),
                error=errors.get(symbol),
                run_id=run_id,
                instrument_state=instrument_states.get(
                    request.instrument.instrument_id
                ),
                obligation_states=obligation_states,
                now_utc=now_utc,
            )
            result += request_result
    except Exception as exc:
        database.finish_fetch_run(run_id, "failed", str(exc))
        raise

    status = (
        "complete"
        if not result.failed
        else ("failed" if result.failed == len(requests) else "partial")
    )
    database.finish_fetch_run(
        run_id,
        status,
        f"{result.failed} symbol(s) returned no data" if result.failed else None,
    )
    return result


def _store_request_response(
    database: Database,
    *,
    request: _Request,
    frame: pd.DataFrame | None,
    error: str | None,
    run_id: int,
    instrument_state: sqlite3.Row | None,
    obligation_states: dict[tuple[int, str], sqlite3.Row],
    now_utc: datetime,
) -> _ExecutionResult:
    if frame is None:
        for obligation_type in _request_obligations(request):
            _record_obligation_failure(
                database,
                request=request,
                obligation_type=obligation_type,
                state=obligation_states.get(
                    (request.instrument.instrument_id, obligation_type)
                ),
                now_utc=now_utc,
                error=error or "Yahoo returned no usable daily rows after all retries",
            )
        database.satisfy_ready_sync_requirements(request.instrument.instrument_id)
        return _ExecutionResult(failed=1)

    factor_changes = _append_response(database, request, frame, run_id=run_id)
    failures = 0
    if request.is_backfill:
        failures += _store_history_response(
            database,
            request=request,
            frame=frame,
            run_id=run_id,
            state=obligation_states.get((request.instrument.instrument_id, "history")),
            now_utc=now_utc,
        )
    if request.covers_forward:
        failures += _store_forward_response(
            database,
            request=request,
            frame=frame,
            run_id=run_id,
            state=obligation_states.get((request.instrument.instrument_id, "forward")),
            instrument_state=instrument_state,
            now_utc=now_utc,
        )
    database.satisfy_ready_sync_requirements(request.instrument.instrument_id)
    return _ExecutionResult(
        successful=int(failures == 0),
        factor_changes=factor_changes,
        failed=int(failures > 0),
    )


def _append_response(
    database: Database,
    request: _Request,
    frame: pd.DataFrame,
    *,
    run_id: int,
) -> int:
    if frame.empty:
        return 0
    database.reopen_empty_history_if_data_arrived(request.instrument.instrument_id)
    return database.append_series(
        run_id=run_id,
        instrument_id=request.instrument.instrument_id,
        frame=frame,
    )


def _store_history_response(
    database: Database,
    *,
    request: _Request,
    frame: pd.DataFrame,
    run_id: int,
    state: sqlite3.Row | None,
    now_utc: datetime,
) -> int:
    if frame.empty:
        empty_history_from = database.record_empty_history_probe(
            instrument_id=request.instrument.instrument_id,
            target_start=request.history_target or request.start,
            run_id=run_id,
        )
        if empty_history_from is None:
            _record_obligation_failure(
                database,
                request=request,
                obligation_type="history",
                state=state,
                now_utc=now_utc,
                error="Explicit empty Yahoo history needs an independent confirming run",
            )
            return 1
        database.mark_history_checked(
            request.instrument.instrument_id, empty_history_from
        )
        database.update_sync_success(
            instrument_id=request.instrument.instrument_id,
            checked_through=request.start,
            latest_trade_date=None,
            backfill_attempted=True,
            obligation_type="history",
        )
        return 0

    latest = pd.Timestamp(frame.index.max()).date()
    database.update_sync_success(
        instrument_id=request.instrument.instrument_id,
        checked_through=request.start,
        latest_trade_date=latest,
        backfill_attempted=True,
        obligation_type="history",
    )
    verified_history_from = database.record_history_probe(
        instrument_id=request.instrument.instrument_id,
        target_start=request.history_target or request.start,
        earliest_returned=pd.Timestamp(frame.index.min()).date(),
        run_id=run_id,
    )
    if verified_history_from is not None:
        database.mark_history_checked(
            request.instrument.instrument_id, verified_history_from
        )
    return 0


def _store_forward_response(
    database: Database,
    *,
    request: _Request,
    frame: pd.DataFrame,
    run_id: int,
    state: sqlite3.Row | None,
    instrument_state: sqlite3.Row | None,
    now_utc: datetime,
) -> int:
    known_latest = _state_date(instrument_state, "latest_trade_date")
    needs_confirmation = _needs_forward_confirmation(
        request,
        frame,
        known_latest=known_latest,
    )
    if needs_confirmation:
        forward_confirmed = database.record_forward_probe(
            instrument_id=request.instrument.instrument_id,
            target_end=request.end,
            response_signature=_forward_signature(
                frame,
                known_latest=known_latest,
            ),
            run_id=run_id,
        )
    else:
        database.clear_forward_probe(request.instrument.instrument_id)
        forward_confirmed = True

    if forward_confirmed:
        latest = pd.Timestamp(frame.index.max()).date() if not frame.empty else None
        database.update_sync_success(
            instrument_id=request.instrument.instrument_id,
            checked_through=request.end,
            latest_trade_date=latest,
            backfill_attempted=False,
            obligation_type="forward",
        )
        return 0

    _record_obligation_failure(
        database,
        request=request,
        obligation_type="forward",
        state=state,
        now_utc=now_utc,
        error="Sparse or empty Yahoo horizon needs an independent confirming run",
    )
    return 1


def _with_cached_dates(database: Database, request: _Request) -> _Request:
    return _Request(
        instrument=request.instrument,
        start=request.start,
        end=request.end,
        is_backfill=request.is_backfill,
        covers_forward=request.covers_forward,
        history_target=request.history_target,
        expected_anchor=request.expected_anchor,
        expected_cached_dates=database.cached_trade_dates(
            request.instrument.instrument_id,
            start=request.start,
            end=request.end,
        ),
    )


def _plan_forward_request(
    instrument: SnapshotInstrument,
    checked: date | None,
    latest: date | None,
    initial_start: date,
    end: date,
) -> _Request:
    if latest is not None and (checked is None or (checked - latest).days <= 14):
        # A short overlap permits bounded repair of delayed provider changes.
        start = max(initial_start, latest - timedelta(days=10))
    elif checked is not None:
        # A halted/delisted symbol gets only a short probe, not its whole gap.
        start = max(initial_start, checked - timedelta(days=10))
    elif latest is not None:
        start = latest
    else:
        start = max(initial_start, end - timedelta(days=10))
    expected_anchor = latest if latest is not None and start <= latest else None
    return _Request(
        instrument,
        start,
        end,
        False,
        covers_forward=True,
        expected_anchor=expected_anchor,
    )


def _merge_request(left: _Request, right: _Request) -> _Request:
    if (left.start, left.end, left.instrument.instrument_id) != (
        right.start,
        right.end,
        right.instrument.instrument_id,
    ):
        raise ValueError("Cannot merge requests with different boundaries")
    return _Request(
        instrument=left.instrument,
        start=left.start,
        end=left.end,
        is_backfill=left.is_backfill or right.is_backfill,
        covers_forward=left.covers_forward or right.covers_forward,
        history_target=left.history_target or right.history_target,
        expected_anchor=left.expected_anchor or right.expected_anchor,
    )


def _request_obligations(request: _Request) -> tuple[str, ...]:
    obligations: list[str] = []
    if request.is_backfill:
        obligations.append("history")
    if request.covers_forward:
        obligations.append("forward")
    return tuple(obligations)


def _in_backoff(state: sqlite3.Row | None, now_utc: datetime) -> bool:
    retry_after = _datetime_value(state["retry_after"]) if state is not None else None
    return retry_after is not None and retry_after > now_utc


def _record_obligation_failure(
    database: Database,
    *,
    request: _Request,
    obligation_type: str,
    state: sqlite3.Row | None,
    now_utc: datetime,
    error: str,
) -> None:
    previous_failures = (
        int(state["consecutive_failures"] or 0) if state is not None else 0
    )
    delay_hours = min(24 * 7, 6 * (2 ** min(previous_failures, 5)))
    previous_checked = (
        _date_value(state["checked_through"])
        if state is not None and state["checked_through"]
        else request.start
    )
    database.update_sync_failure(
        instrument_id=request.instrument.instrument_id,
        checked_through=previous_checked,
        backfill_attempted=False,
        error=error,
        retry_after=now_utc + timedelta(hours=delay_hours),
        obligation_type=obligation_type,
    )


def _last_expected_weekday(end: date) -> date:
    candidate = end - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _needs_forward_confirmation(
    request: _Request,
    frame: pd.DataFrame,
    *,
    known_latest: date | None,
) -> bool:
    returned_dates = sorted({pd.Timestamp(value).date() for value in frame.index})
    effective_latest = (
        max(
            ([known_latest] if known_latest is not None else [])
            + ([returned_dates[-1]] if returned_dates else [])
        )
        if known_latest is not None or returned_dates
        else None
    )
    expected_latest = _last_expected_weekday(request.end)
    if effective_latest is None or effective_latest < expected_latest:
        return True
    # A forward overlap with a current endpoint can still be a truncated two-row
    # response. Tolerate a couple of exchange holidays, but independently
    # confirm a material run of missing weekdays (also valid for a long halt).
    if request.expected_anchor is None or not returned_dates:
        return False
    expected_dates = []
    candidate = request.expected_anchor + timedelta(days=1)
    while candidate <= expected_latest:
        if candidate.weekday() < 5:
            expected_dates.append(candidate)
        candidate += timedelta(days=1)
    if len(expected_dates) < 5:
        return False
    returned = set(returned_dates) | set(request.expected_cached_dates)
    missing = sum(trade_date not in returned for trade_date in expected_dates)
    return missing > max(2, math.ceil(len(expected_dates) * 0.20))


def _forward_signature(
    frame: pd.DataFrame,
    *,
    known_latest: date | None,
) -> str:
    returned_dates = sorted({pd.Timestamp(value).date() for value in frame.index})
    payload = ",".join(value.isoformat() for value in returned_dates)
    latest = (
        max(
            ([known_latest] if known_latest is not None else [])
            + ([returned_dates[-1]] if returned_dates else [])
        )
        if known_latest is not None or returned_dates
        else None
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{latest.isoformat() if latest else 'none'}:{digest}"


def _response_validation_error(
    request: _Request,
    frame: pd.DataFrame | None,
) -> str | None:
    if frame is None or frame.empty:
        return None
    returned_dates = {pd.Timestamp(value).date() for value in frame.index}
    if (
        request.expected_anchor is not None
        and request.expected_anchor not in returned_dates
    ):
        return (
            "Yahoo response omitted required cache anchor "
            f"{request.expected_anchor.isoformat()}"
        )
    missing_cached = sorted(
        trade_date
        for trade_date in request.expected_cached_dates
        if trade_date not in returned_dates
    )
    if missing_cached:
        preview = ", ".join(value.isoformat() for value in missing_cached[:3])
        return f"Yahoo response omitted cached overlap date(s): {preview}"
    return None


def _fetch_with_retries(
    provider: YahooProvider,
    *,
    requests: dict[str, _Request],
    start: date,
    end: date,
    options: SyncOptions,
) -> tuple[dict[str, pd.DataFrame], dict[str, str], frozenset[str]]:
    context = _FetchContext(provider, requests, start, end, options)
    state = _FetchState.for_requests(requests)
    for attempt in range(1, options.retries + 1):
        for batch_number, batch in enumerate(
            _chunks(list(state.pending), options.batch_size), 1
        ):
            _fetch_batch(
                context,
                state,
                batch,
                attempt=attempt,
                batch_number=batch_number,
            )
            _pause_between_batches(options)
        if not state.pending or attempt == options.retries:
            break
        _backoff_before_retry(state, options, attempt)
    return state.finalise()


def _is_provider_outage(
    requests: list[_Request],
    *,
    frames: dict[str, pd.DataFrame],
    ambiguous: frozenset[str],
) -> bool:
    """Recognise a broad unusable response without blaming every instrument.

    A wholly empty Yahoo batch is deliberately ambiguous: it can mean a
    transport, authentication, dependency, or upstream service failure. Once
    that happens across a sizeable request group, applying hundreds of symbol
    cooldowns destroys the cache's graceful-degradation behaviour. Empty
    frames remain ordinary symbol results when the same group also contains
    usable responses; an all-empty group is a provider-level failure.
    """
    unusable = set(ambiguous)
    unusable.update(symbol for symbol, frame in frames.items() if frame.empty)
    return (
        len(requests) >= MIN_PROVIDER_OUTAGE_SYMBOLS
        and len(unusable) == len(requests)
        and not any(not frame.empty for frame in frames.values())
    )


def _dominant_error(errors: dict[str, str], symbols: frozenset[str]) -> str:
    messages = [errors[symbol] for symbol in symbols if symbol in errors]
    if not messages:
        return "Yahoo returned no usable responses after all retries"
    return Counter(messages).most_common(1)[0][0]


def _fetch_batch(
    context: _FetchContext,
    state: _FetchState,
    batch: list[str],
    *,
    attempt: int,
    batch_number: int,
) -> None:
    logging.info(
        "Yahoo %s to %s: attempt %d/%d, batch %d, %d symbol(s)",
        context.start,
        context.end,
        attempt,
        context.options.retries,
        batch_number,
        len(batch),
    )
    try:
        returned = context.provider.fetch(
            batch,
            start=context.start,
            end=context.end,
            threads=context.options.threads,
            timeout=context.options.timeout,
        )
    except Exception as exc:
        state.record_batch_error(batch, exc)
        return
    state.record_frames(returned, context.requests)


def _pause_between_batches(options: SyncOptions) -> None:
    if options.batch_pause > 0:
        time.sleep(options.batch_pause + random.uniform(0, options.batch_pause / 3))


def _backoff_before_retry(
    state: _FetchState,
    options: SyncOptions,
    attempt: int,
) -> None:
    delay = min(90.0, options.base_backoff * (2 ** (attempt - 1)))
    if delay > 0:
        delay += random.uniform(0, delay * 0.2)
    logging.warning(
        "%d symbol(s) still missing; backing off %.1fs before retry %d/%d",
        len(state.pending),
        delay,
        attempt + 1,
        options.retries,
    )
    time.sleep(delay)


def _chunks(values: Iterable[str], size: int) -> Iterator[list[str]]:
    iterator = iter(values)
    while batch := list(islice(iterator, size)):
        yield batch


def _date_value(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _state_date(state: sqlite3.Row | None, field: str) -> date | None:
    return _date_value(state[field]) if state is not None and state[field] else None


def _datetime_value(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )
