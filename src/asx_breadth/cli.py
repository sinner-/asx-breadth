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
    result.add_argument("--lookback-sessions", type=int, default=1000)
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
    result.add_argument("--batch-size", type=int, default=20)
    result.add_argument("--threads", type=int, default=2)
    result.add_argument("--retries", type=int, default=4)
    result.add_argument("--timeout", type=float, default=30.0)
    result.add_argument("--batch-pause", type=float, default=1.25)
    result.add_argument("--base-backoff", type=float, default=5.0)
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
                benchmark_symbol=arguments.benchmark_symbol,
            )
        except ValueError as exc:
            # Hard temporal/source-identity invariants remain non-bypassable.
            logging.error("Cannot import holdings: %s", exc)
            return 2
        membership = database.membership_for_snapshot(snapshot.snapshot_id)
        if not arguments.no_download:
            options = SyncOptions(
                lookback_sessions=max(arguments.lookback_sessions, 1),
                batch_size=max(arguments.batch_size, 1),
                threads=max(arguments.threads, 1),
                retries=max(arguments.retries, 1),
                timeout=max(arguments.timeout, 1),
                batch_pause=max(arguments.batch_pause, 0),
                base_backoff=max(arguments.base_backoff, 0),
            )
            try:
                sync_targets = database.sync_targets_for_snapshot(snapshot.snapshot_id)
                sync_report = synchronise(database, sync_targets, options)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                # A provider or single-run failure must not prevent a dashboard
                # from being rebuilt from the last valid cache state.
                logging.exception("Download stopped safely; using cached data: %s", exc)

        factors = database.factors_for_membership(membership)
        benchmark_factors = database.factors_for_instrument(
            snapshot.benchmark.instrument_id if snapshot.benchmark else None
        )
        benchmark_anchor_price = database.latest_adjusted_close(
            snapshot.benchmark.instrument_id if snapshot.benchmark else None
        )
        results = run_indicators(
            IndicatorContext(
                snapshot=snapshot,
                factors=factors,
                benchmark_factors=benchmark_factors,
                benchmark_anchor_price=benchmark_anchor_price,
                membership=membership,
            ),
            BUILT_IN_INDICATORS,
        )
        summary = database.sync_summary(snapshot.snapshot_id)
        render_dashboard(
            arguments.output,
            snapshot=snapshot,
            results=results,
            panels=BUILT_IN_PANELS,
            sync_summary=summary,
        )

    if sync_report is not None:
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
