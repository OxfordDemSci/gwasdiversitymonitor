"""Deterministic, synthetic GWAS release; never reads or writes checkout data."""
import hashlib
import json
import os
from pathlib import Path
import sys
import types
import zipfile


REPOSITORY = Path(__file__).resolve().parents[2]
ANCESTRIES = ['European', 'Asian', 'African', 'African American or Afro-Caribbean',
              'Hispanic or Latin American', 'Other/Mixed']
TERMS = ['cardiovascular disease', 'metabolic disease']
TRAITS = ['Fixture blood pressure', 'Fixture glucose']
FIXED_TIME = '2026-01-01T00:00:00+00:00'


def configure(workspace):
    """Import real application code without a developer's ignored config.py."""
    sys.path.insert(0, str(REPOSITORY))
    config = types.ModuleType('config')
    config.DEBUG = False
    config.TESTING = False
    config.GOATCOUNTER_URL = ''
    config.STATIC_GZIP_CACHE_DIRECTORY = str(Path(workspace) / 'static-gzip')
    sys.modules['config'] = config
    # Default caches live beneath each store's isolated data directory. Do not
    # inherit a developer's override, or force one onto independent unit fixtures.
    os.environ.pop('GWAS_DASHBOARD_FILTER_CACHE', None)
    os.environ['GOATCOUNTER_URL'] = ''


def build_fixture(workspace):
    """Return expected metadata for a complete, small, manifest-validated release."""
    import pandas as pd
    from app import DataLoader
    from app.Provenance import dataset_identity
    import funder_pipeline as pipeline

    workspace = Path(workspace).resolve()
    if workspace == REPOSITORY or REPOSITORY in workspace.parents:
        raise ValueError('Fixtures must live outside the checkout, in a temporary workspace')
    root = workspace / 'data'
    if root.exists():
        raise ValueError('Refusing to overwrite an existing fixture directory')
    root.mkdir()

    def write(relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def write_json(relative, value):
        write(relative, json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')

    studies, ancestry, bubbles, funding = [], [], [], {}
    for index in range(72):
        accession, pmid = 'GCSTFIX%04d' % index, str(1000 + index // 2)
        date = '%d-%02d-15' % (2020 + index % 2, 1 + index % 12)
        cohort = 'Alpha|Beta' if index % 3 == 0 else 'Alpha' if index % 3 == 1 else 'Beta'
        groups = []
        if int(pmid) % 2 == 0:
            groups.append({'agency': 'Funder One', 'grant_id': 'FIXTURE-ONE'})
        if int(pmid) % 3 == 0:
            groups.append({'agency': 'Funder Two', 'grant_id': 'FIXTURE-TWO'})
        funding[pmid] = {'grants': groups}
        studies.append({'STUDY ACCESSION': accession, 'PUBMEDID': pmid, 'DATE': date,
                        'COHORT': cohort, 'DISEASE/TRAIT': TRAITS[index % 2],
                        'ASSOCIATION COUNT': 1 + index % 4, 'JOURNAL': 'Synthetic fixture journal',
                        'GENOTYPING TECHNOLOGY': 'Genome-wide genotyping array',
                        'FULL SUMMARY STATISTICS': 'yes'})
        for stage in ('initial', 'replication'):
            broader = ANCESTRIES[index % 6]
            count = 100 + index if stage == 'initial' else 20 + index
            ancestry.append({'STUDY ACCESSION': accession, 'PUBMEDID': pmid, 'DATE': date,
                             'STAGE': stage, 'Broader': broader, 'N': count,
                             'COUNTRY OF RECRUITMENT': 'United Kingdom' if index % 2 else 'United States'})
            bubbles.append({'ACCESSION': accession, 'PUBMEDID': pmid, 'DATE': date,
                            'AUTHOR': 'Fixture author', 'STAGE': stage, 'Broader': broader, 'N': count,
                            'DiseaseOrTrait': TRAITS[index % 2], 'parentterm': TERMS[index % 2],
                            'COHORT': cohort, 'FUNDER': ' | '.join(item['agency'] for item in groups),
                            'JOURNAL': 'Synthetic fixture journal'})
    studies = pd.DataFrame(studies)
    ancestry = pd.DataFrame(ancestry)
    bubbles = pd.DataFrame(bubbles)
    mappings = pd.DataFrame({'Disease trait': TRAITS, 'Parent term': TERMS})
    countries = pd.DataFrame({'Country': ['United Kingdom', 'United States'],
                              '2017population': [66000000, 325000000]})
    for relative, frame, separator in (
            ('catalog/raw/Cat_Stud.tsv', studies, '\t'),
            ('catalog/raw/Cat_Map.tsv', mappings, '\t'),
            ('catalog/synthetic/Cat_Anc_wBroader.tsv', ancestry, '\t'),
            ('support/Country_Lookup.csv', countries, ','),
            ('toplot/bubble_df.csv', bubbles, ',')):
        write(relative, frame.to_csv(index=False, sep=separator))
    write_json('funders/pubmed_grants.json', {'records': funding})
    write_json('funders/funder_cleaner.json', {})
    write_json('support/cohort_cleaner.json', {})
    write('summary/uniq_broader.txt', '\n'.join(ANCESTRIES) + '\n')
    write('summary/uniq_parent.txt', '\n'.join(TERMS) + '\n')
    write('summary/uniq_dis_trait.txt', '\n'.join(TRAITS) + '\n')

    merged = pipeline.build_study_parent_map(studies, mappings).merge(ancestry, on='STUDY ACCESSION')
    merged['Year'] = pd.to_datetime(merged['DATE']).dt.year
    summary = pipeline.build_summary(ancestry)
    summary.update({'total_' + key: value for key, value in summary['overallParticipants'].items()})
    summary.update({'number_studies': len(studies), 'number_accessions': len(studies),
                    'number_diseasestraits': 2, 'number_mappedtrait': 2,
                    'found_associations': int(studies['ASSOCIATION COUNT'].sum()),
                    'average_associations': float(studies['ASSOCIATION COUNT'].mean())})
    loader = DataLoader.DataLoader(str(root))
    plots = {
        'ancestries': loader.getAncestriesList(), 'ancestriesOrdered': loader.getAncestriesListOrder(),
        'parentTerms': loader.getTermsList(), 'traits': loader.getTraitsList(),
        'bubbleGraph': pipeline.build_bubble_payload(bubbles),
        'tsPlot': pipeline.build_time_series(ancestry, ANCESTRIES, 2021),
        'heatMap': pipeline.build_heat_map(merged, ANCESTRIES, TERMS, 2021),
        'doughnutGraph': pipeline.build_doughnut(merged, ANCESTRIES, TERMS, 2021),
        'chloroMap': pipeline.build_country_map(ancestry, countries, 2021), 'summary': summary,
    }
    for name, payload in plots.items():
        write_json('toplot/' + name + '.json', payload)
    write_json('summary/summary.json', summary)
    # Legacy downloadable aggregate CSVs are not chart inputs. Keep small valid
    # placeholders so production startup exercises its complete-file checks.
    for filename in DataLoader.TOPLOT_RUNTIME_FILES:
        if filename.endswith('.csv') and not (root / 'toplot' / filename).exists():
            write('toplot/' + filename, 'fixture_only\n1\n')
    for relative in ('todownload/gwasdiversitymonitor_download.zip', 'todownload/heatmap.zip',
                     'todownload/timeseries.zip'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(zipfile.ZipInfo('README.txt', (1980, 1, 1, 0, 0, 0)),
                             'Synthetic browser fixture; not research data.\n')

    def fingerprint(path):
        content = path.read_bytes()
        return {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}

    files = sorted(set(DataLoader.RUNTIME_DATA_FILES + DataLoader.FILTER_RUNTIME_FILES))
    manifest = {'version': 3, 'completed_at': FIXED_TIME,
                'generation_parameters': {'fixture': True, 'final_year': 2021},
                'artifact_fingerprints': {name: fingerprint(root / name) for name in files},
                'provenance': {'version': 1, 'fetchCompletedAt': FIXED_TIME, 'sources': []}}
    write_json(DataLoader.GENERATION_STATE_FILE, manifest)
    assert DataLoader.runtime_release_ready(str(root)), 'Synthetic release failed production validation'
    expected = {'datasetId': dataset_identity(manifest), 'studyCount': len(studies),
                'bubbleRowsPerStage': len(studies), 'years': [2020, 2021],
                'funders': ['funder-one', 'funder-two'], 'cohorts': ['alpha', 'beta']}
    (workspace / 'fixture.json').write_text(json.dumps(expected, indent=2), encoding='utf-8')
    return expected
