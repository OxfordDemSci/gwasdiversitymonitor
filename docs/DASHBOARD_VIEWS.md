# Shareable dashboard views

Use **Copy link** to share the current dashboard. The URL preserves funders,
cohorts, metric, stage, chart years, parent terms, selected traits, ancestry
filters, the “Include not recorded” option, associations, and map zoom. Browser
Back and Forward restore previous selections. If clipboard access is unavailable,
the toolbar provides a selectable URL.

Links select a view of the currently published dataset; they do not freeze an
older Catalog release. Unavailable chart years fall back to an available year,
and unknown parent terms or ancestry controls fall back to their default. Unknown
funder/cohort IDs produce a visible selection error. The map retains its fitted
layout on small screens where interactive zoom is disabled. Bubble axes are
derived from the selected data, with no independent axis setting.

## Version 1 URL contract

The `view=1` query parameter marks the format. Defaults are omitted. Unrelated
query parameters and URL fragments are preserved. IDs are deduplicated and sorted.
JSON values are encoded by `URLSearchParams`, not concatenated into HTML.

| Query field | Value / default |
| --- | --- |
| `metric` | `participants` (default) or `studies` |
| `stage` | `discovery` (default) or `replication` |
| `funders`, `cohorts` | Comma-separated IDs; empty means all |
| `heatYear`, `mapYear`, `doughnutYear` | Four-digit year; omitted means latest available |
| `doughnutTerm`, `bubbleTerm` | Parent-term option value; default `all` |
| `associations`, `notRecorded` | `1` for enabled; otherwise disabled |
| `ancestry` | Time-series ancestry option; default `all-ancestries` |
| `bubbleAncestries` | Comma-separated ancestry option values; empty means all |
| `traits` | JSON array of `{id, text}` selected trait entries |
| `mapZoom` | JSON `{x, y, k}`; default `{x: 0, y: 0, k: 1}` |

Input normalization accepts at most 50 values per selection, validates ID syntax,
limits text lengths, and rejects unsupported versions and oversized links. Copying
links longer than 7,500 characters is refused with an explanation, keeping below
common proxy request-line limits. Trait text is displayed using DOM text APIs.
Facet existence is validated by the existing filtered-dashboard endpoint; loading
a shared view does not fetch the full funder/cohort catalogues.

## Browser integration

`app/static/js/dashboard-state.js` provides the pure `GwasDashboardState`
`normalize(input)`, `parse(search)`, and `url(state, absoluteBaseURL)` helpers.
`parse()` returns `{state, present, warnings}`. The browser controller is exposed
as `window.gwasDashboardState` after DOM initialization:

- `capture()` returns the normalized current state shown below.
- `url()` returns a complete link for that state.
- `restore(state)` restores controls and charts, returning `Promise<boolean>`.
- `isReady()` reports baseline readiness; `isRestoring()` reports navigation in progress.
- `changed()` schedules a history entry for an interaction; `settled()` updates
  that entry after asynchronous rendering clamps chart years.
- `copy()` attempts clipboard copying and provides a manual fallback.

```javascript
{
  version: 1,
  metric: 'participants', stage: 'discovery',
  funders: [], cohorts: [],
  heatMap: {year: null},
  worldMap: {year: null, zoom: {x: 0, y: 0, k: 1}},
  doughnut: {year: null, parentTerm: 'all', associations: false},
  timeSeries: {ancestry: 'all-ancestries', notRecorded: false},
  bubble: {parentTerm: 'all', ancestries: [], traits: []}
}
```

The controller emits `gwas:viewchange` on `window`, with the normalized state in
`event.detail`, after initial readiness and navigation changes. Consumers such as
exports should call `capture()` when invoked, rather than keep a separate copy of
chart controls. Restoration waits for every baseline plot and then uses one
filtered-dashboard request when needed. Both request and navigation revisions
prevent older responses or completion messages from overriding later navigation.

Run the URL normalization/navigation tests with
`python -m unittest tests.test_dashboard_state` (requires Node).
