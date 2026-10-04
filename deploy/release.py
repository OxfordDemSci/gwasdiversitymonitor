#!/usr/bin/env python3
"""Digest-only host releases. Install this controller as a root-owned file."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile
import time


STATE = Path('/var/lib/gwas-release')
COMPOSE = Path('/opt/gwas-release/compose.release.yml')
SERVICES = ('flask', 'data', 'nginx')
IMAGE = re.compile(r'ghcr\.io/oxforddemsci/gwasdiversitymonitor-(flask|data|nginx)@sha256:[0-9a-f]{64}\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')


def read_json(path):
    with open(path, 'rb') as stream:
        content = stream.read(16385)
    if len(content) > 16384:
        raise ValueError('Configuration exceeds 16 KiB')
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def validate_release(value):
    if set(value) != {'version', 'commit', 'images'} or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Unsupported release manifest')
    if not isinstance(value['commit'], str) or not SHA.fullmatch(value['commit']):
        raise ValueError('Expected a full lowercase Git commit SHA')
    if not isinstance(value['images'], dict) or set(value['images']) != set(SERVICES):
        raise ValueError('Release must contain exactly flask, data and nginx images')
    for service, image in value['images'].items():
        match = IMAGE.fullmatch(image) if isinstance(image, str) else None
        if not match or match[1] != service:
            raise ValueError('Images must use the project repository and an immutable digest')
    return value


def atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix='.release-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def docker(arguments, *, environment=None, **options):
    environment = dict(os.environ if environment is None else environment)
    for name in list(environment):
        if name.startswith(('COMPOSE_', 'DOCKER_')):
            del environment[name]
    config = str(Path(pwd.getpwuid(os.geteuid()).pw_dir) / '.docker')
    return subprocess.run([
        '/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', '--config', config,
        *arguments,
    ], env=environment, check=True, **options)


def site_environment(site):
    required = {'domain', 'dataDirectory', 'runtimeDirectory', 'logDirectory', 'goatcounterImage'}
    if set(site) != required:
        raise ValueError('site.json must contain exactly ' + ', '.join(sorted(required)))
    if site['domain'] not in {'gwasdiversitymonitor.com', 'dev.gwasdiversitymonitor.com'}:
        raise ValueError('Unrecognized deployment domain')
    for key in ('dataDirectory', 'runtimeDirectory', 'logDirectory'):
        path = site[key]
        if not isinstance(path, str) or not Path(path).is_absolute() or path in ('/', '/home', '/var', '/opt'):
            raise ValueError('Use specific absolute persistent directories')
        if not Path(path).is_dir() or any(char in path for char in '\n\r:$'):
            raise ValueError('Persistent directories must already exist and have safe paths')
    image = site['goatcounterImage']
    if not isinstance(image, str) or not re.fullmatch(r'(?:docker\.io/)?arp242/goatcounter@sha256:[0-9a-f]{64}', image):
        raise ValueError('Pin the existing GoatCounter image to its verified repository digest')
    return {
        'GWAS_DEPLOYMENT_DOMAIN': site['domain'],
        'GWAS_NOINDEX': '0' if site['domain'] == 'gwasdiversitymonitor.com' else '1',
        'GWAS_DATA_DIRECTORY': site['dataDirectory'],
        'GWAS_RUNTIME_DIRECTORY_HOST': site['runtimeDirectory'],
        'GWAS_LOG_DIRECTORY': site['logDirectory'],
        'GWAS_GOATCOUNTER_IMAGE': image,
    }


def compose(release, site, *arguments):
    environment = dict(os.environ, **site_environment(site))
    # Do not allow a caller's Compose switches or .env file to change targets.
    for name in list(environment):
        if name.startswith('COMPOSE_'):
            del environment[name]
    environment.update({f'GWAS_{name.upper()}_IMAGE': release['images'][name] for name in SERVICES})
    docker([
        'compose', '--env-file', '/dev/null',
        '--project-name', 'gwasdiversitymonitor', '--file', str(COMPOSE), *arguments,
    ], environment=environment)


def verify_images(release, site):
    compose(release, site, 'pull', *SERVICES, 'goatcounter')
    for image in release['images'].values():
        result = docker([
            'image', 'inspect', image,
            '--format', '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
        ], text=True, capture_output=True)
        if result.stdout.strip() != release['commit']:
            raise ValueError('Image revision label does not match release commit')


def check_data(release, site):
    # Full preflight happens before replacing any running web containers.
    compose(release, site, 'run', '--rm', '--no-deps', 'flask', 'python3', '-c',
            'from app.Health import readiness; import sys; sys.exit(0 if readiness()["ready"] else 1)')
    bundle = Path(site['runtimeDirectory']) / 'data_static.zip'
    if not bundle.is_file():
        raise ValueError('Migrate the verified runtime data_static.zip before deploying')
    with open(Path(site['dataDirectory']) / '.generation_complete.json', 'rb') as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('Oversized data manifest')
    expected = json.loads(raw)['static_bundle_fingerprint']
    digest = hashlib.sha256()
    with open(bundle, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if bundle.stat().st_size != expected['size'] or digest.hexdigest() != expected['sha256']:
        raise ValueError('Runtime bundle differs from the published data manifest')


def start(release, site):
    # A short maintenance window prevents old nginx assets being cached under
    # the new Flask HTML's immutable asset URLs (or vice versa).
    compose(release, site, 'stop', 'nginx')
    compose(release, site, 'up', '--detach', '--no-build', '--wait', '--wait-timeout', '180',
            'flask', 'goatcounter')
    # nginx resolves Docker upstream DNS at startup; recreate it even when its
    # image did not change, including after a failed candidate/rollback.
    compose(release, site, 'up', '--detach', '--no-build', '--no-deps', '--force-recreate',
            '--wait', '--wait-timeout', '180', 'nginx')
    compose(release, site, 'exec', '-T', 'flask', 'python3', '-c',
            'import json, urllib.request; '
            'r=urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=20); '
            f'p=json.load(r); assert p["ready"] and p["application"]["commit"] == {release["commit"]!r}')


def apply_release(release, site):
    current_path = STATE / 'current.json'
    previous = validate_release(read_json(current_path)) if current_path.exists() else None
    verify_images(release, site)
    check_data(release, site)
    atomic_json(STATE / 'pending.json', {'candidate': release, 'previous': previous})
    try:
        start(release, site)
        if previous and previous != release:
            atomic_json(STATE / 'previous.json', previous)
        atomic_json(current_path, release)
        (STATE / 'pending.json').unlink()
        sync_directory(STATE)
    except (OSError, subprocess.CalledProcessError, ValueError):
        if previous:
            print('New release failed; attempting the previous recorded image release.', file=sys.stderr)
            check_data(previous, site)
            start(previous, site)
            atomic_json(current_path, previous)
        else:
            print('First managed deployment failed; no previous managed release exists.', file=sys.stderr)
        print('pending.json retained: inspect recovery before re-enabling releases/data jobs.', file=sys.stderr)
        raise
    print('Active release: ' + release['commit'])


@contextlib.contextmanager
def deployment_lock(wait_seconds=0):
    with open(STATE / 'operation.lock', 'a') as stream:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('Another release/data job holds the deployment lock; timed out before making changes')
                time.sleep(min(5, remaining))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'deploy', 'rollback', 'data'))
    parser.add_argument('manifest', nargs='?')
    parser.add_argument('--expected-domain', choices=('gwasdiversitymonitor.com', 'dev.gwasdiversitymonitor.com'))
    parser.add_argument('--lock-wait-seconds', type=int, default=0,
                        help='Wait up to this many seconds for another release/data job (0–7200)')
    args = parser.parse_args()
    if not 0 <= args.lock_wait_seconds <= 7200:
        parser.error('--lock-wait-seconds must be between 0 and 7200')
    if args.action == 'validate':
        if not args.manifest:
            parser.error('validate requires a manifest')
        validate_release(read_json(args.manifest))
        print('Release manifest: valid')
        return
    if os.geteuid() != 0:
        parser.error('Host operations require the root-owned controller and sudo')
    if args.action != 'deploy' and args.manifest:
        parser.error('Only deploy/validate accept a manifest')
    site = read_json(STATE / 'site.json')
    site_environment(site)
    if args.expected_domain and site['domain'] != args.expected_domain:
        raise ValueError('Remote host belongs to the wrong deployment environment')
    with deployment_lock(args.lock_wait_seconds):
        if (STATE / 'pending.json').exists():
            raise ValueError('An interrupted/failed release needs operator recovery: pending.json exists')
        if args.action == 'data':
            release = validate_release(read_json(STATE / 'current.json'))
            # Stay attached: nonzero generator status reaches cron/monitoring.
            compose(release, site, 'run', '--rm', '--no-deps', 'data')
        else:
            path = args.manifest if args.action == 'deploy' else STATE / 'previous.json'
            if not path:
                parser.error('deploy requires a manifest')
            apply_release(validate_release(read_json(path)), site)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print('Release operation failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
