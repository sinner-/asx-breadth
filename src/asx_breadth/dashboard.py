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
from .panels.base import DashboardPanel
from .panels.charts import display_frame


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
    ad_result = results["advance_decline"]
    rasi_result = results["mcclellan_rasi"]
    benchmark_result = results["benchmark_trend"]
    new_high_low_result = results["new_high_low"]
    ad = ad_result.frame
    rasi = display_frame(rasi_result)
    benchmark = benchmark_result.frame
    new_high_low = new_high_low_result.frame
    latest_ad = ad.iloc[-1] if not ad.empty else None
    latest_rasi = rasi.iloc[-1] if not rasi.empty else None
    latest_benchmark = benchmark.iloc[-1] if not benchmark.empty else None
    latest_new_high_low = new_high_low.iloc[-1] if not new_high_low.empty else None

    vas_detail, vas_tone = _relative_state(
        latest_benchmark["total_return_index"]
        if latest_benchmark is not None
        else None,
        latest_benchmark["total_return_ema19"]
        if latest_benchmark is not None
        else None,
        latest_benchmark["total_return_ema39"]
        if latest_benchmark is not None
        else None,
        include_values=True,
    )
    if latest_benchmark is not None:
        band_detail, _ = _band_state(
            latest_benchmark["total_return_index"],
            latest_benchmark["low_ema200"],
            latest_benchmark["high_ema200"],
        )
        vas_detail = f"{vas_detail} · {band_detail}"
    ad_detail, ad_tone = _relative_state(
        latest_ad["cumulative_ad"] if latest_ad is not None else None,
        latest_ad["cumulative_ad_ema19"] if latest_ad is not None else None,
        latest_ad["cumulative_ad_ema39"] if latest_ad is not None else None,
    )
    oscillator = (
        latest_rasi["mcclellan_oscillator"] if latest_rasi is not None else None
    )
    latest_rasi_value = latest_rasi["rasi"] if latest_rasi is not None else None
    nh_nl = latest_new_high_low["nh_nl"] if latest_new_high_low is not None else None
    latest_quality = _latest_quality(ad_result, latest_ad)
    rasi_quality = _latest_quality(rasi_result, latest_rasi)
    if rasi_quality is None:
        rasi_quality = latest_quality
    new_high_low_quality = _latest_quality(
        new_high_low_result,
        latest_new_high_low,
    )
    last_accepted = _last_accepted_session(ad_result, ad)
    rasi_warmup = int(rasi_result.metadata.get("warmup_sessions", 0) or 0)

    if latest_quality is False:
        ad_detail, ad_tone = _held_state(last_accepted), "neutral"

    oscillator_detail = _signal_label(
        oscillator,
        positive="Positive impulse",
        negative="Negative impulse",
    )
    oscillator_tone = _sign_tone(oscillator)
    rasi_detail = _signal_label(
        latest_rasi_value,
        positive="Above zero",
        negative="Below zero",
    )
    rasi_tone = _sign_tone(latest_rasi_value)
    if latest_rasi is None and rasi_warmup:
        oscillator_detail = rasi_detail = (
            f"Insufficient {rasi_warmup}-session display warm-up"
        )
        oscillator_tone = rasi_tone = "neutral"
    elif rasi_quality is False:
        oscillator_detail = rasi_detail = _held_state(last_accepted)
        oscillator_tone = rasi_tone = "neutral"

    nh_detail = (
        f"{_number(latest_new_high_low['new_highs'], 0)} highs · "
        f"{_number(latest_new_high_low['new_lows'], 0)} lows"
        if latest_new_high_low is not None
        else "Unavailable"
    )
    nh_tone = _sign_tone(nh_nl)
    if new_high_low_quality is False:
        nh_detail = _held_state(
            _last_accepted_session(new_high_low_result, new_high_low)
        )
        nh_tone = "neutral"

    kpis = [
        (
            "VAS total return",
            _number(
                latest_benchmark["total_return_index"]
                if latest_benchmark is not None
                else None,
                2,
            ),
            vas_detail,
            vas_tone,
        ),
        (
            "Cumulative A/D",
            _number(latest_ad["cumulative_ad"] if latest_ad is not None else None, 0),
            ad_detail,
            ad_tone,
        ),
        (
            "McClellan oscillator",
            _number(oscillator, 1),
            oscillator_detail,
            oscillator_tone,
        ),
        (
            "RASI",
            _number(latest_rasi_value, 1),
            rasi_detail,
            rasi_tone,
        ),
        (
            "New highs − lows",
            _number(nh_nl, 0),
            nh_detail,
            nh_tone,
        ),
        (
            "Breadth coverage",
            _percent(latest_ad["coverage"] if latest_ad is not None else None),
            (
                "Latest session accepted"
                if latest_quality is True
                else (
                    "Latest session withheld"
                    if latest_quality is False
                    else "Unavailable"
                )
            ),
            (
                "positive"
                if latest_quality is True
                else ("negative" if latest_quality is False else "neutral")
            ),
        ),
    ]
    kpi_html = "".join(
        f'<div class="kpi {tone}"><span>{html.escape(label)}</span>'
        f"<strong>{html.escape(value)}</strong><small>{html.escape(detail)}</small></div>"
        for label, value, detail, tone in kpis
    )

    chart_html: list[str] = []
    plot_ids: list[str] = []
    plotly_included = False
    for panel in panels:
        result = results.get(panel.indicator_key)
        heading_id = f"heading-{panel.key}"
        unavailable = result is None or result.frame.empty
        figure = None if unavailable else panel.figure(result)
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
            chart_html.append(
                '<section class="chart-card unavailable" '
                f'aria-labelledby="{html.escape(heading_id)}">'
                '<div class="chart-heading">'
                f'<h2 id="{html.escape(heading_id)}">{html.escape(panel.title)}</h2>'
                f"</div><p>{html.escape(message)}</p></section>"
            )
            continue
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
        chart_html.append(
            '<section class="chart-card" '
            f'aria-labelledby="{html.escape(heading_id)}">'
            '<div class="chart-heading">'
            f'<h2 id="{html.escape(heading_id)}">{html.escape(panel.title)}</h2>'
            "<span>Drag to zoom · Ctrl/⌘ + wheel · double-click to reset</span>"
            "</div>"
            f"{_mobile_legend(figure)}{chart}</section>"
        )
    if not plot_ids:
        chart_html = [
            '<section class="empty"><h2>No market data cached yet</h2>'
            "<p>The dashboard shell was still generated. Run the command again later; "
            "provider failures are retried with backoff and do not corrupt the cache.</p></section>"
        ]

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
    .shell {{ width:min(1440px,calc(100% - 36px)); margin:0 auto; padding:38px 0 50px; }}
    header {{ display:flex; gap:28px; justify-content:space-between; align-items:end; margin-bottom:24px; }}
    .eyebrow {{ color:var(--green); font-size:.75rem; font-weight:800; letter-spacing:.14em;
                text-transform:uppercase; }}
    h1 {{ margin:.3rem 0 .45rem; font-family:Georgia,"Times New Roman",serif;
          font-size:clamp(2rem,4vw,3.7rem); font-weight:500; line-height:1; }}
    .subtitle,.stamp {{ margin:0; color:var(--muted); }}
    .stamp {{ max-width:680px; text-align:right; font-size:.84rem; line-height:1.55; }}
    .kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin-bottom:18px; }}
    .kpi,.range-control,.chart-card,.empty {{ background:var(--card); border:1px solid var(--line);
                                     border-radius:16px; box-shadow:var(--shadow); }}
    .kpi {{ padding:16px 16px 14px; border-top:3px solid var(--line); }}
    .kpi.positive {{ border-top-color:var(--green); }}
    .kpi.negative {{ border-top-color:var(--red); }}
    .kpi.neutral {{ border-top-color:var(--amber); }}
    .kpi span {{ display:block; color:var(--muted); font-size:.75rem; font-weight:700;
                 letter-spacing:.07em; text-transform:uppercase; }}
    .kpi strong {{ display:block; margin-top:7px; font-family:Georgia,"Times New Roman",serif;
                   font-size:1.55rem; font-weight:500; }}
    .kpi small {{ display:block; min-height:2.5em; margin-top:5px; color:var(--muted);
                  font-size:.72rem; line-height:1.25; }}
    .range-control {{ display:flex; align-items:center; justify-content:space-between; gap:14px;
                      position:sticky; top:10px; z-index:20; padding:10px 14px;
                      margin-bottom:2px; }}
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
    .chart-card {{ padding:0 10px 4px; margin-top:18px; overflow:hidden; }}
    .hoverlayer .legend > rect.bg,
    .hoverlayer .hovertext > path {{ fill:var(--card) !important; fill-opacity:1 !important;
                                     stroke:rgba(22,33,29,.28) !important; }}
    .js-plotly-plot .nsewdrag {{ cursor:default !important; }}
    .js-plotly-plot .nsewdrag:active {{ cursor:ew-resize !important; }}
    .chart-heading {{ display:flex; justify-content:space-between; gap:20px; align-items:baseline;
                      padding:20px 20px 0; }}
    .chart-heading h2 {{ margin:0; font-family:Georgia,"Times New Roman",serif;
                         font-size:1.35rem; font-weight:500; }}
    .chart-heading span {{ color:var(--muted); font-size:.78rem; }}
    .mobile-legend {{ display:none; }}
    .legend-item {{ display:inline-flex; align-items:center; gap:5px; }}
    .legend-swatch {{ width:17px; height:3px; border-radius:2px; background:var(--swatch); }}
    .empty {{ padding:42px; margin-top:18px; text-align:center; }}
    .unavailable {{ min-height:150px; }}
    .unavailable p {{ margin:24px 20px 34px; color:var(--muted); }}
    @media (max-width:1100px) {{ .kpis {{ grid-template-columns:repeat(3,1fr); }} }}
    @media (max-width:800px) {{ header {{ align-items:start; flex-direction:column; }}
      .stamp {{ text-align:left; }} .kpis {{ grid-template-columns:repeat(2,1fr); }}
      .chart-heading {{ align-items:start; flex-direction:column; gap:4px; }}
      .chart-heading span {{ display:none; }} }}
    @media (max-width:460px) {{ .shell {{ width:min(100% - 20px,1440px); padding-top:22px; }}
      .kpis {{ gap:8px; }} .kpi {{ padding:13px 12px 11px; }}
      .kpi strong {{ font-size:1.35rem; }} .kpi small {{ font-size:.68rem; }} }}
    @media (max-width:600px) {{ .modebar {{ display:none !important; }}
      .range-control {{ top:6px; padding:8px 9px; }}
      .range-control > span {{ position:absolute; width:1px; height:1px; overflow:hidden;
                               clip:rect(0 0 0 0); white-space:nowrap; }}
      .range-buttons {{ width:100%; }}
      .range-buttons button {{ flex:1; min-height:40px; }}
      .mobile-legend {{ display:flex; flex-wrap:wrap; gap:7px 12px; padding:8px 20px 0;
                        color:var(--muted); font-size:.69rem; line-height:1.15; }} }}
    @media (pointer:coarse) {{ .nsewdrag {{ touch-action:pan-y !important; }}
      .modebar-btn {{ width:34px !important; height:34px !important; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <div class="eyebrow">ASX market internals</div>
        <h1>{html.escape(snapshot.universe_code)} breadth</h1>
        <p class="subtitle">{html.escape(snapshot.universe_name)} · holdings as at {snapshot.as_of_date:%d %b %Y}</p>
      </div>
      <p class="stamp">Generated {html.escape(generated)}<br>{html.escape(sync_text)}</p>
    </header>
    <section class="kpis">{kpi_html}</section>
    {_range_controls(bool(plot_ids))}
    {"".join(chart_html)}
    {_visible_y_script(plot_ids)}
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


def _mobile_legend(figure: Figure) -> str:
    items: list[tuple[str, str]] = []
    for trace in figure.data:
        metadata = trace.meta if isinstance(trace.meta, Mapping) else {}
        if metadata.get("external_legend") is False:
            continue
        name = str(trace.name or "").strip()
        colour = getattr(getattr(trace, "line", None), "color", None)
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
    if len(items) < 2:
        return ""
    content = "".join(
        '<span class="legend-item"><i class="legend-swatch" aria-hidden="true" '
        f'style="--swatch:{html.escape(colour, quote=True)}"></i>'
        f"{html.escape(name)}</span>"
        for name, colour in items
    )
    return f'<div class="mobile-legend" aria-label="Chart legend">{content}</div>'


def _range_controls(available: bool) -> str:
    if not available:
        return ""
    buttons = "".join(
        f'<button type="button" data-dashboard-range="{period}" '
        f'aria-pressed="{str(period == "all").lower()}">{label}</button>'
        for period, label in (("3m", "3m"), ("6m", "6m"), ("1y", "1y"), ("all", "All"))
    )
    return (
        '<section class="range-control" aria-label="Dashboard date range">'
        '<span>Date range</span><div class="range-buttons" role="group" '
        f'aria-label="Show date range">{buttons}</div></section>'
    )


def _latest_quality(result: IndicatorResult, latest: object) -> bool | None:
    for key in (
        "latest_signal_updated",
        "latest_quality_ok",
        "latest_input_quality_ok",
    ):
        parsed = _optional_bool(result.metadata.get(key))
        if parsed is not None:
            return parsed
    if latest is None:
        return None
    try:
        value = latest["quality_ok"]
    except (KeyError, TypeError):
        return None
    return None if _is_missing(value) else bool(value)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in {"true", "yes", "1"}:
            return True
        if normalised in {"false", "no", "0"}:
            return False
        return None
    if _is_missing(value):
        return None
    return bool(value)


def _last_accepted_session(result: IndicatorResult, frame: object) -> object:
    metadata_value = result.metadata.get("last_accepted_session")
    if metadata_value:
        return metadata_value
    if getattr(frame, "empty", True) or "quality_ok" not in frame:
        return None
    accepted = frame.index[frame["quality_ok"].fillna(False).astype(bool)]
    return accepted[-1] if len(accepted) else None


def _held_state(last_accepted: object) -> str:
    if last_accepted:
        try:
            accepted = datetime.fromisoformat(str(last_accepted)).strftime("%-d %b %Y")
            return f"Held · accepted through {accepted}"
        except ValueError:
            pass
    return "Held · latest session withheld"


def _number(value: object, decimals: int) -> str:
    try:
        number = float(value)
        return f"{number:,.{decimals}f}" if math.isfinite(number) else "—"
    except (TypeError, ValueError):
        return "—"


def _percent(value: object) -> str:
    try:
        number = float(value)
        return f"{number:.1%}" if math.isfinite(number) else "—"
    except (TypeError, ValueError):
        return "—"


def _is_missing(value: object) -> bool:
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def _relative_state(
    value: object,
    ema19: object,
    ema39: object,
    *,
    include_values: bool = False,
) -> tuple[str, str]:
    if any(_is_missing(item) for item in (value, ema19, ema39)):
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


def _band_state(
    value: object,
    low: object,
    high: object,
) -> tuple[str, str]:
    if any(_is_missing(item) for item in (value, low, high)):
        return "200-day band unavailable", "neutral"
    level = float(value)
    if level > float(high):
        return "Above 200-day band", "positive"
    if level < float(low):
        return "Below 200-day band", "negative"
    return "Inside 200-day band", "neutral"


def _sign_tone(value: object) -> str:
    if _is_missing(value):
        return "neutral"
    number = float(value)
    if number > 0:
        return "positive"
    if number < 0:
        return "negative"
    return "neutral"


def _signal_label(value: object, *, positive: str, negative: str) -> str:
    tone = _sign_tone(value)
    if tone == "positive":
        return positive
    if tone == "negative":
        return negative
    return "Neutral" if not _is_missing(value) else "Unavailable"


def _sync_text(
    summary: Mapping[str, object],
    *,
    today: date | None = None,
) -> str:
    holdings = int(summary.get("holdings", 0) or 0)
    quoted = int(summary.get("quoted_latest", 0) or 0)
    with_history = int(summary.get("with_history", 0) or 0)
    failures = int(summary.get("provider_failures", 0) or 0)
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
    result = f"{prefix} · {quoted}/{holdings} quoted ({coverage:.1%})"
    if holdings and "with_history" in summary and with_history < holdings:
        result += f" · history {with_history}/{holdings}"
    if failures:
        result += f" · {failures} sync error{'s' if failures != 1 else ''}"
    stale_days = summary.get("stale_business_days")
    try:
        stale_count = int(stale_days) if stale_days is not None else None
    except (TypeError, ValueError):
        stale_count = None
    if stale_count is None and session is not None:
        stale_count = _business_days_since(session, today or date.today())
    explicitly_stale = _optional_bool(summary.get("latest_is_stale")) is True
    if stale_count is not None and (explicitly_stale or stale_count > 1):
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


def _visible_y_script(plot_ids: Sequence[str]) -> str:
    """Refit y to visible x data without enabling direct vertical zoom."""
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
    const bounds = dataBounds(plot);
    if (!bounds) return null;
    const end = new Date(bounds[1]);
    let sessions = 0;
    while (sessions < 2) {{
      end.setUTCDate(end.getUTCDate() + 1);
      if (end.getUTCDay() !== 0 && end.getUTCDay() !== 6) sessions += 1;
    }}
    return [bounds[0], end.getTime()];
  }};

  let synchronisingX = false;
  const synchroniseXFrom = source => {{
    if (synchronisingX) return;
    const sourceRange = source?._fullLayout?.xaxis?.range?.map(asTime);
    if (!sourceRange || sourceRange.some(value => value === null)) return;
    synchronisingX = true;
    const updates = [];
    for (const id of plotIds) {{
      const target = document.getElementById(id);
      if (!target || target === source) continue;
      const bounds = displayBounds(target);
      if (!bounds) continue;
      const requestedSpan = sourceRange[1] - sourceRange[0];
      const fullSpan = bounds[1] - bounds[0];
      let start = sourceRange[0];
      let end = sourceRange[1];
      if (requestedSpan >= fullSpan) {{
        [start, end] = bounds;
      }} else {{
        if (start < bounds[0]) {{ start = bounds[0]; end = start + requestedSpan; }}
        if (end > bounds[1]) {{ end = bounds[1]; start = end - requestedSpan; }}
      }}
      target.dataset.syncingX = "1";
      updates.push(
        Plotly.relayout(target, {{
          "xaxis.range": [new Date(start).toISOString(), new Date(end).toISOString()],
        }}).then(() => fitVisibleY(target)).finally(() => {{
          delete target.dataset.syncingX;
        }})
      );
    }}
    Promise.allSettled(updates).then(() => {{ synchronisingX = false; }});
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
        Plotly.relayout(plot, {{
          "xaxis.range": bounds.map(value => new Date(value).toISOString()),
        }}).then(() => fitVisibleY(plot));
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
      Plotly.relayout(plot, {{
        "xaxis.range": [new Date(start).toISOString(), new Date(end).toISOString()],
      }}).then(() => fitVisibleY(plot));
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
    if (!source) return;
    const requested = dashboardRange(source, period);
    if (!requested) return;
    const [start, end] = requested;
    setActiveRange(period);
    source.dataset.rangeControl = "1";
    Plotly.relayout(source, {{
      "xaxis.range": [new Date(start).toISOString(), new Date(end).toISOString()],
    }}).then(() => fitVisibleY(source)).finally(() => {{
      delete source.dataset.rangeControl;
    }});
  }};

  for (const button of rangeButtons) {{
    button.addEventListener("click", () => applyDashboardRange(button.dataset.dashboardRange));
  }}

  const responsiveDefaults = new Map();
  const applyResponsiveLayout = () => {{
    const compact = compactQuery.matches;
    const updates = [];
    for (const id of plotIds) {{
      const plot = document.getElementById(id);
      if (!plot?._fullLayout) continue;
      if (!responsiveDefaults.has(id)) {{
        responsiveDefaults.set(id, {{
          showlegend: Boolean(plot._fullLayout.showlegend),
          marginTop: Number(plot._fullLayout.margin?.t) || 72,
        }});
      }}
      const defaults = responsiveDefaults.get(id);
      updates.push(
        Plotly.relayout(plot, {{
          "showlegend": compact ? false : defaults.showlegend,
          "margin.t": compact ? 48 : defaults.marginTop,
        }}).then(() => {{
          Plotly.Plots.resize(plot);
          return fitVisibleY(plot);
        }})
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
        if (!plot.dataset.syncingX && !plot.dataset.rangeControl) {{
          requestAnimationFrame(() => setActiveRange(matchingDashboardRange(plot)));
        }}
        scheduleFit();
        if (!plot.dataset.syncingX) {{
          requestAnimationFrame(() => synchroniseXFrom(plot));
        }}
      }}
    }});
    plot.on("plotly_relayouting", changes => {{
      if (Object.keys(changes).some(key =>
        key === "xaxis.autorange" || key.startsWith("xaxis.range")
      )) scheduleFit();
    }});
    plot.on("plotly_legendclick", () => setTimeout(scheduleFit, 50));
    plot.on("plotly_legenddoubleclick", () => setTimeout(scheduleFit, 50));
    plot.on("plotly_restyle", changes => {{
      const update = Array.isArray(changes) ? changes[0] : changes;
      if (update && Object.prototype.hasOwnProperty.call(update, "visible")) {{
        setTimeout(scheduleFit, 50);
      }}
    }});
    attachBoundedWheelZoom(plot);
    scheduleFit();
  }}
  applyResponsiveLayout();
  if (typeof compactQuery.addEventListener === "function") {{
    compactQuery.addEventListener("change", applyResponsiveLayout);
  }} else {{
    compactQuery.addListener(applyResponsiveLayout);
  }}
}})();
</script>"""
