# Self-hosted GoatCounter on AWS Lightsail

Last verified: 10 August 2026. The repository pins GoatCounter 2.7.0.

This runbook replaces Google Analytics with a GoatCounter instance owned and
operated by the GWAS Diversity Monitor project. The live website already uses
an AWS Lightsail content-delivery distribution (CloudFront) for HTTPS, so the
deployment uses the same AWS-managed pattern without exposing GoatCounter's
port 8080 to the internet.

## Resulting architecture

```text
www.gwasdiversitymonitor.com
  -> existing Lightsail distribution
  -> nginx on the Lightsail instance
  -> Flask

stats.gwasdiversitymonitor.com
  -> separate dynamic-content Lightsail distribution
  -> the same nginx container
  -> GoatCounter 2.7.0
  -> Docker volume gwas_goatcounter_data (SQLite)
```

The public site loads `https://stats.gwasdiversitymonitor.com/count.js` and
sends page counts to `https://stats.gwasdiversitymonitor.com/count`. Nothing is
loaded from Google or GoatCounter.com.

SQLite is intentional here. GoatCounter's own documentation recommends it for
smaller sites, and this dashboard has a small set of page paths. PostgreSQL
would add another database, credentials, memory use, and backup procedure
without a useful benefit at the expected traffic level.

## Important account distinction

Do **not** register at `goatcounter.com/signup` for this deployment. That form
creates an account on GoatCounter's hosted service. A self-hosted GoatCounter
account is created in the local SQLite database with the command in step 2.
The account email is an identifier; the password is stored only in the local
GoatCounter database.

You need:

- shell access to the existing Lightsail instance;
- permission to manage its Lightsail distributions and certificates;
- permission to edit DNS for `gwasdiversitymonitor.com`;
- an email address for the first GoatCounter administrator;
- Docker Engine and Docker Compose v2 (already used by this repository).

## 0. Publish the code and snapshot production

The production instance cannot pull these changes until they have been
committed, pushed, reviewed, and merged into `origin/main`. Complete that Git
workflow first.

The repository protects `main`, so a direct `git push origin main:main` is
expected to fail with `GH006`. Do not force-push or weaken the branch rule.
From the development checkout, preserve the local commit on a feature branch,
commit this runbook's final edits, and push that branch:

```bash
git status -sb
git switch -c agent/goatcounter-production-deployment
git add .gitignore README.md app deploy docker-compose.yml tests
git commit -m "Complete GoatCounter production deployment guide"
git push -u origin agent/goatcounter-production-deployment
```

The new branch starts at the current local `main`, so any commit that GitHub
rejected is retained. If `git commit` reports that there is nothing to commit,
continue with the push; all changes are already in the branch.

Open
[the GitHub comparison page](https://github.com/OxfordDemSci/gwasdiversitymonitor/compare/main...agent/goatcounter-production-deployment?expand=1),
create a pull request into `main`, and use **Replace Google Analytics with
self-hosted GoatCounter** as its title. Merge it only after the repository
checks pass and the review is complete. The production commands below must be
run after that merge, not merely after the feature branch is pushed.

Before changing the instance, open the AWS Lightsail console, select the GWAS
Diversity Monitor instance, open **Snapshots**, and create a manual snapshot.
Wait until it is available before continuing.

## 1. Prepare and start GoatCounter

Connect to the instance and update the checkout:

```bash
cd ~/gwasdiversitymonitor
git status --short
git fetch origin
git pull --ff-only origin main
docker compose config --quiet
docker compose pull goatcounter nginx
docker compose up -d goatcounter
docker compose ps goatcounter
docker compose logs --tail=100 goatcounter
```

Do not continue if `git status` shows production-only edits that would be
overwritten by the update. Preserve and reconcile them first.

Validate the updated nginx configuration while GoatCounter is available on the
Docker network:

```bash
docker compose exec nginx nginx -t
```

If the existing nginx container is not running, use:

```bash
docker compose run --rm --no-deps nginx nginx -t
```

Do not continue unless nginx reports that its syntax and configuration test
are successful. Then deploy Flask and nginx:

```bash
docker compose up -d --build flask nginx
docker compose ps
```

The Compose deployment does the following:

- runs the official `arp242/goatcounter:2.7.0` image;
- runs pending database migrations automatically at startup;
- persists the database in the named volume `gwas_goatcounter_data`;
- makes GoatCounter reachable only on the internal Docker network;
- tells Flask to use `https://stats.gwasdiversitymonitor.com` as its tracker
  base URL;
- routes the analytics hostname through nginx.

Check startup before creating the site:

```bash
docker compose logs --tail=100 goatcounter nginx
curl --fail --show-error \
  --header 'Host: stats.gwasdiversitymonitor.com' \
  http://127.0.0.1/status
```

The final command should succeed. It tests nginx and GoatCounter locally,
before DNS or a certificate is involved.

## 2. Create the self-hosted account and site

Run this once, replacing the email address:

```bash
docker compose exec goatcounter goatcounter db create site \
  -vhost=stats.gwasdiversitymonitor.com \
  -user.email=YOUR_ADMIN_EMAIL
```

GoatCounter prompts for a password without echoing it. Use a unique password
stored in the project's password manager. Do not add `-password=...` to the
command: doing so would put the password in shell history and possibly process
listings.

Create the account before publishing the analytics hostname. This avoids
leaving a fresh installation on the public internet without an administrator.

You can also verify the JavaScript through nginx:

```bash
curl --fail --show-error \
  --header 'Host: stats.gwasdiversitymonitor.com' \
  http://127.0.0.1/count.js >/dev/null
```

## 3. Create a separate Lightsail distribution

Use a separate distribution rather than adding the analytics hostname to the
website's existing distribution. GoatCounter needs POST requests, login
cookies, query strings, the original user agent, and no page caching. Keeping
those settings separate avoids changing the live dashboard's cache behavior.
At the time this runbook was verified, AWS listed the smallest 50 GB Lightsail
CDN distribution as free for the first eligible year and USD 2.50 per month
afterwards. Confirm the current price in the account before creating it.

In the AWS Lightsail console:

1. Open **Networking** and choose **Create distribution**.
2. Select the AWS Region containing the existing GWAS Diversity Monitor
   instance, then select that instance as the origin.
3. Use **HTTP only** between the distribution and the origin. Viewer HTTPS is
   terminated by the distribution.
4. Choose the **Best for dynamic content** caching preset.
5. Name the distribution clearly, for example `gwas-goatcounter`.
6. Create it and wait for its status to become **Enabled**.

The origin instance must have a static IPv4 address attached. The existing
website distribution normally means this is already true; verify it under the
instance's **Networking** tab. The Lightsail firewall needs inbound TCP port 80
for the distribution's connection to nginx. Do **not** open port 8080.

After creation, open the distribution's **Cache** tab and verify these settings:

| Setting | Required value |
| --- | --- |
| Default cache behavior | Cache nothing |
| Allowed HTTP methods | GET, HEAD, OPTIONS, PUT, PATCH, POST, DELETE |
| Forwarded headers | `Host`, `Origin`, `Referer`, `User-Agent`, `CloudFront-Forwarded-Proto` |
| Cookies | Forward all cookies |
| Query strings | Forward all query strings |
| Directory/file overrides | None |

Why each non-default matters:

- `Host` lets nginx select the `stats.gwasdiversitymonitor.com` virtual host.
- `User-Agent` prevents every visit appearing to come from `Amazon CloudFront`.
- `CloudFront-Forwarded-Proto` lets nginx tell GoatCounter that the viewer used
  HTTPS, so login cookies are marked correctly.
- `Origin` and `Referer` preserve cross-origin tracker and referrer behavior.
- cookies are required for administrator login; the public tracker itself is
  cookie-free.
- GoatCounter's count endpoint uses query strings and may use POST.

Keep **Cache nothing** even though `count.js` could technically be cached. It is
safer for the dashboard, login, status, API, and count endpoints, and the small
script does not justify a complex path override.

## 4. Add the certificate and DNS

On the new distribution's **Custom domains** tab:

1. Create a Lightsail certificate containing only
   `stats.gwasdiversitymonitor.com`.
2. Add the certificate-validation CNAME record shown by Lightsail to the DNS
   provider for `gwasdiversitymonitor.com`.
3. Wait for the certificate status to become **Valid**.
4. Attach the certificate to the new distribution.
5. Choose **Add domain assignment** for
   `stats.gwasdiversitymonitor.com` if DNS is managed in Lightsail.

If DNS is managed elsewhere, create this record there instead:

```text
stats.gwasdiversitymonitor.com  CNAME  DISTRIBUTION_ID.cloudfront.net
```

Use the exact default distribution hostname displayed in Lightsail; do not
literally use `DISTRIBUTION_ID.cloudfront.net`. Keep the separate certificate
validation CNAME in DNS so AWS can renew the certificate automatically.

Wait until the distribution is **Enabled**, the certificate is attached, and
DNS resolves before proceeding. DNS and distribution changes can take several
minutes to propagate.

## 5. First login and privacy settings

Open `https://stats.gwasdiversitymonitor.com` and sign in with the local email
and password created in step 2. Never sign in over the instance IP or plain
HTTP.

In GoatCounter settings:

1. Set the website/link domain to
   `https://www.gwasdiversitymonitor.com`.
2. Set the reporting timezone to `Europe/London`.
3. Keep the dashboard private unless public analytics are an explicit project
   decision.
4. Keep **individual pageview storage disabled**. The repository privacy notice
   describes GoatCounter's default aggregate mode.
5. Keep collection limited to the categories disclosed in the privacy notice:
   paths, page titles, referrers, approximate country, language, screen width,
   browser, and operating system.
6. Enable two-factor authentication for administrator accounts if offered by
   the installed release.
7. Add separate named administrators rather than sharing the first password.

Self-hosting does not configure outbound email automatically. Do not rely on
password-reset or email-report delivery until SMTP has been deliberately
configured and tested.

The privacy notice in this repository reflects aggregate mode, but it is still
appropriate to have the University/Oxford data-protection contact review it.
GoatCounter's GDPR discussion is guidance, not legal advice.

## 6. Verify the complete cutover

From any machine with DNS access:

```bash
curl --fail --show-error --head \
  https://stats.gwasdiversitymonitor.com/status
curl --fail --show-error --head \
  https://stats.gwasdiversitymonitor.com/count.js
curl --fail --show-error --silent \
  https://www.gwasdiversitymonitor.com/ \
  | rg 'data-goatcounter|googletagmanager|gtag\('
```

The final command should print the `data-goatcounter` script tag and nothing
from Google. In a browser with extensions temporarily disabled:

1. Open the public dashboard's developer tools and select **Network**.
2. Reload the page.
3. Confirm `count.js` returns 200 from the `stats` hostname.
4. Confirm a request to `/count` succeeds.
5. Sign in to GoatCounter and wait about 10 seconds for the visit to appear.
6. Visit `/privacy-policy` and verify that no cookie banner is present.
7. Confirm that the public site creates no analytics cookie or local-storage
   entry.

On the instance, also check:

```bash
cd ~/gwasdiversitymonitor
docker compose ps
docker compose logs --tail=100 goatcounter nginx flask
```

Clear the existing website distribution's cached HTML so that it cannot serve
the retired analytics markup: in Lightsail, open the existing website
distribution, choose **Cache**, select **Reset cache**, and confirm.

Finally, simulate a browser that still has the old consent cookie and ensure
Google's scripts remain absent:

```bash
curl --fail --show-error --silent \
  --header 'Cookie: cookie_consent=true' \
  https://www.gwasdiversitymonitor.com/ \
  | grep -E 'googletagmanager|gtag\('
```

The command should print nothing. A match means the cutover is not complete.

After the cutover is verified, export any Google Analytics history that must be
retained, then disable or delete the old GA data stream/property according to
the project's retention requirements. If a Google Tag Manager container was
used only for analytics, retire that as well. The deployed application no
longer loads either service.

## 7. Backups

The GoatCounter database is not in Git. It lives in the Docker volume
`gwas_goatcounter_data`, and losing that volume loses the account and history.

Create a consistent backup with:

```bash
cd ~/gwasdiversitymonitor
./deploy/backup_goatcounter.sh
```

The script briefly stops GoatCounter, archives the full volume, restarts it even
if archiving fails, and writes the result under `backups/goatcounter/`. Pass an
absolute directory as its first argument to store the archive elsewhere:

```bash
./deploy/backup_goatcounter.sh /mnt/backup/gwas-goatcounter
```

The first run downloads the small pinned `alpine:3.22` helper image. Copy each
archive off the instance—to an encrypted S3 bucket, institutional backup
storage, or another controlled location. A backup that remains only on the
Lightsail disk does not protect against instance or disk loss.

Recommended schedule:

- nightly volume archive, retained for at least 30 days;
- Lightsail instance snapshot before every GoatCounter upgrade;
- periodic restore test, not just a check that archive files exist.

To restore, first stop GoatCounter and preserve the current volume under a
different name. Restoration replaces the account and all analytics with the
backup's state, so do not extract an archive over the production volume without
a tested rollback copy. A safe rehearsal is:

```bash
docker volume create gwas_goatcounter_restore_test
docker run --rm \
  --volume gwas_goatcounter_restore_test:/restore \
  --volume /ABSOLUTE/PATH/TO/BACKUPS:/backup:ro \
  alpine:3.22 \
  tar -xzf /backup/GOATCOUNTER_BACKUP.tar.gz -C /restore
docker run --rm \
  --publish 127.0.0.1:18080:8080 \
  --volume gwas_goatcounter_restore_test:/home/goatcounter/goatcounter-data \
  arp242/goatcounter:2.7.0 serve -automigrate
```

In another shell, test the restored instance with:

```bash
curl --fail --header 'Host: stats.gwasdiversitymonitor.com' \
  http://127.0.0.1:18080/status
```

Stop the rehearsal with Ctrl-C and remove its test volume only after confirming
that the restore succeeded.

## 8. Upgrades and rollback

Do not use `latest` for GoatCounter. For an upgrade:

1. Read GoatCounter's release notes.
2. Run `./deploy/backup_goatcounter.sh` and copy the result off-instance.
3. Take a Lightsail snapshot.
4. Change the pinned image tag in `docker-compose.yml`.
5. Run:

   ```bash
   docker compose pull goatcounter
   docker compose up -d goatcounter nginx
   docker compose logs --tail=100 goatcounter
   curl --fail --header 'Host: stats.gwasdiversitymonitor.com' \
     http://127.0.0.1/status
   ```

The image starts with automatic migrations. If a migration makes the database
incompatible with the prior version, rolling back requires both the old image
tag and the pre-upgrade volume backup.

For an application-only rollback, set `GOATCOUNTER_URL` to an empty string and
recreate Flask:

```bash
GOATCOUNTER_URL='' docker compose up -d --force-recreate flask nginx
```

That disables the tracker without deleting GoatCounter data. Do not run
`docker compose down --volumes`; `--volumes` deletes the analytics database.

## 9. Troubleshooting

**The analytics hostname shows the Flask dashboard or a 404.** The distribution
is not forwarding the viewer `Host` header. Recheck the distribution's header
allow-list and wait for it to finish propagating.

**Login loops back to the login page.** Confirm cookies are forwarded, caching
is disabled, `CloudFront-Forwarded-Proto` is forwarded, and nginx was reloaded
with the repository configuration.

**Every browser is reported as Amazon CloudFront.** Forward the original
`User-Agent` header on the analytics distribution.

**`count.js` loads but no visits appear.** Confirm query strings and POST are
forwarded, look for `/count` in browser developer tools, inspect GoatCounter
logs, and allow about 10 seconds for aggregation. Test once without browser
extensions.

**nginx returns 502 for the analytics hostname.** Run
`docker compose ps goatcounter` and `docker compose logs goatcounter`. Confirm
the service is listening on `:8080` and belongs to `my-network`.

**The disk is filling.** Check `docker system df`, backup the analytics volume,
and review GoatCounter retention settings. Do not delete the named volume.

## Primary references

- [GoatCounter self-hosting and Docker documentation](https://github.com/arp242/goatcounter#self-hosting-goatcounter)
- [GoatCounter privacy model](https://www.goatcounter.com/help/privacy)
- [GoatCounter GDPR/consent discussion](https://www.goatcounter.com/help/gdpr)
- [AWS Lightsail distribution creation](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-creating-content-delivery-network-distribution.html)
- [AWS Lightsail pricing](https://aws.amazon.com/lightsail/pricing/)
- [AWS Lightsail cache and forwarding settings](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-changing-default-cache-behavior.html)
- [AWS Lightsail custom domains and certificates](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-enabling-distribution-custom-domains.html)
- [AWS Lightsail DNS assignment](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-point-domain-to-distribution.html)
