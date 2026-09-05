"""Self-contained HTML dashboard renderer driven by panel plugins."""

from __future__ import annotations

import html
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

import plotly.io as pio
from plotly.graph_objects import Figure

from .indicators.base import IndicatorResult
from .models import Snapshot
from .panels.base import DashboardPanel, PanelSummary


PLOT_CONFIG = {
    "responsive": True,
    # Wheel zoom is implemented below so an out-of-bounds proposal can be
    # rejected before Plotly draws it (native clamping visibly snaps back).
    "scrollZoom": False,
    "displaylogo": False,
    "modeBarButtonsToRemove": [
        "select2d",
        "lasso2d",
        "autoScale2d",
        "zoomIn2d",
        "zoomOut2d",
        "pan2d",
    ],
    "doubleClick": "reset",
}


def render_dashboard(
    output: Path,
    *,
    snapshot: Snapshot,
    results: Mapping[str, IndicatorResult],
    panels: Sequence[DashboardPanel],
    sync_summary: Mapping[str, object],
) -> None:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    selector_html: list[str] = []
    chart_views: list[str] = []
    plot_ids: list[str] = []
    plotly_included = False
    prepared_panels: list[
        tuple[DashboardPanel, IndicatorResult | None, Figure | None]
    ] = []
    for panel in panels:
        result = results.get(panel.indicator_key)
        figure = None if result is None or result.frame.empty else panel.figure(result)
        prepared_panels.append((panel, result, figure))
    active_index = next(
        (
            index
            for index, (_, _, figure) in enumerate(prepared_panels)
            if figure is not None and _figure_has_data(figure)
        ),
        0,
    )

    for index, (panel, result, figure) in enumerate(prepared_panels):
        heading_id = f"heading-{panel.key}"
        view_id = f"view-{panel.key}"
        active = index == active_index
        plot_id = ""
        if figure is None or not _figure_has_data(figure):
            message = "Unavailable or insufficient verified history."
            if (
                result is not None
                and result.metadata.get("warmup_sessions")
                and result.frame.empty is False
            ):
                message = (
                    "Insufficient history after the "
                    f"{int(result.metadata['warmup_sessions'])}-session display warm-up."
                )
            chart_views.append(
                '<section class="chart-view unavailable'
                f'{" is-active" if active else ""}" id="{html.escape(view_id)}" '
                f'aria-hidden="{str(not active).lower()}" '
                f'aria-labelledby="{html.escape(heading_id)}">'
                '<div class="chart-heading">'
                f'<h2 id="{html.escape(heading_id)}">{html.escape(panel.title)}</h2>'
                f"</div><p>{html.escape(message)}</p></section>"
            )
        else:
            plot_id = f"plot-{panel.key}"
            chart = pio.to_html(
                figure,
                full_html=False,
                include_plotlyjs="inline" if not plotly_included else False,
                config=PLOT_CONFIG,
                div_id=plot_id,
            )
            chart = chart.replace(
                f'id="{plot_id}"',
                f'id="{plot_id}" role="group" aria-label="{html.escape(panel.title)} interactive chart"',
                1,
            )
            plot_ids.append(plot_id)
            plotly_included = True
            chart_views.append(
                '<section class="chart-view'
                f'{" is-active" if active else ""}" id="{html.escape(view_id)}" '
                f'aria-hidden="{str(not active).lower()}" '
                f'aria-labelledby="{html.escape(heading_id)}">'
                '<div class="chart-heading">'
                f'<h2 id="{html.escape(heading_id)}">{html.escape(panel.title)}</h2>'
                "<span>Drag to zoom · Ctrl/⌘ + wheel · double-click to reset</span>"
                "</div>"
                f"{_chart_meta(figure, plot_id)}{chart}</section>"
            )

        summary = (
            panel.summary(result)
            if result is not None
            else PanelSummary(panel.title, "—", "Unavailable")
        )
        selector_html.append(
            '<button type="button" class="kpi chart-selector '
            f'{html.escape(summary.tone)}{" is-active" if active else ""}" '
            f'data-chart-target="{html.escape(view_id)}" '
            f'data-plot-id="{html.escape(plot_id)}" '
            f'aria-controls="{html.escape(view_id)}" '
            f'aria-pressed="{str(active).lower()}">'
            f'<span class="kpi-label">{html.escape(summary.label)}</span>'
            f"<strong>{html.escape(summary.value)}</strong>"
            f"<small>{html.escape(summary.detail)}</small>"
            '<span class="spark-period" aria-hidden="true">1Y</span>'
            f"{_sparkline_svg(figure, panel.title) if figure is not None else _empty_sparkline(panel.title)}"
            "</button>"
        )

    kpi_html = "".join(selector_html)
    chart_pane_html = (
        '<section class="chart-pane" aria-label="Expanded chart">'
        f"{''.join(chart_views)}</section>"
    )

    generated_at = datetime.now(ZoneInfo("Australia/Sydney"))
    generated = generated_at.strftime("%d %b %Y, %H:%M %Z")
    sync_text = _sync_text(sync_summary, today=generated_at.date())
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(snapshot.universe_code)} market breadth</title>
  <style>
    :root {{ --ink:#16211d; --muted:#64726c; --paper:#f4f1e9; --card:#fffdf8;
             --line:#ded9cc; --green:#147d64; --red:#b84b45; --amber:#9b6818;
             --shadow:0 14px 36px rgba(35,43,38,.08); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--paper);
            font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    .shell {{ width:min(1440px,calc(100% - 36px)); margin:0 auto; padding:18px 0 38px; }}
    .kpis {{ display:grid; grid-template-columns:repeat(5,1fr); gap:8px; margin-bottom:10px; }}
    .kpi,.range-control,.chart-pane,.empty {{ background:var(--card); border:1px solid var(--line);
                                     border-radius:16px; box-shadow:var(--shadow); }}
    .kpi {{ --spark:var(--ink); position:relative; min-width:0; padding:10px 12px 8px; color:var(--ink);
            border-top:3px solid var(--line); text-align:left; font:inherit; cursor:pointer;
            appearance:none; transition:border-color .15s ease,box-shadow .15s ease; }}
    .kpi:hover {{ border-color:#aeb8b1; }}
    .kpi:focus-visible {{ outline:3px solid rgba(20,125,100,.28); outline-offset:2px; }}
    .kpi.is-active {{ border-color:var(--ink); box-shadow:0 0 0 1px var(--ink),var(--shadow); }}
    .kpi.positive {{ border-top-color:var(--green); }}
    .kpi.negative {{ border-top-color:var(--red); }}
    .kpi.neutral {{ border-top-color:var(--amber); }}
    .kpi.ink {{ border-top-color:var(--ink); }}
    .kpi-label {{ display:block; overflow:hidden; color:var(--muted); font-size:.67rem;
                  font-weight:750; letter-spacing:.06em; text-overflow:ellipsis;
                  text-transform:uppercase; white-space:nowrap; }}
    .kpi strong {{ display:block; margin-top:3px; font-family:Georgia,"Times New Roman",serif;
                   font-size:1.3rem; font-weight:500; line-height:1; }}
    .kpi small {{ display:-webkit-box; overflow:hidden; height:2.35em; margin-top:3px;
                  color:var(--muted); font-size:.64rem; line-height:1.18;
                  -webkit-box-orient:vertical; -webkit-line-clamp:2; }}
    .sparkline {{ display:block; width:100%; height:34px; margin-top:4px; overflow:visible;
                  color:var(--spark); }}
    .spark-path {{ fill:none; stroke:var(--spark-stroke,currentColor); stroke-width:1.7;
                   vector-effect:non-scaling-stroke; }}
    .spark-end {{ fill:currentColor; }}
    .spark-zero {{ stroke:rgba(22,33,29,.18); stroke-width:1;
                   vector-effect:non-scaling-stroke; }}
    .spark-empty line {{ stroke:var(--line); stroke-width:1; }}
    .spark-period {{ position:absolute; right:10px; bottom:5px; color:rgba(100,114,108,.72);
                     font-size:.55rem; font-weight:750; letter-spacing:.04em; }}
    .range-control {{ display:flex; align-items:center; justify-content:space-between; gap:14px;
                      position:sticky; top:10px; z-index:20; padding:10px 14px;
                      margin-bottom:10px; }}
    .range-control > span {{ color:var(--muted); font-size:.75rem; font-weight:750;
                             letter-spacing:.07em; text-transform:uppercase; }}
    .range-buttons {{ display:flex; gap:6px; }}
    .range-buttons button {{ min-width:44px; min-height:34px; padding:6px 11px; border:1px solid var(--line);
                             border-radius:8px; color:var(--ink); background:#f8f5ed; font:inherit;
                             font-size:.78rem; font-weight:750; cursor:pointer; }}
    .range-buttons button:hover {{ border-color:#9aab9f; }}
    .range-buttons button:focus-visible {{ outline:3px solid rgba(20,125,100,.28); outline-offset:2px; }}
    .range-buttons button[aria-pressed="true"] {{ color:#fff; border-color:var(--green);
                                                   background:var(--green); }}
    .chart-pane {{ position:relative; min-height:520px; padding:0 10px 4px; overflow:hidden; }}
    .chart-view {{ display:none; }}
    .chart-view.is-active {{ display:block; }}
    .hoverlayer .legend,.hoverlayer .hovertext {{ display:none !important; }}
    .js-plotly-plot .nsewdrag {{ cursor:default !important; }}
    .js-plotly-plot .nsewdrag:active {{ cursor:ew-resize !important; }}
    .dragcover {{ cursor:ew-resize !important; }}
    .js-plotly-plot .zoombox {{ fill:rgba(63,111,160,.20) !important;
                               stroke:#3f6fa0 !important; stroke-width:1px !important; }}
    .chart-heading {{ display:flex; justify-content:space-between; gap:20px; align-items:baseline;
                      padding:20px 20px 0; }}
    .chart-heading h2 {{ margin:0; font-family:Georgia,"Times New Roman",serif;
                         font-size:1.35rem; font-weight:500; }}
    .chart-heading span {{ color:var(--muted); font-size:.78rem; }}
    .chart-meta {{ display:flex; align-items:center; justify-content:flex-start;
                   gap:12px 24px; min-height:38px; padding:9px 20px 2px; }}
    .chart-legend,.hover-readout {{ display:flex; flex-wrap:wrap; align-items:center;
                                   gap:7px 14px; color:var(--muted);
                                   font-size:.72rem; line-height:1.2; }}
    .hover-readout {{ justify-content:flex-start; min-width:260px; color:var(--ink); }}
    .hover-date {{ color:var(--muted); font-weight:750; }}
    .legend-item {{ display:inline-flex; align-items:center; gap:5px; white-space:nowrap; }}
    .legend-value {{ color:var(--ink); font-weight:750; font-variant-numeric:tabular-nums; }}
    .legend-swatch {{ width:17px; height:3px; border-radius:2px; background:var(--swatch); }}
    .empty {{ padding:42px; margin-top:18px; text-align:center; }}
    .unavailable {{ min-height:150px; }}
    .unavailable p {{ margin:24px 20px 34px; color:var(--muted); }}
    footer {{ display:flex; justify-content:space-between; gap:18px; padding:18px 4px 0;
              color:var(--muted); font-size:.72rem; line-height:1.45; }}
    footer p {{ margin:0; }}
    footer p:last-child {{ max-width:760px; text-align:right; }}
    @media (max-width:1100px) {{ .kpis {{ grid-template-columns:repeat(4,1fr); }} }}
    @media (max-width:720px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }}
      .chart-heading {{ align-items:start; flex-direction:column; gap:4px; }}
      .chart-heading span {{ display:none; }} footer {{ flex-direction:column; }}
      footer p:last-child {{ text-align:left; }} }}
    @media (max-width:460px) {{ .shell {{ width:min(100% - 20px,1440px); padding-top:22px; }}
      .kpis {{ gap:7px; }} .kpi {{ padding:9px 9px 7px; }}
      .kpi strong {{ font-size:1.2rem; }} .kpi small {{ font-size:.61rem; }}
      .sparkline {{ height:30px; }} }}
    @media (max-width:600px) {{ .modebar {{ display:none !important; }}
      .range-control {{ top:6px; padding:8px 9px; }}
      .range-control > span {{ position:absolute; width:1px; height:1px; overflow:hidden;
                               clip:rect(0 0 0 0); white-space:nowrap; }}
      .range-buttons {{ width:100%; }}
      .range-buttons button {{ flex:1; min-height:40px; }}
      .chart-meta {{ align-items:flex-start; flex-direction:column; padding:9px 10px 0; }}
      .chart-legend,.hover-readout {{ gap:7px 11px; font-size:.69rem; }}
      .hover-readout {{ justify-content:flex-start; min-width:0; }} }}
    @media (pointer:coarse) {{ .nsewdrag {{ touch-action:pan-y !important; }}
      .modebar-btn {{ width:34px !important; height:34px !important; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="kpis" aria-label="Chart selector">{kpi_html}</section>
    {_range_controls(bool(plot_ids))}
    {chart_pane_html}
    <footer>
      <p>{html.escape(snapshot.universe_name)} · holdings as at {snapshot.as_of_date:%d %b %Y}</p>
      <p>Generated {html.escape(generated)}<br>{html.escape(sync_text)}</p>
    </footer>
    {_interaction_script(plot_ids)}
  </main>
</body>
</html>
"""
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(page, encoding="utf-8")
    temporary.replace(output)


def _figure_has_data(figure: Figure) -> bool:
    return any(
        not _is_missing(value)
        for trace in figure.data
        for value in (trace.y if trace.y is not None else ())
    )


def _sparkline_svg(figure: Figure, title: str) -> str:
    anchor, series = _sparkline_traces(figure)
    if anchor is None:
        return _empty_sparkline(title)

    anchor_points = [
        (timestamp, value)
        for timestamp, value in _sparkline_points(anchor)
        if value is not None
    ]
    if not anchor_points:
        return _empty_sparkline(title)
    recent_anchor = anchor_points[-252:]
    start = recent_anchor[0][0]
    end = max(recent_anchor[-1][0], start + 1.0)

    plotted = [
        (trace, points)
        for trace in series
        if (points := _sparkline_points(trace, start=start, end=end))
    ]
    finite = [
        value for _, points in plotted for _, value in points if value is not None
    ]
    if not finite:
        return _empty_sparkline(title)

    low, high = _sparkline_y_bounds(finite)
    paths = "".join(
        path
        for trace, points in plotted
        if (path := _sparkline_path(trace, points, start, end, low, high))
    )
    return (
        '<svg class="sparkline" viewBox="0 0 240 42" preserveAspectRatio="none" '
        f'role="img" aria-label="One-year history for {html.escape(title, quote=True)}">'
        f"{_sparkline_zero_line(low, high)}{paths}</svg>"
    )


def _sparkline_traces(figure: Figure) -> tuple[object | None, list[object]]:
    anchor = next(
        (
            item
            for item in figure.data
            if isinstance(item.meta, Mapping)
            and item.meta.get("sparkline_anchor") is True
        ),
        None,
    )
    series = [
        item
        for item in figure.data
        if isinstance(item.meta, Mapping) and item.meta.get("sparkline") is True
    ]
    if anchor is None:
        anchor = series[0] if series else None
    if anchor is None:
        anchor = next(
            (
                item
                for item in figure.data
                if any(
                    not _is_missing(value)
                    for value in (item.y if item.y is not None else ())
                )
            ),
            None,
        )
    if anchor is None or anchor.y is None or anchor.x is None:
        return None, []
    if not series:
        series = [anchor]
    return anchor, series


def _sparkline_points(
    trace: object,
    *,
    start: float = -math.inf,
    end: float = math.inf,
) -> list[tuple[float, float | None]]:
    points: list[tuple[float, float | None]] = []
    raw_x_values = getattr(trace, "x", None)
    raw_y_values = getattr(trace, "y", None)
    if raw_x_values is None or raw_y_values is None:
        return points
    for raw_x, raw_y in zip(raw_x_values, raw_y_values, strict=False):
        timestamp = _spark_timestamp(raw_x)
        if timestamp is None or timestamp < start or timestamp > end:
            continue
        points.append((timestamp, None if _is_missing(raw_y) else float(raw_y)))
    return points


def _sparkline_y_bounds(values: list[float]) -> tuple[float, float]:
    low = min(values)
    high = max(values)
    span = high - low
    if span == 0:
        span = max(abs(high) * 0.1, 1.0)
        low -= span / 2
        high += span / 2
    return low, high


def _sparkline_zero_line(low: float, high: float) -> str:
    if not low < 0 < high:
        return ""
    width = 240.0
    height = 42.0
    inset = 2.5
    zero_y = inset + high / (high - low) * (height - inset * 2)
    return (
        f'<line class="spark-zero" x1="0" y1="{zero_y:.2f}" '
        f'x2="{width:.0f}" y2="{zero_y:.2f}"></line>'
    )


def _sparkline_path(
    trace: object,
    points: list[tuple[float, float | None]],
    start: float,
    end: float,
    low: float,
    high: float,
) -> str:
    width = 240.0
    height = 42.0
    inset = 2.5
    commands: list[str] = []
    drawing = False
    for timestamp, value in points:
        if value is None:
            drawing = False
            continue
        x = (timestamp - start) / (end - start) * width
        y = inset + (high - value) / (high - low) * (height - inset * 2)
        commands.append(f"{'L' if drawing else 'M'}{x:.2f},{y:.2f}")
        drawing = True
    if not commands:
        return ""
    colour = getattr(getattr(trace, "line", None), "color", None) or "#16211d"
    return (
        '<path class="spark-path" '
        f'style="--spark-stroke:{html.escape(str(colour), quote=True)}" '
        f'd="{" ".join(commands)}"></path>'
    )


def _spark_timestamp(value: object) -> float | None:
    try:
        return float(value.timestamp())
    except (AttributeError, TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except ValueError:
            return None


def _empty_sparkline(title: str) -> str:
    return (
        '<svg class="sparkline spark-empty" viewBox="0 0 240 42" '
        f'role="img" aria-label="No one-year history for {html.escape(title, quote=True)}">'
        '<line x1="0" y1="21" x2="240" y2="21"></line></svg>'
    )


def _chart_meta(figure: Figure, plot_id: str) -> str:
    items: list[tuple[str, str]] = []
    for trace in figure.data:
        metadata = trace.meta if isinstance(trace.meta, Mapping) else {}
        if metadata.get("external_legend") is False:
            continue
        name = str(metadata.get("external_legend_label") or trace.name or "").strip()
        colour = metadata.get("external_legend_color") or getattr(
            getattr(trace, "line", None), "color", None
        )
        trace_values = trace.y if trace.y is not None else ()
        if (
            not name
            or not colour
            or not any(not _is_missing(value) for value in trace_values)
        ):
            continue
        key = (name, str(colour))
        if key not in items:
            items.append(key)
    content = "".join(
        '<span class="legend-item" '
        f'data-series="{html.escape(name, quote=True)}">'
        '<i class="legend-swatch" aria-hidden="true" '
        f'style="--swatch:{html.escape(colour, quote=True)}"></i>'
        f'<span class="legend-label">{html.escape(name)}</span>'
        '<span class="legend-value"></span></span>'
        for name, colour in items
    )
    readout_id = f"hover-{plot_id}"
    return (
        '<div class="chart-meta">'
        f'<div class="chart-legend" aria-label="Chart legend">{content}</div>'
        f'<div class="hover-readout" id="{html.escape(readout_id)}" '
        'aria-live="polite"></div></div>'
    )


def _range_controls(available: bool) -> str:
    if not available:
        return ""
    buttons = "".join(
        f'<button type="button" data-dashboard-range="{period}" '
        f'aria-pressed="{str(period == "1y").lower()}">{label}</button>'
        for period, label in (("3m", "3m"), ("6m", "6m"), ("1y", "1y"), ("all", "All"))
    )
    return (
        '<section class="range-control" aria-label="Dashboard date range">'
        '<span>Date range</span><div class="range-buttons" role="group" '
        f'aria-label="Show date range">{buttons}</div></section>'
    )


def _is_missing(value: object) -> bool:
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def _sync_text(
    summary: Mapping[str, object],
    *,
    today: date | None = None,
) -> str:
    holdings = int(summary.get("holdings", 0) or 0)
    quoted = int(summary.get("quoted_latest", 0) or 0)
    with_history = int(summary.get("with_history", 0) or 0)
    failures = int(summary.get("provider_failures", 0) or 0)
    provider_outage = bool(summary.get("provider_outage", False))
    latest = summary.get("latest_session")
    coverage = quoted / holdings if holdings else 0.0
    prefix = "No cached session"
    session: date | None = None
    if latest:
        try:
            session = datetime.fromisoformat(str(latest)).date()
            prefix = f"Cached through {session:%-d %b %Y}"
        except ValueError:
            prefix = f"Cached through {latest}"
    if provider_outage:
        result = "Yahoo sync unavailable"
        result += (
            f" · showing validated cache through {session:%-d %b %Y}"
            if session is not None
            else " · no validated cached session"
        )
    else:
        result = prefix
    result += f" · {quoted}/{holdings} quoted ({coverage:.1%})"
    if holdings and "with_history" in summary and with_history < holdings:
        result += f" · verified history {with_history}/{holdings}"
    if failures and not provider_outage:
        denominator = f"/{holdings}" if holdings else ""
        noun = "holding" if failures == 1 else "holdings"
        result += f" · sync failed for {failures}{denominator} {noun}"
    stale_count = (
        _business_days_since(session, today or date.today())
        if session is not None
        else None
    )
    if stale_count is not None and stale_count > 1:
        result += f" · STALE ({stale_count} business days behind)"
    unavailable = tuple(summary.get("unavailable_symbols", ()) or ())
    if unavailable:
        visible = ", ".join(str(symbol) for symbol in unavailable[:5])
        if len(unavailable) > 5:
            visible += f" +{len(unavailable) - 5}"
        result += f" · unavailable: {visible}"
    return result


def _business_days_since(session: date, today: date) -> int:
    if today <= session:
        return 0
    cursor = session + timedelta(days=1)
    total = 0
    while cursor <= today:
        if cursor.weekday() < 5:
            total += 1
        cursor += timedelta(days=1)
    return total


def _interaction_script(plot_ids: Sequence[str]) -> str:
    """Link chart selection, readouts, date ranges, and visible-value scaling."""
    ids = json.dumps(list(plot_ids))
    return f"""<script>
(() => {{
  const plotIds = {ids};
  const compactQuery = window.matchMedia("(max-width: 600px)");
  const asTime = value => {{
    // Plotly mixes date-only axis bounds with timezone-free trace timestamps.
    // Parse both as local midnight so an already-full range compares exactly
    // with its data bounds and an outward wheel gesture is a true no-op.
    const normalised = typeof value === "string"
      && /^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(value)
      ? `${{value}}T00:00:00`
      : value;
    const time = new Date(normalised).getTime();
    return Number.isFinite(time) ? time : null;
  }};

  const traceValueAt = (trace, targetTime) => {{
    const xs = trace.x || [];
    const ys = trace.y || [];
    for (let index = 0; index < Math.min(xs.length, ys.length); index += 1) {{
      const time = asTime(xs[index]);
      const value = Number(ys[index]);
      if (time !== null && Math.abs(time - targetTime) < 1000
        && Number.isFinite(value)) return value;
    }}
    return null;
  }};

  const hoverDecimals = trace => {{
    const match = String(trace.hovertemplate || "").match(/y:[.]([0-9]+)f/);
    return match ? Number(match[1]) : 2;
  }};

  const isHoverTrace = trace => {{
    const meta = trace.meta && typeof trace.meta === "object" ? trace.meta : {{}};
    return meta.external_hover !== false && trace.hoverinfo !== "skip"
      && trace.visible !== false && trace.visible !== "legendonly";
  }};

  const latestHoverTime = plot => {{
    let latest = null;
    for (const trace of plot._fullData || []) {{
      if (!isHoverTrace(trace)) continue;
      const xs = trace.x || [];
      const ys = trace.y || [];
      for (let index = 0; index < Math.min(xs.length, ys.length); index += 1) {{
        const time = asTime(xs[index]);
        const value = Number(ys[index]);
        if (time !== null && Number.isFinite(value)
          && (latest === null || time > latest)) latest = time;
      }}
    }}
    return latest;
  }};

  const renderReadout = (plot, targetTime) => {{
    const readout = document.getElementById(`hover-${{plot.id}}`);
    if (targetTime === null || !readout) return;
    const legend = readout.closest(".chart-meta")?.querySelector(".chart-legend");
    const legendItems = [...(legend?.querySelectorAll(".legend-item") || [])];
    for (const item of legendItems) {{
      const valueNode = item.querySelector(".legend-value");
      if (valueNode) valueNode.textContent = "";
    }}
    const date = document.createElement("span");
    date.className = "hover-date";
    date.textContent = new Intl.DateTimeFormat("en-AU", {{
      day: "numeric",
      month: "short",
      year: "numeric",
    }}).format(new Date(targetTime));
    const seen = new Set();
    for (const trace of plot._fullData || []) {{
      const meta = trace.meta && typeof trace.meta === "object" ? trace.meta : {{}};
      const name = String(meta.external_hover_label
        || meta.external_legend_label || trace.name || "").trim();
      if (!name || seen.has(name) || !isHoverTrace(trace)) continue;
      const value = traceValueAt(trace, targetTime);
      if (value === null) continue;
      seen.add(name);
      const item = legendItems.find(candidate => candidate.dataset.series === name);
      const valueNode = item?.querySelector(".legend-value");
      if (!valueNode) continue;
      valueNode.textContent = value.toLocaleString("en-AU", {{
        minimumFractionDigits: hoverDecimals(trace),
        maximumFractionDigits: hoverDecimals(trace),
      }});
    }}
    readout.replaceChildren(date);
  }};

  const resetHoverReadout = plot => renderReadout(plot, latestHoverTime(plot));

  const updateHoverReadout = (plot, event) => {{
    const hoveredTime = asTime(event?.points?.[0]?.x);
    if (hoveredTime !== null) renderReadout(plot, hoveredTime);
  }};

  const visibleValues = plot => {{
    const range = plot?._fullLayout?.xaxis?.range;
    if (!range || range.length !== 2) return [];
    const first = asTime(range[0]);
    const second = asTime(range[1]);
    if (first === null || second === null) return [];
    const start = Math.min(first, second);
    const end = Math.max(first, second);
    const visible = [];

    for (const trace of plot._fullData || []) {{
      if (trace.visible === false || trace.visible === "legendonly"
        || (trace.yaxis && trace.yaxis !== "y")) continue;
      const xs = trace.x || [];
      const ys = trace.y || [];
      const length = Math.min(xs.length, ys.length);
      for (let index = 0; index < length; index += 1) {{
        const x = asTime(xs[index]);
        const y = Number(ys[index]);
        if (x !== null && x >= start && x <= end && Number.isFinite(y)) visible.push(y);
      }}
    }}
    return visible;
  }};

  const fitVisibleY = plot => {{
    const visible = visibleValues(plot);
    if (!visible.length) return;
    const meta = plot?._fullLayout?.meta || {{}};
    if (meta.include_zero) visible.push(0);
    let low = Math.min(...visible);
    let high = Math.max(...visible);
    if (meta.scale_group) {{
      const grouped = plotIds
        .map(id => document.getElementById(id))
        .filter(candidate => candidate?._fullLayout?.meta?.scale_group === meta.scale_group)
        .flatMap(candidate => visibleValues(candidate));
      if (grouped.length) {{
        const magnitude = Math.max(...grouped.map(value => Math.abs(value)), 1);
        if (low >= 0) {{ low = 0; high = magnitude; }}
        else if (high <= 0) {{ low = -magnitude; high = 0; }}
      }}
    }}
    let span = high - low;
    if (!Number.isFinite(span) || span === 0) span = Math.max(Math.abs(high) * 0.1, 1);
    const padding = span * 0.08;
    const desired = [low - padding, high + padding];
    const current = plot?._fullLayout?.yaxis?.range;
    const tolerance = Math.max(span * 0.000001, 0.000001);
    if (current && Math.abs(current[0] - desired[0]) <= tolerance
      && Math.abs(current[1] - desired[1]) <= tolerance) return Promise.resolve();
    return Plotly.relayout(plot, {{
      "yaxis.range": desired,
      "yaxis.autorange": false,
    }});
  }};

  const dataBounds = plot => {{
    const values = [];
    for (const trace of plot._fullData || []) {{
      for (const value of trace.x || []) {{
        const time = asTime(value);
        if (time !== null) values.push(time);
      }}
    }}
    return values.length ? [Math.min(...values), Math.max(...values)] : null;
  }};

  const displayBounds = plot => {{
    const bounds = plot?._fullLayout?.meta?.display_bounds?.map(asTime);
    return bounds?.length === 2 && bounds.every(value => value !== null)
      ? bounds : null;
  }};

  const boundedRange = (plot, requestedRange) => {{
    const bounds = displayBounds(plot);
    if (!bounds || !requestedRange || requestedRange.some(value => value === null)) return null;
    const requestedSpan = requestedRange[1] - requestedRange[0];
    const fullSpan = bounds[1] - bounds[0];
    let start = requestedRange[0];
    let end = requestedRange[1];
    if (requestedSpan >= fullSpan) return bounds;
    if (start < bounds[0]) {{ start = bounds[0]; end = start + requestedSpan; }}
    if (end > bounds[1]) {{ end = bounds[1]; start = end - requestedSpan; }}
    return [start, end];
  }};

  let rangeGeneration = 0;
  const rangeQueues = new Map();
  const programmedRanges = new WeakMap();
  const applyLinkedRange = requestedRange => {{
    if (!requestedRange || requestedRange.some(value => value === null)) {{
      return Promise.resolve();
    }}
    const linkedRange = requestedRange.slice();
    const generation = ++rangeGeneration;
    const updates = [];
    for (const id of plotIds) {{
      const target = document.getElementById(id);
      if (!target) continue;
      const previous = rangeQueues.get(id) || Promise.resolve();
      const update = previous.catch(() => undefined).then(() => {{
        if (generation !== rangeGeneration) return;
        const targetRange = boundedRange(target, linkedRange);
        if (!targetRange) return;
        programmedRanges.set(target, {{generation, range: targetRange}});
        return Plotly.relayout(target, {{
          "xaxis.range": targetRange.map(value => new Date(value).toISOString()),
        }}).then(() => fitVisibleY(target)).finally(() => {{
          if (programmedRanges.get(target)?.generation === generation) {{
            programmedRanges.delete(target);
          }}
        }});
      }});
      rangeQueues.set(id, update);
      updates.push(update);
    }}
    return Promise.allSettled(updates);
  }};

  const isProgrammedRange = plot => {{
    const intended = programmedRanges.get(plot)?.range;
    if (!intended) return false;
    const current = plot?._fullLayout?.xaxis?.range?.map(asTime);
    return current && current.every((value, index) =>
      value !== null && Math.abs(value - intended[index]) < 1000);
  }};

  const synchroniseXFrom = source => {{
    const sourceRange = source?._fullLayout?.xaxis?.range?.map(asTime);
    if (sourceRange && sourceRange.every(value => value !== null)) {{
      applyLinkedRange(sourceRange);
    }}
  }};

  const attachBoundedWheelZoom = plot => {{
    const bounds = displayBounds(plot);
    if (!bounds || bounds[0] === bounds[1]) return;
    plot.addEventListener("wheel", event => {{
      const dragArea = plot.querySelector(".nsewdrag");
      if (!dragArea) return;
      const rectangle = dragArea.getBoundingClientRect();
      const overPlot = event.clientX >= rectangle.left && event.clientX <= rectangle.right
        && event.clientY >= rectangle.top && event.clientY <= rectangle.bottom;
      if (!overPlot) return;
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();

      const current = plot._fullLayout.xaxis.range.map(asTime);
      if (current.some(value => value === null)) return;
      const fullSpan = bounds[1] - bounds[0];
      const currentSpan = current[1] - current[0];
      const factor = Math.exp(event.deltaY * 0.002);
      const minimumSpan = Math.max(24 * 60 * 60 * 1000, fullSpan / 10000);
      const proposedSpan = Math.min(fullSpan, Math.max(minimumSpan, currentSpan * factor));
      if (proposedSpan >= fullSpan) {{
        if (Math.abs(current[0] - bounds[0]) < 1 && Math.abs(current[1] - bounds[1]) < 1) return;
        applyLinkedRange(bounds);
        return;
      }}

      const pointer = Math.min(1, Math.max(0, (event.clientX - rectangle.left) / rectangle.width));
      const focus = current[0] + currentSpan * pointer;
      let start = focus - proposedSpan * pointer;
      let end = start + proposedSpan;
      if (start < bounds[0]) {{
        start = bounds[0];
        end = start + proposedSpan;
      }}
      if (end > bounds[1]) {{
        end = bounds[1];
        start = end - proposedSpan;
      }}
      applyLinkedRange([start, end]);
    }}, {{passive: false}});
  }};

  const rangeButtons = [...document.querySelectorAll("[data-dashboard-range]")];
  const setActiveRange = period => {{
    for (const button of rangeButtons) {{
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.dashboardRange === period),
      );
    }}
  }};

  const dashboardRange = (plot, period) => {{
    const data = dataBounds(plot);
    const bounds = displayBounds(plot);
    if (!data || !bounds) return null;
    let start = data[0];
    const end = bounds[1];
    if (period !== "all") {{
      const boundary = new Date(data[1]);
      if (period === "3m") boundary.setUTCMonth(boundary.getUTCMonth() - 3);
      else if (period === "6m") boundary.setUTCMonth(boundary.getUTCMonth() - 6);
      else if (period === "1y") boundary.setUTCFullYear(boundary.getUTCFullYear() - 1);
      start = Math.max(data[0], boundary.getTime());
    }}
    return [start, end];
  }};

  const matchingDashboardRange = plot => {{
    const current = plot?._fullLayout?.xaxis?.range?.map(asTime);
    if (!current || current.some(value => value === null)) return null;
    for (const period of ["3m", "6m", "1y", "all"]) {{
      const expected = dashboardRange(plot, period);
      if (expected && Math.abs(current[0] - expected[0]) < 1000
        && Math.abs(current[1] - expected[1]) < 1000) return period;
    }}
    return null;
  }};

  const applyDashboardRange = period => {{
    const source = plotIds
      .map(id => document.getElementById(id))
      .find(plot => dataBounds(plot));
    if (!source) return Promise.resolve();
    const requested = dashboardRange(source, period);
    if (!requested) return Promise.resolve();
    const [start, end] = requested;
    setActiveRange(period);
    return applyLinkedRange([start, end]);
  }};

  for (const button of rangeButtons) {{
    button.addEventListener("click", () => applyDashboardRange(button.dataset.dashboardRange));
  }}

  const chartSelectors = [...document.querySelectorAll(".chart-selector")];
  const chartViews = [...document.querySelectorAll(".chart-view")];
  const activateChart = button => {{
    const targetId = button?.dataset.chartTarget;
    if (!targetId) return;
    for (const selector of chartSelectors) {{
      const selected = selector === button;
      selector.classList.toggle("is-active", selected);
      selector.setAttribute("aria-pressed", String(selected));
    }}
    for (const view of chartViews) {{
      const selected = view.id === targetId;
      view.classList.toggle("is-active", selected);
      view.setAttribute("aria-hidden", String(!selected));
    }}
    const plot = document.getElementById(button.dataset.plotId || "");
    if (plot?._fullLayout) {{
      requestAnimationFrame(() => {{
        Plotly.Plots.resize(plot);
        fitVisibleY(plot);
      }});
    }}
    const pane = document.querySelector(".chart-pane");
    const shouldScroll = pane && (compactQuery.matches
      || pane.getBoundingClientRect().top > window.innerHeight);
    if (shouldScroll) {{
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      document.querySelector(".range-control")?.scrollIntoView({{
        behavior: reducedMotion ? "auto" : "smooth",
        block: "start",
      }});
    }}
  }};
  for (const selector of chartSelectors) {{
    selector.addEventListener("click", () => activateChart(selector));
  }}

  const applyResponsiveLayout = () => {{
    const compact = compactQuery.matches;
    const updates = [];
    for (const id of plotIds) {{
      const plot = document.getElementById(id);
      if (!plot?._fullLayout) continue;
      updates.push(
        Plotly.relayout(plot, {{
          "margin.t": compact ? 20 : 28,
          "margin.r": compact ? 42 : 26,
        }}).then(() => fitVisibleY(plot))
      );
    }}
    return Promise.allSettled(updates);
  }};

  for (const id of plotIds) {{
    const plot = document.getElementById(id);
    if (!plot) continue;
    let animationFrame = null;
    const scheduleFit = () => {{
      if (animationFrame !== null) cancelAnimationFrame(animationFrame);
      animationFrame = requestAnimationFrame(() => {{
        animationFrame = null;
        fitVisibleY(plot);
      }});
    }};
    plot.on("plotly_relayout", changes => {{
      if (Object.keys(changes).some(key =>
        key === "xaxis.autorange" || key.startsWith("xaxis.range")
      )) {{
        const programmed = isProgrammedRange(plot);
        if (!programmed) {{
          requestAnimationFrame(() => setActiveRange(matchingDashboardRange(plot)));
        }}
        scheduleFit();
        if (!programmed) {{
          requestAnimationFrame(() => synchroniseXFrom(plot));
        }}
      }}
    }});
    plot.on("plotly_relayouting", changes => {{
      if (Object.keys(changes).some(key =>
        key === "xaxis.autorange" || key.startsWith("xaxis.range")
      )) scheduleFit();
    }});
    plot.on("plotly_hover", event => updateHoverReadout(plot, event));
    plot.on("plotly_unhover", () => resetHoverReadout(plot));
    attachBoundedWheelZoom(plot);
    resetHoverReadout(plot);
    scheduleFit();
  }}
  applyResponsiveLayout()
    .then(() => applyDashboardRange("1y"))
    .then(() => {{ document.documentElement.dataset.dashboardReady = "true"; }});
  compactQuery.addEventListener("change", applyResponsiveLayout);
}})();
</script>"""
