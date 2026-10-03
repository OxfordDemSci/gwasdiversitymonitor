# Data pipeline and methodology

This document defines the Monitor's measurement model as well as its data
engineering. It explains what each quantity represents, how source records
become analytical products, and which claims the resulting data can—and
cannot—support.

## Comparing funders and cohorts

The dashboard's **Compare** dialog is loaded on demand. Each side can contain
several funders and cohorts, or remain empty to include all published GWAS.
Entities within a facet are combined by union; the funder and cohort facets
are intersected. Each matching study accession contributes once per side,
even when several selected entities link to it. The shared stage and optional
inclusive publication years apply to both sides. The date is the Catalog study
publication date; undated records are excluded only when a year limit is set.

Participant ancestry shares divide participant instances with a given ancestry
by all participant instances with recorded ancestry in that side and stage.
Blank ancestry and `In Part Not Recorded` are excluded from this denominator
and remain visible in coverage totals. Participant instances are not unique
people. Distinct-study ancestry shares instead divide unique accessions for an
ancestry by unique accessions with any recorded ancestry. A study may occur in
several ancestry groups, so these shares can sum to more than 100%. This unit
differs from the ancestry-record counts used by the existing dashboard charts;
the comparison describes that distinction beside its results.

The comparison reports the exact intersection of study accessions and of
publication IDs across its two sides. It cannot estimate shared unique people.
Full-counted entity totals must not be added together. Cohort coverage uses
study accessions, funding-metadata coverage uses publications, and ancestry
coverage is reported for participant instances and distinct studies. Missing
funding metadata does not imply an absence of funding. A zero denominator is
shown as unavailable, rather than as a measured zero percentage.

`POST /api/comparison` accepts JSON with `left` and `right` objects containing
`funders` and `cohorts` arrays, plus shared `stage` (`initial` or `replication`),
`metric` (`participants` or `studies`), and optional integer `fromYear`/`toYear`.
Year bounds are 1900–2100. Requests are limited to 16 KiB and 20 identifiers per
facet per side. Unknown identifiers return 404, invalid settings 400, and
oversized requests 413. The endpoint holds the published-data read lock,
aggregates a narrow ancestry index without association/bubble data, and returns
`Cache-Control: no-store`. Comparison settings are independent of the main
dashboard's chart-specific years and traits.

The optional `datasetId` field binds a comparison to the dataset loaded by the
page. A mismatch returns 409 with `code: "dataset_changed"`; successful responses
include the same ID in their JSON and `X-GWAS-Dataset-ID` header.

## Data sources

### NHGRI–EBI GWAS Catalog

The principal source is the
[NHGRI–EBI GWAS Catalog](https://www.ebi.ac.uk/gwas/). `generate_data.py`
retrieves the Catalog download bundle and requires four raw tables:

| Local artifact | Principal contents used by the Monitor |
|---|---|
| `catalog/raw/Cat_Anc.tsv` | Study accession, publication, date, stage, participant count, ancestry, and country of recruitment |
| `catalog/raw/Cat_Full.tsv` | Association-level information, including p-values |
| `catalog/raw/Cat_Map.tsv` | Disease-trait to Experimental Factor Ontology parent mappings |
| `catalog/raw/Cat_Stud.tsv` | Study metadata, cohorts, technology, association counts, and summary-statistics availability |

The loader accepts a limited set of known header aliases, including historical
variants of PubMed and study-accession columns, but validation fails if required
semantic fields are unavailable.

### PubMed funding metadata

`funder_pipeline.py` retrieves GrantList records through the NCBI Entrez EFetch
API and caches them in `data/funders/pubmed_grants.json`. The version-controlled
`data/funders/funder_cleaner.json` map resolves known aliases to canonical
funder names.

The collector only publishes a cache batch when PubMed returns every requested
PMID. This distinguishes an article that was returned without a GrantList from
an article omitted by an incomplete response. Rate-limit and transient server
responses are retried with bounded backoff (respecting `Retry-After`), while
permanent client errors fail immediately with the HTTP status and affected
batch. Cache validation requires one valid returned record for every PMID in
the current Catalog snapshot.

`data/funders/normalization-audit.json` reports retrieval and mapping coverage,
including publications with and without GrantLists, grant records without an
agency, reviewed exclusions, alias merges, and source names that do not yet
have an explicit alias. Unaliased non-empty agencies remain available under
their cleaned source name rather than being silently discarded.

The generated funder dashboard applies the pipeline's minimum-publication
threshold (50 by default) to individually exposed funder reports. Smaller
funders may be grouped for those generated products. Analyses should state the
funder universe and threshold they use.

## Core analytical units

The unit of analysis determines the scientific question. The Monitor therefore
keeps the following units explicit rather than presenting them as
interchangeable measures of “GWAS diversity”:

- **Publication:** a unique PubMed record.
- **GWAS Catalog accession:** a Catalog study accession. A publication can have
  multiple accessions.
- **Association:** an association reported by the Catalog.
- **Participant instance:** a participant count attached to an ancestry record
  for an accession and stage. These are not deduplicated individuals.
- **Cohort link:** a named cohort associated with an accession.
- **Funder link:** a canonical funding agency associated with a publication's
  PubMed GrantList.

Publication counts describe the structure of the literature; accession and
association counts describe its study and result structure; participant
instances weight that record by reported sample size. Participant totals can
count the same person more than once across studies, accessions, stages, or
overlapping cohorts. None is a census of unique research participants.

## Ancestry classification

Catalog ancestry descriptions are harmonised to the broad categories used by
the Monitor. The version-controlled classification support is seeded from
`data_static.zip` and published under `data/support/`.

Previously unseen combinations composed entirely of known terms can be
classified into an existing broad category. The classifier is constrained to
the existing `Broader` values; it does not invent new categories. A description
containing an unknown component remains unclassified and is written to
`data/unmapped/unmapped_broader.txt` for manual review.

This conservative behaviour makes uncertainty visible: an unresolved value is
retained for review rather than silently converted into a confident but
unsupported ancestry assignment. The broad categories are analytical
harmonisations of Catalog terminology, not biological essences or self-evident
population boundaries.

## Cohort and funder attribution

### Cohorts

The Catalog `COHORT` field can contain multiple pipe-delimited identifiers.
The pipeline normalises explicit aliases using
`data/support/cohort_cleaner.json`, removes non-informative sentinel values,
and retains the many-to-many relationship between accessions and cohorts.

### Funders

Funder names are derived from PubMed GrantList agencies and normalised through
`data/funders/funder_cleaner.json`. A publication may retain several canonical
funders.

Both dimensions use **full counting**. If one publication is linked to three
funders, it contributes one publication credit to each; the same principle
applies to cohorts. This preserves collaboration rather than assigning an
arbitrary fractional share, but entity totals are consequently non-additive.

Cohort-linked ancestry results describe the subset of published GWAS connected
to that cohort. Selection into analysis, study design, and Catalog coverage all
intervene between a source cohort and this published subset; the result is not
an estimate of the cohort's complete demographic composition.

## Processing stages

`generate_data.py` performs the following sequence:

1. Acquire a process-level generation lock.
2. Recover an interrupted publication, if one is recorded.
3. Download or reuse a validated raw Catalog snapshot.
4. Compare input, code, configuration, and support-data fingerprints with the
   published state.
5. Harmonise the Catalog and build analytical, funder, filter, and download
   products in staging.
6. Validate the complete staged release and its fingerprints.
7. Publish it atomically and write `data/.generation_complete.json`.

The process returns one of three successful states internally:

- `published` — a new complete release was generated and promoted;
- `unchanged` — all inputs and published artifacts still match; or
- `resumed` — an interrupted staged or publication operation was completed.

## Transactional publication and recovery

### Upstream input safeguards

Validation runs in the data-generation job, before publication—not on page
loads, dropdown searches, or filter requests. The published payload format,
filter semantics, and application appearance are unchanged.

Catalog inputs are checked for ambiguous headers, missing required columns,
empty tables, malformed records, invalid identifiers/dates/stages, and invalid
supplied counts. Counts must be finite, nonnegative integers within the
supported range; they are never repaired by truncation or silently converted
to zero. Existing missing participant-count markers (blank, `NA`, `N/A`, `NR`)
remain supported and their number is logged. Additional optional columns and
new funder/cohort names remain allowed. Conflicting duplicate study accessions
fail validation; identical duplicate rows are reported without rewriting them.

Downloads use temporary files and reject empty/truncated HTTP bodies, ambiguous
ZIP contents, and oversized downloads or expanded TSVs. The default per-file
limit is 8 GiB; set `GWAS_CATALOG_MAX_DOWNLOAD_BYTES` to a larger positive integer
when a genuine future release requires it. PubMed responses must contain valid,
uniquely identified article/book records. A missing GrantList remains valid;
an incomplete or malformed record must not become a confirmed absence of funding.
Unreadable or unsupported cache formats are not silently overwritten.

Generated JSON is checked for duplicate keys, non-finite numbers, required chart
structures, and valid dictionary-encoded row references. Legitimate empty stages
and existing missing-value markers remain supported. Both validation modules are
included in generation fingerprints, so a previous staging checkpoint cannot
bypass updated checks.

A rejected update leaves the last published release available. Review the
generator's error and the staged input before retrying; do not delete live data
to make a failed update pass. These checks add background generation work, not
request-time validation or extra browser downloads.

### Publication boundary

Generation takes place under `data/.generate_data/workspace/`; incomplete work
is never presented as the active release. Before promotion, required files are
checked for existence, structure, size, and SHA-256 fingerprint.

Application readers acquire a shared lock through
`app.DataLoader.published_data_lock()`. Publication acquires an exclusive lock.
If a process is interrupted during release replacement, a persistent marker
directs readers to `data/.generate_data/previous-release/`. The next generation
run completes recovery before starting new work.

The application launcher also calls `runtime_release_ready()` and refuses to
start if any application-consumed artifact is missing or fails its published
manifest check.

## Generated artifact groups

The active release is organised as follows:

| Directory | Purpose |
|---|---|
| `catalog/raw/` | Validated Catalog inputs |
| `catalog/synthetic/` | Harmonised intermediate tables |
| `summary/` | Headline statistics and filter vocabularies |
| `toplot/` | CSV and JSON payloads consumed by Flask and D3 |
| `todownload/` | Public download archives |
| `funders/` | PubMed cache, canonical index, reports, and downloads |
| `support/` | Ancestry, country, and cohort mappings |
| `unmapped/` | Values requiring review |

`data/toplot/dashboard_filters.zip` contains its own manifest and precomputed
discovery and replication filter options. `app/DashboardFilters.py` resolves
filtered payloads and downloads.

## Reproducibility and provenance

Reproducibility here means identifying both the upstream snapshot and the
transformation that produced a release. The completion manifest records
artifact fingerprints and the inputs needed to decide whether regeneration is
necessary. A change to `generate_data.py`,
`funder_pipeline.py`, relevant application dependencies, normalisation maps, or
the static support bundle invalidates the corresponding generation state.

The dashboard's **Data details** panel separates three facts:

- **Catalog release date** comes only from agreeing `rYYYY-MM-DD` release markers
  in the downloaded study, ancestry, and association filenames (including the
  validated TSV member of a ZIP). This follows the
  [Catalog's filename convention](https://www.ebi.ac.uk/gwas/docs/file-downloads/).
  HTTP Last-Modified, local file modification times, and download times are not
  substituted for a release date. Missing or conflicting evidence remains
  “Not recorded”.
- **Last successful update check** is when all four raw downloads completed and
  passed validation. It can advance even when the data are unchanged, or when
  subsequent transformation fails. A successful resumed run does not imply a
  new upstream check.
- **Dashboard dataset version** is `gwas-` followed by a SHA-256 digest of the
  manifest's artifact and source fingerprints, implementation fingerprints,
  generation parameters, and static-bundle fingerprints. Canonical JSON makes
  key order irrelevant; completion timestamps and acquisition metadata are
  excluded. The ID is computed for legacy manifests without modifying them.

The existing completion manifest now carries optional `provenance` metadata:
`version`, `datasetId`, `fetchCompletedAt`, and at most four `sources` entries.
Each entry records its local `path`, requested `url`, response `filename`,
optional `archiveMember`, UTC `fetchedAt`, verified `releaseDate`, `etag`, and
`lastModified`. Source metadata are copied from the retained raw snapshot, so
recovery preserves the original acquisition evidence. Older manifests have
unknown acquisition dates; `completed_at` identifies generation completion only.
This metadata is additive to manifest schema version 3.

Operational state is separate, in
`data/.generate_data/runtime-status.json`. Atomic writes record
`lastRunStartedAt`, `lastRunFinishedAt`, `lastRunStatus`, `lastRunOutcome`,
`lastSuccessfulRunAt`, `lastSuccessfulFetchAt`, `lastPublicationAt`, and
`lastPublicationDatasetId`. Outcomes remain `unchanged`, `published`, or
`resumed`. Only the bounded exception class enters `lastErrorType`; diagnostic
messages remain in logs. Records are updated under the generation lock, so a
rejected overlapping invocation cannot overwrite the active run. Status-write
failures are logged without changing the publication result or original error.

An unchanged run leaves the scientific files and release ID unchanged. A resumed
publication advances successful-run status and records the actual publication
completion, but does not invent a fresh fetch. During recovery, page metadata
comes from the same previous-release snapshot as the plotted data, while job
status comes from the live control directory. Verified filter/comparison source
dependencies are retained alongside plot files using hard links. If an older
fallback lacks those dependencies, optional selection views return a temporary
503 rather than mixing in newer source data.

`GET /api/provenance` returns these normalized fields with `Cache-Control:
no-store`. `?coverage=1` adds funder/cohort coverage from the existing precomputed
or cached facet overview; it is requested only when Data details first opens.
These percentages describe all study accessions, and missing metadata do not
imply an absence of funding or cohorts. No facet/source scan is added to the
initial dashboard request. Manifest metadata are bounded and cached by file
identity and modification metadata, without hashing large data files on requests.

The page freezes its served provenance in `window.gwasProvenance.loaded`;
`capture()` returns a copy and `isCurrent()` reports whether it remains safe to
share/export. Plot and selected-data requests carry `datasetId` and verify the
response's `X-GWAS-Dataset-ID` before drawing or caching data. A changed dataset
or an unbound successful response shows a reload action and blocks sharing and
exports. Comparison requests obey the same contract. Shared view links omit
`datasetId`: reloading selects the current published release and binds all that
page's subsequent requests to it. This prevents parallel plot requests from
silently combining releases during publication.

The static figure additionally exports:

- a long-form source-data CSV;
- audit metadata containing the snapshot date, counting rules, entity counts,
  concentration statistics, and palette; and
- a generated caption recording the principal methodological caveats.

## Interpretation checklist

When reporting results from the Monitor:

1. Name the GWAS Catalog snapshot date.
2. State whether counts refer to publications, accessions, associations, or
   participant instances.
3. State whether discovery, replication, or combined stages are used.
4. Treat participant counts as instances rather than unique people.
5. Describe funder and cohort attribution as full counting.
6. Avoid interpreting cohort-linked GWAS composition as cohort demographics.
7. Expect historical values to change when the Catalog is retrospectively
   curated.

## Data rights

GWAS Catalog data remain subject to the
[EMBL–EBI Terms of Use](https://www.ebi.ac.uk/about/terms-of-use/). The Monitor
processes aggregate study metadata and participant counts; it does not ingest
individual-level genotype or phenotype records.
