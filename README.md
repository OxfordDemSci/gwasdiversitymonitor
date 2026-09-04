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

- Daily ingestion of the GWAS Catalog export, with validated and
  atomic publication of each generated release.
- A reproducible static figure, source-data table, audit metadata, and a
  manuscript-ready caption under [`static_figure/`](static_figure/).

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

Run the full test suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

The suite tests both analytical meaning and operational integrity: ancestry
metadata, cohort and funder filtering, artifact validation, atomic publication
and recovery, failure notifications, optional analytics, and robots policy.

## Static publication figure

The reproducible figure workflow is contained in
[`static_figure/gwas_growth_diversity_figure.ipynb`](static_figure/gwas_growth_diversity_figure.ipynb).
Running the notebook produces manuscript-ready PDF, SVG, and PNG artwork plus:

- `gwas_growth_diversity_source_data.csv` — plotted source values;
- `gwas_growth_diversity_figure_metadata.json` — audit metadata; and
- `gwas_growth_diversity_caption.txt` — suggested manuscript caption.

Execute it with:

```bash
jupyter nbconvert \
  --to notebook \
  --execute \
  --inplace \
  static_figure/gwas_growth_diversity_figure.ipynb
```

The notebook uses the version-controlled Monitor palette and published local
data snapshot.

## Repository structure

```text
.
├── app/                    Flask application, templates, D3 code, and loaders
├── data/                   Generated runtime release and maintained mappings
├── deploy/                 Docker, nginx, Gunicorn, cron, mail, and analytics
├── docs/                   Methodology and operational documentation
├── static_figure/          Reproducible manuscript figure and source data
├── tests/                  Unit and integration tests
├── generate_data.py        Transactional Catalog data-generation pipeline
├── funder_pipeline.py      PubMed funder normalisation and report generation
├── gwasdiversitymonitor.py Production application launcher
├── docker-compose.yml      Four-service deployment definition
└── requirements.txt        Python runtime dependencies
```

See [Operations and deployment](docs/OPERATIONS.md) for production procedures.

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
