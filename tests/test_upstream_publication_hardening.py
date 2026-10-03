"""Malformed candidates must fail before publication, without changing live data."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import generate_data
from generated_data_validation import read_generated_json, validate_plot_payload


def bubble_payload():
    row = {'ACCESSION': 'GCSTTEST1', 'AUTHOR': None, 'Broader': 'New ancestry category',
           'DATE': '2026-01-01', 'DiseaseOrTrait': 'New trait', 'N': '12.5',
           'STAGE': 'initial', 'parentterm': 'New parent category'}
    columns = list(row)
    initial = {'columns': columns, 'dicts': {key: [value] for key, value in row.items()},
               'codes': {key: [0] for key in columns},
               'meta': {'rowCount': 1, 'maxN': 12.5, 'minDateMS': None, 'maxDateMS': None}}
    empty = {'columns': columns, 'dicts': {key: [] for key in columns},
             'codes': {key: [] for key in columns}, 'meta': {'rowCount': 0, 'maxN': 0}}
    return {'__format': 'dict_columnar_v2', 'bubblegraph_initial': initial,
            'bubblegraph_replication': empty}


def valid_payloads():
    series = {f'ts_{recorded}_{stage}_{metric}': {'New ancestry': {'7': {'year': '2026', 'value': '100'}}}
              for recorded in ('recorded', 'notrecorded') for stage in ('discovery', 'replication')
              for metric in ('studies', 'participants')}
    heat = {f'heatmap_{stage}_{metric}': {'2026': {'0': {'ancestry': 'New ancestry', 'term': 'New term', 'value': ''}}}
            for stage in ('discovery', 'replication') for metric in ('studies', 'participants')}
    doughnut = {f'doughnut_{stage}_{metric}': {'2026': {'All': [{'ancestry': 'New ancestry', 'value': None}]}}
                for stage in ('discovery', 'replication') for metric in ('studies', 'participants')}
    doughnut['doughnut_associations'] = {}
    country = {'country': 'New country', 'population': '', 'studies': '1', 'studiesPercentage': '100',
               'participants': 12.5, 'participantsPercentage': 100}
    return {'bubbleGraph.json': bubble_payload(), 'tsPlot.json': series, 'heatMap.json': heat,
            'doughnutGraph.json': doughnut, 'chloroMap.json': {'initial': {'2026': [country]}, 'replication': {}},
            'summary.json': {'number_studies': 1, 'number_accessions': 1, 'average_associations': 0},
            'ancestries.json': {'New ancestry': 'new-ancestry'}, 'ancestriesOrdered.json': {'1': 'New ancestry'},
            'parentTerms.json': {'New term': 'new-term'}, 'traits.json': {'New trait': 'new-trait'}}


class GeneratedJsonValidationTests(unittest.TestCase):
    def test_strict_reader_rejects_constants_overflow_and_duplicate_keys(self):
        invalid = ('{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}',
                   '{"value":1e999}', '{"value":-1e999}', '{"value":' + '9' * 310 + '}',
                   '{"nested":{"value":1,"value":2}}', '{"value":1} trailing')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candidate.json'
            for contents in invalid:
                with self.subTest(contents=contents[:60]):
                    path.write_text(contents, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        read_generated_json(path)
            path.write_text('{"value":1e308,"metadata":null,"label":"NaN"}', encoding='utf-8')
            self.assertEqual(read_generated_json(path), {'value': 1e308, 'metadata': None, 'label': 'NaN'})

    def test_all_supported_plot_shapes_preserve_categories_and_missing_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            for filename, payload in valid_payloads().items():
                with self.subTest(filename=filename):
                    path = Path(directory) / filename
                    original = json.dumps(payload, allow_nan=False)
                    path.write_text(original, encoding='utf-8')
                    loaded = read_generated_json(path)
                    validate_plot_payload('toplot/' + filename, loaded)
                    self.assertEqual(json.dumps(loaded), original)
        legacy_map = valid_payloads()['chloroMap.json']['initial']
        validate_plot_payload('toplot/chloroMap.json', legacy_map)
        encoded = bubble_payload()['bubblegraph_initial']
        row = {column: encoded['dicts'][column][0] for column in encoded['columns']}
        validate_plot_payload('toplot/bubbleGraph.json', {
            'bubblegraph_initial': {'42': row}, 'bubblegraph_replication': []})

    def test_nonempty_wrong_shape_is_not_a_valid_plot(self):
        for filename in valid_payloads():
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    validate_plot_payload('toplot/' + filename, {'unexpected': [1]})

    def test_encoded_bubble_columns_counts_and_dictionary_codes_are_consistent(self):
        mutations = (
            lambda s: s['meta'].update(rowCount=True),
            lambda s: s['meta'].update(rowCount=-1),
            lambda s: s['meta'].update(rowCount=2),
            lambda s: s['columns'].append('N'),
            lambda s: s['dicts'].update(unlisted=['x']),
            lambda s: s['codes'].pop('N'),
            lambda s: s['codes'].update(N=[1]),
            lambda s: s['codes'].update(N=[-1]),
            lambda s: s['codes'].update(N=[True]),
            lambda s: s['codes'].update(N=[0.0]),
            lambda s: s['dicts'].update(N=['Infinity']),
            lambda s: s['dicts'].update(N=['1e999']),
            lambda s: s['dicts'].update(STAGE=['replication']),
            lambda s: s['dicts'].update(Broader=[{'wrong': 'shape'}]),
            lambda s: s['dicts'].update(Broader=[23]),
            lambda s: s['dicts'].update(ACCESSION=[None]),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                payload = bubble_payload()
                mutate(payload['bubblegraph_initial'])
                with self.assertRaises(ValueError):
                    validate_plot_payload('toplot/bubbleGraph.json', payload)
        payload = bubble_payload()
        payload['__format'] = 'unknown-v99'
        with self.assertRaises(ValueError):
            validate_plot_payload('toplot/bubbleGraph.json', payload)

    def test_chart_numeric_fields_reject_nonfinite_strings_and_wrong_types(self):
        for invalid in ('NaN', 'Infinity', '-Infinity', '1e999', True, [], {}):
            for filename in ('tsPlot.json', 'heatMap.json', 'doughnutGraph.json', 'chloroMap.json'):
                with self.subTest(filename=filename, value=invalid):
                    payload = valid_payloads()[filename]
                    if filename == 'tsPlot.json':
                        payload['ts_recorded_discovery_studies']['New ancestry']['7']['value'] = invalid
                    elif filename == 'heatMap.json':
                        payload['heatmap_discovery_studies']['2026']['0']['value'] = invalid
                    elif filename == 'doughnutGraph.json':
                        payload['doughnut_discovery_studies']['2026']['All'][0]['value'] = invalid
                    else:
                        payload['initial']['2026'][0]['participants'] = invalid
                    with self.assertRaises(ValueError):
                        validate_plot_payload(filename, payload)

    def test_mixed_map_schema_and_missing_chart_modes_are_rejected(self):
        payload = valid_payloads()['chloroMap.json']
        payload['2026'] = payload['initial']['2026']
        with self.assertRaisesRegex(ValueError, 'mixes stage and year'):
            validate_plot_payload('chloroMap.json', payload)
        for filename in ('tsPlot.json', 'heatMap.json', 'doughnutGraph.json'):
            payload = valid_payloads()[filename]
            payload.pop(next(iter(payload)))
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'missing fields'):
                validate_plot_payload(filename, payload)

    def test_validation_module_has_no_runtime_application_or_dataframe_dependency(self):
        repository = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-c',
                                 'import sys; import generated_data_validation; '
                                 'assert "app" not in sys.modules; assert "pandas" not in sys.modules'],
                                cwd=repository, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


class CandidatePublicationSafetyTests(unittest.TestCase):
    def test_nonfinite_atomic_json_write_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            original = b'{"previous":"complete"}\n'
            path.write_bytes(original)
            for invalid in (float('nan'), float('inf'), -float('inf')):
                with self.subTest(value=invalid), self.assertRaises(ValueError):
                    generate_data._atomic_write_json(path, {'nested': [invalid]})
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual([p.name for p in Path(directory).iterdir()], ['state.json'])

    def test_malformed_generated_candidates_never_enter_publication(self):
        malformed_code = bubble_payload()
        malformed_code['bubblegraph_initial']['codes']['N'] = [99]
        cases = ('{"bad":NaN}', '{"bad":1e999}', '{"bad":1,"bad":2}',
                 '{"unexpected":[1]}', json.dumps(malformed_code))
        for contents in cases:
            with self.subTest(contents=contents[:60]), tempfile.TemporaryDirectory() as directory:
                root, data = Path(directory), Path(directory) / 'data'
                live = data / 'toplot/bubbleGraph.json'
                live.parent.mkdir(parents=True)
                live.write_text(json.dumps(bubble_payload()), encoding='utf-8')
                manifest = data / generate_data.GENERATION_STATE_FILE
                manifest.write_text('{"previousRelease":"verified"}', encoding='utf-8')
                previous = {path: path.read_bytes() for path in (live, manifest)}
                paths = generate_data._generation_paths(str(data))
                candidate = Path(paths['workspace_data']) / 'toplot/bubbleGraph.json'
                candidate.parent.mkdir(parents=True)
                candidate.write_text(contents, encoding='utf-8')
                Path(paths['raw_state']).write_text('{"generation_failures":0}', encoding='utf-8')

                def validate_candidate(workspace, _bundle):
                    validate_plot_payload('toplot/bubbleGraph.json', read_generated_json(
                        Path(workspace) / 'toplot/bubbleGraph.json'))
                    self.fail('Invalid candidate unexpectedly passed validation')

                with mock.patch.object(generate_data, 'diversity_logger', mock.Mock(), create=True), \
                        mock.patch.object(generate_data, 'final_year', 2026, create=True), \
                        mock.patch.object(generate_data, '_resume_publication_if_needed', return_value=False), \
                        mock.patch.object(generate_data, '_staged_state_valid', return_value=False), \
                        mock.patch.object(generate_data, '_prepare_generation_workspace', return_value=(paths, {})), \
                        mock.patch.object(generate_data, '_completion_state_valid', return_value=False), \
                        mock.patch.object(generate_data, '_reset_workspace_for_wrangling', return_value={}), \
                        mock.patch.object(generate_data, '_release_timeupdated', return_value='2026-01-01'), \
                        mock.patch.object(generate_data, '_run_wrangling'), \
                        mock.patch.object(generate_data, '_run_funder_wrangling'), \
                        mock.patch.object(generate_data, 'validate_generated_release', side_effect=validate_candidate), \
                        mock.patch.object(generate_data, '_publish_staged_release') as publish:
                    with self.assertRaises(ValueError):
                        generate_data.generate_and_publish(root, 'https://unused.test/', generation_year=2026)
                    publish.assert_not_called()
                self.assertEqual({path: path.read_bytes() for path in previous}, previous)
                self.assertFalse(Path(paths['publication']).exists())
                self.assertFalse(Path(paths['staged_state']).exists())
                self.assertEqual(read_generated_json(paths['raw_state'])['generation_failures'], 1)
                status = generate_data.runtime_status(data)
                self.assertEqual(status['lastRunStatus'], 'failed')
                self.assertEqual(status['lastErrorType'], 'ValueError')


if __name__ == '__main__':
    unittest.main()
