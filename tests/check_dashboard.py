# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.50,<2"]
# ///
"""Browser regression check for an already-generated dashboard.

Run with: uv run tests/check_dashboard.py [dashboard.html]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
html_path = (
    Path(sys.argv[1]).expanduser().resolve()
    if len(sys.argv) > 1
    else ROOT / "dashboard.html"
)
if not html_path.is_file():
    raise SystemExit(f"Generate the dashboard first; not found: {html_path}")

errors: list[str] = []
with sync_playwright() as playwright:
    launch_options: dict[str, object] = {
        "headless": True,
        "args": ["--allow-file-access-from-files"],
    }
    system_chrome = Path("/usr/bin/google-chrome")
    if system_chrome.is_file():
        launch_options["executable_path"] = str(system_chrome)
    browser = playwright.chromium.launch(**launch_options)
    page = browser.new_page(viewport={"width": 900, "height": 800})
    page.on("pageerror", lambda error: errors.append(f"page: {error}"))
    page.on(
        "console",
        lambda message: (
            errors.append(f"console: {message.text}")
            if message.type == "error"
            else None
        ),
    )
    page.goto(html_path.as_uri(), wait_until="load")
    page.wait_for_function(
        "document.querySelectorAll('.js-plotly-plot').length === 7"
        " && [...document.querySelectorAll('.js-plotly-plot')]"
        ".every(plot => plot._fullLayout)"
    )

    initial = page.evaluate(
        """
        () => {
          const plots = [...document.querySelectorAll('.js-plotly-plot')];
          const rasi = document.getElementById('plot-rasi-chart');
          const positive = rasi._fullData.find(trace => trace.name === 'RASI above zero');
          const negative = rasi._fullData.find(trace => trace.name === 'RASI below zero');
          const carrier = rasi._fullData.find(trace => trace.name === 'RASI');
          const zeros = trace => new Set(trace.x
            .filter((_, index) => Number(trace.y[index]) === 0)
            .map(value => new Date(value).getTime()));
          const positiveZeros = zeros(positive);
          const sharedZeros = [...zeros(negative)].filter(value => positiveZeros.has(value));
          const weekendCrossings = sharedZeros.filter(value => {
            const day = new Date(value).getDay();
            return day === 0 || day === 6;
          });
          return {
            plots: plots.length,
            kpis: document.querySelectorAll('.kpi').length,
            methodologyCards: document.querySelectorAll('.notes').length,
            controls: [...document.querySelectorAll('[data-dashboard-range]')]
              .map(button => button.textContent),
            allFinite: plots.every(plot => plot._fullData.some(trace =>
              [...(trace.y || [])].some(value => Number.isFinite(Number(value))))),
            verticalFixed: plots.every(plot => plot._fullLayout.yaxis.fixedrange === true),
            localSelectors: document.querySelectorAll('.rangeselector').length,
            activeRange: document.querySelector('[data-dashboard-range][aria-pressed="true"]')
              ?.dataset.dashboardRange || null,
            weekendsCompressed: plots.every(plot =>
              (plot._fullLayout.xaxis.rangebreaks || []).some(breakItem =>
                breakItem.enabled !== false
                && breakItem.pattern === 'day of week'
                // Plotly normalises the input ['sat', 'mon'] to [6, 1].
                && breakItem.bounds?.[0] === 6
                && breakItem.bounds?.[1] === 1)),
            opaqueHoverCards: plots.every(plot =>
              plot._fullLayout.hoverlabel.bgcolor === '#fffdf8'),
            labelledSections: [...document.querySelectorAll('.chart-card')].every(section => {
              const id = section.getAttribute('aria-labelledby');
              return id && document.getElementById(id);
            }),
            chartRoles: plots.every(plot => plot.getAttribute('role') === 'group'),
            sharedRasiCrossings: sharedZeros.length,
            weekendRasiCrossings: weekendCrossings.length,
            carrierHover: carrier?.hovertemplate || '',
            carrierPoints: carrier?.x?.length || 0,
            visualPoints: positive?.x?.length || 0,
            health: document.querySelector('.stamp')?.textContent || '',
          };
        }
        """
    )
    assert initial["plots"] == 7 and initial["kpis"] == 6, initial
    assert initial["methodologyCards"] == 0, initial
    assert initial["controls"] == ["3m", "6m", "1y", "All"], initial
    assert initial["allFinite"] and initial["verticalFixed"], initial
    assert initial["localSelectors"] == 0 and initial["activeRange"] == "all", initial
    assert initial["weekendsCompressed"], initial
    assert initial["opaqueHoverCards"], initial
    assert initial["labelledSections"] and initial["chartRoles"], initial
    assert initial["sharedRasiCrossings"] > 0, initial
    assert initial["weekendRasiCrossings"] == 0, initial
    assert initial["visualPoints"] > initial["carrierPoints"], initial
    assert "Oscillator" in initial["carrierHover"], initial
    assert "Cached through" in initial["health"], initial

    # Native buttons are keyboard-operable and update every chart through one
    # bounded, linked x-range implementation.
    six_months = page.locator('[data-dashboard-range="6m"]')
    six_months.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(900)
    assert six_months.get_attribute("aria-pressed") == "true"
    linked_ranges = page.evaluate(
        """
        () => [...document.querySelectorAll('.js-plotly-plot')].map(plot =>
          plot._fullLayout.xaxis.range.map(value => new Date(value).getTime()))
        """
    )
    for linked in linked_ranges[1:]:
        assert abs(linked[0] - linked_ranges[0][0]) < 1_000, linked_ranges
        assert abs(linked[1] - linked_ranges[0][1]) < 1_000, linked_ranges

    # The one canonical range control must remain accessible while inspecting
    # charts near the bottom of the report.
    page.locator("#plot-net-new-highs-chart").scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    sticky_control = page.locator(".range-control").bounding_box()
    assert sticky_control is not None, sticky_control
    assert 0 <= sticky_control["y"] <= 12, sticky_control
    assert (
        sticky_control["y"] + sticky_control["height"] <= page.viewport_size["height"]
    )
    one_year = page.locator('[data-dashboard-range="1y"]')
    one_year.click()
    page.wait_for_timeout(500)
    assert one_year.get_attribute("aria-pressed") == "true"
    page.locator('[data-dashboard-range="3m"]').click()
    page.wait_for_timeout(500)

    benchmark = page.locator("#plot-benchmark-trend-chart")
    visible_fit = benchmark.evaluate(
        """
        plot => {
          const range = plot._fullLayout.xaxis.range.map(value => new Date(value).getTime());
          const values = plot._fullData.flatMap(trace => trace.x.map((x, index) =>
            [new Date(x).getTime(), Number(trace.y[index])]
          )).filter(([x, y]) => x >= range[0] && x <= range[1] && Number.isFinite(y))
            .map(([, y]) => y);
          return {
            axis: plot._fullLayout.yaxis.range.slice(),
            low: Math.min(...values),
            high: Math.max(...values),
          };
        }
        """
    )
    visible_span = visible_fit["high"] - visible_fit["low"]
    axis_span = visible_fit["axis"][1] - visible_fit["axis"][0]
    assert visible_fit["axis"][0] < visible_fit["low"], visible_fit
    assert visible_fit["axis"][1] > visible_fit["high"], visible_fit
    assert axis_span <= visible_span * 1.3, visible_fit

    # The latest observation must sit inside the plotting area, not directly
    # on its clipping boundary where a final-session crossover disappears.
    latest_endpoint = benchmark.evaluate(
        """
        plot => {
          const trace = plot._fullData.find(item => item.name === 'VAS total return');
          const latest = new Date(trace.x[trace.x.length - 1]).getTime();
          const rangeEnd = new Date(plot._fullLayout.xaxis.range[1]).getTime();
          return {
            latest,
            rangeEnd,
            pixel: plot._fullLayout.xaxis.l2p(latest),
            plotWidth: plot._fullLayout._size.w,
          };
        }
        """
    )
    assert latest_endpoint["rangeEnd"] > latest_endpoint["latest"], latest_endpoint
    assert latest_endpoint["plotWidth"] - latest_endpoint["pixel"] >= 8, latest_endpoint

    # Ordinary wheel input remains page scrolling; only modifier-wheel zoom is
    # captured by the plot.
    benchmark.scroll_into_view_if_needed()
    before_x = benchmark.evaluate("plot => plot._fullLayout.xaxis.range.slice()")
    before_scroll = page.evaluate("window.scrollY")
    box = benchmark.locator(".nsewdrag").bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert (
        benchmark.locator(".nsewdrag").evaluate("node => getComputedStyle(node).cursor")
        == "default"
    )
    page.mouse.down()
    assert (
        benchmark.locator(".nsewdrag").evaluate("node => getComputedStyle(node).cursor")
        == "ew-resize"
    )
    page.mouse.up()
    page.mouse.wheel(0, 500)
    page.wait_for_timeout(250)
    after_x = benchmark.evaluate("plot => plot._fullLayout.xaxis.range.slice()")
    assert before_x == after_x, (before_x, after_x)
    assert page.evaluate("window.scrollY") > before_scroll

    # The transparent real-session carrier supplies one useful unified hover;
    # interpolated visual crossings intentionally do not create fake sessions.
    page.evaluate(
        """
        () => {
          const plot = document.getElementById('plot-rasi-chart');
          const curve = plot._fullData.findIndex(trace => trace.name === 'RASI');
          Plotly.Fx.hover(plot, [{curveNumber: curve, pointNumber: plot._fullData[curve].x.length - 1}]);
        }
        """
    )
    page.wait_for_timeout(150)
    hover_text = page.locator("#plot-rasi-chart .hoverlayer").text_content() or ""
    assert "RASI" in hover_text and "Oscillator" in hover_text, hover_text

    # Physically hover every plot. Unified pointer hovers use an SVG rect while
    # Plotly.Fx.hover above uses a path, so checking only the layout or the
    # programmatic route misses the transparency failure seen in the browser.
    for plot_id in page.locator(".js-plotly-plot").evaluate_all(
        "plots => plots.map(plot => plot.id)"
    ):
        plot = page.locator(f"#{plot_id}")
        plot.scroll_into_view_if_needed()
        drag_surface = plot.locator(".nsewdrag").bounding_box()
        assert drag_surface is not None, plot_id
        page.mouse.move(
            drag_surface["x"] + drag_surface["width"] * 0.72,
            drag_surface["y"] + drag_surface["height"] * 0.5,
        )
        page.wait_for_timeout(100)
        hover_background = plot.locator(".hoverlayer rect.bg")
        assert hover_background.count() == 1, plot_id
        rendered_background = hover_background.evaluate(
            "node => ({fill: getComputedStyle(node).fill, "
            "opacity: getComputedStyle(node).fillOpacity})"
        )
        assert rendered_background == {
            "fill": "rgb(255, 253, 248)",
            "opacity": "1",
        }, (plot_id, rendered_background)

    # Responsive custom layout must react to orientation/viewport changes, not
    # merely inspect the width once at page load.
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_function(
        "[...document.querySelectorAll('.js-plotly-plot')]"
        ".every(plot => plot._fullLayout.showlegend === false)"
    )
    mobile = page.evaluate(
        """
        () => ({
          overflow: document.documentElement.scrollWidth > window.innerWidth,
          visiblePlotlyLegends: [...document.querySelectorAll('.legend')]
            .filter(node => getComputedStyle(node).display !== 'none').length,
          externalLegends: [...document.querySelectorAll('.mobile-legend')]
            .filter(node => getComputedStyle(node).display !== 'none').length,
          selectorVisible: [...document.querySelectorAll('.js-plotly-plot')]
            .some(plot => plot._fullLayout.xaxis.rangeselector.visible !== false),
          controlHeights: [...document.querySelectorAll('[data-dashboard-range]')]
            .map(button => button.getBoundingClientRect().height),
        })
        """
    )
    assert not mobile["overflow"] and not mobile["selectorVisible"], mobile
    assert mobile["visiblePlotlyLegends"] == 0, mobile
    assert mobile["externalLegends"] >= 3, mobile
    assert min(mobile["controlHeights"]) >= 40, mobile

    page.set_viewport_size({"width": 900, "height": 800})
    page.wait_for_function(
        "[...document.querySelectorAll('.js-plotly-plot')]"
        ".some(plot => plot._fullLayout.showlegend === true)"
    )
    restored = page.evaluate(
        """
        () => ({
          plotlyLegends: [...document.querySelectorAll('.legend')]
            .filter(node => getComputedStyle(node).display !== 'none').length,
          externalLegends: [...document.querySelectorAll('.mobile-legend')]
            .filter(node => getComputedStyle(node).display !== 'none').length,
        })
        """
    )
    assert restored["plotlyLegends"] >= 2, restored
    assert restored["externalLegends"] == 0, restored
    browser.close()

assert not errors, errors
print(json.dumps({"dashboard": str(html_path), **initial, "mobile": mobile}))
