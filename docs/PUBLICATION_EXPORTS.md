# Publication exports

Use **Export view** in the dashboard toolbar for a ZIP containing all five
charts, one chart, or a completed side-by-side comparison. The data-download
icon beside each chart exports that chart. The image menu offers **SVG + data
ZIP** and **PNG + data ZIP**, with the same data and documentation included.

Exports capture the successfully loaded view, including its dataset identity,
entity selections, stage, metric, and chart-specific settings. They are disabled
during loading or view restoration, after a failed selection request, and if the
server reports a different dataset. If settings change while an export is being
prepared, the export stops and asks you to retry; it does not label new artwork
with an old view's metadata.

## Bundle contents and interpretation

- One UTF-8 CSV per selected chart, with unrounded numeric values, column
  headings, and proper quoting for commas, quotes, and newlines.
- `view.json`: shareable URL, export time, full dashboard view, each chart's
  actual settings, column definitions, row count, and methodology.
- `provenance.json`: the immutable dataset identity and provenance captured
  when the dashboard loaded, not a newer release fetched during export.
- `CITATION.txt`: the Monitor publication and software citations.
- `README.txt`: scope, denominator definitions, overlap warnings, and text-cell
  safety notes.
- For an image export, the selected SVG or PNG figure as well.

Ancestry counts need careful interpretation. Legacy dashboard **Studies** values
count ancestry records; the comparison's **Distinct studies** metric counts
distinct study accessions. Participant values are participant instances, not
deduplicated people. Entity attribution uses full counting, so selections may
overlap. Every chart's methodology describes its own denominator and exclusions.

The rows come from the chart's existing transformations, rather than a separate
server calculation. In particular:

| Chart | Exported scope |
| --- | --- |
| Bubble | Currently plotted points after stage, trait, parent-term, and ancestry controls; participant values regardless of the global metric. |
| Time series | The supplied series selected by the not-recorded control and selected ancestry lines, without recalculating percentages. |
| Heat map | The current year, stage, and metric, restricted to the six ancestry groups actually drawn. |
| World map | The current year/stage/metric values joined to map geometry; unmatched countries are omitted. Percentages keep the source denominator, not a recalculated visible-country denominator. Pan/zoom changes presentation, not the row selection. |
| Doughnut | The current year and parent term. Association rows appear only when that overlay is enabled and are explicitly labelled discovery-stage association data. |
| Comparison | Explicit A/B side identifiers, ancestry counts/shares, side denominators, and overlap/coverage information in settings and methodology. |

Known legacy limitation: **Include not recorded** selects the supplied series,
but entity-filtered artifacts can still omit unrecorded ancestry from that
series' denominator. Exports faithfully retain the displayed values and flag
this limitation; they do not silently recalculate scientific results.

SVG figures contain embedded JSON metadata in `gwas-export-metadata` and a
visible dataset/settings/citation footer. PNG figures have the same visible
footer; their machine-readable metadata is in the ZIP sidecars. Export layouts
may reflow labels for publication. Map pan/zoom is retained within its clipped
viewport. The bubble SVG embeds its canvas points as raster artwork: it is **not
an entirely vector figure**.

Formula-like text cells are prefixed with an apostrophe to prevent spreadsheet
formula execution. Numeric columns, including negative numbers, stay numeric.
When doing exact textual comparisons with source data, account for this safety
prefix. CSV creation yields every 500 rows and reports progress. Bundles are
limited to 128 MiB; narrow the selection if that limit is reached. ZIP entries
are stored without compression to avoid loading a heavy compression library or
blocking the initial dashboard load.

## Source archives and reports are broader

The export dialog also offers **Download selection source data** for a selected
funder/cohort set. This source ZIP includes all publication years, all traits,
and both stages for that entity selection. It does **not** apply individual
chart controls. Its `selection.json`, `provenance.json`, `README.txt`, and
`CITATION.txt` document that scope; the underlying study and ancestry source
files are unchanged. Unlike plotted CSVs, original source TSV/CSV text is not
formula-neutralised: import text fields as text in spreadsheet software and do
not execute formula-like values. Source archives are cached by dataset identity
and entity selection, not chart settings.

Entity reports likewise summarize the full entity selection. They show the
dataset identity and citations, and may include the requested dashboard settings
as clearly labelled **context only**. Those settings do not restrict the report's
calculations. Use the chart bundles for exact plotted values. Neither runtime
export path rewrites generated scientific artifacts or release manifests.

## Shared provider contract for accessible data views

`window.gwasChartData` is the small initial registry; the ZIP writer loads only
when requested. Each chart registers a lazy provider:

```javascript
gwasChartData.register('heatMap', () => ({
    title: 'Heat map',
    columns: [{key: 'count', label: 'Count', type: 'number'}],
    rowCount: capturedRows.length,
    rowAt: index => capturedRows[index],
    settings: {year: currentYear, stage: currentStage},
    methodology: ['Explain the actual denominator and exclusions.']
}));
```

Call `snapshot(id)` only when `canExport()` is true. A snapshot adds frozen
`view`, `provenance`, `shareUrl`, `exportedAt`, `revision`, and `signature`
metadata. Providers capture their existing arrays at that point; large bubble
data are not eagerly copied. A paginated table can call `rowAt(index)` for just
its visible page, use `columns` for headers, and present `methodology` and
`settings` with it. Use `assertCurrent(snapshot)` before further pages or async
work, and listen for `gwas:chartdatachanged`, `gwas:viewchange`, and
`gwas:datasetstale` to invalidate stale displays. Render text with `textContent`.

The five IDs are `bubbleGraph`, `timeSeries`, `heatMap`, `worldMap`, and
`doughnutGraph`. `comparison` exists only after a successful comparison and is
invalidated when comparison controls change. `changed(id)` signals local chart
redraws. `setSelectionReady(true)` commits successfully loaded entity data;
metric/stage-only redraws use `setSelectionReady(true, false)` so retained data
after a failed request cannot adopt a different entity identity.

Image code captures a snapshot before lazy dependencies load, calls
`prepareImage(id)` for any canvas preparation, verifies the snapshot before
cloning/rasterization, and passes the resulting Blob to
`gwasExports.downloadSnapshots([snapshot], {image: {name, blob}})`.

## Tests

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/*.test.cjs
```

Focused tests cover snapshot laziness, stale/loading/failed-selection guards,
async image cancellation, CSV encoding and formula safety, chunking and limits,
ZIP checksums with an independent Python reader, source-cache identities,
report scope, and bounded/escaped view context.
