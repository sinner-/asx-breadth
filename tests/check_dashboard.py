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
        "document.querySelectorAll('.chart-selector').length === 10"
        " && document.querySelectorAll('.js-plotly-plot').length > 0"
        " && [...document.querySelectorAll('.js-plotly-plot')]"
        ".every(plot => plot._fullLayout)"
        " && document.querySelectorAll('.hover-readout .hover-date').length"
        " === document.querySelectorAll('.js-plotly-plot').length"
        " && document.querySelectorAll('.chart-view.is-active').length === 1"
    )

    initial = page.evaluate(
        """
        () => {
          const plots = [...document.querySelectorAll('.js-plotly-plot')];
          const rasi = document.getElementById('plot-rasi-chart');
          const ad = document.getElementById('plot-ad-chart');
          const geometric = document.getElementById('plot-geometric-index-chart');
          const volatility = document.getElementById('plot-volatility-trend-chart');
          const currency = document.getElementById('plot-currency-index-trend-chart');
          const geometricTrace = geometric._fullData.find(
            trace => trace.name === 'Geometric index');
          const rasiData = rasi?._fullData || [];
          const positive = rasiData.find(trace => trace.name === 'RASI above zero');
          const negative = rasiData.find(trace => trace.name === 'RASI below zero');
          const carrier = rasiData.find(trace => trace.name === 'RASI');
          const axviAbove = volatility._fullData.find(
            trace => trace.name === 'AXVI above EMA');
          const axviBelow = volatility._fullData.find(
            trace => trace.name === 'AXVI below EMA');
          const axviCarrier = volatility._fullData.find(trace => trace.name === 'AXVI');
          const axviFillTraces = volatility._fullData.filter(
            trace => trace.name?.startsWith('AXVI ') && trace.name.includes(' EMA fill '));
          const zeros = trace => new Set((trace?.x || [])
            .filter((_, index) => Number(trace.y[index]) === 0)
            .map(value => new Date(value).getTime()));
          const positiveZeros = zeros(positive);
          const sharedZeros = [...zeros(negative)].filter(value => positiveZeros.has(value));
          const weekendCrossings = sharedZeros.filter(value => {
            const day = new Date(value).getDay();
            return day === 0 || day === 6;
          });
          const carrierLast = (carrier?.x?.length || 1) - 1;
          return {
            plots: plots.length,
            kpis: document.querySelectorAll('.kpi').length,
            kpiLabels: [...document.querySelectorAll('.kpi-label')]
              .map(label => label.textContent),
            kpiValues: Object.fromEntries([...document.querySelectorAll('.chart-selector')]
              .map(card => [card.querySelector('.kpi-label')?.textContent,
                card.querySelector('strong')?.textContent])),
            mastheads: document.querySelectorAll('header, h1, .eyebrow').length,
            footers: document.querySelectorAll('footer').length,
            footerText: document.querySelector('footer')?.textContent || '',
            selectorCards: document.querySelectorAll('.chart-selector').length,
            sparklineCards: document.querySelectorAll('.chart-selector .sparkline').length,
            sparklinePeriods: [...document.querySelectorAll('.spark-period')]
              .map(label => label.textContent),
            sparklinePaths: [...document.querySelectorAll('.sparkline .spark-path')]
              .filter(path => path.getAttribute('d')?.trim()).length,
            sparklineColours: Object.fromEntries(
              [...document.querySelectorAll('.chart-selector')].map(card => [
                card.querySelector('.kpi-label')?.textContent,
                [...card.querySelectorAll('.spark-path')]
                  .map(path => getComputedStyle(path).stroke),
              ])),
            chartPanes: document.querySelectorAll('.chart-pane').length,
            chartViews: document.querySelectorAll('.chart-view').length,
            activeViews: document.querySelectorAll('.chart-view.is-active').length,
            pressedSelectors: document.querySelectorAll(
              '.chart-selector[aria-pressed="true"]').length,
            maxSelectorHeight: Math.max(...[...document.querySelectorAll('.chart-selector')]
              .map(card => card.getBoundingClientRect().height)),
            selectorColumns: new Set([...document.querySelectorAll('.chart-selector')]
              .map(card => Math.round(card.getBoundingClientRect().x))).size,
            truncatedSelectorLabels: [...document.querySelectorAll('.kpi-label')]
              .filter(label => label.scrollWidth > label.clientWidth).length,
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
            plotlyLegendsDisabled: plots.every(plot => plot._fullLayout.showlegend === false),
            chartMetaRows: document.querySelectorAll('.chart-meta').length,
            externalLegendRows: document.querySelectorAll('.chart-legend').length,
            externalReadouts: document.querySelectorAll('.hover-readout').length,
            legendlessCards: [...document.querySelectorAll('.chart-view:not(.unavailable)')]
              .filter(card => card.querySelectorAll('.legend-item').length === 0).length,
            defaultReadouts: plots.map(plot => {
              const readout = document.getElementById(`hover-${plot.id}`);
              const meta = readout?.closest('.chart-meta');
              return {
                id: plot.id,
                text: meta?.textContent || '',
                dates: readout?.querySelectorAll('.hover-date').length || 0,
                values: [...(meta?.querySelectorAll('.legend-value') || [])]
                  .filter(value => value.textContent.trim()).length,
                readoutChildren: readout?.children.length || 0,
                duplicateSwatches: readout?.querySelectorAll('.legend-swatch').length || 0,
              };
            }),
            defaultRasiReadout: document.getElementById('hover-plot-rasi-chart')
              ?.textContent || '',
            defaultRasiMeta: document.getElementById('hover-plot-rasi-chart')
              ?.closest('.chart-meta')?.textContent || '',
            latestRasiDate: carrier ? new Intl.DateTimeFormat('en-AU', {
              day: 'numeric', month: 'short', year: 'numeric'
            }).format(new Date(carrier.x[carrierLast])) : null,
            latestRasiValue: carrier ? Number(carrier.y[carrierLast]).toLocaleString('en-AU', {
              minimumFractionDigits: 2, maximumFractionDigits: 2
            }) : null,
            labelledSections: [...document.querySelectorAll('.chart-view')].every(section => {
              const id = section.getAttribute('aria-labelledby');
              return id && document.getElementById(id);
            }),
            chartHeadings: [...document.querySelectorAll('.chart-view h2')]
              .map(heading => heading.textContent),
            chartRoles: plots.every(plot => plot.getAttribute('role') === 'group'),
            sharedRasiCrossings: sharedZeros.length,
            weekendRasiCrossings: weekendCrossings.length,
            carrierHover: carrier?.hovertemplate || '',
            carrierPoints: carrier?.x?.length || 0,
            visualPoints: positive?.x?.length || 0,
            geometricPoints: geometricTrace?.x?.length || 0,
            geometricHover: geometricTrace?.hovertemplate || '',
            geometricSeries: geometric._fullData
              .filter(trace => [...(trace.y || [])].some(value => Number.isFinite(Number(value))))
              .map(trace => trace.name),
            axviAbovePoints: axviAbove?.y?.filter(value => Number.isFinite(Number(value))).length || 0,
            axviBelowPoints: axviBelow?.y?.filter(value => Number.isFinite(Number(value))).length || 0,
            axviAboveColour: axviAbove?.line?.color || null,
            axviBelowColour: axviBelow?.line?.color || null,
            axviLineFills: [axviAbove?.fill || null, axviBelow?.fill || null],
            axviFillCount: axviFillTraces.length,
            axviFillModes: [...new Set(axviFillTraces.map(trace => trace.fill))],
            axviFillHasGaps: axviFillTraces.some(trace =>
              [...(trace.y || [])].some(value => !Number.isFinite(Number(value)))),
            axviHover: axviCarrier?.hovertemplate || '',
            adSeries: ad._fullData
              .filter(trace => [...(trace.y || [])].some(value => Number.isFinite(Number(value))))
              .map(trace => trace.name),
            currencyPoints: currency._fullData.find(trace => trace.name === 'XDA')
              ?.y?.filter(value => Number.isFinite(Number(value))).length || 0,
            currencySeries: currency._fullData
              .filter(trace => [...(trace.y || [])].some(value => Number.isFinite(Number(value))))
              .map(trace => trace.name),
            health: document.querySelector('footer')?.textContent || '',
          };
        }
        """
    )
    assert 1 <= initial["plots"] <= 10 and initial["kpis"] == 10, initial
    assert initial["kpiLabels"] == [
        "VAS total return",
        "ASX 300 geometric",
        "Cumulative A/D",
        "RASI",
        "McClellan oscillator",
        "New 52-week highs",
        "New 52-week lows",
        "New highs − lows",
        "AXVI",
        "XDA",
    ], initial
    assert initial["kpiValues"]["New 52-week lows"].endswith(" lows"), initial
    assert initial["mastheads"] == 0 and initial["footers"] == 1, initial
    assert "holdings as at" in initial["footerText"], initial
    assert "Generated" in initial["footerText"], initial
    assert initial["selectorCards"] == 10, initial
    assert initial["sparklineCards"] == 10, initial
    assert initial["sparklinePeriods"] == ["1Y"] * 10, initial
    assert initial["sparklinePaths"] >= initial["plots"], initial
    assert initial["sparklineColours"]["ASX 300 geometric"] == ["rgb(22, 33, 29)"], (
        initial
    )
    assert initial["sparklineColours"]["Cumulative A/D"] == ["rgb(20, 125, 100)"], (
        initial
    )
    assert initial["sparklineColours"]["New 52-week highs"] == ["rgb(17, 17, 17)"], (
        initial
    )
    if "plot-rasi-chart" in {item["id"] for item in initial["defaultReadouts"]}:
        assert set(initial["sparklineColours"]["RASI"]) == {
            "rgb(20, 125, 100)",
            "rgb(184, 75, 69)",
        }, initial
    assert set(initial["sparklineColours"]["AXVI"]) == {
        "rgb(17, 17, 17)",
        "rgb(184, 75, 69)",
    }, initial
    assert initial["chartPanes"] == 1 and initial["chartViews"] == 10, initial
    assert initial["activeViews"] == 1 and initial["pressedSelectors"] == 1, initial
    assert initial["maxSelectorHeight"] <= 130, initial
    assert initial["selectorColumns"] == 4, initial
    assert initial["truncatedSelectorLabels"] == 0, initial
    assert initial["methodologyCards"] == 0, initial
    assert initial["controls"] == ["3m", "6m", "1y", "All"], initial
    assert initial["allFinite"] and initial["verticalFixed"], initial
    assert initial["localSelectors"] == 0 and initial["activeRange"] == "all", initial
    assert initial["weekendsCompressed"], initial
    assert initial["plotlyLegendsDisabled"], initial
    assert initial["chartMetaRows"] == initial["plots"], initial
    assert initial["externalLegendRows"] == initial["plots"], initial
    assert initial["externalReadouts"] == initial["plots"], initial
    assert initial["legendlessCards"] == 0, initial
    assert all(
        item["dates"] == 1
        and item["values"] >= 1
        and item["readoutChildren"] == 1
        and item["duplicateSwatches"] == 0
        and "Hover chart for values" not in item["text"]
        for item in initial["defaultReadouts"]
    ), initial
    if initial["latestRasiDate"] is not None:
        assert initial["latestRasiDate"] in initial["defaultRasiReadout"], initial
        assert initial["latestRasiValue"] in initial["defaultRasiMeta"], initial
    assert initial["labelledSections"] and initial["chartRoles"], initial
    assert initial["chartHeadings"][-2:] == [
        "S&P/ASX 200 VIX (AXVI)",
        "Australian Dollar Currency Index (XDA)",
    ], initial
    if initial["latestRasiDate"] is not None:
        assert initial["sharedRasiCrossings"] > 0, initial
        assert initial["weekendRasiCrossings"] == 0, initial
        assert initial["visualPoints"] > initial["carrierPoints"], initial
        assert "Oscillator" not in initial["carrierHover"], initial
    assert initial["geometricPoints"] > 0, initial
    assert "Daily geometric return" not in initial["geometricHover"], initial
    assert "Coverage" not in initial["geometricHover"], initial
    assert "Geometric index" in initial["geometricSeries"], initial
    assert set(initial["geometricSeries"]) <= {
        "Geometric index",
        "19-session EMA",
        "39-session EMA",
        "200-session EMA",
    }, initial
    assert initial["axviAbovePoints"] > 0 and initial["axviBelowPoints"] > 0, initial
    assert initial["axviAboveColour"] == "#b84b45", initial
    assert initial["axviBelowColour"] == "#111111", initial
    assert initial["axviLineFills"] == ["none", "none"], initial
    assert initial["axviFillCount"] > 2, initial
    assert initial["axviFillModes"] == ["toself"], initial
    assert not initial["axviFillHasGaps"], initial
    assert "Spread" not in initial["axviHover"], initial
    assert "Above" not in initial["axviHover"], initial
    assert "Cumulative A/D" in initial["adSeries"], initial
    assert set(initial["adSeries"]) <= {
        "Cumulative A/D",
        "19-session EMA",
        "39-session EMA",
        "200-session EMA",
    }, initial
    assert initial["currencyPoints"] > 0, initial
    assert set(initial["currencySeries"]) == {
        "XDA",
        "19-session EMA",
        "39-session EMA",
        "200-session EMA",
    }, initial
    assert "Cached through" in initial["health"], initial
    rasi_available = any(
        item["id"] == "plot-rasi-chart" for item in initial["defaultReadouts"]
    )

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
        # A newer series cannot expose dates before its first honest observation;
        # it clamps only that left edge while preserving the shared right edge.
        assert linked[0] >= linked_ranges[0][0] - 1_000, linked_ranges
        assert abs(linked[1] - linked_ranges[0][1]) < 1_000, linked_ranges

    # Rapid range changes are last-action-wins; no in-flight fan-out may drop
    # the final selection for hidden plots.
    page.evaluate(
        """
        () => {
          document.querySelector('[data-dashboard-range="3m"]').click();
          document.querySelector('[data-dashboard-range="all"]').click();
          document.querySelector('[data-dashboard-range="6m"]').click();
        }
        """
    )
    page.wait_for_timeout(1200)
    assert six_months.get_attribute("aria-pressed") == "true"
    rapid_ranges = page.evaluate(
        """
        () => [...document.querySelectorAll('.js-plotly-plot')].map(plot =>
          plot._fullLayout.xaxis.range.map(value => new Date(value).getTime()))
        """
    )
    for linked in rapid_ranges[1:]:
        assert linked[0] >= rapid_ranges[0][0] - 1_000, rapid_ranges
        assert abs(linked[1] - rapid_ranges[0][1]) < 1_000, rapid_ranges

    # The one canonical range control remains accessible while inspecting the
    # expanded pane and footer.
    page.locator("footer").scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    sticky_control = page.locator(".range-control").bounding_box()
    assert sticky_control is not None, sticky_control
    assert sticky_control["y"] >= 0, sticky_control
    assert (
        page.locator(".range-control").evaluate(
            "node => getComputedStyle(node).position"
        )
        == "sticky"
    )
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
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(100)
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

    if rasi_available:
        # The transparent real-session carrier supplies one useful external hover;
        # interpolated visual crossings intentionally do not create fake sessions.
        rasi_selector = page.locator('[data-plot-id="plot-rasi-chart"]')
        rasi_selector.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(450)
        assert rasi_selector.get_attribute("aria-pressed") == "true"
        assert page.locator(".chart-view.is-active h2").text_content() == (
            "McClellan Ratio-Adjusted Summation Index"
        )
        rasi_plot = page.locator("#plot-rasi-chart")
        rasi_plot.scroll_into_view_if_needed()
        rasi_surface = rasi_plot.locator(".nsewdrag").bounding_box()
        assert rasi_surface is not None
        page.mouse.move(
            rasi_surface["x"] + rasi_surface["width"] * 0.72,
            rasi_surface["y"] + rasi_surface["height"] * 0.5,
        )
        page.wait_for_timeout(150)
        hover_readout = page.locator("#hover-plot-rasi-chart")
        hover_text = hover_readout.text_content() or ""
        hover_meta = hover_readout.locator(
            "xpath=ancestor::div[contains(@class, 'chart-meta')]"
        )
        hover_meta_text = hover_meta.text_content() or ""
        assert (
            "RASI" not in hover_text
            and hover_readout.locator(".legend-swatch").count() == 0
        )
        assert "RASI" in hover_meta_text and "Oscillator" not in hover_meta_text
        assert "EMA" not in hover_meta_text and "Above" not in hover_meta_text
        page.mouse.move(0, 0)
        page.wait_for_timeout(150)
        assert (
            page.locator("#hover-plot-rasi-chart").text_content()
            == initial["defaultRasiReadout"]
        )
        assert hover_meta.text_content() == initial["defaultRasiMeta"]

    # Physically hover every plot. Values must appear in the permanent row above
    # the plot and native Plotly hover cards must remain hidden.
    for plot_id in page.locator(".js-plotly-plot").evaluate_all(
        "plots => plots.map(plot => plot.id)"
    ):
        selector = page.locator(f'[data-plot-id="{plot_id}"]')
        selector.click()
        page.wait_for_timeout(250)
        assert selector.get_attribute("aria-pressed") == "true", plot_id
        assert page.locator(".chart-selector[aria-pressed='true']").count() == 1
        assert page.locator(".chart-view.is-active").count() == 1
        assert (
            page.locator(".chart-view.is-active .js-plotly-plot").get_attribute("id")
            == plot_id
        )
        plot = page.locator(f"#{plot_id}")
        plot.scroll_into_view_if_needed()
        drag_surface = plot.locator(".nsewdrag").bounding_box()
        assert drag_surface is not None, plot_id
        page.mouse.move(
            drag_surface["x"] + drag_surface["width"] * 0.72,
            drag_surface["y"] + drag_surface["height"] * 0.5,
        )
        page.wait_for_timeout(100)
        readout = page.locator(f"#hover-{plot_id}")
        readout_text = readout.text_content() or ""
        chart_meta = readout.locator(
            "xpath=ancestor::div[contains(@class, 'chart-meta')]"
        )
        chart_meta_text = chart_meta.text_content() or ""
        assert "Hover chart for values" not in readout_text, (plot_id, readout_text)
        assert readout.locator(".legend-swatch, .hover-value").count() == 0, plot_id
        assert readout.locator(".hover-date").count() == 1, plot_id
        assert all(
            value.strip()
            for value in chart_meta.locator(".legend-value").all_text_contents()
        ), (plot_id, chart_meta_text)
        assert not any(
            unwanted in chart_meta_text
            for unwanted in (
                "Coverage",
                "Quality",
                "Eligible",
                "Spread",
                "Above 200",
                "Below 200",
            )
        ), (plot_id, readout_text)
        native_hovers = plot.locator(".hoverlayer .legend, .hoverlayer .hovertext")
        for index in range(native_hovers.count()):
            assert (
                native_hovers.nth(index).evaluate(
                    "node => getComputedStyle(node).display"
                )
                == "none"
            ), plot_id

    # Responsive custom layout must react to orientation/viewport changes, not
    # merely inspect the width once at page load.
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_function(
        "[...document.querySelectorAll('.js-plotly-plot')]"
        ".every(plot => plot._fullLayout.showlegend === false)"
    )
    page.evaluate("window.scrollTo(0, 0)")
    mobile_plot_id = (
        "plot-rasi-chart" if rasi_available else "plot-benchmark-trend-chart"
    )
    page.locator(f'[data-plot-id="{mobile_plot_id}"]').click()
    page.wait_for_timeout(800)
    assert page.evaluate("window.scrollY") > 0
    active_pane_box = page.locator(".chart-pane").bounding_box()
    assert active_pane_box is not None and active_pane_box["y"] < 100, active_pane_box
    mobile = page.evaluate(
        """
        () => ({
          overflow: document.documentElement.scrollWidth > window.innerWidth,
          visiblePlotlyLegends: [...document.querySelectorAll('.legend')]
            .filter(node => getComputedStyle(node).display !== 'none').length,
          externalLegends: [...document.querySelectorAll('.chart-view.is-active .chart-legend')]
            .filter(node => getComputedStyle(node).display !== 'none').length,
          activeViews: document.querySelectorAll('.chart-view.is-active').length,
          selectorColumns: new Set([...document.querySelectorAll('.chart-selector')]
            .map(card => Math.round(card.getBoundingClientRect().x))).size,
          selectorVisible: [...document.querySelectorAll('.js-plotly-plot')]
            .some(plot => plot._fullLayout.xaxis.rangeselector.visible !== false),
          rightmostTickInside: (() => {
            const pane = document.querySelector('.chart-pane').getBoundingClientRect();
            const ticks = [...document.querySelectorAll(
              '.chart-view.is-active .xaxislayer-above .xtick text')];
            return ticks.length > 0
              && Math.max(...ticks.map(tick => tick.getBoundingClientRect().right))
                <= pane.right + 1;
          })(),
          controlHeights: [...document.querySelectorAll('[data-dashboard-range]')]
            .map(button => button.getBoundingClientRect().height),
        })
        """
    )
    assert not mobile["overflow"] and not mobile["selectorVisible"], mobile
    assert mobile["visiblePlotlyLegends"] == 0, mobile
    assert mobile["externalLegends"] == 1 and mobile["activeViews"] == 1, mobile
    assert mobile["selectorColumns"] == 2, mobile
    assert mobile["rightmostTickInside"], mobile
    assert min(mobile["controlHeights"]) >= 40, mobile

    page.set_viewport_size({"width": 900, "height": 800})
    page.wait_for_function(
        "[...document.querySelectorAll('.js-plotly-plot')]"
        ".every(plot => plot._fullLayout.showlegend === false)"
    )
    restored = page.evaluate(
        """
        () => ({
          plotlyLegends: [...document.querySelectorAll('.legend')]
            .filter(node => getComputedStyle(node).display !== 'none').length,
          externalLegends: [...document.querySelectorAll('.chart-view.is-active .chart-legend')]
            .filter(node => getComputedStyle(node).display !== 'none').length,
        })
        """
    )
    assert restored["plotlyLegends"] == 0, restored
    assert restored["externalLegends"] == 1, restored
    browser.close()

assert not errors, errors
print(json.dumps({"dashboard": str(html_path), **initial, "mobile": mobile}))
