# Versioned releases, rollback and monitoring

This is an **opt-in operating path**, not a migration that runs when code is
pushed. The existing Compose deployment remains supported. A push to `dev`
does not merge `main`, modify Lightsail, deploy either website, create GitHub
environments/secrets, or turn on alerts.

## What is versioned

`Build and promote an immutable release` is manually dispatched. It tests the
selected commit, builds Flask/data/nginx once, and publishes a `release.json`
artifact containing the full commit and three `@sha256:` image references.
The SHA tag is convenient for humans; deployment uses the digest, not that tag.
Image revision labels must match the manifest. Third-party GoatCounter is
separately pinned to its existing verified digest in host configuration.

With deployment unchecked, this only builds/publishes images. With staging
checked, the same images go to the configured `staging` host. Production also
requires staging success, the `main` ref, and an explicitly approved protected
`production` environment. The workflow refuses production unless the environment
has named required reviewers, prevents self-review, and disables administrator
bypass. These protections need to be created by an authorized repository owner;
the code does not invent an approver or weaken that gate. Protect `main` and
review changes to workflows and the host controller as security-sensitive code.

The running full commit is exposed in health/provenance responses and the page
footer. Unversioned local builds say so rather than claiming a clean commit.
Application version is not the scientific dataset identity.

## One-time setup, separately on each Lightsail host

Do this in an approved maintenance window, first on dev. Keep the current VM
snapshot and a verified backup of `data/`, the **current runtime**
`data_static.zip`, logs and the analytics volume. Record the existing Compose
configuration and image IDs for a first-deployment rollback. Do not delete,
regenerate or overwrite working data as an installation shortcut.

1. Verify Docker Compose V2 supports `--wait`, `--wait-timeout` and profiles.
   Review/install `deploy/release.py` and `deploy/compose.release.yml` as
   root-owned, non-group/world-writable files under `/opt/gwas-release/`.
   The `/opt/gwas-release/` directory itself must also be root-owned and not
   writable by the deploy account or other users.
   Create root-owned mode0700 `/var/lib/gwas-release/`.
2. Ensure the current published data includes the funder/cohort inputs required
   by this version. Run the normal generator under supervision if a legacy
   release lacks them. The preflight refuses incomplete data; it does not
   silently remove the requirement. Check the last completed job and logs.
3. Create a dedicated persistent runtime directory, e.g.
   `/var/lib/gwas-runtime/`. Copy the current `data_static.zip` into it and
   verify its SHA256 against `static_bundle_fingerprint.sha256` in the data
   completion manifest. **Mount the directory, never just the ZIP file**:
   the generator replaces the bundle atomically. Preserve this directory in
   backups with the data directory.
4. Save mode0600 `/var/lib/gwas-release/site.json` with the following shape,
   using verified host paths and the existing analytics image's repository
   digest (`docker image inspect` → `RepoDigests`). This is JSON, never sourced
   as shell code. All three directories must already exist.

   ```json
   {
     "domain": "dev.gwasdiversitymonitor.com",
     "dataDirectory": "/opt/gwasdiversitymonitor/data",
     "runtimeDirectory": "/var/lib/gwas-runtime",
     "logDirectory": "/opt/gwasdiversitymonitor/app/logging",
     "goatcounterImage": "arp242/goatcounter@sha256:REPLACE_WITH_VERIFIED_DIGEST"
   }
   ```

   Production uses `gwasdiversitymonitor.com` and its actual data/log paths
   (the migrated host previously used `/home/ubuntu/gwasdiversitymonitor/`).
   The strict parser rejects the example placeholder. Dev always enables
   `noindex`; canonical production does not.
5. Confirm external Docker volume `gwas_goatcounter_data` exists and contains
   the intended analytics data. For a site without analytics, deliberately
   provision an empty volume or adapt the reviewed configuration; do not point
   at an unrelated volume. nginx needs the GoatCounter service for DNS.
6. Authenticate the host to GHCR with a read-only package token if packages
   are private. Do not put registry tokens in Git, `site.json` or release
   manifests. Do not make the Docker socket world-writable.
7. Configure GitHub `staging` and protected `production` environments with
   separate `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` and
   `DEPLOY_KNOWN_HOSTS` secrets. Verify SSH host fingerprints out of band;
   deployment refuses unknown/changed hosts. Restrict the deploy account's
   sudo access to the reviewed root-owned controller; it must not be able to
   edit that controller, Compose file or state directory. Docker access is
   root-equivalent. The controller additionally checks the expected domain to
   catch swapped staging/production host secrets.
8. Check the CDN settings below. Disable the **old data cron**, wait for any
   active generation to complete, and make the first managed deployment.
   Review output and public smoke checks before proceeding to production.
   A failed first managed deployment has no `previous.json`: use the recorded
   legacy configuration/images, not an invented fallback.
9. Only after success, install `deploy/gwas-release-cron` under `/etc/cron.d/`
   with root ownership/mode0644. It runs the approved data image attached,
   records failures in its exit status, and shares a nonblocking lock with
   deployment. It does not rebuild or pull a moving branch nightly. The
   configured midnight is host time; use UTC deliberately. Add log rotation
   for `/var/log/gwas-data-cron.log` and application logs.

Maintained funder/cohort cleaners come from `/app/release-inputs` in the data
image, are fingerprinted as generation inputs, and are copied only into the
staged generation. A bind-mounted live `data/` cannot accidentally hide the
new image's configuration. Publishing still preserves a validated prior release
before replacing any live artifacts. No scientific aggregation formula changes
as part of this deployment mechanism.

## Rollout and rollback

The controller pulls and checks image labels, validates current data before
replacing the web containers, waits for health, checks the running commit, then
atomically records `current.json` and `previous.json`. A failed rollout attempts
the recorded previous images, reports failure, and leaves the current ledger
unchanged. Inspect the output if recovery also fails.
This is a **brief maintenance-window rollout**, not zero downtime: after
preflight it stops nginx, replaces/checks Flask, then recreates nginx (up to the
configured 180-second health wait per phase). This avoids serving old static
files under new HTML's immutable asset URLs while images are being swapped.
It also refreshes nginx's startup-resolved Docker upstream addresses. Plan
blue/green origins separately if uninterrupted rollout is required.
An interrupted/failed operation retains `pending.json` and blocks subsequent
data jobs/deployments, preventing the old generator from silently running under
new web images after a ledger-write failure. Inspect the candidate/previous
manifests, containers and health; reconcile `current.json` to the actually
healthy release, then archive/remove that one pending marker deliberately.
Do not clear it merely to suppress the error.

Public CDN smoke checks run after the host commits the release. Their failure
fails the workflow and requires investigation, but does **not** automatically
roll back an internally healthy candidate: network/cache propagation failures
are ambiguous. The candidate remains active unless an operator rolls it back.

On the intended host, an operator can explicitly roll back:

```bash
sudo /usr/bin/python3 /opt/gwas-release/release.py rollback
```

Rollback revalidates the existing dataset against the old application first.
It never deletes data. **Code rollback is not data rollback**: incompatible data
requires a separately approved, coordinated snapshot/backup restore. Keep image
digests and release artifacts available; do not prune the rollback images during
the observation period. Healthcheck failure by itself does not restart Docker
containers: `unless-stopped` handles process exit/reboot, not an unhealthy but
running process. Investigate alerts rather than scheduling blind reboots.

## Health, stale jobs and alert delivery

All probes send `Cache-Control: no-store`:

- `/health/live`: Flask is answering. No large data reads.
- `/health/ready`: consumed artifacts and filter inputs match their manifest.
  Hash results are cached by file stat identity; unchanged probes only stat
  files. A valid previous release during publication remains ready and is
  identified explicitly.
- `/health/data`: last successful run **and** upstream check within 36 hours,
  no failed last run, and no run stuck over 6 hours. Unknown/future timestamps
  fail visibly. `GWAS_DATA_MAX_AGE_HOURS` and `GWAS_DATA_MAX_RUN_HOURS` can
  change these thresholds in a reviewed Compose configuration. A successful
  `unchanged` run is healthy; publication date alone is not a job heartbeat.

Readiness is independent of data freshness: stale data alerts should not make
an otherwise valid website disappear. The standalone checker works from another
host without repository dependencies:

```bash
python3 deploy/monitor.py https://gwasdiversitymonitor.com
python3 deploy/monitor.py https://dev.gwasdiversitymonitor.com
```

Wire nonzero exits to your chosen paging/email service and verify delivery with
a controlled failed check. Run it externally, not solely on the monitored VM.
Use an external dead-man monitor for the check scheduler itself. No alert
recipient or third-party account is configured by these files.

The optional GitHub monitoring workflow is disabled until the repository
variable `GWAS_MONITOR_ENABLED=true` is set and Actions failure notifications
are configured/tested by the team. Schedules only activate on the default
branch, can be delayed, and are not an uptime SLA or a replacement for an
independent dead-man monitor. Pushing this work to `dev` does not activate them.

## Lightsail/CloudFront compatibility checklist

Keep `Distribution-gwas-dev` pointing at `gwas_dev` (eu-west-2), and
`Distribution-gwas-useast` pointing at `gwas-production-2604` (us-east-1).
No DNS, origin, static-IP or certificate change is needed for this code release.

- Permit JSON POST to `/api/comparison`. Lightsail's full allowed-methods
  setting includes GET/HEAD/OPTIONS/PUT/PATCH/POST/DELETE; application routes
  still reject unsupported methods. Review this explicitly rather than changing
  it via an unattended deployment.
- Forward query strings and include them in cache keys: `datasetId`, `funders`,
  `cohorts`, `search`, `page`, `v`, `format` and all view settings. Otherwise
  distinct selections can receive the wrong cached response.
- Set minimum cache TTL 0 where application cache headers control caching.
  Never cache `/health/*`, `/api/provenance`, POST responses or 409 errors;
  preserve `X-GWAS-Dataset-ID`. Test missing headers, stale dataset reload,
  a real comparison POST and a filtered options query through the public CDN.
- Save the current distribution JSON before any configuration change. CLI
  cache-setting objects can replace other fields; use reviewed read/modify
  updates, not a partial object that silently discards existing settings.

See the official [Lightsail update reference](https://docs.aws.amazon.com/cli/latest/reference/lightsail/update-distribution.html)
and [GitHub deployment protection documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).
