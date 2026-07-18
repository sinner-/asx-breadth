"""Shared formatting and quality-state helpers for panel-owned summaries."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Sequence

from ..indicators.base import IndicatorResult


def latest_row(result: IndicatorResult) -> object:
    return None if result.frame.empty else result.frame.iloc[-1]


def is_missing(value: object) -> bool:
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def number(value: object, decimals: int) -> str:
    try:
        numeric = float(value)
        return f"{numeric:,.{decimals}f}" if math.isfinite(numeric) else "—"
    except (TypeError, ValueError):
        return "—"


def percent(value: object) -> str:
    try:
        numeric = float(value)
        return f"{numeric:.1%}" if math.isfinite(numeric) else "—"
    except (TypeError, ValueError):
        return "—"


def ema_detail(row: object, columns: Sequence[tuple[str, str]]) -> str:
    if row is None:
        return "Unavailable"
    return " · ".join(f"{label} {number(row[column], 2)}" for column, label in columns)


def constituent_share(count: object, eligible: object) -> str:
    if is_missing(count) or is_missing(eligible) or float(eligible) <= 0:
        return "Unavailable"
    return (
        f"{percent(float(count) / float(eligible))} of {number(eligible, 0)} eligible"
    )


def positive(value: object) -> bool:
    return not is_missing(value) and float(value) > 0


def latest_quality(result: IndicatorResult, latest: object) -> bool | None:
    for key in (
        "latest_signal_updated",
        "latest_quality_ok",
        "latest_input_quality_ok",
    ):
        parsed = optional_bool(result.metadata.get(key))
        if parsed is not None:
            return parsed
    if latest is None:
        return None
    try:
        value = latest["quality_ok"]
    except (KeyError, TypeError):
        return None
    return None if is_missing(value) else bool(value)


def optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in {"true", "yes", "1"}:
            return True
        if normalised in {"false", "no", "0"}:
            return False
        return None
    if is_missing(value):
        return None
    return bool(value)


def last_accepted_session(result: IndicatorResult) -> object:
    metadata_value = result.metadata.get("last_accepted_session")
    if metadata_value:
        return metadata_value
    frame = result.frame
    if frame.empty or "quality_ok" not in frame:
        return None
    accepted = frame.index[frame["quality_ok"].fillna(False).astype(bool)]
    return accepted[-1] if len(accepted) else None


def held_state(last_accepted: object) -> str:
    if last_accepted:
        try:
            accepted = datetime.fromisoformat(str(last_accepted)).strftime("%-d %b %Y")
            return f"Held · accepted through {accepted}"
        except ValueError:
            pass
    return "Held · latest session withheld"


def relative_state(
    value: object,
    ema19: object,
    ema39: object,
    *,
    include_values: bool = False,
) -> tuple[str, str]:
    if any(is_missing(item) for item in (value, ema19, ema39)):
        return "Trend unavailable", "neutral"
    level = float(value)
    short = float(ema19)
    long = float(ema39)

    def comparison(name: str, average: float) -> str:
        if level > average:
            relation = f"Above {name}"
        elif level < average:
            relation = f"Below {name}"
        else:
            relation = f"At {name}"
        return f"{relation} ({average:.2f})" if include_values else relation

    if level > max(short, long):
        tone = "positive"
    elif level < min(short, long):
        tone = "negative"
    else:
        tone = "neutral"
    return f"{comparison('EMA19', short)} · {comparison('EMA39', long)}", tone


def band_state(value: object, low: object, high: object) -> tuple[str, str]:
    if any(is_missing(item) for item in (value, low, high)):
        return "200-day band unavailable", "neutral"
    level = float(value)
    if level > float(high):
        return "Above 200-day band", "positive"
    if level < float(low):
        return "Below 200-day band", "negative"
    return "Inside 200-day band", "neutral"


def sign_tone(value: object) -> str:
    if is_missing(value):
        return "neutral"
    numeric = float(value)
    if numeric > 0:
        return "positive"
    if numeric < 0:
        return "negative"
    return "neutral"


def signal_label(value: object, *, positive_label: str, negative_label: str) -> str:
    tone = sign_tone(value)
    if tone == "positive":
        return positive_label
    if tone == "negative":
        return negative_label
    return "Neutral" if not is_missing(value) else "Unavailable"
