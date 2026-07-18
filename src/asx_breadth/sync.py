"""Rate-conscious incremental synchronisation into append-only factors."""

from __future__ import annotations

import logging
import math
import random
import time
import hashlib
from collections import defaultdict
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


@dataclass(frozen=True, slots=True)
class SyncReport:
    requested: int
    skipped_current: int
    skipped_backoff: int
    successful: int
    factor_changes: int
    failed: int
    completed_through: date


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
    calendar_days = math.ceil(options.lookback_sessions * 7 / 5) + 45
    targets = tuple(targets)
    instrument_ids = [target.instrument.instrument_id for target in targets]
    states = database.sync_states(instrument_ids)
    obligation_states = database.obligation_states(instrument_ids)
    earliest_dates = database.earliest_trade_dates(instrument_ids)
    history_watermarks = database.history_watermarks(instrument_ids)
    groups: dict[tuple[date, date], list[_Request]] = defaultdict(list)
    skipped_current = 0
    skipped_backoff = 0

    for target in targets:
        instrument = target.instrument
        target_end = (
            run_end
            if target.active or target.required_end is None
            else min(run_end, target.required_end + timedelta(days=1))
        )
        initial_start = target_end - timedelta(days=calendar_days)
        required_history_start = database.prepare_sync_requirements(
            instrument.instrument_id,
            calendar_days=calendar_days,
        )
        if required_history_start is not None:
            initial_start = min(initial_start, required_history_start)
        database.satisfy_ready_sync_requirements(instrument.instrument_id)
        if (
            not target.active
            and required_history_start is not None
            and not database.has_pending_sync_requirements(instrument.instrument_id)
        ):
            skipped_current += 1
            continue
        state = states.get(instrument.instrument_id)
        forward_state = obligation_states.get((instrument.instrument_id, "forward"))
        history_state = obligation_states.get((instrument.instrument_id, "history"))
        checked = (
            _date_value(forward_state["checked_through"])
            if forward_state and forward_state["checked_through"]
            else (_date_value(state["checked_through"]) if state else None)
        )

        earliest = earliest_dates.get(instrument.instrument_id)
        history_watermark = history_watermarks.get(instrument.instrument_id)
        needs_history = (target.active or required_history_start is not None) and (
            history_watermark is None or history_watermark > initial_start
        )
        needs_forward = checked is None or checked < target_end
        history_allowed = needs_history and not _in_backoff(
            history_state,
            now_utc,
        )
        forward_allowed = needs_forward and not _in_backoff(
            forward_state,
            now_utc,
        )
        if needs_history and not history_allowed:
            skipped_backoff += 1
        if needs_forward and not forward_allowed:
            skipped_backoff += 1
        planned: list[_Request] = []

        # A cold request may serve both obligations, but their completion and
        # cooldown state are persisted independently.
        cold_cache = (
            earliest is None or state is None or not bool(state["backfill_attempted"])
        )
        if history_allowed and forward_allowed and cold_cache:
            planned.append(
                _Request(
                    instrument,
                    initial_start,
                    target_end,
                    True,
                    covers_forward=True,
                    history_target=initial_start,
                )
            )
        else:
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
                        expected_anchor=earliest if earliest is not None else None,
                    )
                )
            if forward_allowed:
                forward = _plan_forward_request(
                    instrument,
                    state,
                    initial_start,
                    target_end,
                )
                if all(
                    (forward.start, forward.end) != (request.start, request.end)
                    for request in planned
                ):
                    planned.append(forward)
                else:
                    planned = [_merge_request(request, forward) for request in planned]

        accepted = 0
        for request in planned:
            if request.start >= request.end:
                continue
            request = _with_cached_dates(database, request)
            groups[(request.start, request.end)].append(request)
            accepted += 1
        if not accepted:
            skipped_current += 1

    total_requested = sum(len(requests) for requests in groups.values())
    total_success = 0
    total_inserted = 0
    total_failed = 0

    for start, request_end in sorted(groups):
        requests = groups[(start, request_end)]
        by_symbol = {
            request.instrument.provider_symbol: request for request in requests
        }
        run_id = database.start_fetch_run(start, request_end)
        frames, errors = _fetch_with_retries(
            provider,
            requests=by_symbol,
            start=start,
            end=request_end,
            options=options,
        )
        group_failures = 0
        try:
            for symbol, request in by_symbol.items():
                frame = frames.get(symbol)
                request_failures = 0
                latest: date | None = None
                if frame is not None and not frame.empty:
                    database.reopen_empty_history_if_data_arrived(
                        request.instrument.instrument_id
                    )
                    inserted = database.append_series(
                        run_id=run_id,
                        instrument_id=request.instrument.instrument_id,
                        frame=frame,
                    )
                    latest = pd.Timestamp(frame.index.max()).date()
                    total_inserted += inserted

                obligations = _request_obligations(request)
                if frame is None:
                    for obligation_type in obligations:
                        _record_obligation_failure(
                            database,
                            request=request,
                            obligation_type=obligation_type,
                            state=obligation_states.get(
                                (
                                    request.instrument.instrument_id,
                                    obligation_type,
                                )
                            ),
                            now_utc=now_utc,
                            error=errors.get(
                                symbol,
                                "Yahoo returned no usable daily rows after all retries",
                            ),
                        )
                        request_failures += 1
                else:
                    if request.is_backfill:
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
                                    state=obligation_states.get(
                                        (
                                            request.instrument.instrument_id,
                                            "history",
                                        )
                                    ),
                                    now_utc=now_utc,
                                    error=(
                                        "Explicit empty Yahoo history needs an "
                                        "independent confirming run"
                                    ),
                                )
                                request_failures += 1
                            else:
                                database.mark_history_checked(
                                    request.instrument.instrument_id,
                                    empty_history_from,
                                )
                                database.update_sync_success(
                                    instrument_id=request.instrument.instrument_id,
                                    checked_through=request.start,
                                    latest_trade_date=None,
                                    backfill_attempted=True,
                                    obligation_type="history",
                                )
                        else:
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
                                earliest_returned=pd.Timestamp(
                                    frame.index.min()
                                ).date(),
                                run_id=run_id,
                            )
                            if verified_history_from is not None:
                                database.mark_history_checked(
                                    request.instrument.instrument_id,
                                    verified_history_from,
                                )

                    if request.covers_forward:
                        state = states.get(request.instrument.instrument_id)
                        known_latest = (
                            _date_value(state["latest_trade_date"])
                            if state and state["latest_trade_date"]
                            else None
                        )
                        needs_confirmation = _needs_forward_confirmation(
                            request,
                            frame,
                            known_latest=known_latest,
                        )
                        forward_confirmed = True
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
                            database.clear_forward_probe(
                                request.instrument.instrument_id
                            )
                        if forward_confirmed:
                            database.update_sync_success(
                                instrument_id=request.instrument.instrument_id,
                                checked_through=request.end,
                                latest_trade_date=latest,
                                backfill_attempted=False,
                                obligation_type="forward",
                            )
                        else:
                            _record_obligation_failure(
                                database,
                                request=request,
                                obligation_type="forward",
                                state=obligation_states.get(
                                    (request.instrument.instrument_id, "forward")
                                ),
                                now_utc=now_utc,
                                error=(
                                    "Sparse or empty Yahoo horizon needs an "
                                    "independent confirming run"
                                ),
                            )
                            request_failures += 1

                database.satisfy_ready_sync_requirements(
                    request.instrument.instrument_id
                )
                if request_failures == 0:
                    total_success += 1
                    continue
                group_failures += 1
                total_failed += 1
        except Exception as exc:
            database.finish_fetch_run(run_id, "failed", str(exc))
            raise
        else:
            status = (
                "complete"
                if not group_failures
                else ("failed" if group_failures == len(requests) else "partial")
            )
            database.finish_fetch_run(
                run_id,
                status,
                f"{group_failures} symbol(s) returned no data"
                if group_failures
                else None,
            )

    return SyncReport(
        requested=total_requested,
        skipped_current=skipped_current,
        skipped_backoff=skipped_backoff,
        successful=total_success,
        factor_changes=total_inserted,
        failed=total_failed,
        completed_through=run_end,
    )


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
    state: object | None,
    initial_start: date,
    end: date,
) -> _Request:
    checked = (
        _date_value(state["checked_through"]) if state is not None else None  # type: ignore[index]
    )
    latest = (
        _date_value(state["latest_trade_date"]) if state is not None else None  # type: ignore[index]
    )
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
        return left
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


def _in_backoff(state: object | None, now_utc: datetime) -> bool:
    retry_after = (
        _datetime_value(state["retry_after"])  # type: ignore[index]
        if state is not None
        else None
    )
    return retry_after is not None and retry_after > now_utc


def _record_obligation_failure(
    database: Database,
    *,
    request: _Request,
    obligation_type: str,
    state: object | None,
    now_utc: datetime,
    error: str,
) -> None:
    previous_failures = (
        int(state["consecutive_failures"] or 0)  # type: ignore[index]
        if state is not None
        else 0
    )
    delay_hours = min(24 * 7, 6 * (2 ** min(previous_failures, 5)))
    previous_checked = (
        _date_value(state["checked_through"])  # type: ignore[index]
        if state is not None and state["checked_through"]  # type: ignore[index]
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
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    successes: dict[str, pd.DataFrame] = {}
    explicit_empty: set[str] = set()
    errors: dict[str, str] = {}
    pending = list(requests)
    attempts = max(options.retries, 1)

    for attempt in range(1, attempts + 1):
        round_pending = list(pending)
        for batch_number, batch in enumerate(
            _chunks(round_pending, options.batch_size), 1
        ):
            logging.info(
                "Yahoo %s to %s: attempt %d/%d, batch %d, %d symbol(s)",
                start,
                end,
                attempt,
                attempts,
                batch_number,
                len(batch),
            )
            try:
                returned = provider.fetch(
                    batch,
                    start=start,
                    end=end,
                    threads=options.threads,
                    timeout=options.timeout,
                )
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                for symbol in batch:
                    errors[symbol] = message
                logging.warning(
                    "Yahoo batch failed without stopping the run: %s", message
                )
                returned = {}
            for symbol, frame in returned.items():
                if symbol not in pending:
                    continue
                if frame.empty:
                    explicit_empty.add(symbol)
                    errors[symbol] = "Yahoo returned an empty daily range"
                    continue
                validation_error = _response_validation_error(
                    requests[symbol],
                    frame,
                )
                if validation_error is None:
                    successes[symbol] = frame
                    errors.pop(symbol, None)
                else:
                    errors[symbol] = validation_error
                    logging.warning("%s: %s", symbol, validation_error)
            pending = [symbol for symbol in pending if symbol not in successes]
            if options.batch_pause > 0:
                time.sleep(
                    options.batch_pause + random.uniform(0, options.batch_pause / 3)
                )

        if not pending or attempt == attempts:
            break
        delay = min(90.0, options.base_backoff * (2 ** (attempt - 1)))
        if delay > 0:
            delay += random.uniform(0, delay * 0.2)
        logging.warning(
            "%d symbol(s) still missing; backing off %.1fs before retry %d/%d",
            len(pending),
            delay,
            attempt + 1,
            attempts,
        )
        time.sleep(delay)

    for symbol in pending:
        errors.setdefault(
            symbol, "Yahoo returned no usable daily rows after all retries"
        )
        if symbol in explicit_empty:
            successes[symbol] = pd.DataFrame()
    return successes, errors


def _chunks(values: Iterable[str], size: int) -> Iterator[list[str]]:
    iterator = iter(values)
    size = max(size, 1)
    while batch := list(islice(iterator, size)):
        yield batch


def _date_value(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _datetime_value(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )
