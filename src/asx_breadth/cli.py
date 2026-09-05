"""Command-line orchestration; business logic lives in replaceable modules."""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path
from typing import Sequence

from .dashboard import render_dashboard
from .db import Database
from .holdings import parse_holdings
from .indicators import BUILT_IN_INDICATORS, IndicatorContext, run_indicators
from .models import SyncOptions
from .panels import BUILT_IN_PANELS
from .sync import SyncReport, synchronise


ROOT = Path(__file__).resolve().parents[2]


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return parsed


def _asx_provider_symbol(value: str) -> str:
    symbol = value.strip().upper()
    return f"{symbol}.AX" if symbol and "." not in symbol else symbol


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="breadth.py",
        description=(
            "Cache Yahoo daily data for an ASX holdings workbook and build an "
            "interactive market-breadth dashboard."
        ),
    )
    result.add_argument("holdings_file", type=Path, help="XLSX holdings workbook")
    result.add_argument(
        "--db",
        type=Path,
        default=ROOT / "data" / "asx_breadth.sqlite3",
        help="SQLite cache path (default: %(default)s)",
    )
    result.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dashboard.html",
        help="HTML dashboard path (default: %(default)s)",
    )
    result.add_argument("--universe-code", default="VAS")
    result.add_argument(
        "--universe-name", default="Vanguard Australian Shares Index ETF"
    )
    result.add_argument(
        "--benchmark-symbol",
        default="VAS.AX",
        help="Yahoo symbol for the total-return card (default: %(default)s)",
    )
    result.add_argument(
        "--volatility-symbol",
        default="^AXVI",
        help="Yahoo symbol for the S&P/ASX 200 VIX card (default: %(default)s)",
    )
    result.add_argument(
        "--currency-index-symbol",
        default="^XDA",
        help="Yahoo symbol for the Australian Dollar Currency Index card (default: %(default)s)",
    )
    result.add_argument("--lookback-sessions", type=_positive_int, default=1000)
    result.add_argument(
        "--as-of-date",
        type=date.fromisoformat,
        help="Override the workbook effective date (YYYY-MM-DD)",
    )
    result.add_argument(
        "--allow-suspicious-holdings",
        action="store_true",
        help="Import despite snapshot count/date/composition safety checks",
    )
    result.add_argument("--batch-size", type=_positive_int, default=20)
    result.add_argument("--threads", type=_positive_int, default=2)
    result.add_argument("--retries", type=_positive_int, default=4)
    result.add_argument("--timeout", type=_positive_float, default=30.0)
    result.add_argument("--batch-pause", type=_nonnegative_float, default=1.25)
    result.add_argument("--base-backoff", type=_nonnegative_float, default=5.0)
    result.add_argument(
        "--no-download",
        action="store_true",
        help="Rebuild the dashboard entirely from the existing cache",
    )
    result.add_argument("--verbose", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if not arguments.verbose:
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    try:
        holdings_file = parse_holdings(
            arguments.holdings_file,
            as_of_date_override=arguments.as_of_date,
        )
    except (OSError, ValueError) as exc:
        logging.error("Cannot import holdings: %s", exc)
        return 2

    logging.info(
        "Imported %d holdings from %s (as at %s)",
        len(holdings_file.holdings),
        holdings_file.path,
        holdings_file.as_of_date,
    )
    sync_report: SyncReport | None = None
    with Database(arguments.db) as database:
        universe_code = arguments.universe_code.upper()
        admission_issues = database.snapshot_admission_issues(
            holdings_file,
            universe_code=universe_code,
        )
        is_known_snapshot = database.snapshot_exists(
            universe_code,
            holdings_file.sha256,
            as_of_date=holdings_file.as_of_date,
        )
        if arguments.no_download and not is_known_snapshot:
            # This is a data-availability invariant, not an admission heuristic;
            # --allow-suspicious-holdings must never bypass it.
            logging.error(
                "--no-download cannot make an unseen holdings snapshot authoritative"
            )
            return 2
        if admission_issues and not arguments.allow_suspicious_holdings:
            for issue in admission_issues:
                logging.error("Refusing suspicious holdings snapshot: %s", issue)
            logging.error("Review the file or rerun with --allow-suspicious-holdings")
            return 2
        try:
            snapshot = database.import_snapshot(
                holdings_file,
                universe_code=universe_code,
                universe_name=arguments.universe_name,
            )
            benchmark_symbol = _asx_provider_symbol(arguments.benchmark_symbol)
            snapshot = database.register_universe_series(
                snapshot.snapshot_id,
                role="benchmark",
                provider_symbol=benchmark_symbol,
                local_symbol=benchmark_symbol.removesuffix(".AX"),
                name=arguments.universe_name,
            )
            snapshot = database.register_universe_series(
                snapshot.snapshot_id,
                role="volatility",
                provider_symbol=arguments.volatility_symbol,
                local_symbol=".AXVI",
                name="S&P/ASX 200 VIX",
            )
            snapshot = database.register_universe_series(
                snapshot.snapshot_id,
                role="currency_index",
                provider_symbol=arguments.currency_index_symbol,
                local_symbol="XDA",
                name="Australian Dollar Currency Index",
            )
        except ValueError as exc:
            # Hard temporal/source-identity invariants remain non-bypassable.
            logging.error("Cannot import holdings: %s", exc)
            return 2
        membership = database.membership_for_snapshot(snapshot.snapshot_id)
        if not arguments.no_download:
            options = SyncOptions(
                lookback_sessions=arguments.lookback_sessions,
                batch_size=arguments.batch_size,
                threads=arguments.threads,
                retries=arguments.retries,
                timeout=arguments.timeout,
                batch_pause=arguments.batch_pause,
                base_backoff=arguments.base_backoff,
            )
            sync_targets = database.sync_targets_for_snapshot(snapshot.snapshot_id)
            sync_report = synchronise(database, sync_targets, options)

        factors = database.factors_for_membership(membership)
        series_factors = {
            series.role: database.factors_for_instrument(
                series.instrument.instrument_id
            )
            for series in snapshot.market_series
        }
        series_anchor_prices = {
            series.role: database.latest_adjusted_close(series.instrument.instrument_id)
            for series in snapshot.market_series
        }
        results = run_indicators(
            IndicatorContext(
                snapshot=snapshot,
                factors=factors,
                membership=membership,
                series_factors=series_factors,
                series_anchor_prices=series_anchor_prices,
            ),
            BUILT_IN_INDICATORS,
        )
        summary = database.sync_summary(snapshot.snapshot_id)
        if sync_report is not None:
            summary.update(
                {
                    "provider_outage": sync_report.provider_outage,
                    "provider_outage_requests": sync_report.provider_outage_requests,
                }
            )
        render_dashboard(
            arguments.output,
            snapshot=snapshot,
            results=results,
            panels=BUILT_IN_PANELS,
            sync_summary=summary,
        )

    if sync_report is not None:
        if sync_report.provider_outage:
            individual_failures = max(
                0, sync_report.failed - sync_report.provider_outage_requests
            )
            logging.warning(
                "Sync degraded: Yahoo unavailable for %d/%d requests; "
                "%d succeeded, %d individual failure(s), %d factor changes; "
                "dashboard rebuilt from validated cached data",
                sync_report.provider_outage_requests,
                sync_report.requested,
                sync_report.successful,
                individual_failures,
                sync_report.factor_changes,
            )
        else:
            logging.info(
                "Sync: %d requests, %d succeeded, %d failed, %d factor changes",
                sync_report.requested,
                sync_report.successful,
                sync_report.failed,
                sync_report.factor_changes,
            )
    logging.info("Dashboard written to %s", arguments.output.expanduser().resolve())
    logging.info("SQLite cache: %s", arguments.db.expanduser().resolve())
    return 0
