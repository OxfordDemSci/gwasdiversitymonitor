"""Generation-only checks before a candidate dataset can become a release.

This module deliberately has no application imports and never rewrites values.
Empty stages and existing blank/null missing-value markers remain supported.
"""
import json
import math
from pathlib import PurePosixPath


def _fail(where, message):
    raise ValueError(f'{where}: {message}')


def read_generated_json(path):
    """Read strict JSON, rejecting duplicate keys and non-finite JS numbers."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail(path, f'duplicate JSON key {key!r}')
            result[key] = value
        return result

    def number(token):
        value = float(token)
        if not math.isfinite(value):
            _fail(path, 'non-finite JSON number')
        return value

    def integer(token):
        if len(token.lstrip('-')) >= 309:
            number(token)
        return int(token)

    def constant(token):
        _fail(path, f'non-standard JSON constant {token}')

    with open(path, encoding='utf-8') as source:
        return json.load(source, object_pairs_hook=pairs, parse_float=number,
                         parse_int=integer, parse_constant=constant)


def _mapping(value, where):
    if not isinstance(value, dict):
        _fail(where, 'expected an object')
    return value


def _required(value, names, where):
    _mapping(value, where)
    missing = set(names) - value.keys()
    if missing:
        _fail(where, 'missing fields ' + ', '.join(sorted(missing)))


def _text(value, where, missing=False):
    if missing and value in (None, ''):
        return
    if not isinstance(value, str) or not value.strip():
        _fail(where, 'expected nonempty text')


def _number(value, where, missing=False, nonnegative=True):
    if missing and value in (None, ''):
        return
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        _fail(where, 'expected a finite numeric value')
    try:
        number = float(value)
    except (ValueError, OverflowError):
        _fail(where, 'expected a finite numeric value')
    if not math.isfinite(number) or (nonnegative and number < 0):
        _fail(where, 'expected a finite nonnegative numeric value')


def _rows(value, where):
    if isinstance(value, list):
        return enumerate(value)
    if isinstance(value, dict) and all(
            not isinstance(key, bool) and str(key).isdigit() for key in value):
        return value.items()
    _fail(where, 'expected an array or numeric-keyed row object')


def _years(value, where):
    for year, rows in _mapping(value, where).items():
        if not str(year).isdigit() or len(str(year)) != 4:
            _fail(where, 'expected four-digit year keys')
        yield str(year), rows


def _chart_row(row, where, text_fields, number_fields, optional_numbers=()):
    _required(row, (*text_fields, *number_fields, *optional_numbers), where)
    for field in text_fields:
        _text(row[field], where + '.' + field)
    for field in number_fields:
        _number(row[field], where + '.' + field)
    for field in optional_numbers:
        _number(row[field], where + '.' + field, missing=True)


def _bubble(value, where):
    stages = ('bubblegraph_initial', 'bubblegraph_replication')
    core = {'ACCESSION', 'Broader', 'DATE', 'DiseaseOrTrait', 'N', 'STAGE', 'parentterm'}
    _required(value, stages, where)
    encoding = value.get('__format')
    if encoding not in (None, 'dict_columnar_v2'):
        _fail(where, 'unsupported bubble encoding')
    for key, stage in zip(stages, ('initial', 'replication')):
        source, location = value[key], where + '.' + key
        if encoding is None:
            for index, row in _rows(source, location):
                _required(row, core, f'{location}[{index}]')
                for column in core - {'N', 'STAGE'}:
                    _text(row[column], location + '.' + column, missing=column != 'ACCESSION')
                _number(row['N'], location + '.N', missing=True)
                if row['STAGE'] != stage:
                    _fail(location, 'row belongs to a different stage')
            continue
        _required(source, ('columns', 'dicts', 'codes', 'meta'), location)
        columns, dictionaries, codes, meta = (source[name] for name in ('columns', 'dicts', 'codes', 'meta'))
        if not isinstance(columns, list) or any(not isinstance(name, str) or not name for name in columns) \
                or len(set(columns)) != len(columns):
            _fail(location, 'bubble columns must be unique names')
        _required(meta, ('rowCount',), location + '.meta')
        count = meta['rowCount']
        if type(count) is not int or count < 0:
            _fail(location, 'rowCount must be a nonnegative integer')
        if count and not core.issubset(columns):
            _fail(location, 'missing required bubble columns')
        if set(_mapping(dictionaries, location + '.dicts')) != set(columns) \
                or set(_mapping(codes, location + '.codes')) != set(columns):
            _fail(location, 'dictionary/code columns do not match declared columns')
        for column in columns:
            values, indices = dictionaries[column], codes[column]
            if not isinstance(values, list) or not isinstance(indices, list) or len(indices) != count:
                _fail(location + '.' + column, 'dictionary/code arrays disagree with rowCount')
            if any(isinstance(item, (dict, list)) for item in values):
                _fail(location + '.' + column, 'dictionary values must be scalar')
            if any(type(index) is not int or index < 0 or index >= len(values) for index in indices):
                _fail(location + '.' + column, 'dictionary code out of bounds or not an integer')
            if column in ('N', '__Nnum'):
                for item in values:
                    _number(item, location + '.' + column, missing=True)
            elif column in core - {'STAGE'}:
                for item in values:
                    _text(item, location + '.' + column, missing=column != 'ACCESSION')
            if column == 'STAGE' and any(values[index] != stage for index in set(indices)):
                _fail(location, 'encoded row belongs to a different stage')
        for field in ('maxN', 'minDateMS', 'maxDateMS'):
            if field in meta:
                _number(meta[field], location + '.meta.' + field, missing=True,
                        nonnegative=field == 'maxN')


def validate_plot_payload(relative_path, value):
    """Validate supported plot shapes without restricting scientific categories."""
    where = str(relative_path)
    name = PurePosixPath(where).name
    _mapping(value, where)
    if not value:
        _fail(where, 'empty plot object')
    if name == 'bubbleGraph.json':
        _bubble(value, where)
    elif name in ('ancestries.json', 'ancestriesOrdered.json', 'parentTerms.json', 'traits.json'):
        for key, text in value.items():
            _text(str(key), where + '.key')
            _text(text, where + '.' + str(key))
    elif name == 'summary.json':
        _required(value, ('number_studies', 'number_accessions'), where)
        for field in ('number_studies', 'number_accessions', 'number_diseasestraits', 'number_mappedtrait',
                      'found_associations', 'average_associations'):
            if field in value:
                _number(value[field], where + '.' + field)
    elif name == 'tsPlot.json':
        keys = {f'ts_{recorded}_{stage}_{metric}' for recorded in ('recorded', 'notrecorded')
                for stage in ('discovery', 'replication') for metric in ('studies', 'participants')}
        _required(value, keys, where)
        for variant in keys:
            for ancestry, rows in _mapping(value[variant], where + '.' + variant).items():
                _text(ancestry, where + '.ancestry')
                for index, row in _rows(rows, where + '.' + variant):
                    _chart_row(row, f'{where}.{variant}[{index}]', (), ('year', 'value'))
    elif name in ('heatMap.json', 'doughnutGraph.json'):
        prefix = 'heatmap' if name == 'heatMap.json' else 'doughnut'
        keys = {f'{prefix}_{stage}_{metric}' for stage in ('discovery', 'replication')
                for metric in ('studies', 'participants')}
        if prefix == 'doughnut':
            keys.add('doughnut_associations')
        _required(value, keys, where)
        for variant in keys:
            for year, rows in _years(value[variant], where + '.' + variant):
                groups = {'': rows} if prefix == 'heatmap' else _mapping(rows, where + '.' + year)
                for term, group in groups.items():
                    if prefix == 'doughnut':
                        _text(term, where + '.term')
                    for index, row in _rows(group, where + '.' + year):
                        _chart_row(row, f'{where}.{variant}.{year}[{index}]',
                                   ('ancestry', 'term') if prefix == 'heatmap' else ('ancestry',),
                                   (), ('value',))
    elif name == 'chloroMap.json':
        staged = 'initial' in value or 'replication' in value
        if staged and set(value) != {'initial', 'replication'}:
            _fail(where, 'country map mixes stage and year schemas')
        for source in value.values() if staged else (value,):
            for year, rows in _years(source, where):
                for index, row in _rows(rows, where + '.' + year):
                    _chart_row(row, f'{where}.{year}[{index}]', ('country',),
                               ('studies', 'studiesPercentage', 'participants', 'participantsPercentage'),
                               ('population',))
    else:
        _fail(where, 'unrecognized generated plot artifact')
