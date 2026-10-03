"""PubMed lookup failures retain resumable work without weakening raw checks."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import funder_pipeline
import generate_data


class PubMedGenerationResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / 'data'
        self.paths = generate_data._generation_paths(str(self.data))
        self.workspace = Path(self.paths['workspace_data'])
        self.workspace.mkdir(parents=True)
        self.cache = self.workspace / 'funders/pubmed_grants.json'
        self.cache.parent.mkdir()
        self.cache.write_text(json.dumps({'version': funder_pipeline.CACHE_VERSION,
                                         'records': {str(1000 + i): {'grants': []} for i in range(400)}}),
                              encoding='utf-8')
        self.cache_before = self.cache.read_bytes()
        self.raw_state = Path(self.paths['raw_state'])
        self.raw_state.write_text('{"generation_failures":1}', encoding='utf-8')
        logger = mock.patch.object(generate_data, 'diversity_logger', mock.Mock(), create=True)
        logger.start()
        self.addCleanup(logger.stop)

    def fail_funding(self, error):
        """Exercise real failure bookkeeping; replace expensive generation only."""
        with mock.patch.object(generate_data, 'final_year', 2026, create=True), \
                mock.patch.object(generate_data, '_resume_publication_if_needed', return_value=False), \
                mock.patch.object(generate_data, '_staged_state_valid', return_value=False), \
                mock.patch.object(generate_data, '_prepare_generation_workspace', return_value=(self.paths, {})), \
                mock.patch.object(generate_data, '_completion_state_valid', return_value=False), \
                mock.patch.object(generate_data, '_reset_workspace_for_wrangling', return_value={}), \
                mock.patch.object(generate_data, '_release_timeupdated', return_value='2026-01-01'), \
                mock.patch.object(generate_data, '_run_wrangling'), \
                mock.patch.object(generate_data, '_run_funder_wrangling', side_effect=error), \
                mock.patch.object(generate_data, '_publish_staged_release') as publish:
            with self.assertRaises(type(error)) as caught:
                generate_data.generate_and_publish(self.root, 'https://unused.test/', generation_year=2026)
            self.assertIs(caught.exception, error)
            publish.assert_not_called()

    def test_repeated_pubmed_lookup_failures_do_not_poison_raw_snapshot(self):
        original_state = self.raw_state.read_bytes()
        for attempt in range(3):
            self.fail_funding(funder_pipeline.PubMedCollectionError('incomplete upstream lookup'))
            self.assertEqual(self.raw_state.read_bytes(), original_state, attempt)
            self.assertEqual(self.cache.read_bytes(), self.cache_before)
            status = generate_data.runtime_status(self.data)
            self.assertEqual(status['lastRunStatus'], 'failed')
            self.assertEqual(status['lastErrorType'], 'PubMedCollectionError')
        self.assertFalse(Path(self.paths['publication']).exists())
        self.assertFalse(Path(self.paths['staged_state']).exists())
        self.assertTrue(any('retaining the validated raw' in call.args[0]
                            for call in generate_data.diversity_logger.warning.call_args_list))

    def test_other_funding_generation_errors_still_count_against_raw_limit(self):
        self.fail_funding(ValueError('invalid generated artifact'))
        self.assertEqual(json.loads(self.raw_state.read_text())['generation_failures'], 2)
        self.assertEqual(self.cache.read_bytes(), self.cache_before)
        self.assertEqual(generate_data.runtime_status(self.data)['lastErrorType'], 'ValueError')

    def prepare_raw_snapshot(self, failures=1):
        for relative in generate_data.RAW_INPUT_FILES:
            path = self.workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('validated fixture bytes: ' + relative, encoding='utf-8')
        fingerprints = generate_data._fingerprint_files(self.workspace, generate_data.RAW_INPUT_FILES)
        self.raw_state.write_text(json.dumps({'version': generate_data.GENERATION_STATE_VERSION,
                                              'raw_fingerprints': fingerprints,
                                              'generation_failures': failures}), encoding='utf-8')
        return fingerprints

    def test_repaired_implementation_reuses_matching_retained_raw_bytes(self):
        fingerprints = self.prepare_raw_snapshot()
        # New code invalidates staged outputs, not independently validated raw inputs.
        with mock.patch.object(generate_data, '_implementation_fingerprints', return_value={'new-code': 'changed'}) as code, \
                mock.patch.object(generate_data, '_validate_raw_inputs', return_value=fingerprints) as validate, \
                mock.patch.object(generate_data, '_initialize_generation_workspace') as download:
            actual_paths, actual_fingerprints = generate_data._prepare_generation_workspace(
                self.root, str(self.data), 'https://unused.test/')
        self.assertEqual(actual_paths, self.paths)
        self.assertEqual(actual_fingerprints, fingerprints)
        validate.assert_called_once_with(str(self.workspace))
        download.assert_not_called()
        code.assert_not_called()
        self.assertEqual(self.cache.read_bytes(), self.cache_before)

    def test_changed_raw_bytes_or_exhausted_limit_still_require_a_fresh_snapshot(self):
        self.prepare_raw_snapshot()
        (self.workspace / generate_data.RAW_INPUT_FILES[0]).write_text('changed', encoding='utf-8')
        self.assertIsNone(generate_data._workspace_raw_fingerprints(self.paths))
        self.prepare_raw_snapshot(failures=generate_data.GENERATION_RAW_FAILURE_LIMIT)
        self.assertIsNone(generate_data._workspace_raw_fingerprints(self.paths))

    def test_resetting_derived_outputs_preserves_the_completed_pubmed_checkpoint(self):
        bundle = self.root / 'data_static.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('support/fixture.txt', 'maintained static support')
        plot = self.workspace / 'toplot/old.json'
        plot.parent.mkdir()
        plot.write_text('{"old":true}', encoding='utf-8')
        Path(self.paths['staged_state']).write_text('{"old":true}', encoding='utf-8')
        with mock.patch.object(generate_data, '_runtime_bundle_path', return_value=str(bundle)):
            generate_data._reset_workspace_for_wrangling(self.root, self.paths)
        self.assertEqual(self.cache.read_bytes(), self.cache_before)
        self.assertEqual(len(json.loads(self.cache.read_text())['records']), 400)
        self.assertFalse(plot.exists())
        self.assertFalse(Path(self.paths['staged_state']).exists())
        self.assertEqual(json.loads(self.raw_state.read_text())['generation_failures'], 1)
        self.assertTrue((self.workspace / 'support/fixture.txt').is_file())


if __name__ == '__main__':
    unittest.main()
