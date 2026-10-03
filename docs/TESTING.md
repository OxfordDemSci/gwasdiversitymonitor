# Reproducible tests and browser performance

Tests do not need a downloaded GWAS Catalog, a local `config.py`, or a running
dashboard. Use a development virtual environment and Node.js 24:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/browser/run_unit_tests.py
node --test tests/js/*.test.cjs
.venv/bin/python tests/browser/run_browser_tests.py
```

On a fresh Linux host, Playwright may need operating-system libraries:
`python -m playwright install --with-deps chromium`. The Playwright Python
version is pinned in `requirements-dev.txt`; it selects the matching Chromium
revision. The application dependency ranges remain in `requirements.txt` and
are not a full transitive lockfile.

## Isolation and fixture scope

Each runner creates a new temporary workspace outside the checkout. The fixture
contains 72 synthetic accessions, 36 publications, six recorded ancestry groups,
two funders, two overlapping cohorts, two traits/parent terms, two years, and
both study stages. Real aggregation helpers build its chart JSON. Every runtime
file has a genuine content fingerprint in a complete generation manifest, which
the production startup validator checks.

The browser runner starts the real Gunicorn launcher on an ephemeral loopback
port with that workspace as its working directory. It injects test-only
configuration before importing the app, while serving the checkout's real
templates and static assets. Browser requests to external origins are blocked.
No Catalog/PubMed downloads, deployment credentials, real dataset reads/writes,
or changes to an existing server are needed. Only the owned server process group
is stopped afterwards. Generated files and caches are removed with the temporary
workspace; requested test evidence is kept separately.

Legacy downloadable aggregate CSVs in this fixture are minimal valid placeholders
to exercise startup manifest checks; the displayed charts use the real helper-
generated JSON. This fixture tests application contracts, not the full scientific
generation pipeline or real-Catalog distributions. Existing unit tests cover
those calculations separately. The unit runner also supplies the one relative
CSS input used by a legacy test, without symlinking the checkout into the fixture.

## Browser checks and evidence

The Chromium runner verifies all five charts, initial lazy loading, entity search
and intersection, share-state refresh and browser history, comparison, exact ZIP
row counts and dataset binding, SVG/PNG figure bundles, paginated data tables,
keyboard opening/Escape/focus return, on-demand metadata/examples, mobile layout,
transient chart/selection retries, mixed-release rejection, delayed selection
responses, and cancellation when settings change during lazy image export. JavaScript unit
tests additionally exercise asynchronous export cancellation, stale response
revisions, malformed state, and table laziness on a 200,001-row provider.

Use an explicit evidence directory if useful:

```bash
.venv/bin/python tests/browser/run_browser_tests.py --artifacts /tmp/gwas-browser-evidence
```

It contains metrics/check results, the real server log, downloaded test bundles,
a mobile screenshot, and Playwright traces for the main, fault and race scenarios.
Failures also save a screenshot.
Open the trace with `python -m playwright show-trace /tmp/gwas-browser-evidence/trace.zip`.
CI uploads this evidence even after failure, retaining it for seven days. Tests
never silently convert a missing browser or failed assertion into a pass.

## Performance budgets are regression guards

`tests/browser/budgets.json` records explicit compressed-byte, request-count, and
readiness limits. Cold Chromium requests real gzip responses; recorded resource
sizes come from the browser's `encodedBodySize`, and decoded sizes are retained
for diagnosis. The small deterministic fixture lets CI detect growing static
payloads, duplicate requests, eager export libraries, eager entity/comparison
requests, and broken readiness without downloading production data.

These are **not production load-time claims**. The 20-second readiness ceiling is
a deliberately generous shared-runner regression/hang guard, not a target user
experience. A fixture cannot measure a 200,000-row Catalog, server cold-cache
costs, or real users' hardware. Review and justify budget changes rather than
automatically raising thresholds to make a failing check pass.

For a separately running real-data dashboard, measure explicitly:

```bash
.venv/bin/python scripts/performance/browser_metrics.py \
  --url http://127.0.0.1:8011/ --runs 3 --width 1440 --cpu 4 --mbps 10 \
  --output /tmp/gwas-real-data-performance.json
```

This command only navigates the supplied URL; it does not start, restart, mutate,
or deploy that server. It creates a new browser context for each run, records the
served dataset identity, plotted bubble count, viewport, CPU/network emulation,
resource sizes, long tasks, LCP observations and chart readiness. Server cache
warmth and host load still vary. Browser version, all runs, median readiness and
nearest-rank p95 are included (with very few runs, p95 is just the slowest run).
Report the run conditions and all runs; do not
compare fixture timings to real-data timings as if they were equivalent.

The pinned-action GitHub workflow supports pull requests, dev/main/master pushes,
manual runs, and `workflow_call` so a release build can require these checks first.
It also builds the Flask, data-job and nginx Dockerfiles without pushing images.
It has read-only repository permissions and performs no deployment.
