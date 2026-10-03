# Dashboard loading, help and accessible data

The dashboard keeps its compact chart layout. A collapsed **Explore an example · chart tips** section loads examples only on first opening. It requests one bounded page (up to 50 entries) for each entity type and offers recorded, non-empty examples from that response. Selecting an example replaces the funder/cohort selection and preserves the other chart settings. Examples are not hardcoded promises about future datasets.

## Loading and retry

All seven plot downloads still begin while the deferred chart scripts load. Responses are parsed through `dashboard-loading.js`; the large bubble response is consumed after the first paint. Initial requests, explicit retries and the XMLHttpRequest compatibility path all verify `X-GWAS-Dataset-ID` before parsing data. Missing or mismatched headers on a dataset-bound page, or HTTP 409, require a reload rather than an unverified fallback.

Each panel has a compact loading/error status and a Retry chart action. Successful dependencies are reused: a heat-map retry does not download its already-loaded ancestry order again. Chart controls are disabled while their panel loads; global metric/stage controls and entity filters wait for a complete baseline. A render error does not count as baseline readiness.

A failed entity selection retains the prior charts and says so beside **Retry selection**. Sharing, figure/data exports and selection reports remain unavailable until the controls match successfully loaded data. Changing metric or stage on the prior plots does not make a failed entity selection valid. Retries use the current selected settings, and obsolete requests cannot replace a newer selection.

Entity and trait option APIs, including the comparison dialog, carry the same dataset binding as chart requests. Their response headers are verified before options enter the visible list or its cache.

## Chart tables and controls

Each chart's **View data** action provides a native table with named columns, chart settings, calculation notes, and at most 50 rows per page. Tables use the same frozen, lazy chart-data provider as publication exports, not a second calculation. Changing the view invalidates an open table; **Refresh** explicitly loads the current chart snapshot. Keyboard users do not need to tab through thousands of marks to reach every value.

Chart controls have explicit names, keyboard focus styling and touch targets. One keyboard stop per chart supports arrow-key and Home/End navigation; tapping a mark exposes its value. The bubble canvas navigates only currently visible points. Dialogs support Escape and return focus to their opener. Table data remains the complete accessible alternative for every chart.

These changes do not alter scientific outputs, ancestry denominators, or publication-history semantics. The existing recorded/unrecorded ancestry limitations remain described in the exported methodology.
