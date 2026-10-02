import contextlib
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from app import app
from app import Provenance
from app import DataLoader
import generate_data


EARLIER = '2026-09-01T12:00:00+00:00'
LATER = '2026-10-02T12:00:00+00:00'


def fingerprint(content=b'{}'):
    return {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}


def manifest():
    return {
        'version': 3, 'completed_at': EARLIER,
        'artifact_fingerprints': {'toplot/summary.json': fingerprint()},
        'raw_fingerprints': {'catalog/raw/Cat_Stud.tsv': fingerprint(b'raw')},
        'implementation_fingerprints': {'generate_data.py': fingerprint(b'code')},
        'generation_parameters': {'final_year': 2026},
        'input_static_bundle_fingerprint': fingerprint(b'input bundle'),
        'static_bundle_fingerprint': fingerprint(b'bundle'),
    }


def sources():
    return [{
        'path': path, 'url': 'https://example.test/' + path,
        'filename': 'catalog_r2026-09-01.tsv', 'archiveMember': None,
        'fetchedAt': EARLIER, 'etag': '"version"',
        'lastModified': 'Wed, 02 Sep 2026 12:00:00 GMT',
    } for path in Provenance.SOURCE_PATHS]


class ProvenanceFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.manifest_path = self.data / Provenance.MANIFEST_FILE
        self.manifest_path.write_text(json.dumps(manifest()))
        Provenance._cached_manifest.cache_clear()


class ProvenanceTests(ProvenanceFixture, unittest.TestCase):
    def test_identity_ignores_time_metadata_and_key_order_but_tracks_artifacts(self):
        original = manifest()
        identifier = Provenance.dataset_identity(original)
        changed = dict(reversed(list(original.items())))
        changed.update(completed_at=LATER, provenance={'sources': sources()})
        self.assertEqual(Provenance.dataset_identity(changed), identifier)
        changed['artifact_fingerprints'] = {'toplot/summary.json': fingerprint(b'new')}
        self.assertNotEqual(Provenance.dataset_identity(changed), identifier)
        self.assertTrue(Provenance.valid_dataset_id(identifier))
        self.assertIsNone(Provenance.dataset_identity({}))
        original['artifact_fingerprints']['toplot/summary.json']['size'] = True
        self.assertIsNone(Provenance.dataset_identity(original))

    def test_only_filename_evidence_can_supply_catalog_release_date(self):
        value = manifest()
        value['provenance'] = {'version': 1, 'sources': sources(), 'fetchCompletedAt': EARLIER}
        result = Provenance.manifest_provenance(value)
        self.assertEqual(result['catalogReleaseDate'], '2026-09-01')
        self.assertEqual(result['fetchCompletedAt'], EARLIER)
        value['provenance']['sources'][0]['filename'] = 'undated.tsv'
        value['provenance']['sources'][0]['releaseDate'] = '2026-09-01'
        self.assertIsNone(Provenance.manifest_provenance(value)['catalogReleaseDate'])
        self.assertIsNone(Provenance.release_date_from_filenames('file_r2026-02-31.tsv'))
        self.assertIsNone(Provenance.release_date_from_filenames(
            'file_r2026-09-01.zip', 'file_r2026-09-02.tsv'))
        self.assertEqual(Provenance.release_date_from_filenames(
            'file_r2026%2D09%2D01.tsv'), '2026-09-01')

    def test_legacy_metadata_does_not_invent_fetch_or_release_dates(self):
        result = Provenance.provenance_for_release(self.data)
        self.assertIsNotNone(result['datasetId'])
        self.assertEqual(result['generatedAt'], EARLIER)
        for field in ('catalogReleaseDate', 'fetchCompletedAt', 'lastSuccessfulRunAt',
                      'lastSuccessfulFetchAt', 'lastPublicationAt'):
            self.assertIsNone(result[field], field)
        self.assertEqual(result['lastRunStatus'], 'unknown')

    def test_cache_reads_small_manifest_once_and_invalidates_on_replacement(self):
        with mock.patch.object(Provenance, '_read_bounded_json', wraps=Provenance._read_bounded_json) as read:
            first = Provenance.published_provenance(self.data)
            second = Provenance.published_provenance(self.data)
            self.assertEqual(read.call_count, 1)
            second['sources'].append('modified by caller')
            self.assertEqual(Provenance.published_provenance(self.data), first)
            value = manifest()
            value['artifact_fingerprints']['toplot/summary.json'] = fingerprint(b'next')
            replacement = self.data / 'replacement.json'
            replacement.write_text(json.dumps(value))
            replacement.replace(self.manifest_path)
            self.assertNotEqual(Provenance.published_provenance(self.data)['datasetId'], first['datasetId'])
            self.assertEqual(read.call_count, 2)

    def test_untrusted_metadata_is_bounded_and_timestamps_require_timezone(self):
        self.assertIsNone(Provenance.timestamp('2026-10-02T12:00:00'))
        self.assertEqual(Provenance.timestamp('2026-10-02T13:00:00+01:00'), LATER)
        self.manifest_path.write_bytes(b'x' * (Provenance.MAX_MANIFEST_BYTES + 1))
        self.assertIsNone(Provenance.published_provenance(self.data)['datasetId'])
        path = self.data / '.generate_data' / Provenance.RUNTIME_STATUS_FILE
        path.parent.mkdir()
        path.write_text(json.dumps({'version': 1, 'lastRunStatus': {}, 'lastRunOutcome': [],
                                   'lastSuccessfulFetchAt': 'yesterday'}))
        result = Provenance.runtime_status(self.data)
        self.assertEqual(result['lastRunStatus'], 'unknown')
        self.assertIsNone(result['lastSuccessfulFetchAt'])
        entry = sources()[0]
        entry['url'] = 'https://password:secret@example.test/'
        entry['etag'] = 'x' * 251
        result = Provenance.normalize_sources([entry])[0]
        self.assertIsNone(result['url'])
        self.assertIsNone(result['etag'])

    def test_fallback_keeps_old_identity_but_reads_the_live_operational_status(self):
        fallback = self.data / '.generate_data' / 'previous-release'
        fallback.mkdir(parents=True)
        (fallback / Provenance.MANIFEST_FILE).write_text(self.manifest_path.read_text())
        generate_data._record_runtime_status(
            self.data, lastSuccessfulRunAt=LATER, lastSuccessfulFetchAt=EARLIER,
            lastPublicationAt=LATER, lastPublicationDatasetId='gwas-' + 'a' * 64,
        )
        result = Provenance.provenance_for_release(fallback)
        self.assertEqual(result['datasetId'], Provenance.dataset_identity(manifest()))
        self.assertEqual(result['lastSuccessfulRunAt'], LATER)
        self.assertEqual(result['lastSuccessfulFetchAt'], EARLIER)
        self.assertIsNone(result['lastPublicationAt'])


class ProvenanceRouteTests(ProvenanceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (self.data / 'toplot').mkdir()
        (self.data / 'toplot' / 'summary.json').write_bytes(b'{}')
        patch = mock.patch('app.routes.DataLoader.published_data_lock',
                           side_effect=lambda: contextlib.nullcontext(str(self.data)))
        patch.start()
        self.addCleanup(patch.stop)
        self.client = app.test_client()
        self.identifier = Provenance.dataset_identity(manifest())

    def test_plot_headers_bind_identity_and_reject_a_stale_page_before_plot_reads(self):
        response = self.client.get('/json/summary.json?datasetId=' + self.identifier)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-GWAS-Dataset-ID'], self.identifier)
        response.close()
        response = self.client.get('/json/summary.json?datasetId=gwas-' + 'f' * 64)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'dataset_changed')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_provenance_is_no_store_and_does_not_load_facet_indexes_until_requested(self):
        coverage = {'study_count': 100, 'funder_linked_study_percentage': 40,
                    'cohort_linked_study_percentage': 30}
        with mock.patch('app.routes.load_precomputed_facet_overview', return_value=coverage) as load, \
                mock.patch('app.routes.get_dashboard_filter_store') as store:
            response = self.client.get('/api/provenance?datasetId=' + self.identifier)
            self.assertEqual(response.json['datasetId'], self.identifier)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            load.assert_not_called()
            store.assert_not_called()
            response = self.client.get('/api/provenance?datasetId=' + self.identifier + '&coverage=1')
            self.assertEqual(response.json['coverage'], coverage)
            store.assert_not_called()

    def test_selection_comparison_and_source_downloads_reject_stale_identity(self):
        query = '?datasetId=gwas-' + 'f' * 64
        with mock.patch('app.routes.get_dashboard_filter_store') as store:
            for path in ('/json/filtered-dashboard.json', '/download/filtered-dashboard.zip',
                         '/reports/filtered-dashboard'):
                response = self.client.get(path + query + '&funders=nih')
                self.assertEqual(response.status_code, 409, path)
            response = self.client.get('/getCSV/bubble_df' + query)
            self.assertEqual(response.status_code, 409)
            response = self.client.post('/api/comparison', json={
                'left': {}, 'right': {}, 'datasetId': 'gwas-' + 'f' * 64,
            })
            self.assertEqual(response.status_code, 409)
            store.assert_not_called()

    def test_filtered_file_identity_header_does_not_rewrite_scientific_json(self):
        selected = self.data / 'selection.json'
        content = b'{"selection":{"funders":[]},"summary":{}}'
        selected.write_bytes(content)
        store = mock.Mock()
        store.dashboard_path.return_value = str(selected)
        with mock.patch('app.routes.get_dashboard_filter_store', return_value=store):
            response = self.client.get('/json/filtered-dashboard.json?funders=nih&datasetId=' + self.identifier)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, content)
        self.assertEqual(response.headers['X-GWAS-Dataset-ID'], self.identifier)
        response.close()
        self.assertEqual(selected.read_bytes(), content)


class ProvenanceLifecycleTests(ProvenanceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(generate_data, 'diversity_logger', mock.Mock(), create=True)
        patch.start()
        self.addCleanup(patch.stop)
        generate_data._record_runtime_status(
            self.data, lastSuccessfulRunAt=EARLIER, lastSuccessfulFetchAt=EARLIER,
        )

    def run_generator(self):
        return generate_data.generate_and_publish(str(self.root), 'https://example.test/', generation_year=2026)

    def test_unchanged_updates_run_heartbeat_without_touching_published_manifest(self):
        before = self.manifest_path.read_bytes()
        paths = generate_data._generation_paths(self.data)
        with mock.patch.object(generate_data, '_resume_publication_if_needed', return_value=False), \
                mock.patch.object(generate_data, '_staged_state_valid', return_value=False), \
                mock.patch.object(generate_data, '_prepare_generation_workspace', return_value=(paths, {})), \
                mock.patch.object(generate_data, '_completion_state_valid', return_value=True), \
                mock.patch.object(generate_data, '_cleanup_committed_publication'), \
                mock.patch.object(generate_data, 'utc_now', return_value=LATER):
            self.assertEqual(self.run_generator(), 'unchanged')
        result = Provenance.runtime_status(self.data)
        self.assertEqual(result['lastSuccessfulRunAt'], LATER)
        self.assertEqual(result['lastSuccessfulFetchAt'], EARLIER)
        self.assertEqual(result['lastRunOutcome'], 'unchanged')
        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_resumed_publication_does_not_claim_an_upstream_check(self):
        with mock.patch.object(generate_data, '_resume_publication_if_needed', return_value=True), \
                mock.patch.object(generate_data, '_prepare_generation_workspace') as prepare, \
                mock.patch.object(generate_data, 'utc_now', return_value=LATER):
            self.assertEqual(self.run_generator(), 'resumed')
            prepare.assert_not_called()
        result = Provenance.runtime_status(self.data)
        self.assertEqual(result['lastSuccessfulRunAt'], LATER)
        self.assertEqual(result['lastSuccessfulFetchAt'], EARLIER)

    def test_fetch_is_recorded_only_after_all_raw_inputs_pass_validation(self):
        with zipfile.ZipFile(self.root / 'data_static.zip', 'w'):
            pass
        with mock.patch.object(generate_data, 'download_cat', return_value=sources()), \
                mock.patch.object(generate_data, '_validate_raw_inputs', side_effect=ValueError('partial snapshot')):
            with self.assertRaisesRegex(ValueError, 'partial snapshot'):
                generate_data._initialize_generation_workspace(str(self.root), str(self.data), 'https://example.test/')
        self.assertEqual(Provenance.runtime_status(self.data)['lastSuccessfulFetchAt'], EARLIER)
        with mock.patch.object(generate_data, 'download_cat', return_value=sources()), \
                mock.patch.object(generate_data, '_validate_raw_inputs', return_value={}), \
                mock.patch.object(generate_data, 'utc_now', return_value=LATER):
            paths, _ = generate_data._initialize_generation_workspace(str(self.root), str(self.data), 'https://example.test/')
        self.assertEqual(Provenance.runtime_status(self.data)['lastSuccessfulFetchAt'], LATER)
        state = json.loads(Path(paths['raw_state']).read_text())
        self.assertEqual(state['fetch_completed_at'], LATER)
        self.assertEqual(len(state['sources']), 4)

    def test_failed_generation_after_fetch_preserves_last_successful_run_and_error(self):
        def failing_generation(*args):
            generate_data._record_runtime_status(self.data, lastSuccessfulFetchAt=LATER)
            raise RuntimeError('sensitive diagnostic must not enter public status')
        with mock.patch.object(generate_data, '_generate_locked', side_effect=failing_generation), \
                mock.patch.object(generate_data, 'utc_now', return_value=LATER):
            with self.assertRaisesRegex(RuntimeError, 'sensitive diagnostic'):
                self.run_generator()
        status = Provenance.runtime_status(self.data)
        self.assertEqual(status['lastRunStatus'], 'failed')
        self.assertEqual(status['lastSuccessfulRunAt'], EARLIER)
        self.assertEqual(status['lastSuccessfulFetchAt'], LATER)
        self.assertEqual(status['lastErrorType'], 'RuntimeError')
        self.assertNotIn('sensitive', json.dumps(status))

    def test_status_write_failure_never_changes_success_or_original_failure(self):
        with mock.patch.object(generate_data, '_atomic_write_json', side_effect=OSError('read only')), \
                mock.patch.object(generate_data, '_generate_locked', return_value='unchanged'):
            self.assertEqual(self.run_generator(), 'unchanged')
        with mock.patch.object(generate_data, '_atomic_write_json', side_effect=OSError('read only')), \
                mock.patch.object(generate_data, '_generate_locked', side_effect=ValueError('original')):
            with self.assertRaisesRegex(ValueError, 'original'):
                self.run_generator()

    def test_publication_status_is_written_only_after_verified_commit(self):
        paths = generate_data._generation_paths(str(self.data))
        Path(paths['workspace']).mkdir(parents=True)
        state = manifest()
        Path(paths['staged_state']).write_text(json.dumps(state))
        with mock.patch.object(generate_data, '_staged_state_valid', return_value=True), \
                mock.patch.object(generate_data, '_create_previous_release_snapshot', return_value=None), \
                mock.patch.object(generate_data, '_atomic_copy'), \
                mock.patch.object(generate_data, '_cleanup_committed_publication'), \
                mock.patch.object(generate_data, '_completion_state_valid', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'completion-state validation'):
                generate_data._publish_staged_release(str(self.root), str(self.data), paths)
        self.assertIsNone(Provenance.runtime_status(self.data)['lastPublicationAt'])
        self.assertTrue(Path(paths['publication']).exists())
        with mock.patch.object(generate_data, '_staged_state_valid', return_value=True), \
                mock.patch.object(generate_data, '_atomic_copy'), \
                mock.patch.object(generate_data, '_cleanup_committed_publication'), \
                mock.patch.object(generate_data, '_completion_state_valid', return_value=True), \
                mock.patch.object(generate_data, 'utc_now', return_value=LATER):
            generate_data._publish_staged_release(str(self.root), str(self.data), paths)
        self.assertFalse(Path(paths['publication']).exists())
        status = Provenance.runtime_status(self.data)
        self.assertEqual(status['lastPublicationAt'], LATER)
        self.assertEqual(status['lastPublicationDatasetId'], Provenance.dataset_identity(state))

    def test_concurrent_run_rejection_cannot_overwrite_active_run_status(self):
        before = Provenance.runtime_status(self.data)
        with generate_data._generation_lock(str(self.data)):
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                self.run_generator()
        self.assertEqual(Provenance.runtime_status(self.data), before)

    def test_completion_manifest_preserves_acquisition_evidence_without_identity_churn(self):
        validation = manifest()
        with mock.patch.object(generate_data, '_implementation_fingerprints', return_value={}), \
                mock.patch.object(generate_data, '_generation_parameters', return_value={'final_year': 2026}):
            first = generate_data._build_completion_state(str(self.root), validation, fingerprint(), {
                'sources': sources(), 'fetch_completed_at': EARLIER,
            })
            second = generate_data._build_completion_state(str(self.root), validation, fingerprint(), {
                'sources': sources(), 'fetch_completed_at': LATER,
            })
        self.assertEqual(first['provenance']['datasetId'], second['provenance']['datasetId'])
        self.assertEqual(Provenance.manifest_provenance(first)['catalogReleaseDate'], '2026-09-01')
        self.assertEqual(first['provenance']['fetchCompletedAt'], EARLIER)

    def test_fallback_preserves_verified_dependencies_identity_coverage_and_comparison(self):
        for relative in DataLoader.RUNTIME_DATA_FILES:
            path = self.data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith('.zip'):
                with zipfile.ZipFile(path, 'w'):
                    pass
            else:
                path.write_text('{}' if relative.endswith('.json') else 'Example\n')
        contents = {
            'catalog/raw/Cat_Stud.tsv': 'STUDY ACCESSION\tPUBMEDID\tCOHORT\tDATE\tASSOCIATION COUNT\nA\t1\tAlpha\t2020-01-01\t1\n',
            'catalog/raw/Cat_Map.tsv': 'DISEASE/TRAIT\tEFO URI\nExample\texample\n',
            'catalog/synthetic/Cat_Anc_wBroader.tsv': 'STUDY ACCESSION\tSTAGE\tBroader\tN\nA\tinitial\tEuropean\t10\n',
            'support/Country_Lookup.csv': 'Country\nExample\n',
            'support/cohort_cleaner.json': '{}',
            'funders/funder_cleaner.json': '{}',
            'funders/pubmed_grants.json': json.dumps({'records': {'1': {'grants': [{'agency': 'Example Funder'}]}}}),
        }
        for relative, content in contents.items():
            path = self.data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        value = manifest()
        value['artifact_fingerprints'] = {
            relative: fingerprint((self.data / relative).read_bytes())
            for relative in (*DataLoader.RUNTIME_DATA_FILES, *DataLoader.FILTER_RUNTIME_FILES)
        }
        self.manifest_path.write_text(json.dumps(value))
        original_id = Provenance.published_provenance(self.data)['datasetId']
        paths = generate_data._generation_paths(str(self.data))
        generate_data._create_previous_release_snapshot(str(self.root), str(self.data), paths)
        fallback = Path(paths['fallback_data'])
        self.assertEqual(Provenance.published_provenance(fallback)['datasetId'], original_id)
        for relative in DataLoader.FILTER_RUNTIME_FILES:
            self.assertEqual((fallback / relative).read_bytes(), (self.data / relative).read_bytes())
            self.assertEqual((fallback / relative).stat().st_ino, (self.data / relative).stat().st_ino)
        with mock.patch('app.routes.DataLoader.published_data_lock',
                        side_effect=lambda: contextlib.nullcontext(str(fallback))):
            client = app.test_client()
            coverage = client.get('/api/provenance?coverage=1&datasetId=' + original_id)
            self.assertEqual(coverage.status_code, 200)
            self.assertEqual(coverage.json['coverage']['funder_linked_study_percentage'], 100)
            self.assertEqual(coverage.json['coverage']['cohort_linked_study_percentage'], 100)
            comparison = client.post('/api/comparison', json={
                'left': {}, 'right': {}, 'datasetId': original_id,
            })
            self.assertEqual(comparison.status_code, 200)
            self.assertEqual(comparison.json['left']['participantCount'], 10)
            self.assertEqual(comparison.json['datasetId'], original_id)
            (fallback / 'catalog/raw/Cat_Stud.tsv').unlink()
            missing = client.post('/api/comparison', json={'left': {}, 'right': {}, 'datasetId': original_id})
            self.assertEqual(missing.status_code, 503)
            self.assertIn('temporarily unavailable', missing.json['error'])


if __name__ == '__main__':
    unittest.main()
