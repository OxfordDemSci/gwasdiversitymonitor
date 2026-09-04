# Data pipeline and methodology

This document defines the Monitor's measurement model as well as its data
engineering. It explains what each quantity represents, how source records
become analytical products, and which claims the resulting data can—and
cannot—support.

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
