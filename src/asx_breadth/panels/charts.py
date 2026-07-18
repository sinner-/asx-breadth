"""Plotly panels for the built-in price and breadth indicator set."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..indicators.base import IndicatorResult
from .base import PanelSummary
from .summary import (
    band_state,
    constituent_share,
    held_state,
    is_missing,
    last_accepted_session,
    latest_quality,
    latest_row,
    level_state,
    number,
    positive,
    relative_state,
    sign_tone,
    signal_label,
)


COLORS = {
    "ink": "#16211d",
    "muted": "#64726c",
    "grid": "rgba(22, 33, 29, 0.10)",
    "green": "#147d64",
    "gold": "#d18d22",
    "blue": "#3f6fa0",
    "red": "#b84b45",
}


def _add_line_trace(
    figure: go.Figure,
    frame: pd.DataFrame,
    *,
    column: str,
    label: str,
    colour: str,
    width: float,
    sparkline: bool = False,
) -> None:
    figure.add_trace(
        go.Scatter(
            x=frame.index,
            y=frame[column],
            name=label,
            mode="lines",
            line={"color": colour, "width": width},
            hovertemplate=f"<b>{label}</b>: %{{y:.2f}}<extra></extra>",
            meta={"sparkline": True} if sparkline else None,
        )
    )


def _add_three_ema_trend(
    figure: go.Figure,
    frame: pd.DataFrame,
    *,
    level_column: str,
    level_label: str,
    ema_prefix: str,
) -> None:
    _add_line_trace(
        figure,
        frame,
        column=f"{ema_prefix}200",
        label="200-session EMA",
        colour="rgba(22, 33, 29, 0.55)",
        width=1.4,
    )
    _add_line_trace(
        figure,
        frame,
        column=level_column,
        label=level_label,
        colour=COLORS["ink"],
        width=2.2,
        sparkline=True,
    )
    for span, colour in ((19, COLORS["gold"]), (39, COLORS["blue"])):
        _add_line_trace(
            figure,
            frame,
            column=f"{ema_prefix}{span}",
            label=f"{span}-session EMA",
            colour=colour,
            width=1.4,
        )


class BenchmarkTrendPanel:
    key = "benchmark-trend-chart"
    indicator_key = "benchmark_trend"
    title = "VAS Total Return & Hysteresis Band"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        latest = latest_row(result)
        if latest is None:
            return PanelSummary("VAS total return", "—", "Unavailable")
        detail, tone = relative_state(
            latest["total_return_index"],
            latest["total_return_ema19"],
            latest["total_return_ema39"],
        )
        band_detail, _ = band_state(
            latest["total_return_index"], latest["low_ema200"], latest["high_ema200"]
        )
        return PanelSummary(
            "VAS total return",
            number(latest["total_return_index"], 2),
            f"{detail} · {band_detail}",
            tone,
        )

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        figure = go.Figure()
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["low_ema200"],
                name="200 EMA Low",
                mode="lines",
                line={"color": "rgba(22, 33, 29, 0.55)", "width": 1.2},
                hovertemplate="<b>200 EMA Low</b>: %{y:.2f}<extra></extra>",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["high_ema200"],
                name="200 EMA High",
                mode="lines",
                line={"color": "rgba(22, 33, 29, 0.55)", "width": 1.2},
                fill="tonexty",
                fillcolor="rgba(22, 33, 29, 0.12)",
                hovertemplate="<b>200 EMA High</b>: %{y:.2f}<extra></extra>",
            )
        )
        for column, label, colour, width in (
            ("total_return_index", "VAS total return", COLORS["ink"], 2.0),
            ("total_return_ema19", "19-session EMA", COLORS["gold"], 1.4),
            ("total_return_ema39", "39-session EMA", COLORS["blue"], 1.4),
        ):
            _add_line_trace(
                figure,
                frame,
                column=column,
                label=label,
                colour=colour,
                width=width,
                sparkline=column == "total_return_index",
            )
        return _style(figure, "Adjusted price (AUD)")


class GeometricIndexPanel:
    key = "geometric-index-chart"
    indicator_key = "geometric_index"
    title = "ASX 300 Geometric Index"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        latest = latest_row(result)
        if latest is None:
            return PanelSummary("ASX 300 geometric", "—", "Unavailable")
        detail, tone = relative_state(
            latest["geometric_index"],
            latest["geometric_ema19"],
            latest["geometric_ema39"],
        )
        detail = f"{detail} · {level_state(latest['geometric_index'], latest['geometric_ema200'], 'EMA200')}"
        if latest_quality(result, latest) is False:
            detail = held_state(last_accepted_session(result))
            tone = "neutral"
        return PanelSummary(
            "ASX 300 geometric",
            number(latest["geometric_index"], 2),
            detail,
            tone,
        )

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        figure = go.Figure()
        figure.add_hline(y=100, line_width=1, line_color="rgba(22, 33, 29, 0.22)")
        _add_three_ema_trend(
            figure,
            frame,
            level_column="geometric_index",
            level_label="Geometric index",
            ema_prefix="geometric_ema",
        )
        return _style(figure, "Geometric total-return index (base 100)")


class CurrencyIndexTrendPanel:
    key = "currency-index-trend-chart"
    indicator_key = "currency_index_trend"
    title = "Australian Dollar Currency Index (XDA)"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        latest = latest_row(result)
        if latest is None:
            return PanelSummary("XDA", "—", "Unavailable")
        detail, tone = relative_state(
            latest["currency_index"],
            latest["currency_index_ema19"],
            latest["currency_index_ema39"],
        )
        detail = f"{detail} · {level_state(latest['currency_index'], latest['currency_index_ema200'], 'EMA200')}"
        return PanelSummary(
            "XDA",
            number(latest["currency_index"], 2),
            detail,
            tone,
        )

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        figure = go.Figure()
        _add_three_ema_trend(
            figure,
            frame,
            level_column="currency_index",
            level_label="XDA",
            ema_prefix="currency_index_ema",
        )
        return _style(figure, "Currency index level")


class VolatilityTrendPanel:
    key = "volatility-trend-chart"
    indicator_key = "volatility_trend"
    title = "S&P/ASX 200 VIX (AXVI)"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        latest = latest_row(result)
        if latest is None or is_missing(latest["axvi"]):
            return PanelSummary("AXVI", "—", "Unavailable")
        ema = latest["axvi_ema200"]
        tone = (
            "negative"
            if not is_missing(ema) and float(latest["axvi"]) > float(ema)
            else "ink"
        )
        return PanelSummary(
            "AXVI",
            number(latest["axvi"], 2),
            level_state(latest["axvi"], ema, "EMA200"),
            tone,
        )

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame.dropna(subset=["axvi", "axvi_ema200"])
        figure = go.Figure()
        if frame.empty:
            return _style(figure, "Volatility index level")

        above, below, above_ema, below_ema = _ema_regime_paths(frame)
        regimes = (
            (
                above,
                above_ema,
                "AXVI above EMA",
                COLORS["red"],
                "rgba(184, 75, 69, 0.18)",
            ),
            (
                below,
                below_ema,
                "AXVI below EMA",
                "#111111",
                "rgba(17, 17, 17, 0.14)",
            ),
        )
        # Plotly's `tonexty` fill closes across NaN-separated runs and can
        # bridge unrelated regimes. Render each contiguous regime as its own
        # closed AXVI-to-EMA polygon so fills stop exactly at both crossings.
        for values, baseline, name, _colour, fillcolour in regimes:
            for polygon_number, (polygon_x, polygon_y) in enumerate(
                _regime_fill_polygons(values, baseline),
                start=1,
            ):
                figure.add_trace(
                    go.Scatter(
                        x=polygon_x,
                        y=polygon_y,
                        name=f"{name} fill {polygon_number}",
                        mode="lines",
                        line={"color": "rgba(0,0,0,0)", "width": 0},
                        fill="toself",
                        fillcolor=fillcolour,
                        hoverinfo="skip",
                        showlegend=False,
                        meta={"external_legend": False},
                    )
                )
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["axvi_ema200"],
                name="200-session EMA",
                mode="lines",
                line={"color": "rgba(22, 33, 29, 0.50)", "width": 1.5},
                hovertemplate="<b>200-session EMA</b>: %{y:.2f}<extra></extra>",
            )
        )
        for values, _baseline, name, colour, _fillcolour in regimes:
            figure.add_trace(
                go.Scatter(
                    x=values.index,
                    y=values,
                    name=name,
                    mode="lines",
                    line={"color": colour, "width": 2.4},
                    hoverinfo="skip",
                    connectgaps=False,
                    meta={"external_legend": False, "sparkline": True},
                )
            )

        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["axvi"],
                name="AXVI",
                mode="markers",
                marker={"color": "rgba(0,0,0,0)", "size": 10},
                hovertemplate="<b>AXVI</b>: %{y:.2f}<extra></extra>",
                showlegend=False,
                meta={
                    "external_legend_label": "AXVI",
                    "external_legend_color": (
                        "linear-gradient(90deg, #b84b45 0 50%, #111111 50% 100%)"
                    ),
                    "sparkline_anchor": True,
                },
            )
        )
        return _style(figure, "Volatility index level")


class AdvanceDeclinePanel:
    key = "ad-chart"
    indicator_key = "advance_decline"
    title = "Cumulative Advance–Decline"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        latest = latest_row(result)
        if latest is None:
            return PanelSummary("Cumulative A/D", "—", "Unavailable")
        detail, tone = relative_state(
            latest["cumulative_ad"],
            latest["cumulative_ad_ema19"],
            latest["cumulative_ad_ema39"],
        )
        detail = f"{detail} · {level_state(latest['cumulative_ad'], latest['cumulative_ad_ema200'], 'EMA200')}"
        if latest_quality(result, latest) is False:
            detail = held_state(last_accepted_session(result))
            tone = "neutral"
        return PanelSummary(
            "Cumulative A/D",
            number(latest["cumulative_ad"], 0),
            detail,
            tone,
        )

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        figure = go.Figure()
        traces = (
            ("cumulative_ad", "Cumulative A/D", COLORS["green"], 2.7),
            ("cumulative_ad_ema19", "19-session EMA", COLORS["gold"], 1.8),
            ("cumulative_ad_ema39", "39-session EMA", COLORS["blue"], 1.8),
            (
                "cumulative_ad_ema200",
                "200-session EMA",
                "rgba(22, 33, 29, 0.55)",
                1.5,
            ),
        )
        for column, label, colour, width in traces:
            _add_line_trace(
                figure,
                frame,
                column=column,
                label=label,
                colour=colour,
                width=width,
                sparkline=column == "cumulative_ad",
            )
        return _style(figure, "Cumulative net issues")


class RasiPanel:
    key = "rasi-chart"
    indicator_key = "mcclellan_rasi"
    title = "McClellan Ratio-Adjusted Summation Index"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        return _mcclellan_summary(result, oscillator=False)

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = display_frame(result)
        figure = go.Figure()
        figure.add_hline(y=0, line_width=1, line_color="rgba(22, 33, 29, 0.28)")
        positive, negative = _signed_rasi_paths(frame)
        for values, label, colour, fill in (
            (
                positive,
                "RASI above zero",
                COLORS["green"],
                "rgba(20, 125, 100, 0.13)",
            ),
            (
                negative,
                "RASI below zero",
                COLORS["red"],
                "rgba(184, 75, 69, 0.14)",
            ),
        ):
            figure.add_trace(
                go.Scatter(
                    x=values.index,
                    y=values,
                    name=label,
                    mode="lines",
                    fill="tozeroy",
                    fillcolor=fill,
                    line={"color": colour, "width": 2.7},
                    hoverinfo="skip",
                    showlegend=False,
                    connectgaps=False,
                    meta={"external_legend": False, "sparkline": True},
                )
            )
        # Keep hover on real market sessions only. The two visible traces also
        # contain interpolated zero-crossing timestamps so their strokes meet
        # exactly; a single transparent carrier prevents duplicate hover rows.
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["rasi"],
                name="RASI",
                mode="markers",
                marker={"color": "rgba(0,0,0,0)", "size": 10},
                hovertemplate="<b>RASI</b>: %{y:.2f}<extra></extra>",
                showlegend=False,
                meta={
                    "external_legend_label": "RASI",
                    "external_legend_color": (
                        "linear-gradient(90deg, #147d64 0 50%, #b84b45 50% 100%)"
                    ),
                    "sparkline_anchor": True,
                },
            )
        )
        return _style(figure, "Ratio-adjusted cumulative oscillator")


class McClellanOscillatorPanel:
    key = "mcclellan-oscillator-chart"
    indicator_key = "mcclellan_rasi"
    title = "McClellan Oscillator"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        return _mcclellan_summary(result, oscillator=True)

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = display_frame(result)
        figure = go.Figure()
        figure.add_hline(y=0, line_width=1, line_color="rgba(22, 33, 29, 0.28)")
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["mcclellan_oscillator"],
                name="McClellan Oscillator",
                mode="lines",
                line={"color": COLORS["ink"], "width": 2.0},
                hovertemplate="<b>Oscillator</b>: %{y:.2f}<extra></extra>",
                meta={"sparkline": True},
            )
        )
        return _style(figure, "EMA19 − EMA39", include_zero=True)


class NewHighsPanel:
    key = "new-highs-chart"
    indicator_key = "new_high_low"
    title = "New 52-Week Highs"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        return _new_high_low_summary(result, "highs")

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        figure = go.Figure()
        figure.add_hline(y=0, line_width=1, line_color="rgba(22, 33, 29, 0.28)")
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["new_highs"],
                name="New highs",
                mode="lines",
                line={"color": "#111111", "width": 2.0},
                fill="tozeroy",
                fillcolor="rgba(17, 17, 17, 0.18)",
                hovertemplate="<b>New highs</b>: %{y:.0f}<extra></extra>",
                meta={"sparkline": True},
            )
        )
        return _style(
            figure,
            "Constituent count",
            include_zero=True,
            scale_group="new-high-low",
        )


class NewLowsPanel:
    key = "new-lows-chart"
    indicator_key = "new_high_low"
    title = "New 52-Week Lows"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        return _new_high_low_summary(result, "lows")

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        negative_lows = -frame["new_lows"]
        figure = go.Figure()
        figure.add_hline(y=0, line_width=1, line_color="rgba(22, 33, 29, 0.28)")
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=negative_lows,
                name="New lows",
                mode="lines",
                line={"color": COLORS["red"], "width": 2.0},
                fill="tozeroy",
                fillcolor="rgba(184, 75, 69, 0.20)",
                hovertemplate="<b>New lows</b>: %{y:.0f}<extra></extra>",
                meta={"sparkline": True},
            )
        )
        return _style(
            figure,
            "Negative constituent count",
            include_zero=True,
            scale_group="new-high-low",
        )


class NetNewHighsPanel:
    key = "net-new-highs-chart"
    indicator_key = "new_high_low"
    title = "New Highs − New Lows"

    def summary(self, result: IndicatorResult) -> PanelSummary:
        return _new_high_low_summary(result, "net")

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        figure = go.Figure()
        figure.add_hline(y=0, line_width=1, line_color="rgba(22, 33, 29, 0.28)")
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["nh_nl"],
                name="NH − NL",
                mode="lines",
                line={"color": COLORS["ink"], "width": 2.0},
                hovertemplate="<b>NH − NL</b>: %{y:.0f}<extra></extra>",
                meta={"sparkline": True},
            )
        )
        return _style(
            figure,
            "NH − NL",
            include_zero=True,
        )


def _mcclellan_summary(
    result: IndicatorResult,
    *,
    oscillator: bool,
) -> PanelSummary:
    frame = display_frame(result)
    latest = None if frame.empty else frame.iloc[-1]
    warmup = int(result.metadata.get("warmup_sessions", 0) or 0)
    label = "McClellan oscillator" if oscillator else "RASI"
    column = "mcclellan_oscillator" if oscillator else "rasi"
    if latest is None:
        detail = (
            f"Insufficient {warmup}-session display warm-up"
            if warmup
            else "Unavailable"
        )
        return PanelSummary(label, "—", detail)
    value = latest[column]
    detail = signal_label(
        value,
        positive_label="Breadth momentum positive" if oscillator else "Above zero",
        negative_label="Breadth momentum negative" if oscillator else "Below zero",
    )
    tone = sign_tone(value)
    if latest_quality(result, latest) is False:
        detail = held_state(last_accepted_session(result))
        tone = "neutral"
    return PanelSummary(label, number(value, 1), detail, tone)


def _new_high_low_summary(
    result: IndicatorResult,
    kind: str,
) -> PanelSummary:
    latest = latest_row(result)
    labels = {
        "highs": "New 52-week highs",
        "lows": "New 52-week lows",
        "net": "New highs − lows",
    }
    label = labels[kind]
    if latest is None:
        return PanelSummary(label, "—", "Unavailable")
    highs = latest["new_highs"]
    lows = latest["new_lows"]
    net = latest["nh_nl"]
    eligible = latest["eligible_issues"]
    if kind == "highs":
        value = number(highs, 0)
        detail = constituent_share(highs, eligible)
        tone = "positive" if positive(highs) else "neutral"
    elif kind == "lows":
        value = f"{number(lows, 0)} lows" if not is_missing(lows) else "—"
        detail = constituent_share(lows, eligible)
        tone = "negative" if positive(lows) else "neutral"
    else:
        value = number(net, 0)
        detail = f"{number(highs, 0)} highs · {number(lows, 0)} lows"
        tone = sign_tone(net)
    if latest_quality(result, latest) is False:
        detail = held_state(last_accepted_session(result))
        tone = "neutral"
    return PanelSummary(label, value, detail, tone)


def _style(
    figure: go.Figure,
    y_title: str,
    *,
    include_zero: bool = False,
    scale_group: str | None = None,
) -> go.Figure:
    x_values = [
        value
        for trace in figure.data
        for value in (trace.x if trace.x is not None else [])
    ]
    minimum_x = min(x_values) if x_values else None
    maximum_x = max(x_values) if x_values else None
    display_bounds = None
    if minimum_x is not None and maximum_x is not None and minimum_x < maximum_x:
        display_maximum_x = pd.Timestamp(maximum_x) + pd.offsets.BDay(2)
        display_bounds = [
            pd.Timestamp(minimum_x).isoformat(),
            display_maximum_x.isoformat(),
        ]
    figure.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": "Inter, ui-sans-serif, system-ui, sans-serif",
            "color": COLORS["ink"],
        },
        height=445,
        margin={"l": 62, "r": 26, "t": 28, "b": 48},
        hovermode="x unified",
        dragmode="zoom",
        showlegend=False,
        meta={
            "include_zero": include_zero,
            "scale_group": scale_group,
            "display_bounds": display_bounds,
        },
    )
    figure.update_xaxes(
        title=None,
        fixedrange=False,
        showgrid=False,
        # Market series have no weekend observations. Compress Saturday and
        # Sunday out of the coordinate scale instead of drawing dead space
        # between Friday and Monday on every card.
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        rangeslider={"visible": False},
    )
    if display_bounds is not None:
        # Leave one market-session gutter after the last observation. With the
        # final point placed exactly on the SVG clipping boundary, a late
        # crossover can be visually hidden even though its numeric coordinate
        # is correct (the latest VAS/EMA19 crossover exposed this).
        figure.update_xaxes(range=display_bounds)
    # fixedrange is the explicit guarantee that wheel/box interactions cannot
    # zoom the value axis. Only the date axis remains interactive.
    figure.update_yaxes(
        title=y_title,
        fixedrange=True,
        gridcolor=COLORS["grid"],
        zeroline=False,
    )
    return figure


def display_frame(result: IndicatorResult) -> pd.DataFrame:
    """Return the observations a panel may honestly display."""
    warmup = int(result.metadata.get("warmup_sessions", 0) or 0)
    return result.frame.iloc[warmup:] if warmup else result.frame


def _signed_rasi_paths(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Split RASI by sign while joining both colours at the exact zero crossing."""
    if frame.empty:
        empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        return empty, empty.copy()

    dates: list[pd.Timestamp] = []
    positive: list[float] = []
    negative: list[float] = []
    previous_date: pd.Timestamp | None = None
    previous_value: float | None = None
    for raw_date, raw_value in frame["rasi"].items():
        trade_date = pd.Timestamp(raw_date)
        value = float(raw_value) if pd.notna(raw_value) else np.nan
        if (
            previous_date is not None
            and previous_value is not None
            and np.isfinite(value)
            and previous_value * value < 0
        ):
            fraction = abs(previous_value) / (abs(previous_value) + abs(value))
            crossing = _interpolate_without_weekends(
                previous_date,
                trade_date,
                fraction,
            )
            dates.append(crossing)
            positive.append(0.0)
            negative.append(0.0)
        dates.append(trade_date)
        positive.append(value if np.isfinite(value) and value >= 0 else np.nan)
        negative.append(value if np.isfinite(value) and value <= 0 else np.nan)
        previous_date = trade_date
        previous_value = value if np.isfinite(value) else None
    index = pd.DatetimeIndex(dates)
    return (
        pd.Series(positive, index=index, dtype=float),
        pd.Series(negative, index=index, dtype=float),
    )


def _ema_regime_paths(
    frame: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Split AXVI/EMA paths with both regimes meeting at exact crossovers."""
    dates: list[pd.Timestamp] = []
    above: list[float] = []
    below: list[float] = []
    above_ema: list[float] = []
    below_ema: list[float] = []
    previous_date: pd.Timestamp | None = None
    previous_level: float | None = None
    previous_ema: float | None = None
    previous_difference: float | None = None

    for raw_date, row in frame[["axvi", "axvi_ema200"]].iterrows():
        trade_date = pd.Timestamp(raw_date)
        level = float(row["axvi"])
        ema = float(row["axvi_ema200"])
        difference = level - ema
        if (
            previous_date is not None
            and previous_level is not None
            and previous_ema is not None
            and previous_difference is not None
            and previous_difference * difference < 0
        ):
            fraction = abs(previous_difference) / (
                abs(previous_difference) + abs(difference)
            )
            crossing_date = _interpolate_without_weekends(
                previous_date,
                trade_date,
                fraction,
            )
            crossing_level = previous_level + (level - previous_level) * fraction
            crossing_ema = previous_ema + (ema - previous_ema) * fraction
            crossing = (crossing_level + crossing_ema) / 2.0
            dates.append(crossing_date)
            above.append(crossing)
            below.append(crossing)
            above_ema.append(crossing)
            below_ema.append(crossing)

        dates.append(trade_date)
        if difference > 0:
            above.append(level)
            above_ema.append(ema)
            below.append(np.nan)
            below_ema.append(np.nan)
        elif difference < 0:
            above.append(np.nan)
            above_ema.append(np.nan)
            below.append(level)
            below_ema.append(ema)
        else:
            above.append(level)
            above_ema.append(ema)
            below.append(level)
            below_ema.append(ema)
        previous_date = trade_date
        previous_level = level
        previous_ema = ema
        previous_difference = difference

    index = pd.DatetimeIndex(dates)
    return (
        pd.Series(above, index=index, dtype=float),
        pd.Series(below, index=index, dtype=float),
        pd.Series(above_ema, index=index, dtype=float),
        pd.Series(below_ema, index=index, dtype=float),
    )


def _regime_fill_polygons(
    values: pd.Series,
    baseline: pd.Series,
) -> list[tuple[list[pd.Timestamp], list[float]]]:
    """Close each finite regime run against its matching EMA path."""
    aligned = pd.DataFrame({"value": values, "baseline": baseline})
    valid = aligned["value"].notna() & aligned["baseline"].notna()
    polygons: list[tuple[list[pd.Timestamp], list[float]]] = []
    start: int | None = None
    for position, is_valid in enumerate(valid.tolist() + [False]):
        if is_valid and start is None:
            start = position
            continue
        if is_valid or start is None:
            continue
        run = aligned.iloc[start:position]
        start = None
        if len(run) < 2:
            continue
        run_dates = [pd.Timestamp(item) for item in run.index]
        polygon_x = [*run_dates, *reversed(run_dates)]
        polygon_y = [
            *run["value"].astype(float).tolist(),
            *reversed(run["baseline"].astype(float).tolist()),
        ]
        polygons.append((polygon_x, polygon_y))
    return polygons


def _interpolate_without_weekends(
    start: pd.Timestamp,
    end: pd.Timestamp,
    fraction: float,
) -> pd.Timestamp:
    """Interpolate in the same weekday-only time scale Plotly displays."""
    segments: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = start
    while cursor < end:
        next_midnight = cursor.normalize() + pd.Timedelta(days=1)
        segment_end = min(next_midnight, end)
        if cursor.weekday() < 5 and segment_end > cursor:
            segments.append((cursor, segment_end))
        cursor = segment_end

    visible_duration = sum(
        (segment_end - segment_start for segment_start, segment_end in segments),
        pd.Timedelta(0),
    )
    if not segments or visible_duration <= pd.Timedelta(0):
        return start + (end - start) * fraction

    remaining = visible_duration * fraction
    for segment_start, segment_end in segments:
        duration = segment_end - segment_start
        if remaining < duration:
            return segment_start + remaining
        remaining -= duration
    return segments[-1][1]
