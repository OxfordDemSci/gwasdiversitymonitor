"""Bounded provenance metadata, separate from immutable scientific artifacts."""

import copy
import datetime
from functools import lru_cache
import hashlib
import json
import os
import re
from urllib.parse import unquote, urlsplit


MANIFEST_FILE = ".generation_complete.json"
RUNTIME_STATUS_FILE = "runtime-status.json"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_STATUS_BYTES = 16384
SOURCE_PATHS = (
    "catalog/raw/Cat_Stud.tsv", "catalog/raw/Cat_Anc.tsv",
    "catalog/raw/Cat_Full.tsv", "catalog/raw/Cat_Map.tsv",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DATASET_ID = re.compile(r"gwas-[0-9a-f]{64}\Z")
_RELEASE_DATE = re.compile(r"(?:^|[_-])r(\d{4}-\d{2}-\d{2})(?=[_.-]|$)")
_TIMESTAMP_FIELDS = (
    "lastRunStartedAt", "lastRunFinishedAt", "lastSuccessfulRunAt",
    "lastSuccessfulFetchAt", "lastPublicationAt",
)

MONITOR_CITATION = (
    "Mills, M. C. & Rahal, C. (2020). The GWAS Diversity Monitor tracks "
    "diversity by disease in real time. Nature Genetics 52, 242–243. "
    "https://doi.org/10.1038/s41588-020-0580-y"
)
SOFTWARE_CITATION = (
    "Boef, N., Brunier, Q., Knowles, I., Malowany, A., May, J., Mills, M. C., "
    "Misseri, L., Nixon, G., Ntova, V., Rahal, C. & Sinclair, C. (2020). "
    "Source code for the GWAS Diversity Monitor (Version 1.0.0). Zenodo. "
    "https://doi.org/10.5281/zenodo.3600472"
)
SOURCE_EXPORT_SCOPE = (
    "Entity-selection source data: selected funders and cohorts, all publication "
    "years, all traits and both discovery and replication stages. Chart-specific "
    "filters are not applied. Participant counts are participant instances, not "
    "unique people. Entity attribution uses full counting and totals overlap."
)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def bounded_text(value, limit=250):
    return value if isinstance(value, str) and len(value) <= limit and not any(
        ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value
    ) else None


def timestamp(value):
    if not bounded_text(value, 50):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(datetime.timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def release_date_from_filenames(*filenames):
    dates = set()
    for filename in filenames:
        if not bounded_text(filename, 1024):
            continue
        for found in _RELEASE_DATE.findall(unquote(filename)):
            parsed = valid_date(found)
            if parsed is not None:
                dates.add(parsed)
    return next(iter(dates)) if len(dates) == 1 else None


def _safe_url(value):
    if not bounded_text(value, 2048):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https", "ftp"} and \
            parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def normalize_sources(value):
    if not isinstance(value, list) or len(value) > len(SOURCE_PATHS):
        return []
    result = []
    seen = set()
    for entry in value:
        if not isinstance(entry, dict) or entry.get("path") not in SOURCE_PATHS \
                or entry["path"] in seen:
            continue
        seen.add(entry["path"])
        filename = bounded_text(entry.get("filename"), 1024)
        member = bounded_text(entry.get("archiveMember"), 1024)
        result.append({
            "path": entry["path"], "url": _safe_url(entry.get("url")),
            "filename": filename, "archiveMember": member,
            "fetchedAt": timestamp(entry.get("fetchedAt")),
            # Re-derive this evidence instead of trusting a claimed releaseDate.
            "releaseDate": release_date_from_filenames(filename, member),
            "etag": bounded_text(entry.get("etag")),
            "lastModified": bounded_text(entry.get("lastModified")),
        })
    return result


def source_metadata(path, url, response, filename=None, archive_member=None):
    return normalize_sources([{
        "path": path, "url": url, "filename": filename,
        "archiveMember": archive_member, "fetchedAt": utc_now(),
        "etag": response.headers.get("ETag"),
        "lastModified": response.headers.get("Last-Modified"),
    }])[0]


def _fingerprints(value, required=False):
    if not isinstance(value, dict) or len(value) > 20000 or (required and not value):
        return None
    result = {}
    for path, fingerprint in value.items():
        if not bounded_text(path, 512) or not isinstance(fingerprint, dict):
            return None
        digest, size = fingerprint.get("sha256"), fingerprint.get("size")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest) \
                or type(size) is not int or size < 0:
            return None
        result[path] = {"sha256": digest, "size": size}
    return result


def dataset_identity(manifest):
    """Identify the artifact set, not the time it was checked or serialized."""
    if not isinstance(manifest, dict):
        return None
    artifacts = _fingerprints(manifest.get("artifact_fingerprints"), required=True)
    if artifacts is None:
        return None
    identity = {"identityVersion": 1, "artifact_fingerprints": artifacts}
    for name in ("raw_fingerprints", "implementation_fingerprints"):
        if name in manifest:
            fingerprints = _fingerprints(manifest[name])
            if fingerprints is None:
                return None
            identity[name] = fingerprints
    for name in ("input_static_bundle_fingerprint", "static_bundle_fingerprint"):
        if name in manifest:
            fingerprints = _fingerprints({name: manifest[name]})
            if fingerprints is None:
                return None
            identity[name] = fingerprints[name]
    parameters = manifest.get("generation_parameters", {})
    if not isinstance(parameters, dict) or len(parameters) > 30:
        return None
    if any(not bounded_text(key, 100) or type(value) not in {str, int, float, bool, type(None)}
           or (isinstance(value, str) and len(value) > 1000)
           for key, value in parameters.items()):
        return None
    identity["generation_parameters"] = parameters
    try:
        serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        return None
    return "gwas-" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def manifest_provenance(manifest):
    manifest = manifest if isinstance(manifest, dict) else {}
    stored = manifest.get("provenance")
    stored = stored if isinstance(stored, dict) and type(stored.get("version")) is int \
        and stored["version"] == 1 else {}
    sources = normalize_sources(stored.get("sources", []))
    primary = [next((entry for entry in sources if entry["path"] == path), {})
               for path in SOURCE_PATHS[:3]]
    release_dates = {entry.get("releaseDate") for entry in primary}
    release_date = next(iter(release_dates)) if len(release_dates) == 1 and None not in release_dates else None
    return {
        "version": 1, "datasetId": dataset_identity(manifest),
        "generatedAt": timestamp(manifest.get("completed_at")),
        "catalogReleaseDate": release_date,
        "catalogReleaseEvidence": "download-filenames" if release_date else None,
        "fetchCompletedAt": timestamp(stored.get("fetchCompletedAt")),
        "sources": sources,
    }


def _read_bounded_json(path, limit):
    try:
        with open(path, "rb") as source:
            content = source.read(limit + 1)
        if len(content) > limit:
            return {}
        value = json.loads(content)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, RecursionError):
        return {}


@lru_cache(maxsize=16)
def _cached_manifest(path, inode, modified_ns, changed_ns, size):
    return manifest_provenance(_read_bounded_json(path, MAX_MANIFEST_BYTES))


def published_provenance(data_path):
    path = os.path.abspath(os.path.join(data_path, MANIFEST_FILE))
    try:
        stat = os.stat(path)
        result = _cached_manifest(path, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    except OSError:
        result = manifest_provenance({})
    return copy.deepcopy(result)


def runtime_status(data_root):
    value = _read_bounded_json(os.path.join(
        data_root, ".generate_data", RUNTIME_STATUS_FILE
    ), MAX_STATUS_BYTES)
    if type(value.get("version")) is not int or value["version"] != 1:
        value = {}
    result = {"version": 1}
    result.update({name: timestamp(value.get(name)) for name in _TIMESTAMP_FIELDS})
    status, outcome = value.get("lastRunStatus"), value.get("lastRunOutcome")
    result["lastRunStatus"] = status if isinstance(status, str) and status in {
        "running", "success", "failed"
    } else "unknown"
    result["lastRunOutcome"] = outcome if isinstance(outcome, str) and outcome in {
        "unchanged", "published", "resumed"
    } else None
    result["lastErrorType"] = bounded_text(value.get("lastErrorType"), 100)
    identifier = value.get("lastPublicationDatasetId")
    result["lastPublicationDatasetId"] = identifier if isinstance(identifier, str) and \
        _DATASET_ID.fullmatch(identifier) else None
    return result


def provenance_for_release(published_path, data_root=None):
    if data_root is None:
        data_root = os.path.abspath(published_path)
        if os.path.basename(data_root) == 'previous-release' and \
                os.path.basename(os.path.dirname(data_root)) == '.generate_data':
            data_root = os.path.dirname(os.path.dirname(data_root))
    result = published_provenance(published_path)
    result.update(runtime_status(data_root))
    # A global heartbeat can describe a newer attempted release. Publication
    # time only belongs to these plotted artifacts if the identities agree.
    if result["lastPublicationDatasetId"] != result["datasetId"]:
        result["lastPublicationAt"] = None
    return result


def valid_dataset_id(value):
    return isinstance(value, str) and bool(_DATASET_ID.fullmatch(value))
