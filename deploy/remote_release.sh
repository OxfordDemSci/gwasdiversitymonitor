#!/usr/bin/env bash
# Called only by the explicitly dispatched, environment-gated release workflow.
set -euo pipefail
test "$#" = 1
test -f "$1"
[[ "${DEPLOY_HOST:-}" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+$ ]]
[[ "${DEPLOY_USER:-}" =~ ^[a-z_][a-z0-9_-]*$ ]]
[[ "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ ]]
[[ "${DEPLOY_DOMAIN:-}" = dev.gwasdiversitymonitor.com || "$DEPLOY_DOMAIN" = gwasdiversitymonitor.com ]]
test -n "${DEPLOY_SSH_KEY:-}"
test -n "${DEPLOY_KNOWN_HOSTS:-}"
GWAS_SSH_TEMP=$(mktemp -d)
remote_directory=''
cleanup() {
  result=$?
  if [[ "$remote_directory" =~ ^/tmp/gwas-release\.[a-zA-Z0-9]+$ ]]; then
    ssh "${options[@]}" "$DEPLOY_USER@$DEPLOY_HOST" \
      "rm -f '$remote_directory/release.json'; rmdir '$remote_directory'" || true
  fi
  rm -f "$GWAS_SSH_TEMP/key" "$GWAS_SSH_TEMP/known_hosts"
  rmdir "$GWAS_SSH_TEMP"
  exit "$result"
}
trap cleanup EXIT
chmod 700 "$GWAS_SSH_TEMP"
printf '%s\n' "$DEPLOY_SSH_KEY" > "$GWAS_SSH_TEMP/key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$GWAS_SSH_TEMP/known_hosts"
chmod 600 "$GWAS_SSH_TEMP/key" "$GWAS_SSH_TEMP/known_hosts"
options=(-i "$GWAS_SSH_TEMP/key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$GWAS_SSH_TEMP/known_hosts" -o ConnectTimeout=20)
remote_directory=$(ssh "${options[@]}" "$DEPLOY_USER@$DEPLOY_HOST" 'umask 077; mktemp -d /tmp/gwas-release.XXXXXXXXXXXX')
[[ "$remote_directory" =~ ^/tmp/gwas-release\.[a-zA-Z0-9]+$ ]]
remote_manifest="$remote_directory/release.json"
scp "${options[@]}" "$1" "$DEPLOY_USER@$DEPLOY_HOST:$remote_manifest"
ssh "${options[@]}" "$DEPLOY_USER@$DEPLOY_HOST" \
  "sudo -n /usr/bin/python3 /opt/gwas-release/release.py deploy '$remote_manifest' --expected-domain '$DEPLOY_DOMAIN'"
