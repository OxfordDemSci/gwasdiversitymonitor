"""Small, uncached operational probes; dataset integrity is stat-keyed in-process."""

import datetime
from functools import lru_cache
import json
import os

from flask import Blueprint, jsonify

from app import DataLoader
from app.Provenance import MAX_MANIFEST_BYTES, published_provenance, runtime_status
from app.Version import application_version


health = Blueprint('health', __name__)
REQUIRED = tuple(dict.fromkeys(DataLoader.RUNTIME_DATA_FILES + DataLoader.FILTER_RUNTIME_FILES))


def _stat_signature(data_path):
    paths = (DataLoader.GENERATION_STATE_FILE,) + REQUIRED
    return tuple((name, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                 for name in paths
                 for stat in [os.stat(os.path.join(data_path, name))])


@lru_cache(maxsize=4)
def _verified_release(data_path, signature):
    """Only re-hash when a consumed artifact or the manifest changes."""
    with open(os.path.join(data_path, DataLoader.GENERATION_STATE_FILE), 'rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        return False
    manifest = json.loads(raw)
    artifacts = manifest['artifact_fingerprints']
    for relative in REQUIRED:
        expected = artifacts[relative]
        path = os.path.join(data_path, relative)
        if (type(expected.get('size')) is not int or
                os.path.getsize(path) != expected['size'] or
                DataLoader._sha256_file(path) != expected.get('sha256')):
            return False
    return bool(published_provenance(data_path)['datasetId'])


def readiness(data_root='data'):
    try:
        with DataLoader.published_data_lock(data_root) as published:
            signature = _stat_signature(published)
            ready = _verified_release(published, signature)
            identifier = published_provenance(published)['datasetId'] if ready else None
            fallback = os.path.abspath(published) != os.path.abspath(data_root)
        return {'ready': ready, 'datasetId': identifier, 'servingPreviousRelease': fallback}
    except (OSError, ValueError, TypeError, KeyError, RecursionError,
            DataLoader.PublishedDataUnavailable):
        return {'ready': False, 'datasetId': None, 'servingPreviousRelease': False}


def _hours_setting(name, default):
    try:
        value = float(os.environ.get(name, default))
        return value if 0 < value <= 720 else default
    except ValueError:
        return default


def data_job_health(data_root='data', now=None):
    status = runtime_status(data_root)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    maximum_age = _hours_setting('GWAS_DATA_MAX_AGE_HOURS', 36) * 3600
    maximum_run = _hours_setting('GWAS_DATA_MAX_RUN_HOURS', 6) * 3600
    issues = []

    def age(name):
        value = status.get(name)
        if value is None:
            return None
        seconds = (now - datetime.datetime.fromisoformat(value)).total_seconds()
        if seconds < -300:
            issues.append('clock_skew')
            return None
        return max(0, seconds)

    successful_age = age('lastSuccessfulRunAt')
    fetch_age = age('lastSuccessfulFetchAt')
    started_age = age('lastRunStartedAt')
    if successful_age is None or successful_age > maximum_age:
        issues.append('successful_run_overdue')
    if fetch_age is None or fetch_age > maximum_age:
        issues.append('upstream_check_overdue')
    if status['lastRunStatus'] == 'failed':
        issues.append('last_run_failed')
    if status['lastRunStatus'] == 'unknown':
        issues.append('run_status_unknown')
    if status['lastRunStatus'] == 'running' and (started_age is None or started_age > maximum_run):
        issues.append('run_stuck')
    return {
        'healthy': not issues, 'issues': sorted(set(issues)),
        'lastRunStatus': status['lastRunStatus'],
        'lastSuccessfulRunAt': status['lastSuccessfulRunAt'],
        'lastSuccessfulFetchAt': status['lastSuccessfulFetchAt'],
        'maximumAgeHours': maximum_age / 3600,
        'maximumRunHours': maximum_run / 3600,
    }


def _response(payload, okay):
    response = jsonify(dict(payload, application=application_version()))
    response.status_code = 200 if okay else 503
    response.headers['Cache-Control'] = 'no-store'
    return response


@health.route('/health/live')
def live():
    return _response({'alive': True}, True)


@health.route('/health/ready')
def ready():
    payload = readiness()
    return _response(payload, payload['ready'])


@health.route('/health/data')
def data():
    payload = data_job_health()
    return _response(payload, payload['healthy'])
