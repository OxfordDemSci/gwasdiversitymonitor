import base64
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

import pandas as pd

from app import app
from app.DashboardFilters import DashboardFilterStore


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is required for browser export unit tests')
class DashboardExportTests(unittest.TestCase):
    def test_browser_snapshot_serialization_and_cancellation(self):
        result = subprocess.run(
            ['node', '--test', 'tests/js/dashboard-export.test.cjs',
             'tests/js/image-export.test.cjs'],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generated_zip_roundtrips_with_independent_python_reader(self):
        script = r"""
const api = require('./app/static/js/dashboard-export.js');
const snapshot = {id:'heatMap',title:'Heat map',rowCount:2,
 columns:[{key:'term',label:'Parent term',type:'text'},{key:'value',label:'Count',type:'number'}],
 rowAt:i=>[{term:'Café, "quoted"\nterm',value:12},{term:'=1+1',value:-2}][i],
 settings:{year:2020},methodology:['Counts are ancestry records.'],
 provenance:{datasetId:'gwas-'+'a'.repeat(64)},view:{metric:'studies'},
 exportedAt:'2026-10-03T00:00:00.000Z',shareUrl:'https://example.test/?view=1'};
api.bundle([snapshot],{yieldWork:async()=>{},image:{name:'heatMap.svg',
 blob:new Blob(['<svg xmlns="http://www.w3.org/2000/svg"/>'])}})
 .then(async blob=>process.stdout.write(Buffer.from(await blob.arrayBuffer()).toString('base64')));
"""
        result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True,
                                capture_output=True, check=True)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(result.stdout))) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {
                'heatMap.csv', 'view.json', 'provenance.json', 'CITATION.txt', 'README.txt', 'heatMap.svg',
            })
            csv = archive.read('heatMap.csv').decode('utf-8')
            self.assertIn('"Café, ""quoted""\nterm",12', csv)
            self.assertIn("'=1+1,-2", csv)
            view = json.loads(archive.read('view.json'))
            self.assertEqual(view['charts'][0]['settings']['year'], 2020)
            self.assertEqual(view['view']['metric'], 'studies')
            self.assertIn('ancestry records', archive.read('README.txt').decode())
            self.assertEqual(json.loads(archive.read('provenance.json'))['datasetId'], 'gwas-' + 'a' * 64)


class SelectionExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.provenance = {'version': 1, 'datasetId': 'gwas-' + 'a' * 64,
                           'generatedAt': '2026-10-03T00:00:00+00:00',
                           'catalogReleaseDate': '2026-10-02'}
        self.store = DashboardFilterStore(str(self.root))
        self.store._cache_root = str(self.root / 'cache')
        self.studies = pd.DataFrame([{'STUDY ACCESSION': 'A', 'PUBMEDID': '1'}])
        self.ancestry = pd.DataFrame([
            {'STUDY ACCESSION': 'A', 'STAGE': 'initial', 'N': 100},
            {'STUDY ACCESSION': 'A', 'STAGE': 'replication', 'N': 50},
        ])
        self.bubbles = pd.DataFrame([
            {'ACCESSION': 'A', 'STAGE': 'initial', 'N': 100, 'DATE': '2020-01-01'},
            {'ACCESSION': 'A', 'STAGE': 'replication', 'N': 50, 'DATE': '2021-01-01'},
        ])
        self.store._selection = mock.Mock(return_value=(
            [{'id': 'ukb', 'name': 'UKB'}], [], self.studies,
            self.ancestry, self.bubbles, None, None,
        ))
        self.store._publication_count = mock.Mock(return_value=1)
        self.store.dashboard = mock.Mock(return_value={
            'selection': {'cohorts': [{'id': 'ukb', 'name': 'UKB'}], 'funders': []},
        })
        self.store.report = mock.Mock(return_value={'studyCount': 1, 'publicationCount': 1})
        for target in ('app.DashboardFilters.published_provenance', 'app.routes.published_provenance'):
            patcher = mock.patch(target, side_effect=lambda _: dict(self.provenance))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch('app.routes.DataLoader.published_data_lock',
                             side_effect=lambda: contextlib.nullcontext(str(self.root)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = app.test_client()

    def test_source_zip_preserves_all_stages_and_adds_bound_identity_scope_and_citation(self):
        path = self.store.download_path(('ukb',), ())
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(json.loads(archive.read('provenance.json')), self.provenance)
            selection = json.loads(archive.read('selection.json'))
            self.assertEqual(selection['datasetId'], self.provenance['datasetId'])
            self.assertIn('Chart-specific filters are not applied', selection['scope'])
            self.assertIn('both discovery and replication', archive.read('README.txt').decode())
            self.assertIn('10.1038/s41588-020-0580-y', archive.read('CITATION.txt').decode())
            self.assertIn('10.5281/zenodo.3600472', archive.read('CITATION.txt').decode())
            self.assertEqual(archive.read('studies.tsv').decode(), self.studies.to_csv(sep='\t', index=False))
            self.assertEqual(archive.read('ancestry.tsv').decode(), self.ancestry.to_csv(sep='\t', index=False))
            self.assertIn('replication', archive.read('bubble_df.csv').decode())
            self.assertNotIn('view.json', archive.namelist())

    def test_source_cache_reuses_selection_across_chart_settings_but_not_dataset_ids(self):
        with mock.patch('app.routes.get_dashboard_filter_store', return_value=self.store):
            responses = []
            for year in (2020, 2021):
                response = self.client.get('/download/filtered-dashboard.zip', query_string={
                    'cohorts': 'ukb', 'datasetId': self.provenance['datasetId'],
                    'viewState': json.dumps({'cohorts': ['ukb'], 'heatMap': {'year': year}}),
                })
                self.assertEqual(response.status_code, 200)
                responses.append(response.data)
                response.close()
            self.assertEqual(responses[0], responses[1])
            self.store._selection.assert_called_once_with(('ukb',), ())
            first_path = self.store.download_path(('ukb',), ())
            self.provenance['datasetId'] = 'gwas-' + 'b' * 64
            second_path = self.store.download_path(('ukb',), ())
            self.assertNotEqual(first_path, second_path)
            self.assertEqual(self.store._selection.call_count, 2)
            with zipfile.ZipFile(second_path) as archive:
                self.assertEqual(json.loads(archive.read('provenance.json'))['datasetId'], self.provenance['datasetId'])

    def test_report_includes_escaped_view_context_without_changing_entity_report_scope(self):
        view = {'version': 1, 'cohorts': ['ukb'], 'funders': [], 'stage': 'replication',
                'bubble': {'traits': ['<script>alert(1)</script>']}}
        with mock.patch('app.routes.get_dashboard_filter_store', return_value=self.store):
            response = self.client.get('/reports/filtered-dashboard', query_string={
                'cohorts': 'ukb', 'datasetId': self.provenance['datasetId'], 'viewState': json.dumps(view),
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.provenance['datasetId'].encode(), response.data)
        self.assertIn(b'context only', response.data)
        self.assertIn(b'do not restrict the calculations', response.data)
        self.assertNotIn(b'<script>alert(1)</script>', response.data)
        self.assertIn(b'\\u003cscript\\u003e', response.data)
        self.store.report.assert_called_once_with(('ukb',), ())

    def test_report_rejects_mismatched_unbounded_or_malformed_context_before_loading_data(self):
        invalid = [
            '[1]', '{', json.dumps({'cohorts': ['different']}),
            json.dumps({'cohorts': ['ukb'], 'unexpected': True}),
            json.dumps({'cohorts': ['ukb'], 'bubble': {'traits': ['x'] * 51}}),
            json.dumps({'cohorts': ['ukb'], 'bubble': {'traits': ['x' * 501]}}),
            json.dumps({'cohorts': ['ukb'], 'bubble': {'traits': ['bad\ntext']}}),
            json.dumps({'cohorts': ['ukb'], 'worldMap': {'zoom': float('nan')}}),
            '{' + ' ' * 4096 + '}',
        ]
        with mock.patch('app.routes.get_dashboard_filter_store') as store:
            for value in invalid:
                with self.subTest(value=value[:60]):
                    response = self.client.get('/reports/filtered-dashboard', query_string={
                        'cohorts': 'ukb', 'viewState': value,
                    })
                    self.assertEqual(response.status_code, 400)
            store.assert_not_called()


if __name__ == '__main__':
    unittest.main()
