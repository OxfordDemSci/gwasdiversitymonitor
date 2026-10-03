"""Published v2 funder files remain recoverable across collector schema changes."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import funder_pipeline
import generate_data


class PreviousFunderSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        funders = self.root / 'funders'
        (funders / 'dashboards').mkdir(parents=True)
        (funders / 'downloads').mkdir()
        self.index = {'version': funder_pipeline.ARTIFACT_VERSION, 'funders': [
            {'slug': 'safe', 'name': 'Safe Funder'},
        ]}
        (funders / 'index.json').write_text(json.dumps(self.index))
        (funders / 'pubmed_grants.json').write_text(json.dumps({
            'version': 2, 'records': {'123': {'grants': []}},
        }))
        (funders / 'normalization-audit.json').write_text('{"version":2}')
        (funders / 'funder_cleaner.json').write_text('{"Safe Funder":"Safe Funder"}')
        (funders / 'dashboards' / 'safe.json').write_text('{"version":2,"funder":{"name":"Safe Funder"}}')
        (funders / 'downloads' / 'safe.zip').write_bytes(b'previously verified download bytes')
        self.paths = funder_pipeline.funder_artifact_files(str(self.root))
        self.fingerprints = generate_data._fingerprint_files(str(self.root), self.paths)
        self.manifest = self.root / generate_data.GENERATION_STATE_FILE
        self.write_manifest(self.fingerprints)

    def write_manifest(self, fingerprints):
        self.manifest.write_text(json.dumps({'artifact_fingerprints': fingerprints}))

    def current_validator_rejects_legacy(self):
        return mock.patch.object(funder_pipeline, 'validate_funder_artifacts',
                                 side_effect=ValueError('Old collector/audit schema'))

    def test_verified_legacy_files_survive_without_current_schema_revalidation(self):
        with self.current_validator_rejects_legacy() as validator:
            selected = generate_data._verified_previous_funder_fingerprints(str(self.root))
        validator.assert_not_called()
        self.assertEqual(selected, self.fingerprints)
        self.assertIn('funders/index.json', selected)
        self.assertIn('funders/dashboards/safe.json', selected)
        self.assertIn('funders/downloads/safe.zip', selected)

    def test_changed_source_bytes_are_not_trusted_as_the_old_release(self):
        (self.root / 'funders' / 'dashboards' / 'safe.json').write_text('{"changed":true}')
        with self.current_validator_rejects_legacy() as validator:
            selected = generate_data._verified_previous_funder_fingerprints(str(self.root))
        self.assertEqual(selected, {})
        validator.assert_called_once_with(str(self.root))

    def test_missing_named_artifact_or_manifest_entry_cannot_form_partial_snapshot(self):
        incomplete = dict(self.fingerprints)
        incomplete.pop('funders/downloads/safe.zip')
        self.write_manifest(incomplete)
        with self.current_validator_rejects_legacy():
            self.assertEqual(generate_data._verified_previous_funder_fingerprints(str(self.root)), {})
        self.write_manifest(self.fingerprints)
        (self.root / 'funders' / 'downloads' / 'safe.zip').unlink()
        with self.current_validator_rejects_legacy():
            self.assertEqual(generate_data._verified_previous_funder_fingerprints(str(self.root)), {})

    def test_arbitrary_manifest_paths_are_ignored(self):
        unexpected = dict(self.fingerprints)
        unexpected['../../outside.txt'] = {'size': 1, 'sha256': '0' * 64}
        unexpected['/etc/passwd'] = {'size': 1, 'sha256': '0' * 64}
        self.write_manifest(unexpected)
        with self.current_validator_rejects_legacy(), mock.patch.object(
                generate_data, '_fingerprints_match', wraps=generate_data._fingerprints_match) as verify:
            selected = generate_data._verified_previous_funder_fingerprints(str(self.root))
        self.assertEqual(selected, self.fingerprints)
        verify.assert_called_once_with(str(self.root), self.fingerprints)

    def test_unsafe_index_slugs_are_rejected_before_reading_named_files(self):
        self.index['funders'][0]['slug'] = '../outside'
        (self.root / 'funders' / 'index.json').write_text(json.dumps(self.index))
        with self.current_validator_rejects_legacy(), mock.patch.object(
                generate_data, '_fingerprints_match') as verify:
            self.assertEqual(generate_data._verified_previous_funder_fingerprints(str(self.root)), {})
        verify.assert_not_called()

    def test_missing_manifest_can_still_use_existing_full_validation_path(self):
        self.manifest.unlink()
        with mock.patch.object(funder_pipeline, 'validate_funder_artifacts',
                               return_value=self.paths) as validator:
            selected = generate_data._verified_previous_funder_fingerprints(str(self.root))
        validator.assert_called_once_with(str(self.root))
        self.assertEqual(selected, self.fingerprints)

    def test_corrupt_manifest_cannot_bypass_schema_validation(self):
        self.manifest.write_text('{invalid json')
        with self.current_validator_rejects_legacy() as validator:
            self.assertEqual(generate_data._verified_previous_funder_fingerprints(str(self.root)), {})
        validator.assert_called_once_with(str(self.root))


if __name__ == '__main__':
    unittest.main()
