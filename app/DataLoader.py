import contextlib
from collections import OrderedDict
import csv
import fcntl
import hashlib
import json
import os
import threading
import time


PUBLICATION_CONTROL_DIRECTORY = '.generate_data'
PUBLICATION_LOCK_FILE = 'publication.lock'
PUBLICATION_MARKER_FILE = 'publication-in-progress.json'
PUBLICATION_FALLBACK_DIRECTORY = 'previous-release'
GENERATION_STATE_FILE = '.generation_complete.json'

# A published trait list is reused by every search. Keep only immutable rows
# between requests, and version them by the actual file rather than the loader
# instance so new releases and publication fallbacks cannot reuse stale names.
TRAIT_INDEX_CACHE_LIMIT = 4
_trait_index_cache = OrderedDict()
_trait_index_lock = threading.RLock()


def _trait_index(path):
    path = os.path.abspath(path)
    stat = os.stat(path)
    signature = (
        path, stat.st_dev, stat.st_ino, stat.st_size,
        stat.st_mtime_ns, stat.st_ctime_ns,
    )
    with _trait_index_lock:
        cached = _trait_index_cache.get(signature)
        if cached is not None:
            _trait_index_cache.move_to_end(signature)
            return cached
        with open(path) as source:
            # dict preserves the old duplicate handling and insertion order.
            names = dict.fromkeys(source.read().splitlines())
        cached = tuple(
            (name, name.lower().replace(" ", "-")
             .replace("(", "").replace(")", ""), name.lower())
            for name in names
        )
        _trait_index_cache[signature] = cached
        while len(_trait_index_cache) > TRAIT_INDEX_CACHE_LIMIT:
            _trait_index_cache.popitem(last=False)
        return cached

# The fallback must support on-demand selections/comparisons as well as plots.
FILTER_RUNTIME_FILES = (
    'catalog/raw/Cat_Stud.tsv',
    'catalog/raw/Cat_Map.tsv',
    'catalog/synthetic/Cat_Anc_wBroader.tsv',
    'support/Country_Lookup.csv',
    'support/cohort_cleaner.json',
    'funders/pubmed_grants.json',
    'funders/funder_cleaner.json',
)

TOPLOT_RUNTIME_FILES = (
    'ancestries.json', 'ancestriesOrdered.json', 'bubbleGraph.json',
    'bubble_df.csv', 'chloroMap.json', 'choro_df.csv',
    'doughnutGraph.json', 'doughnut_df.csv', 'heatMap.json',
    'heatmap_count_initial.csv', 'heatmap_count_replication.csv',
    'heatmap_sum_initial.csv', 'heatmap_sum_replication.csv',
    'parentTerms.json', 'summary.json', 'traits.json',
    'ts1_initial_count.csv', 'ts1_initial_sum.csv',
    'ts1_replication_count.csv', 'ts1_replication_sum.csv',
    'ts2_initial_count.csv', 'ts2_initial_sum.csv',
    'ts2_replication_count.csv', 'ts2_replication_sum.csv', 'tsPlot.json',
)

RUNTIME_DATA_FILES = (
    'summary/uniq_broader.txt',
    'summary/summary.json',
    'summary/uniq_dis_trait.txt',
    'summary/uniq_parent.txt',
    *(f'toplot/{file_name}' for file_name in TOPLOT_RUNTIME_FILES),
    'todownload/gwasdiversitymonitor_download.zip',
    'todownload/heatmap.zip',
    'todownload/timeseries.zip',
)


class PublishedDataUnavailable(RuntimeError):
    """Raised when an interrupted publication has no safe fallback."""


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def runtime_release_ready(data_path):
    """Check every app-consumed file against its published manifest."""
    try:
        with open(os.path.join(data_path, GENERATION_STATE_FILE),
                  encoding='utf-8') as state_file:
            state = json.load(state_file)
        artifacts = state['artifact_fingerprints']
        for relative_path in RUNTIME_DATA_FILES:
            expected = artifacts[relative_path]
            path = os.path.join(data_path, relative_path)
            if not os.path.isfile(path):
                return False
            if os.path.getsize(path) != expected['size']:
                return False
            if _sha256_file(path) != expected['sha256']:
                return False
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return True


@contextlib.contextmanager
def published_data_lock(data_path='data', exclusive=False):
    """Lock a coherent published release and yield the path to read.

    A writer briefly takes the exclusive form at each release transition. If
    it is killed between file replacements, the persistent publication marker
    makes readers use the complete previous-release snapshot until recovery
    finishes.
    """
    data_path = os.path.abspath(data_path)
    control_path = os.path.join(data_path, PUBLICATION_CONTROL_DIRECTORY)
    os.makedirs(control_path, exist_ok=True)
    lock_path = os.path.join(control_path, PUBLICATION_LOCK_FILE)

    with open(lock_path, 'a+', encoding='utf-8') as lock_file:
        lock_mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock_file.fileno(), lock_mode)
        try:
            published_path = data_path
            marker_path = os.path.join(
                control_path, PUBLICATION_MARKER_FILE
            )
            if not exclusive and os.path.isfile(marker_path):
                try:
                    with open(marker_path, encoding='utf-8') as marker_file:
                        marker = json.load(marker_file)
                    fallback_relative = marker.get('fallback_data')
                    if not fallback_relative:
                        raise PublishedDataUnavailable(
                            'Data publication is being recovered'
                        )
                    fallback_path = os.path.abspath(os.path.join(
                        data_path, fallback_relative
                    ))
                    expected_fallback_path = os.path.join(
                        control_path, PUBLICATION_FALLBACK_DIRECTORY
                    )
                    if fallback_path != expected_fallback_path \
                            or not os.path.isdir(fallback_path):
                        raise PublishedDataUnavailable(
                            'The previous published release is unavailable'
                        )
                    published_path = fallback_path
                except (OSError, TypeError, ValueError, json.JSONDecodeError) \
                        as error:
                    raise PublishedDataUnavailable(
                        'Data publication is being recovered'
                    ) from error
            yield published_path
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class DataLoader:
    def __init__(self, data_path='data'):
        self.data_path = data_path

    def _path(self, *parts):
        return os.path.join(self.data_path, *parts)

    def getAncestriesList(self):
        data = []
        with open(self._path('summary', 'uniq_broader.txt')) as file:
            data = file.read().splitlines()
        ancestries = {}
        for ancestry in data:
            ancestries[ancestry] = ancestry.lower().replace(" ", "-").replace("/", "-")
        return ancestries
    def getAncestriesListOrder(self):
        data = []
        with open(self._path('summary', 'uniq_broader.txt')) as file:
            data = file.read().splitlines()
        ancestries = {}
        i = 1
        for ancestry in data:
            ancestries[i] = ancestry
            i = i + 1
        return ancestries
    def getTermsList(self):
        data = []
        with open(self._path('summary', 'uniq_parent.txt')) as file:
            data = file.read().splitlines()
        terms = {}
        for term in data:
            terms[term] = term.lower().replace(" ", "-")
        return terms
    def getTraitsList(self):
        return {
            name: identifier
            for name, identifier, _ in _trait_index(
                self._path('summary', 'uniq_dis_trait.txt')
            )
        }
    def getSummaryStatistics(self):
        summary = {}
        with open(self._path('summary', 'summary.json')) as json_file:
            data = json.load(json_file)
            for value in data:
                summary[value] = data[value]

        return summary

    def getBubbleGraph(self):
        dataInitial = {}
        dataReplication = {}

        with open(self._path('toplot', 'bubble_df.csv')) as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for index, source_row in enumerate(csv_reader):
                row = {
                    key: value for key, value in source_row.items()
                    if key and not key.startswith('Unnamed:')
                }
                if row.get("STAGE") == "replication":
                    dataReplication[index] = row
                elif row.get("STAGE") == "initial":
                    dataInitial[index] = row
        return {
            'bubblegraph_initial': dataInitial,
            'bubblegraph_replication': dataReplication,
        }
    def getDoughnutGraph(self, ancestryOrder):
        dataDiscoveryStudies = {}
        dataDiscoveryParticipants = {}
        dataReplicationStudies = {}
        dataReplicationParticipants = {}
        dataAssociations = {}
        with open(self._path('toplot', 'doughnut_df.csv')) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            line_count = 0
            for row in csv_reader:
                if line_count > 0:
                    year = row[3]
                    if year not in dataDiscoveryStudies:
                        dataDiscoveryStudies[year] = dict()
                        dataDiscoveryParticipants[year] = dict()
                        dataReplicationStudies[year] = dict()
                        dataReplicationParticipants[year] = dict()
                        dataAssociations[year] = dict()
                    term = row[2]
                    if term not in dataDiscoveryStudies[year]:
                        dataDiscoveryStudies[year][term] = dict()
                        dataDiscoveryParticipants[year][term] = dict()
                        dataReplicationStudies[year][term] = dict()
                        dataReplicationParticipants[year][term] = dict()
                        dataAssociations[year][term] = dict()
                    ancestry = row[1]
                    ancestryKey = list(ancestryOrder.keys())[list(ancestryOrder.values()).index(ancestry)]
                    dataDiscoveryStudies[year][term][ancestryKey] = {
                        "ancestry": row[1],
                        "value": row[5]
                    }
                    dataDiscoveryParticipants[year][term][ancestryKey] = {
                        "ancestry": row[1],
                        "value": row[4]
                    }
                    dataReplicationStudies[year][term][ancestryKey] = {
                        "ancestry": row[1],
                        "value": row[7]
                    }
                    dataReplicationParticipants[year][term][ancestryKey] = {
                        "ancestry": row[1],
                        "value": row[6]
                    }
                    dataAssociations[year][term][ancestryKey] = {
                        "ancestry": row[1],
                        "value": row[8]
                    }
                line_count += 1
        return {
            'doughnut_discovery_studies' : dataDiscoveryStudies,
            'doughnut_discovery_participants' : dataDiscoveryParticipants,
            'doughnut_replication_studies' : dataReplicationStudies,
            'doughnut_replication_participants' : dataReplicationParticipants,
            'doughnut_associations' : dataAssociations
        }
    def getHeatMap(self):
        return {
            'heatmap_discovery_studies' : self.getHeatMapData("heatmap_count_initial.csv"),
            'heatmap_replication_studies' : self.getHeatMapData("heatmap_count_replication.csv"),
            'heatmap_replication_participants' : self.getHeatMapData("heatmap_sum_replication.csv"),
            'heatmap_discovery_participants' : self.getHeatMapData("heatmap_sum_initial.csv"),
        }
    def getHeatMapData(self, filename):
        data = {}

        with open(self._path('toplot', filename)) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')

            line_count = 0
            i = 0
            year = 0
            for row in csv_reader:
                if line_count == 0:
                    keys = row
                    keys.remove("")
                    keys.remove("Year")
                else:

                    if row[len(row) - 1] != year:
                        year = row[len(row) - 1]
                        i = 0

                    if year not in data:
                        data[year] = dict()

                    j = 0
                    for value in row[1:len(row) - 1]:
                        data[year][i] = {
                            "ancestry" : row[0],
                            "term" : keys[j],
                            "value" : value,
                        }
                        j += 1
                        i += 1

                line_count += 1

        return data

    def getChloroMap(self):
        data = {'initial': {}, 'replication': {}}
        with open(self._path('toplot', 'choro_df.csv'), newline='') as csv_file:
            csv_reader = csv.DictReader(csv_file)
            has_stage = 'Stage' in (csv_reader.fieldnames or [])
            legacy_data = {}
            for row in csv_reader:
                year = row['Year']
                stage = str(row.get('Stage', '')).strip().casefold()
                target = data.setdefault(stage, {}) if has_stage \
                    else legacy_data
                year_rows = target.setdefault(year, {})
                index = len(year_rows)
                year_rows[index] = {
                    'country': row['Cleaned Country'],
                    'population': row['2017population'],
                    'studies': row['Count'],
                    'studiesPercentage': row['Count (%)'],
                    'participants': row['N'],
                    'participantsPercentage': row['N (%)'],
                }
        return data if has_stage else legacy_data

    def getTSPlot(self):
        return {
            'ts_notrecorded_discovery_studies' : self.getTSPlotData("ts1_initial_count.csv"),
            'ts_notrecorded_discovery_participants' : self.getTSPlotData("ts1_initial_sum.csv"),
            'ts_notrecorded_replication_studies' : self.getTSPlotData("ts1_replication_count.csv"),
            'ts_notrecorded_replication_participants' : self.getTSPlotData("ts1_replication_sum.csv"),
            'ts_recorded_discovery_studies' : self.getTSPlotData("ts2_initial_count.csv"),
            'ts_recorded_discovery_participants' : self.getTSPlotData("ts2_initial_sum.csv"),
            'ts_recorded_replication_studies' : self.getTSPlotData("ts2_replication_count.csv"),
            'ts_recorded_replication_participants' : self.getTSPlotData("ts2_replication_sum.csv"),
        }

    def getTSPlotData(self, filename):
        tsPlot = dict()
        with open(self._path('toplot', filename)) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            line_count = 0
            for row in csv_reader:
                if line_count == 0:
                    keys = row
                    keys.remove("index")
                    for key in keys:
                        tsPlot[key] = dict()
                else:
                    year = row[0]
                    i = 1;
                    for key in keys:
                        tsPlot[key][line_count-1] = {
                            'year' : year,
                            'value' : row[i]
                        }
                        i += 1
                line_count += 1
        return tsPlot

    def filterTraits(self, search_trait):
        search_trait = search_trait.lower()
        return [
            {"id": identifier, "text": name}
            for name, identifier, search_name in _trait_index(
                self._path('summary', 'uniq_dis_trait.txt')
            )
            if search_trait in search_name or search_trait in identifier
        ]
