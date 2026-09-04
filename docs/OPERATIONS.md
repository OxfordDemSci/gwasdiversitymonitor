# Operations and deployment

This guide defines the operating contract for the Monitor: keep the public
application available, publish data only as complete validated releases, and
make failures observable without weakening those guarantees.

## System architecture

The Docker deployment contains four services:

| Service | Container | Responsibility |
|---|---|---|
| `nginx` | `gwas_nginx` | Public HTTP endpoint, static files, and reverse proxy |
| `flask` | `gwas_flask` | Flask application served by Gunicorn |
| `data` | `gwas_data` | Catalog ingestion and transactional artifact generation |
| `goatcounter` | `gwas_goatcounter` | Optional self-hosted aggregate analytics |

The repository is mounted into nginx and Flask, while the active `data/`
directory is mounted at `/app/data` for Flask. The data service is the sole
release writer; application processes are validated readers. GoatCounter data
live separately in the named volume `gwas_goatcounter_data`.

## Initial deployment

Create an ignored environment file from the template:

```bash
cp .env.example .env
chmod 600 .env
```

For local or staging use, leave `GWAS_DEPLOYMENT_DOMAIN` empty. If the host is
publicly reachable but non-canonical, set:

```dotenv
GWAS_NOINDEX=1
```

Build and start the services:

```bash
docker compose up -d --build
docker compose ps
```

Follow startup and generation logs:

```bash
docker compose logs -f flask data nginx
```

Do not mark a development machine as `gwasdiversitymonitor.com`. That exact
marker enables canonical-production behaviour, including failure-notification
attempts and forced search indexing.

## Routine application deployment

Deploy only a clean, tested revision. After updating it, rebuild the affected
images:

```bash
docker compose up -d --build
docker compose ps
```

The data image contains copies of `generate_data.py`, `funder_pipeline.py`,
selected `app/` modules, maintained cleaner files, and `data_static.zip`.
Changes to any of these inputs require rebuilding the data image. Rebuilding
only Flask and nginx does not update data-generation code.

## Data refresh

Run a one-off refresh with:

```bash
docker compose up -d --build --force-recreate data
```

The supplied cron definition runs this command daily from
`/opt/gwasdiversitymonitor`:

```text
deploy/gwasdiversitymonitor_crontab
```

Install it only after adjusting the path and service user for the target host.
The legacy `deploy/deploy.sh` bootstraps an Ubuntu host but should be reviewed
before use rather than treated as an unattended, idempotent installer.

### Rebuild funder products from the existing PubMed cache

When Catalog data are already current and only funder-generation logic or
normalisation has changed:

```bash
docker compose build data
docker compose run --rm data \
  python3 funder_pipeline.py --skip-fetch --repository /app
docker compose restart flask
```

This avoids refetching PubMed records while rebuilding validated funder
artifacts.

## Health and integrity checks

```bash
docker compose ps
docker compose logs --tail=200 flask data nginx
```

The Flask launcher verifies all application-consumed files against
`data/.generation_complete.json`. A startup message stating that the complete
published manifest is missing or invalid is an integrity failure, not a prompt
to disable validation. Run the generator and inspect its logs.

From the host:

```bash
curl --fail --silent --show-error http://localhost/ >/dev/null
curl --fail --silent --show-error http://localhost/api/traits?search=height
```

For a staging deployment with `GWAS_NOINDEX=1`, confirm both HTML metadata and
the response header:

```bash
curl --include --silent http://localhost/privacy-policy
```

The response should contain `X-Robots-Tag: noindex, nofollow, noarchive`.

## Configuration reference

### Application server

| Variable | Default | Notes |
|---|---:|---|
| `GWAS_HOST` | `0.0.0.0` | Used by `gwasdiversitymonitor.py` |
| `GWAS_PORT` | `8000` | Integer |
| `GWAS_WORKERS` | `2` | Integer |
| `GWAS_THREADS` | `4` | Integer |
| `GWAS_TIMEOUT` | `120` | Seconds; integer |

### Deployment behaviour

| Variable | Default | Notes |
|---|---|---|
| `GWAS_DEPLOYMENT_DOMAIN` | empty | Set to exactly `gwasdiversitymonitor.com` only on canonical production |
| `GWAS_NOINDEX` | empty | Truthy values enable noindex outside canonical production |
| `GOATCOUNTER_URL` | production URL in Compose | Set empty to disable analytics injection |
| `GWAS_DASHBOARD_FILTER_CACHE` | system temporary directory | Optional filtered-dashboard runtime cache location |

### Failure notifications

| Variable | Default | Notes |
|---|---|---|
| `GWAS_FAILURE_EMAIL_FROM` | `alerts@mail.gwasdiversitymonitor.com` | Envelope/message sender |
| `GWAS_FAILURE_EMAIL_TO` | `contact@gwasdiversitymonitor.com` | Comma-separated recipients |
| `GWAS_FAILURE_EMAIL_COOLDOWN_SECONDS` | `21600` | Suppression window for identical consecutive failures |
| `GWAS_LOCAL_MAIL_HOST` | `127.0.0.1` | Non-loopback values are rejected |
| `GWAS_LOCAL_MAIL_PORT` | `25` | Local relay port |
| `GWAS_LOCAL_MAIL_TIMEOUT_SECONDS` | `15` | Relay timeout |

## Failure notifications

Only canonical production sends failure email. Generation failures and
interruptions are submitted without credentials to a loopback-only mail relay;
failed alert delivery never masks the generator's non-zero status.

Install and constrain the local Postfix relay with:

```bash
sudo ./deploy/configure_failure_mail.sh
```

Production email delivery also requires correct external infrastructure:

- a stable public IP;
- forward DNS for the mail hostname;
- matching reverse DNS;
- authorization in the domain's SPF policy; and
- removal of any provider restriction on outbound port 25.

No inbound SMTP firewall rule is required for outbound-only alerts. Never
publish multiple SPF records for one hostname; update the existing policy.

Identical consecutive failures are suppressed for six hours by default. A
different error is sent immediately, and a successful generation clears the
failure state. See `app/logging/diversity_logger.log` when alert delivery or
generation fails.

## Recovery behaviour

Do not delete `data/.generate_data/` during an incident. It may contain the
state and previous release required for automatic recovery.

On the next `generate_data.py` run, the pipeline attempts recovery in this
order:

1. Complete or roll forward an interrupted publication.
2. Publish a complete retained staging release, if valid.
3. Reuse a validated raw snapshot and resume wrangling.
4. Start a fresh download only when retained state is unusable.

During an interrupted publication, application readers are directed to the
complete previous release. Investigate logs and manifest state before manually
moving files.

## GoatCounter analytics

The application can inject a self-hosted GoatCounter script when
`GOATCOUNTER_URL` is non-empty. Analytics are optional and do not affect
dashboard data generation.

Back up the persistent volume with:

```bash
./deploy/backup_goatcounter.sh
```

The script stops GoatCounter briefly, archives the complete SQLite volume, and
restarts the service. A custom backup directory can be supplied as its first
argument. Full setup, TLS, backup, upgrade, and rollback procedures are in
[`deploy/GOATCOUNTER.md`](../deploy/GOATCOUNTER.md).

## Rollback principles

Application code and generated data have different rollback mechanisms:

- **Application code:** deploy a previously tested Git revision and rebuild
  the affected images.
- **In-progress data publication:** allow the pipeline's publication marker and
  previous-release fallback to recover automatically.
- **GoatCounter:** restore a consistent volume archive while the service is
  stopped, following `deploy/GOATCOUNTER.md`.

Never repair a release by copying individual generated files. Their provenance
and cross-file consistency matter as much as their presence; the manifest
therefore treats the release as a coherent unit and rejects partial replacement.

## Security and privacy notes

- The Monitor serves aggregate study metadata and participant counts, not
  individual genetic data.
- `.env`, logs, and host-specific credentials or keys must remain untracked.
- The failure-mail path accepts only a loopback relay.
- Non-production public deployments should use `GWAS_NOINDEX=1`.
- GoatCounter is self-hosted and optional; its persistent data should be backed
  up and access-controlled like any operational database.
