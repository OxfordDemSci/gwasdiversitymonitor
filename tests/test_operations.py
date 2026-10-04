import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from app import Health
from app import app
from app.Provenance import dataset_identity
import generate_data


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_controller', ROOT / 'deploy/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def release_manifest(commit='a' * 40):
    return {'version': 1, 'commit': commit, 'images': {
        name: 'ghcr.io/oxforddemsci/gwasdiversitymonitor-' + name + '@sha256:' + 'b' * 64
        for name in release.SERVICES
    }}


class HealthTests(unittest.TestCase):
    def test_probe_routes_are_uncached_and_readiness_does_not_depend_on_job_age(self):
        client = app.test_client()
        live = client.get('/health/live')
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.headers['Cache-Control'], 'no-store')
        with mock.patch.object(Health, 'readiness', return_value={'ready': True, 'datasetId': 'fixture'}):
            self.assertEqual(client.get('/health/ready').status_code, 200)
        with mock.patch.object(Health, 'data_job_health', return_value={'healthy': False}):
            self.assertEqual(client.get('/health/data').status_code, 503)

    def test_readiness_verifies_all_inputs_caches_hashes_and_rechecks_mutations(self):
        with tempfile.TemporaryDirectory() as root:
            artifacts = {}
            for relative in Health.REQUIRED:
                path = Path(root, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'{}')
                artifacts[relative] = {'size': 2, 'sha256': hashlib.sha256(b'{}').hexdigest()}
            manifest = {'artifact_fingerprints': artifacts}
            Path(root, '.generation_complete.json').write_text(json.dumps(manifest))
            Health._verified_release.cache_clear()
            original = Health.DataLoader._sha256_file
            with mock.patch.object(Health.DataLoader, '_sha256_file', wraps=original) as hasher:
                result = Health.readiness(root)
                self.assertTrue(result['ready'])
                self.assertEqual(result['datasetId'], dataset_identity(manifest))
                count = hasher.call_count
                self.assertTrue(Health.readiness(root)['ready'])
                self.assertEqual(hasher.call_count, count)
                Path(root, Health.DataLoader.FILTER_RUNTIME_FILES[0]).write_bytes(b'[]')
                self.assertFalse(Health.readiness(root)['ready'])
                self.assertGreater(hasher.call_count, count)

    def test_missing_release_and_overdue_job_are_distinct(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(Health.readiness(root)['ready'])
            status = Health.data_job_health(root)
            self.assertFalse(status['healthy'])
            self.assertIn('successful_run_overdue', status['issues'])

    def test_fresh_unchanged_runs_healthy_but_failures_stuck_and_future_timestamps_alert(self):
        now = datetime.datetime(2026, 10, 3, tzinfo=datetime.timezone.utc)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, '.generate_data/runtime-status.json')
            path.parent.mkdir()
            state = {'version': 1, 'lastRunStatus': 'success', 'lastRunOutcome': 'unchanged',
                     'lastSuccessfulRunAt': now.isoformat(), 'lastSuccessfulFetchAt': now.isoformat()}
            path.write_text(json.dumps(state))
            self.assertTrue(Health.data_job_health(root, now)['healthy'])
            state['lastRunStatus'] = 'failed'
            path.write_text(json.dumps(state))
            self.assertIn('last_run_failed', Health.data_job_health(root, now)['issues'])
            state.update(lastRunStatus='running', lastRunStartedAt=(now-datetime.timedelta(hours=7)).isoformat())
            path.write_text(json.dumps(state))
            self.assertIn('run_stuck', Health.data_job_health(root, now)['issues'])
            state['lastSuccessfulRunAt'] = (now+datetime.timedelta(hours=1)).isoformat()
            path.write_text(json.dumps(state))
            self.assertIn('clock_skew', Health.data_job_health(root, now)['issues'])


class ReleaseTests(unittest.TestCase):
    def test_deployment_lock_waits_without_starting_a_second_operation(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(release, 'STATE', Path(root)), \
                mock.patch.object(release.fcntl, 'flock', side_effect=[BlockingIOError(), None, None]) as flock, \
                mock.patch.object(release.time, 'monotonic', side_effect=[100, 102]), \
                mock.patch.object(release.time, 'sleep') as sleep:
            with release.deployment_lock(10):
                self.assertEqual(flock.call_count, 2)
            sleep.assert_called_once_with(5)
            self.assertEqual(flock.call_args.args[1], release.fcntl.LOCK_UN)

    def test_deployment_lock_timeout_never_enters_operation(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(release, 'STATE', Path(root)), \
                mock.patch.object(release.fcntl, 'flock', side_effect=BlockingIOError()), \
                mock.patch.object(release.time, 'monotonic', side_effect=[100, 110]), \
                mock.patch.object(release.time, 'sleep') as sleep:
            with self.assertRaisesRegex(ValueError, 'timed out before making changes'):
                with release.deployment_lock(10):
                    self.fail('A busy deployment must not enter its critical section')
            sleep.assert_not_called()

    def test_deployment_lock_is_released_when_operation_fails(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(release, 'STATE', Path(root)), \
                mock.patch.object(release.fcntl, 'flock') as flock:
            with self.assertRaises(RuntimeError):
                with release.deployment_lock():
                    raise RuntimeError('failed operation')
            self.assertEqual(flock.call_args.args[1], release.fcntl.LOCK_UN)

    def test_start_has_explicit_maintenance_boundary_and_recreates_nginx(self):
        with mock.patch.object(release, 'compose') as compose:
            release.start(release_manifest(), {})
        commands = [call.args[2:] for call in compose.call_args_list]
        self.assertEqual(commands[0], ('stop', 'nginx'))
        self.assertEqual(commands[1][-2:], ('flask', 'goatcounter'))
        self.assertEqual(commands[2][-1], 'nginx')
        self.assertIn('--force-recreate', commands[2])
        self.assertEqual(commands[3][0], 'exec')

    def test_bundle_preflight_rejects_missing_or_different_runtime_bundle(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(release, 'compose'):
            site = {'dataDirectory': root, 'runtimeDirectory': root}
            with self.assertRaises(ValueError):
                release.check_data(release_manifest(), site)
            bundle = Path(root, 'data_static.zip')
            bundle.write_bytes(b'fixture bundle')
            manifest = {'static_bundle_fingerprint': {'size': bundle.stat().st_size,
                        'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()}}
            Path(root, '.generation_complete.json').write_text(json.dumps(manifest))
            release.check_data(release_manifest(), site)
            bundle.write_bytes(b'other bundle')
            with self.assertRaises(ValueError):
                release.check_data(release_manifest(), site)

    def test_docker_target_is_local_and_caller_routing_variables_are_removed(self):
        with mock.patch.object(release.subprocess, 'run') as run:
            release.docker(['info'], environment={'DOCKER_HOST': 'tcp://evil:2375',
                'DOCKER_CONTEXT': 'remote', 'DOCKER_CONFIG': '/tmp/evil', 'COMPOSE_FILE': 'evil.yml', 'PATH': '/usr/bin'})
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[:3], ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock'])
        self.assertEqual(run.call_args.kwargs['env'], {'PATH': '/usr/bin'})

    def test_manifest_rejects_tags_wrong_repositories_services_and_shell_strings(self):
        self.assertEqual(release.validate_release(release_manifest())['commit'], 'a'*40)
        for replacement in ('nginx:latest', 'ghcr.io/evil/repo@sha256:'+'a'*64,
                            '$(touch /tmp/no)', 'ghcr.io/oxforddemsci/gwasdiversitymonitor-data@sha256:'+'a'*64):
            value = release_manifest()
            value['images']['flask'] = replacement
            with self.assertRaises(ValueError):
                release.validate_release(value)
        value = release_manifest('dev')
        with self.assertRaises(ValueError):
            release.validate_release(value)

    def test_failed_deployment_keeps_ledger_and_attempts_previous_images(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(release, 'STATE', Path(root)):
            previous = release_manifest('c'*40)
            current = Path(root, 'current.json')
            current.write_text(json.dumps(previous))
            failure = subprocess.CalledProcessError(1, ['docker'])
            with mock.patch.object(release, 'verify_images'), mock.patch.object(release, 'check_data'), \
                    mock.patch.object(release, 'start', side_effect=[failure, None]) as start:
                with self.assertRaises(subprocess.CalledProcessError):
                    release.apply_release(release_manifest(), {})
                self.assertEqual(start.call_args_list[1].args[0], previous)
            self.assertEqual(json.loads(current.read_text()), previous)
            self.assertFalse(Path(root, 'previous.json').exists())

    def test_success_records_previous_and_current_only_after_health_checks(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(release, 'STATE', Path(root)):
            old = release_manifest('c'*40)
            Path(root, 'current.json').write_text(json.dumps(old))
            with mock.patch.object(release, 'verify_images'), mock.patch.object(release, 'check_data'), \
                    mock.patch.object(release, 'start'):
                release.apply_release(release_manifest(), {})
            self.assertEqual(json.loads(Path(root, 'previous.json').read_text()), old)
            self.assertEqual(json.loads(Path(root, 'current.json').read_text()), release_manifest())
            self.assertFalse(Path(root, 'pending.json').exists())

    def test_ledger_failure_recovers_web_and_retains_pending_marker_for_operator(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(release, 'STATE', Path(root)):
            old = release_manifest('c'*40)
            current = Path(root, 'current.json')
            current.write_text(json.dumps(old))
            original = release.atomic_json
            failed = [False]
            def write(path, value):
                if path == current and not failed[0]:
                    failed[0] = True
                    raise OSError('simulated full disk')
                original(path, value)
            with mock.patch.object(release, 'verify_images'), mock.patch.object(release, 'check_data'), \
                    mock.patch.object(release, 'start') as start, mock.patch.object(release, 'atomic_json', side_effect=write):
                with self.assertRaises(OSError):
                    release.apply_release(release_manifest(), {})
                self.assertEqual(start.call_args_list[-1].args[0], old)
            self.assertEqual(json.loads(current.read_text()), old)
            self.assertTrue(Path(root, 'pending.json').exists())

    def test_runtime_bundle_uses_directory_mount_and_cleaners_use_image_inputs(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = str(Path(root, 'runtime'))
            inputs = str(Path(root, 'immutable-inputs'))
            with mock.patch.dict(os.environ, GWAS_RUNTIME_DIRECTORY=runtime, GWAS_RELEASE_INPUTS=inputs):
                self.assertEqual(generate_data._runtime_bundle_path(root), runtime+'/data_static.zip')
                self.assertEqual(generate_data._maintained_inputs_path(root), inputs)
                # Immutable cleaner fingerprints do not accidentally come from the live data mount.
                with mock.patch.object(generate_data, '_fingerprint_files', return_value={}), \
                        mock.patch.object(generate_data, '_file_fingerprint', return_value={'sha256': 'a'*64, 'size': 1}) as fp:
                    result = generate_data._implementation_fingerprints(root)
                self.assertEqual(len(result), 2)
                self.assertTrue(all(call.args[0].startswith(inputs + '/') for call in fp.call_args_list))
            with mock.patch.dict(os.environ, GWAS_RUNTIME_DIRECTORY='relative'):
                with self.assertRaises(ValueError):
                    generate_data._runtime_bundle_path(root)


if __name__ == '__main__':
    unittest.main()
