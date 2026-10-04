# GWAS Diversity Monitor

[![DOI](https://zenodo.org/badge/220447592.svg)](https://zenodo.org/badge/latestdoi/220447592)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-2E8B57.svg)](#license-and-data-use)

The **GWAS Diversity Monitor** is an open research observatory of whose data
underpin published genome-wide association studies (GWAS). It turns the
[NHGRI–EBI GWAS Catalog](https://www.ebi.ac.uk/gwas/) into an explorable account
of ancestral representation: how it has changed, where imbalances persist, and
which disease areas, cohorts, and funders shape the evidence base. The project
provides an interactive dashboard, downloadable research data, entity-linked
reports, and reproducible publication figures.

**Live dashboard:** [gwasdiversitymonitor.com](https://www.gwasdiversitymonitor.com/)

Maintained by the
[Leverhulme Centre for Demographic Science](https://www.demographicscience.ox.ac.uk/)
at the University of Oxford.

## Questions the Monitor supports

- How has ancestral representation in published GWAS changed over time?
- Where do discovery and replication samples differ?
- Which disease areas and recruitment countries contain the largest gaps?
- Which cohorts and funders are linked to the studies that broaden—or
  concentrate—the evidence base?
- How do conclusions change when the unit is a publication, accession,
  association, or participant instance?

Explore these questions interactively or reproduce them from downloadable
selections. The Monitor also provides:

- [Shareable dashboard views](docs/DASHBOARD_VIEWS.md) that retain selected
  filters and chart settings across refresh, bookmarks, and browser navigation.

- A compact **Compare** dialog for two independent funder/cohort selections,
  using the same stage and inclusive publication-year limits. It shows shared
  studies/publications, metadata coverage, absolute counts, and ancestry shares.
- [Publication-ready exports](docs/PUBLICATION_EXPORTS.md) with exact plotted
  CSV values, SVG/PNG figures, captured view settings, dataset provenance,
  methodology, and citations in one ZIP.
- Daily ingestion of the GWAS Catalog export, with validated and
  atomic publication of each generated release.

## Interpretive scope

The Monitor measures the **published evidence base**, not population diversity
or equality of scientific benefit.
Its participant counts are **participant instances**, not deduplicated people:
someone represented in several studies or accessions may contribute more than
once. Discovery and replication stages remain separate wherever the Catalog
provides that distinction.

Funder and cohort attribution uses **full counting**: a publication is credited
to every linked funder or cohort. Cohort-linked panels describe the ancestry
metadata of GWAS associated with a named cohort; they should not be interpreted
as direct estimates of that cohort's demographic composition.

Historical series are reconstructed from publication dates in the current
Catalog snapshot. They are therefore a view of today's curated record through
time—not frozen contemporaneous releases—and may change after retrospective
Catalog curation.
See [Data pipeline and methodology](docs/DATA_PIPELINE.md) for definitions,
provenance, and processing details.

## Quick start with Docker

Docker Compose is the most reproducible way to run the complete stack.

With Docker Engine, Compose v2, and network access to the GWAS Catalog and
PubMed, run from the repository root:

```bash
docker compose up -d --build
```

Open <http://localhost/>. Inspect services with:

```bash
docker compose ps
docker compose logs -f flask data nginx
```

The first run downloads and validates the Catalog snapshot, transforms it into
dashboard products, and retrieves uncached PubMed funding metadata; it can
therefore take substantially longer than application startup. Generated
runtime data are written beneath `data/`.

> **Search-engine safety:** set `GWAS_NOINDEX=1` in a local `.env` file for any
> publicly reachable development or staging deployment. The canonical
> production marker always takes precedence and remains indexable.

## Local Python setup

Use this workflow when developing the Flask application without Docker.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
test -f config.py || cp deploy/config.py config.py
python3 generate_data.py
python3 gwasdiversitymonitor.py
```

Open <http://localhost:8000/>. Before starting Gunicorn, the launcher verifies
the published data manifest; regenerate data if a required artifact is absent
or invalid rather than bypassing this check.

### Server configuration

| Variable | Default | Purpose |
|---|---:|---|
| `GWAS_HOST` | `0.0.0.0` | Gunicorn bind host |
| `GWAS_PORT` | `8000` | Gunicorn bind port |
| `GWAS_WORKERS` | `2` | Worker processes |
| `GWAS_THREADS` | `4` | Threads per worker |
| `GWAS_TIMEOUT` | `120` | Worker timeout in seconds |
| `GOATCOUNTER_URL` | empty locally | Base URL for optional analytics |
| `GWAS_NOINDEX` | unset | Set to `1` on public non-production deployments |

For Docker configuration, copy [`.env.example`](.env.example) to the ignored
`.env` file.

## Data generation

Run the complete data workflow from the repository root:

```bash
python3 generate_data.py
```

Generation treats the published dataset as one indivisible release. Work is
staged in `data/.generate_data/`; every required artifact is validated before
promotion. Readers hold a shared lock, publication takes an exclusive lock, and
an interruption leaves the previous complete release available while the next
run attempts recovery. A dashboard can therefore never knowingly mix files
from different generations.

An unchanged run is skipped only when raw-input fingerprints, generation
parameters, implementation fingerprints, the static bundle, and the published
artifact manifest all match.

For the artifact inventory, recovery, classification, normalisation, and filter
cache, read
[Data pipeline and methodology](docs/DATA_PIPELINE.md).

## Testing

Run the Python suite with isolated synthetic data and configuration, without
requiring a local Catalog download:

```bash
python3 -m pip install -r requirements-dev.txt
python3 tests/browser/run_unit_tests.py
```

The suite tests both analytical meaning and operational integrity: ancestry
metadata, cohort and funder filtering, artifact validation, atomic publication
and recovery, failure notifications, optional analytics, and robots policy.

## Repository structure

```text
.
├── app/                    Flask application, templates, D3 code, and loaders
├── data/                   Generated runtime release and maintained mappings
├── deploy/                 Docker, nginx, Gunicorn, cron, mail, and analytics
├── docs/                   Methodology and operational documentation
├── scripts/performance/    Browser measurement tooling
├── tests/                  Unit and integration tests
├── data_static.zip         Required bootstrap lookup and classification data
├── generate_data.py        Transactional Catalog data-generation pipeline
├── funder_pipeline.py      PubMed funder normalisation and report generation
├── gwasdiversitymonitor.py Production application launcher
├── docker-compose.yml      Four-service deployment definition
└── requirements.txt        Python runtime dependencies
```

See [Operations and deployment](docs/OPERATIONS.md) for production procedures.

### What belongs in Git

Keep the application, browser assets and Sass sources, data pipeline,
`data_static.zip`, maintained funder/cohort mappings, dependency files, tests,
CI workflows, and operating documentation together so a clean checkout can
reproduce and verify the app. Browser-generated publication exports remain an
application feature.

Generated datasets and caches, backups, private outreach (`emails/`), the
separate manuscript figure notebook and outputs (`static_figure/`), IDE files,
credentials, and local test evidence are ignored. Existing local copies can
remain on disk; they are not part of the app checkout. The Docker build context
also excludes private/local material, since `.gitignore` alone does not exclude
files from Docker builds. Use a Git-based deployment rather than copying the
entire workstation directory to the server.

Untracking files removes them from future commits, not from past Git history.
Do not rewrite shared history as routine cleanup.

## Citation

Please cite the Monitor and the accompanying software release:

> Mills, M. C. & Rahal, C. (2020). The GWAS Diversity Monitor tracks diversity
> by disease in real time. *Nature Genetics*, **52**, 242–243.
> <https://doi.org/10.1038/s41588-020-0580-y>

> Boef, N., Brunier, Q., Knowles, I., Malowany, A., May, J., Mills, M. C.,
> Misseri, L., Nixon, G., Ntova, V., Rahal, C. & Sinclair, C. (2020). Source
> code for the GWAS Diversity Monitor (Version 1.0.0). Zenodo.
> <https://doi.org/10.5281/zenodo.3600472>

The Monitor extends the earlier scientometric review:

> Mills, M. C. & Rahal, C. (2019). A scientometric review of genome-wide
> association studies. *Communications Biology*, **2**, 9.
> <https://doi.org/10.1038/s42003-018-0261-x>

## Contributing and support

See [browser/performance testing](docs/TESTING.md),
[accessible dashboard interactions](docs/DASHBOARD_ACCESSIBILITY.md), and
[versioned releases and monitoring](docs/RELEASES.md) for the tested development
and opt-in deployment workflows. Shipping code does not automatically deploy it.

Issues and pull requests are welcome through the
[GitHub repository](https://github.com/OxfordDemSci/gwasdiversitymonitor).
Changes to classifications, counting rules, or normalisation maps should be
treated as methodological changes: include tests, explain their analytical
effect, and regenerate affected artifacts. Do not commit secrets, `.env`,
runtime logs, or partial releases.

For project enquiries, contact `contact@gwasdiversitymonitor.com`.

## License and data use

The software is distributed under the MIT licence. Upstream GWAS Catalog data
remain subject to the
[EMBL–EBI Terms of Use](https://www.ebi.ac.uk/about/terms-of-use/). The Monitor
contains aggregate study metadata and participant counts; it does not process
or distribute individual-level genetic data.

## Acknowledgements

We gratefully acknowledge the NHGRI–EBI GWAS Catalog team and the contributors
who have supported the design and development of the Monitor, including the
Global Initiative team, Ian Knowles, Yi Liu, Jiani Yan, Molly Przeworski, Ben
Domingue, Sam Trejo, and the SOCIOGENOME group.
