"""Plotly panels for the built-in price and breadth indicator set."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from ..indicators.base import IndicatorResult


COLORS = {
    "ink": "#16211d",
    "muted": "#64726c",
    "grid": "rgba(22, 33, 29, 0.10)",
    "green": "#147d64",
    "gold": "#d18d22",
    "blue": "#3f6fa0",
    "red": "#b84b45",
}


class BenchmarkTrendPanel:
    key = "benchmark-trend-chart"
    indicator_key = "benchmark_trend"
    title = "VAS Total Return & Hysteresis Band"

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
            figure.add_trace(
                go.Scatter(
                    x=frame.index,
                    y=frame[column],
                    name=label,
                    mode="lines",
                    line={"color": colour, "width": width},
                    hovertemplate=f"<b>{label}</b>: %{{y:.2f}}<extra></extra>",
                )
            )
        return _style(figure, "Adjusted price (AUD)")


class GeometricIndexPanel:
    key = "geometric-index-chart"
    indicator_key = "geometric_index"
    title = "ASX 300 Geometric Index"

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        figure = go.Figure()
        figure.add_hline(y=100, line_width=1, line_color="rgba(22, 33, 29, 0.22)")
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["geometric_ema200"],
                name="200-session EMA",
                mode="lines",
                line={"color": "rgba(22, 33, 29, 0.55)", "width": 1.4},
                hovertemplate="<b>200-session EMA</b>: %{y:.2f}<extra></extra>",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["geometric_index"],
                name="Geometric index",
                mode="lines",
                line={"color": COLORS["ink"], "width": 2.2},
                customdata=frame[
                    [
                        "daily_geometric_return",
                        "quoted_issues",
                        "active_issues",
                        "coverage",
                        "coverage_floor",
                        "quality_ok",
                    ]
                ],
                hovertemplate=(
                    "<b>Geometric index</b>: %{y:.2f}<br>"
                    "Daily geometric return: %{customdata[0]:+.2%}<br>"
                    "Quotes: %{customdata[1]:.0f} / %{customdata[2]:.0f}<br>"
                    "Coverage: %{customdata[3]:.1%}<br>"
                    "Quality floor: %{customdata[4]:.1%}<br>"
                    "Quality accepted: %{customdata[5]}<extra></extra>"
                ),
            )
        )
        for column, label, colour in (
            ("geometric_ema19", "19-session EMA", COLORS["gold"]),
            ("geometric_ema39", "39-session EMA", COLORS["blue"]),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame.index,
                    y=frame[column],
                    name=label,
                    mode="lines",
                    line={"color": colour, "width": 1.4},
                    hovertemplate=f"<b>{label}</b>: %{{y:.2f}}<extra></extra>",
                )
            )
        return _style(figure, "Geometric total-return index (base 100)")


class CurrencyIndexTrendPanel:
    key = "currency-index-trend-chart"
    indicator_key = "currency_index_trend"
    title = "Australian Dollar Currency Index (XDA)"

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        figure = go.Figure()
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["currency_index_ema200"],
                name="200-session EMA",
                mode="lines",
                line={"color": "rgba(22, 33, 29, 0.55)", "width": 1.4},
                hovertemplate="<b>200-session EMA</b>: %{y:.2f}<extra></extra>",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["currency_index"],
                name="XDA",
                mode="lines",
                line={"color": COLORS["ink"], "width": 2.2},
                hovertemplate="<b>XDA</b>: %{y:.2f}<extra></extra>",
            )
        )
        for column, label, colour in (
            ("currency_index_ema19", "19-session EMA", COLORS["gold"]),
            ("currency_index_ema39", "39-session EMA", COLORS["blue"]),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame.index,
                    y=frame[column],
                    name=label,
                    mode="lines",
                    line={"color": colour, "width": 1.4},
                    hovertemplate=f"<b>{label}</b>: %{{y:.2f}}<extra></extra>",
                )
            )
        return _style(figure, "Currency index level")


class VolatilityTrendPanel:
    key = "volatility-trend-chart"
    indicator_key = "volatility_trend"
    title = "S&P/ASX 200 VIX (AXVI)"

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
            for number, (polygon_x, polygon_y) in enumerate(
                _regime_fill_polygons(values, baseline),
                start=1,
            ):
                figure.add_trace(
                    go.Scatter(
                        x=polygon_x,
                        y=polygon_y,
                        name=f"{name} fill {number}",
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
                hoverinfo="skip",
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
                )
            )

        relation = np.where(
            frame["axvi"] > frame["axvi_ema200"],
            "Above 200-session EMA",
            np.where(
                frame["axvi"] < frame["axvi_ema200"],
                "Below 200-session EMA",
                "At 200-session EMA",
            ),
        )
        customdata = np.column_stack(
            (frame["axvi_ema200"], frame["axvi"] - frame["axvi_ema200"], relation)
        )
        figure.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame["axvi"],
                name="AXVI",
                mode="markers",
                marker={"color": "rgba(0,0,0,0)", "size": 10},
                customdata=customdata,
                hovertemplate=(
                    "<b>AXVI</b>: %{y:.2f}<br>"
                    "200-session EMA: %{customdata[0]:.2f}<br>"
                    "Spread: %{customdata[1]:+.2f}<br>"
                    "%{customdata[2]}<extra></extra>"
                ),
                showlegend=False,
                meta={"external_legend": False},
            )
        )
        return _style(figure, "Volatility index level")


class AdvanceDeclinePanel:
    key = "ad-chart"
    indicator_key = "advance_decline"
    title = "Cumulative Advance–Decline"

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = result.frame
        figure = go.Figure()
        traces = (
            ("cumulative_ad", "Cumulative A/D", COLORS["green"], 2.7, True),
            ("cumulative_ad_ema19", "19-session EMA", COLORS["gold"], 1.8, False),
            ("cumulative_ad_ema39", "39-session EMA", COLORS["blue"], 1.8, False),
            (
                "cumulative_ad_ema200",
                "200-session EMA",
                "rgba(22, 33, 29, 0.55)",
                1.5,
                False,
            ),
        )
        for column, label, colour, width, show_breadth in traces:
            hover = f"<b>{label}</b>: %{{y:.2f}}"
            customdata = None
            if show_breadth:
                customdata = frame[
                    [
                        "advances",
                        "declines",
                        "unchanged",
                        "advances_plus_declines",
                        "coverage",
                        "coverage_floor",
                        "quality_ok",
                    ]
                ]
                hover += (
                    "<br>Advances: %{customdata[0]:.0f}"
                    "<br>Declines: %{customdata[1]:.0f}"
                    "<br>Unchanged: %{customdata[2]:.0f}"
                    "<br>A + D: %{customdata[3]:.0f}"
                    "<br>Coverage: %{customdata[4]:.1%}"
                    "<br>Quality floor: %{customdata[5]:.1%}"
                    "<br>Quality accepted: %{customdata[6]}"
                )
            figure.add_trace(
                go.Scatter(
                    x=frame.index,
                    y=frame[column],
                    name=label,
                    mode="lines",
                    line={"color": colour, "width": width},
                    customdata=customdata,
                    hovertemplate=hover + "<extra></extra>",
                )
            )
        return _style(figure, "Cumulative net issues")


class RasiPanel:
    key = "rasi-chart"
    indicator_key = "mcclellan_rasi"
    title = "McClellan Ratio-Adjusted Summation Index"

    def figure(self, result: IndicatorResult) -> go.Figure:
        frame = display_frame(result)
        figure = go.Figure()
        figure.add_hline(y=0, line_width=1, line_color="rgba(22, 33, 29, 0.28)")
        positive, negative = _signed_rasi_paths(frame)
        customdata = frame[["mcclellan_oscillator", "ratio_ema19", "ratio_ema39"]]
        hover = (
            "<b>RASI</b>: %{y:.2f}<br>"
            "Oscillator: %{customdata[0]:.2f}<br>"
            "Ratio EMA 19: %{customdata[1]:.2f}<br>"
            "Ratio EMA 39: %{customdata[2]:.2f}<extra></extra>"
        )
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
                customdata=customdata,
                hovertemplate=hover,
                showlegend=False,
                meta={"external_legend": False},
            )
        )
        return _style(figure, "Ratio-adjusted cumulative oscillator")


class McClellanOscillatorPanel:
    key = "mcclellan-oscillator-chart"
    indicator_key = "mcclellan_rasi"
    title = "McClellan Oscillator"

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
            )
        )
        return _style(figure, "EMA19 − EMA39", include_zero=True)


class NewHighsPanel:
    key = "new-highs-chart"
    indicator_key = "new_high_low"
    title = "New 52-Week Highs"

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
                customdata=frame[
                    ["eligible_issues", "quoted_issues", "active_issues", "coverage"]
                ],
                hovertemplate=(
                    "<b>New highs</b>: %{y:.0f}<br>"
                    "Eligible history: %{customdata[0]:.0f}<br>"
                    "Quotes: %{customdata[1]:.0f} / %{customdata[2]:.0f}<br>"
                    "Coverage: %{customdata[3]:.1%}<extra></extra>"
                ),
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
                customdata=frame[
                    [
                        "new_lows",
                        "eligible_issues",
                        "quoted_issues",
                        "active_issues",
                        "coverage",
                    ]
                ],
                hovertemplate=(
                    "<b>New lows</b>: %{customdata[0]:.0f}<br>"
                    "Eligible history: %{customdata[1]:.0f}<br>"
                    "Quotes: %{customdata[2]:.0f} / %{customdata[3]:.0f}<br>"
                    "Coverage: %{customdata[4]:.1%}<extra></extra>"
                ),
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
                customdata=frame[
                    [
                        "new_highs",
                        "new_lows",
                        "eligible_issues",
                        "quoted_issues",
                        "active_issues",
                        "coverage",
                    ]
                ],
                hovertemplate=(
                    "<b>NH − NL</b>: %{y:.0f}<br>"
                    "New highs: %{customdata[0]:.0f}<br>"
                    "New lows: %{customdata[1]:.0f}<br>"
                    "Eligible history: %{customdata[2]:.0f}<br>"
                    "Quotes: %{customdata[3]:.0f} / %{customdata[4]:.0f}<br>"
                    "Coverage: %{customdata[5]:.1%}<extra></extra>"
                ),
            )
        )
        return _style(
            figure,
            "NH − NL",
            include_zero=True,
        )


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
    figure.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": "Inter, ui-sans-serif, system-ui, sans-serif",
            "color": COLORS["ink"],
        },
        height=445,
        margin={"l": 62, "r": 26, "t": 72, "b": 48},
        hovermode="x unified",
        hoverlabel={
            "bgcolor": "#fffdf8",
            "bordercolor": "rgba(22, 33, 29, 0.28)",
            "font": {"color": COLORS["ink"]},
        },
        dragmode="zoom",
        legend={
            "orientation": "h",
            "y": 1.13,
            "yanchor": "middle",
            "x": 1,
            "xanchor": "right",
        },
        meta={"include_zero": include_zero, "scale_group": scale_group},
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
    if minimum_x is not None and maximum_x is not None and minimum_x < maximum_x:
        # Leave one market-session gutter after the last observation. With the
        # final point placed exactly on the SVG clipping boundary, a late
        # crossover can be visually hidden even though its numeric coordinate
        # is correct (the latest VAS/EMA19 crossover exposed this).
        display_maximum_x = pd.Timestamp(maximum_x) + pd.offsets.BDay(2)
        figure.update_xaxes(range=[minimum_x, display_maximum_x])
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
    metadata = getattr(result, "metadata", {})
    warmup = int(metadata.get("warmup_sessions", 0) or 0)
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
