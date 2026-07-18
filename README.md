# ASX Breadth

An append-safe, extensible ASX market-breadth engine. The dashboard combines the VAS
total-return trend and hysteresis band with a Value Line-style ASX 300 geometric
total-return index, the Australian Dollar Currency Index (XDA) with 19/39/200-session
EMAs, the S&P/ASX 200 VIX (AXVI) and its 200-session EMA,
advance/decline breadth, the McClellan oscillator and Ratio-Adjusted Summation Index
(RASI), and 52-week highs, lows, and NH-NL. The storage and plugin boundaries remain
deliberately broader so further breadth families can be added without replacing ingestion.

## Run it

The executable is a [PEP 723](https://peps.python.org/pep-0723/) `uv` script, so no
manual virtual environment or install step is required:

```bash
cd ~/Development/asx-breadth
uv run breadth.py ~/Downloads/'Holding details_17_07_2026.xlsx'
```

This creates:

- `data/asx_breadth.sqlite3` — the incremental cache
- `dashboard.html` — a self-contained interactive Plotly dashboard

Open the dashboard directly in a browser. The ten compact cards are chart selectors with
252-session sparklines; selecting one swaps it into the single expanded chart pane. Drag zoom,
range buttons, and Ctrl/Command + wheel work horizontally on dates and stay linked across
chart changes. An ordinary wheel keeps
scrolling the page. Direct vertical zoom is disabled, while each value axis automatically
refits to the data inside the visible date window. The sticky date buttons above the chart
are keyboard-operable. The expanded chart has one permanent legend above the plot, with the
latest series values shown in that legend and the date beside it; hovering temporarily updates
both for the selected session. Holdings and cache status are kept in the footer.

Useful options:

```bash
# Re-render without contacting Yahoo
uv run breadth.py HOLDINGS.xlsx --no-download

# Put the cache and dashboard elsewhere
uv run breadth.py HOLDINGS.xlsx --db /path/cache.sqlite3 --output /path/report.html

# More conservative provider pacing
uv run breadth.py HOLDINGS.xlsx --threads 1 --batch-size 10 --batch-pause 3

# Override Yahoo's AXVI symbol if its identifier ever changes
uv run breadth.py HOLDINGS.xlsx --volatility-symbol '^AXVI'

# Override Yahoo's Australian Dollar Currency Index symbol
uv run breadth.py HOLDINGS.xlsx --currency-index-symbol '^XDA'

# Use only when a workbook has no trustworthy embedded effective date
uv run breadth.py HOLDINGS.xlsx --as-of-date 2026-07-17
```

Use `uv run breadth.py --help` for all settings.

## Why the cache survives corporate actions

Yahoo adjusted-close **levels** are not append-stable. A later dividend can revise the
entire older adjusted history—for example, an old raw close of `$87` might display as an
adjusted `$75`. The database therefore keeps two distinct layers:

1. `provider_observations` append every OHLCV, adjusted close, dividend, split, fetch run,
   and observation time exactly as returned. Repeated overlap dates preserve adjustment
   epochs for audit.
2. `daily_factors` store canonical ratios calculated from a fresh pair returned in the
   same request. Total-return factor is `AdjClose[t] / AdjClose[t-1]`; price and intraday
   high/low factors use unadjusted OHLC relative to the prior close.

Corporate-action revisions rescale the two prices in an older pair together, so the
ratio and advance/decline sign normally remain stable. Incremental requests deliberately
fetch an overlap, require the cached seam anchor to be present, and derive factors only from
internally consistent row pairs. A genuine delayed correction or newly inserted interior
date can repair the canonical factor, while `factor_revisions` retains the replaced value,
source run, reason, and time. An incomplete overlap cannot rewire a factor around a still
cached date; both response validation and the repository enforce the chronological chain.
This also provides what NH–NL and future price-based plugins
need to reconstruct continuous, split-adjusted price/high/low streams without mistaking a
dividend adjustment for the historical traded price.

## Architecture

```text
breadth.py                       PEP 723 CLI entrypoint
src/asx_breadth/
  holdings.py                    workbook discovery and symbol mapping
  db.py                          versioned universes + append-only observations/factors
  providers/yahoo.py             replaceable provider adapter
  sync.py                        batching, retries, backoff, incremental watermarks
  indicators/
    base.py                      dependency-aware indicator protocol
    benchmark_trend.py           VAS total return and high/low hysteresis band
    geometric_index.py           equal-dollar geometric constituent total-return index
    currency_index_trend.py      actual XDA level and 19/39/200-session EMAs
    series_level.py              shared reconstruction for single published indices
    volatility_trend.py          actual AXVI level and 200-session EMA
    advance_decline.py           consecutive-session A/D
    mcclellan.py                 ratio-adjusted oscillator and summation
    new_highs_lows.py            52-week highs, lows, and NH-NL
  panels/
    base.py                      dashboard panel and summary protocol
    charts.py                    Plotly VAS trend, A/D, McClellan, and NH-NL cards
    summary.py                   shared panel summary and quality-state helpers
  dashboard.py                   self-contained HTML composition
```

Every workbook import is a `universe_snapshot` with an effective holdings date and source
hash. Passing a later VAS file does not erase the old composition. A holdings list described
as “as at” a close becomes active on the following VAS session, so additions and removals
do not rewrite earlier breadth history. Breadth history begins on the first VAS session after
the earliest captured holdings date; no composition is projected backward into sessions for
which no point-in-time snapshot exists.

Workbook schema validation fails closed before database import when the required holdings
headers are missing or ambiguous. Column order, unrelated extra columns, a holdings table on
a non-active worksheet, and blank optional row details remain supported.

The importer refuses an undated, implausibly small, badly weighted, sharply discontinuous,
or chronologically older workbook unless the explicit `--allow-suspicious-holdings` escape
hatch is used. `--no-download` also refuses to make a never-synchronised snapshot
authoritative.

The benchmark and other named market series are stored as role-based auxiliary universe
series. They share the same factor cache, retry/backoff, 1,000-session initial backfill, and
incremental watermarks as holdings, but never enter point-in-time membership or breadth
counts. AXVI is registered under the `volatility` role with Yahoo symbol `^AXVI`; XDA
uses the `currency_index` role and Yahoo symbol `^XDA`.

On a composition change, the downloader persists an obligation for every outgoing holding
and synchronises it alongside the incoming basket. The obligation survives failed runs and
later snapshots until a validated response has checked that instrument through its inclusive
final membership date. Removed names are not probed beyond that boundary. Historical depth,
forward freshness, and removal boundaries are planned independently, so a late-listed name
can extend old history and fetch the current week in the same run. Its cached factors then
remain available to historical calculations.

Adding an indicator means implementing the small protocol in `indicators/base.py` and
registering it. Adding a chart is independently handled by the panel protocol. Neither
requires changes to Yahoo synchronisation.

## Calculation policy

- Initial sync requests enough calendar history to target at least 1,000 trading sessions.
- VAS.AX supplies the canonical ASX session calendar. A/D uses adjusted-close total return
  and only counts an issue when its quote and prior quote are on consecutive VAS sessions.
  Membership is selected point-in-time from the dated holdings snapshots.
- Missing data, trading halts, and resumptions are excluded for the affected A/D day; they
  are not silently called unchanged or treated as a multi-day move.
- A broad-universe session is withheld from cumulative A/D and McClellan calculations when
  quote coverage is below the greater of 90% or 95% of the trailing 60-session median. Raw
  counts and the rejected coverage remain visible for diagnosis; cumulative and EMA state
  hold until the next accepted session.
- Ratio-adjusted net advances are exactly
  `1000 × (advances - declines) / (advances + declines)`; unchanged issues are
  excluded from this denominator.
- McClellan oscillator is the 19-session (10% trend) minus the 39-session
  (5% trend) EMA of that ratio. Both trend recurrences are seeded at zero.
- RASI is the cumulative oscillator, also seeded at zero.
- The A/D chart overlays 19-, 39-, and 200-session EMAs of cumulative A/D. The cache also
  retains daily net A/D EMAs for future indicator work.
- The VAS total-return series is anchored to its latest adjusted close, retaining the
  actual VAS.AX adjusted-price scale. It overlays 19/39-session EMAs plus 200-session EMAs
  of dividend/split-adjusted daily highs and lows as a hysteresis band.
- The ASX 300 Geometric Index starts at 100 and links the daily geometric mean of
  consecutive-session adjusted-close total-return factors for the effective-dated VAS
  basket. It borrows Value Line's equal-dollar geometric construction but neutralises both
  distributions and splits so ex-dividend dates do not manufacture market weakness. The
  chart overlays 19-, 39-, and 200-session EMAs of that index. Missing or halted issues are
  omitted for that session; the shared coverage gate holds the index during a broad data
  outage.
- AXVI is a single published index series, not a constituent composite. Its actual level is
  reconstructed backward from the latest cached `^AXVI` close using append-stable daily
  factors, then overlaid with a 200-session EMA. The chart shades the distance to the EMA
  red with a red line while AXVI is above it, and black with a black line while below it.
  A broken factor chain is left unavailable rather than bridged with invented values.
- XDA is likewise a single published index rather than a breadth composite. Its actual
  `^XDA` level is reconstructed from cached daily factors and overlaid with 19-, 39-, and
  200-session EMAs. Because yfinance's repair mode can reject valid caret-prefixed index
  history, a missing index alone is retried once from Yahoo without repair; constituent
  downloads remain on the repaired path.
- New highs and lows use a 252-session window over the continuous total-return-adjusted high/low
  stream. A constituent must have reached the full calendar age and at least 90% of the
  preceding observations, so an isolated quote gap does not suppress it for another year.
  Pre-entry prices can satisfy that warm-up, but an issue is counted only while active.
  NH-NL is the daily new-high count minus the new-low count. It uses the same active-universe
  quote-quality gate as A/D, separately from the count of issues with sufficient long history.

`yfinance` is an unofficial client for Yahoo Finance. Its `repair=True` mode is enabled to
address known missing-price, split, dividend, and unit errors. The downloader uses small
batches, limited concurrency, jittered pauses, exponential retry backoff, per-symbol
cross-run cooldowns, bounded probes for halted securities, and seam validation before a
response can advance state. A suspiciously short initial history needs a second matching
probe—even when the requested lookback date moves between weekly runs—before it is accepted
as a natural listing boundary. Anchor-invalid or internally incomplete overlap responses stay
inside the configured retry/backoff loop. A failed provider run still rebuilds the dashboard
from valid cached data.

## Tests

```bash
uv run tests/test_core.py
uv run tests/check_dashboard.py
```

The tests cover workbook admission, effective-dated composition changes, persistent
transition syncing, audited factor repair, response-anchor/history validation, benchmark
session policy, auxiliary-series caching, AXVI level reconstruction, gap/halt and coverage
policy, RASI state, point-in-time new-high/low eligibility, and horizontal-only chart
configuration. The committed browser smoke check exercises the generated report's linked
zoom, sticky date controls, visible-range y fitting, exact bicolour RASI and AXVI regimes,
external hover readouts, responsive legends, viewport changes, and accessibility wiring. It
uses system Chrome when available; generate `dashboard.html` before running it.
