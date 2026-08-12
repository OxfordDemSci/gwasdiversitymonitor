#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${1:-${project_dir}/backups/goatcounter}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="goatcounter-${timestamp}.tar.gz"
stopped=0

compose=(
    docker compose
    --project-directory "${project_dir}"
    --file "${project_dir}/docker-compose.yml"
)

restart_goatcounter() {
    if [[ "${stopped}" -eq 1 ]]; then
        "${compose[@]}" start goatcounter >/dev/null
    fi
}
trap restart_goatcounter EXIT

mkdir -p -- "${backup_dir}"

# Stopping the writer for the short duration of the archive makes the SQLite
# database and any WAL files a consistent unit.
"${compose[@]}" stop goatcounter >/dev/null
stopped=1

docker run --rm \
    --volume gwas_goatcounter_data:/source:ro \
    --volume "${backup_dir}:/backup" \
    alpine:3.22 \
    tar -czf "/backup/${archive}" -C /source .

"${compose[@]}" start goatcounter >/dev/null
stopped=0
trap - EXIT

printf 'Created %s\n' "${backup_dir}/${archive}"
