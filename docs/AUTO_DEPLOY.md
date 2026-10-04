# Automatic deployments: setup and complete operating procedure

This guide describes work operators must perform. Adding the workflow does
**not** configure GitHub secrets, migrate a server, create backups or enable
automatic deployment. No AWS or GitHub setup is implied by this document.

| Git branch | GitHub environment | Lightsail instance | Instance region | Public site | Repository enable variable |
| --- | --- | --- | --- | --- | --- |
| `dev` | `staging` | `gwas_dev` | `eu-west-2` | `dev.gwasdiversitymonitor.com` | `GWAS_DEPLOY_DEV_ENABLED` |
| `main` | `production` | `gwas-production-2604` | `us-east-1` | `gwasdiversitymonitor.com` | `GWAS_DEPLOY_MAIN_ENABLED` |

The two branches deploy independently. A main push does not deploy main onto
the dev website, and a dev push does not merge or deploy main. Deployments
include a short web maintenance window; builds, queued jobs and data refreshes
mean that an update is neither instantaneous nor guaranteed to have zero downtime.

## Stop condition for the current main branch

At the time this guide was written, `origin/main` was
`66daffb6643e8026a8eb1d597b2ddf63fed581c8`. It has no release workflow, health or
version modules, runtime-ZIP directory support, data-job heartbeat, or compatible
release image definitions. The dev workflow and Dockerfiles also reference
application and test files absent from main.

**Leave `GWAS_DEPLOY_MAIN_ENABLED=false`. A workflow-only copy to main will not
work.** Production needs a separately reviewed operations backport based on main,
with compatible health checks, images, runtime persistence, tests and controller
configuration. Do not merge dev into main to satisfy this prerequisite. Complete
and verify that independent work before applying the production steps below.

## 1. Configure GitHub without activating deployments

Use an account authorized to administer this repository. From a checkout:

```bash
gh auth status
gh variable set GWAS_DEPLOY_DEV_ENABLED --repo OxfordDemSci/gwasdiversitymonitor --body false
gh variable set GWAS_DEPLOY_MAIN_ENABLED --repo OxfordDemSci/gwasdiversitymonitor --body false
```

In repository **Settings → Environments**, create or review `staging` and
`production`. Set deployment branches and tags to **Selected branches and tags**:
allow branch `dev` only for staging and branch `main` only for production; do not
add tag rules or a wildcard. Keep production changes behind reviewed main pull
requests and appropriate branch protection. Configure separate environment
secrets, never shared production credentials at repository scope.

Existing required reviewers and wait timers still apply. The workflow does not
remove them: a protected job waits even when its enable variable is true. For
unattended deployment after a reviewed main merge, an authorized administrator
must deliberately choose environment settings that do not require another
deployment approval. Branch review and deployment approval are separate choices.

Push the reviewed automation changes to dev while its enable variable is false.
The push workflow tests and publishes images and a `release-<full-SHA>` artifact,
but does not deploy. Do not rely on **Run workflow** for initial setup: GitHub
requires the workflow on the default branch for `workflow_dispatch`, and the
current main branch does not have it.

Review and commit only the deployment change set, then push that reviewed dev
commit. Do not include unrelated pending work or merge main. Retain the full dev
SHA for the bootstrap below.

## 2. Verify the host and create a separate deployment identity

Run AWS discovery on the operator's authenticated workstation. For dev:

```bash
aws sts get-caller-identity
aws lightsail get-instance --region eu-west-2 --instance-name gwas_dev \
  --query 'instance.{name:name,state:state.name,publicIp:publicIpAddress,privateIp:privateIpAddress}'
aws lightsail get-distributions --region us-east-1 \
  --query 'distributions[].{name:name,origin:origin,domains:alternativeDomainNames}'
```

For production, only after the main prerequisite is resolved, discover
`gwas-production-2604` with `--region us-east-1`. Do not guess an IP from an old
document. Confirm `Distribution-gwas-dev` targets `gwas_dev` in `eu-west-2`, and
`Distribution-gwas-useast` targets `gwas-production-2604` in `us-east-1`. These
releases need no DNS, origin, static-IP or certificate changes.

Create one dedicated key per environment, with a new filename:

```bash
ssh-keygen -t ed25519 -N '' -C gwas-staging-actions -f "$HOME/.ssh/gwas-staging-actions"
ssh-keygen -t ed25519 -N '' -C gwas-production-actions -f "$HOME/.ssh/gwas-production-actions"
```

Do not overwrite an existing key. Using the existing administrator access on
each verified host, create a separate `gwasdeploy` account if absent. Install only
that host's public key in its mode0600 `authorized_keys`, under a mode0700 `.ssh`
directory owned by that account. Prefix the key with
`no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty`. The account
does not need Docker-group membership or permission to modify controller files.

On the verified host, using your existing administrator session:

```bash
getent passwd gwasdeploy || sudo adduser --disabled-password --gecos '' gwasdeploy
sudo install -d -o gwasdeploy -g gwasdeploy -m 0700 /home/gwasdeploy/.ssh
sudoedit /home/gwasdeploy/.ssh/authorized_keys
sudo chown gwasdeploy:gwasdeploy /home/gwasdeploy/.ssh/authorized_keys
sudo chmod 0600 /home/gwasdeploy/.ssh/authorized_keys
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

In the editor, add the restricted **public** key as one line; preserve existing
keys. Never paste the private key there. Ensure your Actions runner can reach SSH
port 22 under the existing firewall policy. Do not silently widen the firewall;
use reviewed runner/network access if it is restricted.

Obtain the host's SSH fingerprint through a trusted existing connection or the
Lightsail console. Collect its public host-key entry into a workstation file,
then compare fingerprints before trusting it:

```bash
# Set DEPLOY_IP to the verified address returned above.
ssh-keyscan -t ed25519 "$DEPLOY_IP" > /tmp/gwas-staging-known-hosts
ssh-keygen -lf /tmp/gwas-staging-known-hosts
```

`ssh-keyscan` alone does not authenticate the host. After matching the trusted
fingerprint, configure staging; use a different IP, private key and known-hosts
file when configuring production:

```bash
gh secret set DEPLOY_HOST --repo OxfordDemSci/gwasdiversitymonitor --env staging --body "$DEPLOY_IP"
gh secret set DEPLOY_USER --repo OxfordDemSci/gwasdiversitymonitor --env staging --body gwasdeploy
gh secret set DEPLOY_SSH_KEY --repo OxfordDemSci/gwasdiversitymonitor --env staging < "$HOME/.ssh/gwas-staging-actions"
gh secret set DEPLOY_KNOWN_HOSTS --repo OxfordDemSci/gwasdiversitymonitor --env staging < /tmp/gwas-staging-known-hosts
```

The four secret names are identical in production, with `--env production` and
the production values. Keep private keys and registry credentials out of Git,
shell transcripts and `site.json`.

## 3. Inventory, pause collection and preserve recovery material

On the intended host, inspect the existing installation before installing files:

```bash
hostname
sudo docker compose version
sudo docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
sudo docker inspect gwas_flask --format '{{json .Config.Labels}}'
sudo docker inspect gwas_flask --format '{{json .Mounts}}'
sudo docker inspect gwas_data --format '{{json .Mounts}}'
sudo crontab -l
sudo crontab -u ubuntu -l
sudo grep -R -nE 'generate_data|gwas|docker.*compose' /etc/cron.d /etc/crontab
systemctl list-timers --all
```

A missing crontab is normal. Record the Compose project name, working directory,
**every** configuration file from its labels, actual data/log paths, environment
files, service image IDs and restart policies. Inspect only the selected runtime
environment variables when checking ZIP location; avoid printing secret values.
Check available disk space for images, staged data and backups.

If `/var/lib/gwas-release/current.json` already exists, this is an existing
managed deployment: use its controller and recovery procedure. If
`pending.json` exists, stop and reconcile the recorded candidate, previous release
and running containers before any new operation. Never delete that marker merely
to bypass its protection.

Disable the identified legacy data scheduler and save its original definition;
do not stop unrelated cron jobs. Record the old restart policy, then disable an
automatic failure loop before waiting for an active job:

```bash
sudo docker update --restart=no gwas_data
sudo docker inspect gwas_data --format '{{.State.Status}} {{.State.ExitCode}}'
# If it is running, wait for this attempt to finish, then inspect its logs/status.
sudo docker wait gwas_data
sudo docker logs --tail 200 gwas_data
```

`docker wait` prints the container's exit code; a successful invocation of
`docker wait` does not itself mean generation succeeded. Inspect other one-off
containers/processes too. Do not remove or recreate a running generator.

Create a mode0700 backup directory outside the checkout, for example a unique
directory below `/var/backups/gwas/`. With generation stopped, save:

- The **entire** data directory, including `.generate_data/`, ownership and modes.
- Logs, the current code revision, all Compose/environment files and resolved
  Compose configuration. Treat resolved configuration as private.
- All old service image IDs under retained local tags; optionally export them
  with `docker image save` to survive image loss.
- The current runtime ZIP. In the legacy configuration it can exist only inside
  the stopped data container: `docker cp gwas_data:/app/data_static.zip ...`.
  If `GWAS_RUNTIME_DIRECTORY` was configured, use its actual mounted location.
- A consistent archive of the existing `gwas_goatcounter_data` volume, if used.
  Stop its writer while archiving, then restart it; see
  [analytics backup instructions](../deploy/GOATCOUNTER.md).

Create the backup directory and discover the existing data mount. Check these
values against the inventory. Keep this administrator shell open; subsequent
host commands use its variables.

```bash
BACKUP="/var/backups/gwas/dev-before-managed-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o root -g root -m 0700 "$BACKUP"
DATA_DIR=$(sudo docker inspect gwas_flask --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}')
test -n "$DATA_DIR" && sudo test -d "$DATA_DIR"
printf 'Data directory: %s\nBackup directory: %s\n' "$DATA_DIR" "$BACKUP"
sudo tar --acls --xattrs --numeric-owner -cpf "$BACKUP/data.tar" -C "$DATA_DIR" .
sudo tar -tf "$BACKUP/data.tar" > /dev/null
GWAS_OLD_RUNTIME=$(sudo docker inspect gwas_data --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^GWAS_RUNTIME_DIRECTORY=//p')
GWAS_OLD_RUNTIME=${GWAS_OLD_RUNTIME:-/app}
sudo docker cp "gwas_data:$GWAS_OLD_RUNTIME/data_static.zip" "$BACKUP/data_static.runtime.zip"
sudo sha256sum "$BACKUP/data_static.runtime.zip"
```

Compare that ZIP's size and SHA256 with `static_bundle_fingerprint` in the data
completion manifest. Do not assume the checkout's tracked ZIP is the runtime
version. Save the matching pair together; investigate a mismatch before rollout.

```bash
sudo python3 - "$DATA_DIR" "$BACKUP/data_static.runtime.zip" <<'PY'
import hashlib, json, pathlib, sys
data = pathlib.Path(sys.argv[1])
bundle = pathlib.Path(sys.argv[2])
expected = json.loads((data / '.generation_complete.json').read_text())['static_bundle_fingerprint']
assert bundle.stat().st_size == expected['size'], 'Runtime ZIP size mismatch'
digest = hashlib.sha256()
with bundle.open('rb') as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
assert digest.hexdigest() == expected['sha256'], 'Runtime ZIP hash mismatch'
print('Data manifest and runtime ZIP: matched')
PY
```

Also create a Lightsail snapshot from the workstation while writers are quiet;
keep analytics stopped until the snapshot is available:

```bash
SNAPSHOT="gwas-dev-before-managed-$(date -u +%Y%m%dT%H%M%SZ)"
aws lightsail create-instance-snapshot --region eu-west-2 \
  --instance-name gwas_dev --instance-snapshot-name "$SNAPSHOT"
aws lightsail get-instance-snapshot --region eu-west-2 \
  --instance-snapshot-name "$SNAPSHOT" --query 'instanceSnapshot.state'
```

Wait until the snapshot is `available`. Use the production instance and region
only for its separate migration. Do not treat an accepted snapshot request as a
completed backup. Retain the legacy recovery files through the observation period.

## 4. Install the reviewed controller and persistent runtime

Obtain `deploy/release.py` and `deploy/compose.release.yml` from the exact reviewed
commit being bootstrapped. Review them before installing; the controller operates
Docker as root. On the workstation, set `TARGET` to that full dev SHA and
`DEPLOY_IP` to the verified dev IP. Using your administrator SSH identity
(add `-i /path/to/your/admin-key` to SSH/SCP if necessary):

```bash
SOURCE_UPLOAD=$(mktemp -d)
git show "$TARGET:deploy/release.py" > "$SOURCE_UPLOAD/release.py"
git show "$TARGET:deploy/compose.release.yml" > "$SOURCE_UPLOAD/compose.release.yml"
git show "$TARGET:deploy/gwas-release-cron" > "$SOURCE_UPLOAD/gwas-release-cron"
ssh "ubuntu@$DEPLOY_IP" 'install -d -m 0700 /home/ubuntu/gwas-release-upload'
scp "$SOURCE_UPLOAD/release.py" "$SOURCE_UPLOAD/compose.release.yml" \
  "$SOURCE_UPLOAD/gwas-release-cron" "ubuntu@$DEPLOY_IP:/home/ubuntu/gwas-release-upload/"
```

On the host, inspect the uploaded files, then install them:

```bash
SOURCE_DIR=/home/ubuntu/gwas-release-upload
sudo install -d -o root -g root -m 0755 /opt/gwas-release
sudo install -d -o root -g root -m 0700 /var/lib/gwas-release
sudo install -d -o root -g root -m 0755 /var/lib/gwas-runtime
# SOURCE_DIR contains the reviewed deployment files copied to this host.
sudo install -o root -g root -m 0644 "$SOURCE_DIR/release.py" /opt/gwas-release/release.py
sudo install -o root -g root -m 0644 "$SOURCE_DIR/compose.release.yml" /opt/gwas-release/compose.release.yml
sudo install -o root -g root -m 0644 "$BACKUP/data_static.runtime.zip" /var/lib/gwas-runtime/data_static.zip
```

Confirm Compose v2 supports `--wait`, `--wait-timeout` and profiles. The runtime
**directory** is mounted; never replace it with a bind mount of the ZIP alone,
because generation replaces the ZIP atomically. Data, logs and runtime directories
must already exist and be backed up together.

Inspect the actual GoatCounter image with `docker image inspect` and use its
verified repository digest, not its image ID or a guessed tag. The supplied
managed Compose needs the external volume `gwas_goatcounter_data` and the service
for nginx upstream resolution. If analytics did not previously exist, resolve
that host-specific configuration explicitly before proceeding.

Create root-owned mode0600 `/var/lib/gwas-release/site.json`, substituting the
verified host paths and digest:

```json
{
  "domain": "dev.gwasdiversitymonitor.com",
  "dataDirectory": "/ACTUAL/CHECKOUT/data",
  "runtimeDirectory": "/var/lib/gwas-runtime",
  "logDirectory": "/ACTUAL/CHECKOUT/app/logging",
  "goatcounterImage": "arp242/goatcounter@sha256:VERIFIED_64_CHARACTER_DIGEST"
}
```

The placeholders intentionally fail validation. Production's domain is exactly
`gwasdiversitymonitor.com`. The controller enforces dev noindex and checks the
expected deployment domain to catch swapped host credentials.

Using `sudo visudo -f /etc/sudoers.d/gwas-release`, grant the deployment account
only the reviewed controller entry point:

```text
gwasdeploy ALL=(root) NOPASSWD: /usr/bin/python3 /opt/gwas-release/release.py *
```

Keep this sudoers file root-owned mode0440. The account must not be able to edit
the controller, Compose file, state directory or their parent directories.
Do not grant it arbitrary `python3`, a root shell or Docker commands through sudo.

For private GHCR packages, provision root's Docker credentials with a token that
has package-read access. Enter it interactively or through an approved secret
manager, not as a command argument:

```bash
sudo docker --host unix:///var/run/docker.sock --config /root/.docker login ghcr.io --username YOUR_GITHUB_USER
```

The controller explicitly uses root's Docker configuration; logging in only as
`ubuntu` does not configure it. Public packages may not require this login.

## 5. Bootstrap a release, run data collection and verify the complete stack

On the workstation, locate the successful disabled-deployment push run:

```bash
gh run list --repo OxfordDemSci/gwasdiversitymonitor --branch dev --event push \
  --json databaseId,headSha,workflowName,conclusion --limit 10
```

Select the successful **Build and deploy a branch release** run and record its
numeric `RUN_ID` and complete `TARGET` SHA. Download its artifact into a new
temporary directory:

```bash
BOOTSTRAP=$(mktemp -d)
gh run download "$RUN_ID" --repo OxfordDemSci/gwasdiversitymonitor \
  --name "release-$TARGET" --dir "$BOOTSTRAP"
python3 deploy/release.py validate "$BOOTSTRAP/release.json"
```

Verify `release.json` names that exact commit and three digest-pinned project
images. Copy it to a private temporary directory on the verified host using the
trusted administrator SSH connection. Do not manufacture a manifest from moving
image tags.

```bash
scp "$BOOTSTRAP/release.json" "ubuntu@$DEPLOY_IP:/home/ubuntu/gwas-release-upload/release.json"
```

Before the first managed rollout, the candidate application must be able to read
the existing manifest and all required artifacts. Legacy datasets missing dev's
funder/cohort products require a supervised normal generation with the candidate
image and persistent runtime directory before rollout. The controller intentionally
does not bypass readiness or generate missing products. For this **first dev
migration only**, after completed backups, an inactive legacy scheduler and a
finished old data job, stop web traffic during generation:

```bash
sudo docker stop gwas_nginx gwas_flask
```

This administrator-only command uses the installed, reviewed controller to pull
and verify the candidate, run its normal generator with persistent mounts, and
validate output under the shared lock. It does not record an active web release
or bypass recovery. Run on dev, **not** on legacy production.

```bash
sudo /usr/bin/python3 - /home/ubuntu/gwas-release-upload/release.json <<'PY'
import sys
sys.path.insert(0, '/opt/gwas-release')
import release

site = release.read_json(release.STATE / 'site.json')
assert site['domain'] == 'dev.gwasdiversitymonitor.com', 'Wrong host'
candidate = release.validate_release(release.read_json(sys.argv[1]))
with release.deployment_lock(3600):
    if (release.STATE / 'pending.json').exists():
        raise SystemExit('STOP: pending release requires recovery')
    release.verify_images(candidate, site)
    release.compose(candidate, site, 'run', '--rm', '--no-deps', 'data')
    release.check_data(candidate, site)
print('Candidate generation and data preflight: passed')
PY
```

Stop on any error and inspect generation logs. Do not proceed to web rollout
with failed generation. Keep automation disabled until recovery is complete.

The controller uses Compose project `gwasdiversitymonitor`. Compare this with
the recorded legacy labels. If the old fixed container names belong to a
different project, the new project cannot adopt them. After all backups and
preflight checks, stop and remove only the recorded legacy service containers
in the maintenance window; preserve images, bind-mounted data and named volumes.
Never use `down --volumes`. Reconcile the saved legacy configuration for rollback.
Even when project names match, keep the legacy data container stopped with
automatic restart disabled so it cannot become a second writer.

Run the first deployment as the administrator, using the actual uploaded path:

```bash
sudo /usr/bin/python3 /opt/gwas-release/release.py deploy /home/ubuntu/gwas-release-upload/release.json \
  --expected-domain dev.gwasdiversitymonitor.com --lock-wait-seconds 3600
```

This pulls and checks image revisions, preflights data/runtime integrity, stops
nginx, replaces and checks Flask, then recreates nginx so its upstream addresses
are current. Successful health checks precede writing `current.json`. The first
managed deployment has no recorded managed predecessor; a failure requires the
saved legacy recovery procedure, not an invented `previous.json`.

After a successful rollout, verify collection through the **installed runtime
path**, attached, under the same controller lock (an unchanged result is expected
if bootstrap just generated everything):

```bash
sudo /usr/bin/python3 /opt/gwas-release/release.py data --lock-wait-seconds 3600
```

Wait for exit zero and inspect generation logs. The data service has no automatic
failure-restart loop. An unchanged run is valid; a resumed run does not invent a
fresh upstream fetch, so a second normal run may be needed to establish freshness.
Do not delete retained staging or publication-recovery state to force success.

Check `/health/live`, `/health/ready` and `/health/data` through localhost and the
public site. Each must return HTTP 200, `Cache-Control: no-store`, the expected
commit and its corresponding true status. Readiness should no longer indicate
`servingPreviousRelease` after collection completes. From the reviewed checkout:

```bash
python3 deploy/monitor.py https://dev.gwasdiversitymonitor.com --expected-commit "$TARGET"
curl --fail --silent --show-error http://127.0.0.1/health/ready
curl --fail --silent --show-error http://127.0.0.1/health/data
curl --silent --show-error --include https://dev.gwasdiversitymonitor.com/privacy-policy
```

Run localhost commands on the server. Confirm the dev response includes
`X-Robots-Tag: noindex, nofollow, noarchive`; also open the dashboard, change
filters, compare two selections, export data and inspect provenance. Verify that
JSON POST requests and selection query parameters survive the public CDN.

Review CDN rules in [Releases](RELEASES.md#lightsailcloudfront-compatibility-checklist).
Minimum cache TTL must permit application cache headers; health/provenance and
comparison responses must not be cached. New HTML revalidates, but previously
cached HTML may require one reviewed initial distribution-cache reset after
settings are corrected. Reset only the verified site's distribution. The pipeline
does not reset CDN caches on every push and does not require AWS credentials in
GitHub Actions for that purpose.

## 6. Install one scheduler and activate automatic dev deployment

Only after rollout, generation and public checks succeed, install the reviewed
`deploy/gwas-release-cron` as root-owned mode0644
`/etc/cron.d/gwas-release-cron`. Verify the legacy scheduler remains disabled.
The managed cron invokes the active data image with a 3600-second lock wait; it
does not fetch a branch or rebuild nightly. Check the host timezone, configure
log rotation and confirm `/var/log/gwas-data-cron.log` records the next run.

On the dev host:

```bash
sudo install -o root -g root -m 0644 /home/ubuntu/gwas-release-upload/gwas-release-cron /etc/cron.d/gwas-release-cron
sudo systemctl enable --now cron
sudo systemctl restart cron
systemctl is-active cron
timedatectl show --property=Timezone --value
sudo cat /etc/cron.d/gwas-release-cron
```

It runs at midnight in that timezone. Keep exactly one data scheduler enabled.

Activate dev from the workstation:

```bash
gh variable set GWAS_DEPLOY_DEV_ENABLED --repo OxfordDemSci/gwasdiversitymonitor --body true
```

Changing the variable does not deploy an earlier commit. Push the next reviewed
dev commit, watch its workflow and repeat the public expected-commit check. If no
code change is pending, an operator may deliberately create an empty commit on
dev to exercise the push trigger. Manual dispatch with input `deploy=true` is an
alternative only after the workflow is registered on the default branch.

Keep production's variable false until its separate main-compatible backport,
host migration, bootstrap and public checks have succeeded. Then configure its
own environment and set `GWAS_DEPLOY_MAIN_ENABLED=true`; the next reviewed main
push deploys main's images to production only.

## Routine updates, data changes and reboot

Each enabled branch push tests that commit, builds and publishes Flask/data/nginx
images, records their digests, deploys to its own environment and checks the public
site for the expected SHA. Environment protections can still delay deployment.
Active deployments are not cancelled by a newer push. Host operations wait up to
the configured lock timeout rather than interrupting a running data job.

Push deployment does not replace the root-owned controller or managed Compose
file. Changes to those files require a separate review and administrator install
on each host before relying on their new behavior. Preserve their previous copies
as part of rollback preparation.

**A deployment updates application images; it does not automatically regenerate
data.** The nightly job uses the image in `current.json`. For generator or mapping
changes that must take effect immediately, supervise `release.py data` after the
successful deployment, then repeat data-health, readiness and scientific smoke
checks. If the new app requires artifacts absent from the old dataset, preflight
will refuse deployment: arrange a tested, backed-up data migration first. Do not
describe that incompatible transition as an unattended push deployment.

To recreate the managed web stack with the active release after an operational
repair, deploy its saved `current.json` with the expected domain; do not mix in
checkout Compose commands. Restarting Flask alone does not refresh nginx's
startup-resolved upstream address. The controller performs the ordered rollout.

After an authorized host reboot, Docker's `unless-stopped` policy restarts the
web services; health failure alone is not a restart policy. Confirm Docker/cron
are active, no pending marker exists, the public SHA matches `current.json`, and
all three health checks pass. Do not enable the old checkout scheduler again.

Optional one-time reboot verification, **on dev**:

```bash
sudo systemctl enable docker cron
sudo reboot
```

Reconnect to **gwas_dev**, then:

```bash
systemctl is-active docker containerd cron
sudo docker inspect gwas_flask gwas_nginx --format '{{.Name}} status={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}}'
curl --fail --silent --show-error http://127.0.0.1/health/ready
curl --fail --silent --show-error http://127.0.0.1/health/data
```

Repeat the public monitor from the workstation. Routine pushes do not require
rebooting Ubuntu or restarting the Docker daemon.

## Failure handling and rollback

Turn the relevant enable variable false to prevent future automatic deployments
while investigating; this does not undo or cancel an already running operation.
Check Actions logs, container logs, runtime status, `current.json`, `previous.json`
and `pending.json`. A failed public check can leave an internally healthy new
release active; it is not proof that the host automatically rolled back.

For an existing managed predecessor, with no unresolved pending marker:

```bash
sudo /usr/bin/python3 /opt/gwas-release/release.py rollback \
  --expected-domain dev.gwasdiversitymonitor.com --lock-wait-seconds 3600
```

Use the production domain only on production. Rollback verifies compatibility
with existing data and restores previous image digests; it does not restore data.
Failed rollout recovery deliberately retains `pending.json`: reconcile the actual
healthy release and ledger before archiving that marker and resuming operations.
Do not run rollback repeatedly against unresolved state.

If data are incompatible or the first managed rollout failed, pause collection
and use the coherent backups or VM snapshot. Preserve failed data for diagnosis;
restore the complete data directory **and its matching runtime ZIP**, plus the
appropriate code/static assets, images and configuration. Recreate containers
after replacing a host data directory: existing bind mounts retain its previous
directory inode even after `docker restart`. Never repair a release by restoring
individual generated JSON files. Verify locally and publicly before restoring
one appropriate scheduler and re-enabling pushes. Keep rollback images and
archives; do not prune them during the observation period.

See [versioned release recovery](RELEASES.md#rollout-and-rollback) and
[checkout operations](OPERATIONS.md#rollback-principles) for the underlying
contracts. GitHub/AWS activation and the production main backport remain explicit
operator work until separately completed and verified.
