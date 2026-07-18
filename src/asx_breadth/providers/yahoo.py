"""Yahoo Finance adapter isolated from storage and indicator concerns."""

from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd
import yfinance as yf


NORMALISED_COLUMNS = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "adj close": "adjusted_close",
    "volume": "volume",
    "dividends": "dividend",
    "stock splits": "split_ratio",
    "repaired?": "repaired",
}
NORMALISED_COLUMN_ORDER = tuple(NORMALISED_COLUMNS.values())


class YahooProvider:
    name = "yahoo"

    def fetch(
        self,
        symbols: Iterable[str],
        *,
        start: date,
        end: date,
        threads: int,
        timeout: float,
    ) -> dict[str, pd.DataFrame]:
        if threads < 1 or timeout <= 0:
            raise ValueError("Yahoo threads and timeout must be positive")
        requested = list(dict.fromkeys(symbols))
        if not requested:
            return {}
        data = _download(
            requested,
            start=start,
            end=end,
            threads=threads,
            timeout=timeout,
            repair=True,
        )
        result = _normalise_download(
            data,
            requested,
            start=start,
            end=end,
        )

        # yfinance's repair pass can reject an otherwise valid Yahoo index
        # response when the instrument lacks the metadata repair expects. Keep
        # repaired data as the default, but retry only missing caret-prefixed
        # index symbols without repair. Constituents never take this path.
        unrepaired_symbols = [
            symbol
            for symbol in requested
            if symbol.startswith("^") and (symbol not in result or result[symbol].empty)
        ]
        if unrepaired_symbols:
            unrepaired_data = _download(
                unrepaired_symbols,
                start=start,
                end=end,
                threads=threads,
                timeout=timeout,
                repair=False,
            )
            unrepaired_result = _normalise_download(
                unrepaired_data,
                unrepaired_symbols,
                start=start,
                end=end,
            )
            for symbol, frame in unrepaired_result.items():
                if not frame.empty:
                    result[symbol] = frame
        return result


def _download(
    symbols: list[str],
    *,
    start: date,
    end: date,
    threads: int,
    timeout: float,
    repair: bool,
) -> pd.DataFrame:
    return yf.download(
        tickers=symbols,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        actions=True,
        auto_adjust=False,
        back_adjust=False,
        repair=repair,
        keepna=False,
        group_by="ticker",
        threads=min(threads, len(symbols)),
        progress=False,
        timeout=timeout,
        ignore_tz=True,
        multi_level_index=True,
    )


def _normalise_download(
    data: pd.DataFrame | None,
    requested: list[str],
    *,
    start: date,
    end: date,
) -> dict[str, pd.DataFrame]:
    if data is None or data.empty:
        # A wholly empty batch is ambiguous (transport/rate-limit versus
        # genuine no-data), so it remains retryable rather than evidence.
        return {}
    result: dict[str, pd.DataFrame] = {}
    for symbol in requested:
        frame = _symbol_frame(data, symbol, only_symbol=len(requested) == 1)
        if frame is None:
            if len(requested) > 1:
                result[symbol] = pd.DataFrame()
            continue
        normalised = _normalise(frame, start=start, end=end)
        if not normalised.empty or len(requested) > 1:
            result[symbol] = normalised
    return result


def _symbol_frame(
    data: pd.DataFrame, symbol: str, *, only_symbol: bool
) -> pd.DataFrame | None:
    if not isinstance(data.columns, pd.MultiIndex):
        return data.copy() if only_symbol else None
    for level in range(data.columns.nlevels):
        values = {str(value) for value in data.columns.get_level_values(level)}
        if symbol in values:
            return data.xs(symbol, axis=1, level=level, drop_level=True).copy()
    return None


def _normalise(frame: pd.DataFrame, *, start: date, end: date) -> pd.DataFrame:
    frame = _rename_provider_columns(frame.copy())
    if not {"close", "adjusted_close"}.issubset(frame.columns):
        # Total-return methodology requires a real adjusted series. Silently
        # substituting Close would turn one symbol into price return.
        return pd.DataFrame()
    frame = _complete_provider_columns(frame)
    frame = _normalise_index(frame, start=start, end=end)
    return _normalise_values(frame)


def _rename_provider_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(column[-1]) for column in frame.columns]
    return frame.rename(
        columns={
            column: NORMALISED_COLUMNS[key]
            for column in frame.columns
            if (key := str(column).strip().lower()) in NORMALISED_COLUMNS
        }
    )


def _complete_provider_columns(frame: pd.DataFrame) -> pd.DataFrame:
    for column in NORMALISED_COLUMN_ORDER:
        if column not in frame.columns:
            if column == "repaired":
                frame[column] = False
            elif column in {"dividend", "split_ratio"}:
                frame[column] = 0.0
            else:
                frame[column] = float("nan")
    return frame[list(NORMALISED_COLUMN_ORDER)]


def _normalise_index(
    frame: pd.DataFrame,
    *,
    start: date,
    end: date,
) -> pd.DataFrame:
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_localize(None)
    frame = frame[~frame.index.isna()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    start_timestamp = pd.Timestamp(start)
    end_timestamp = pd.Timestamp(end)
    return frame[(frame.index >= start_timestamp) & (frame.index < end_timestamp)]


def _normalise_values(frame: pd.DataFrame) -> pd.DataFrame:
    numeric = [column for column in frame.columns if column != "repaired"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    frame[numeric] = frame[numeric].replace([float("inf"), float("-inf")], pd.NA)
    for column in ("open", "high", "low"):
        frame.loc[frame[column] <= 0, column] = pd.NA
    frame.loc[frame["volume"] < 0, "volume"] = pd.NA
    frame = frame.dropna(subset=["close", "adjusted_close"], how="any")
    return frame[(frame["close"] > 0) & (frame["adjusted_close"] > 0)]
