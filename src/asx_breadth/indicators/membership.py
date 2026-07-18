"""Expand dated holdings snapshots into point-in-time session membership."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import IndicatorContext


def market_sessions(context: IndicatorContext) -> pd.DatetimeIndex:
    """Use the VAS benchmark as the canonical ASX session calendar."""
    source = context.benchmark_factors
    if source.empty or "trade_date" not in source:
        source = context.factors
    if source.empty or "trade_date" not in source:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(
        pd.to_datetime(source["trade_date"]).drop_duplicates().sort_values()
    )


def active_membership_for_sessions(
    context: IndicatorContext,
    sessions: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Return one trade-date/instrument row for every active constituent."""
    sessions = (
        pd.DatetimeIndex(pd.to_datetime(sessions)).drop_duplicates().sort_values()
    )
    columns = ["trade_date", "instrument_id"]
    if sessions.empty:
        return pd.DataFrame(columns=columns)

    membership = context.membership.copy()
    if membership.empty:
        instrument_ids = [
            instrument.instrument_id for instrument in context.snapshot.instruments
        ]
        return _constant_membership(sessions, instrument_ids)

    membership["effective_from"] = pd.to_datetime(membership["effective_from"])
    snapshots = (
        membership[["snapshot_id", "effective_from"]]
        .drop_duplicates()
        .sort_values(["effective_from", "snapshot_id"])
    )
    if snapshots.empty:
        return pd.DataFrame(columns=columns)

    effective_values = snapshots["effective_from"].to_numpy(dtype="datetime64[ns]")
    positions = (
        np.searchsorted(
            effective_values,
            sessions.to_numpy(dtype="datetime64[ns]"),
            # Vanguard describes these as holdings "as at" the close. A new
            # composition therefore becomes active on the next market session.
            side="left",
        )
        - 1
    )
    # Before the first captured snapshot, use the earliest available basket as
    # an explicit approximation. Later snapshots still take effect only after
    # their "as at" close, so subsequent composition changes remain point-in-time.
    positions = np.clip(positions, 0, len(snapshots) - 1)
    snapshot_ids = snapshots["snapshot_id"].to_numpy()[positions]
    compositions = {
        snapshot_id: group["instrument_id"].astype(int).drop_duplicates().tolist()
        for snapshot_id, group in membership.groupby("snapshot_id", sort=False)
    }

    dates: list[pd.Timestamp] = []
    instruments: list[int] = []
    for trade_date, snapshot_id in zip(sessions, snapshot_ids, strict=True):
        active = compositions.get(snapshot_id, [])
        dates.extend([trade_date] * len(active))
        instruments.extend(active)
    return pd.DataFrame({"trade_date": dates, "instrument_id": instruments})


def _constant_membership(
    sessions: pd.DatetimeIndex,
    instrument_ids: list[int],
) -> pd.DataFrame:
    if not instrument_ids:
        return pd.DataFrame(columns=["trade_date", "instrument_id"])
    return pd.DataFrame(
        {
            "trade_date": np.repeat(sessions.to_numpy(), len(instrument_ids)),
            "instrument_id": np.tile(instrument_ids, len(sessions)),
        }
    )
