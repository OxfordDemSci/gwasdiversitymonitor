"""Generation-only checks for external catalogue files before publication.

These checks never rewrite a downloaded row. Unknown extra columns are allowed;
ambiguous headers, malformed records and unsafe supplied values stop generation
so its existing publication workflow can retain the last verified release.
"""

import csv
import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import sqlite3
import tempfile


_PUBMED_HEADERS = frozenset({'PUBMEDID', 'PUBMED ID', 'PUBMED_ID'})
# The audited current catalogue contains blank participant counts. Preserve the
# established missing-count convention, but never treat a supplied NaN/Inf as a
# scientific count. Missing counts are reported, not silently invented as zero.
_MISSING_N = frozenset({'', 'na', 'n/a', 'nr'})
_MISSING_KEY = _MISSING_N | frozenset({'nan', 'null', 'none'})
_MAX_COUNT = Decimal(2 ** 63 - 1)
_MAX_FIELD_BYTES = 16 * 1024 * 1024
_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')


class _RecordLines:
    """Remember only the physical lines consumed for the current CSV record."""
    def __init__(self, source):
        self.source = source
        self.lines = []
        self.quoted = False

    def __iter__(self):
        return self

    def __next__(self):
        line = next(self.source)
        self.lines.append(line)
        self.quoted = self.quoted or '"' in line
        return line

    def validate_quotes(self, path, row):
        if self.quoted:
            # GWAS titles legitimately contain fields such as
            # '"The Heidelberg Five" personality dimensions'. csv strict=True
            # rejects this existing source format. Match the permissive parser
            # while still rejecting an unterminated quoted field at EOF.
            text = ''.join(self.lines)
            state, position = 'field', 0
            for match in re.finditer('[\t"\r\n]', text):
                if match.start() > position and state in {'field', 'closed'}:
                    state = 'plain'
                char = match.group()
                if state == 'quoted':
                    if char == '"':
                        state = 'closed'
                elif state == 'closed' and char == '"':
                    state = 'quoted'
                elif char in '\t\r\n':
                    state = 'field'
                elif state == 'field' and char == '"':
                    state = 'quoted'
                position = match.end()
            if state == 'quoted':
                _error(path, row, '<record>', 'unterminated quoted TSV field')
        self.lines.clear()
        self.quoted = False


def _error(path, row, column, reason, value=None):
    detail = '' if value is None else f'; value={str(value)[:120]!r}'
    raise ValueError(f'{os.fspath(path)}: row {row}, column {column!r}: {reason}{detail}')


def _normalise_header(value):
    return ' '.join(value.strip().replace('_', ' ').casefold().split())


def _is_error_body(value, *, include_json=True):
    prefix = value.lstrip().casefold()
    return prefix.startswith(('<!doctype html', '<html', '<?xml', '<error')) \
        or (include_json and prefix.startswith(('{', '[')))


def _read_header(source, path):
    tracked = _RecordLines(source)
    reader = csv.reader(tracked, delimiter='\t')
    try:
        header = next(reader)
    except StopIteration:
        _error(path, 1, '<header>', 'empty TSV')
    except (csv.Error, UnicodeDecodeError) as error:
        _error(path, 1, '<header>', f'invalid UTF-8/TSV header: {error}')
    tracked.validate_quotes(path, 1)
    if not header or _is_error_body(header[0], include_json=len(header) == 1):
        _error(path, 1, '<header>', 'expected a TSV header, not an error/HTML/JSON/XML body')
    seen = {}
    for name in header:
        if '\x00' in name:
            _error(path, 1, '<header>', 'NUL character in header', name)
        normalized = _normalise_header(name)
        if not normalized:
            _error(path, 1, '<header>', 'empty column name')
        if normalized in seen:
            _error(path, 1, name, f'duplicate/ambiguous header also matches {seen[normalized]!r}')
        seen[normalized] = name
    return header, reader, tracked


def read_tsv_header(path):
    """Read original header spellings, without pandas' duplicate-name mangling."""
    with open(path, encoding='utf-8-sig', newline='') as source:
        header, _, _ = _read_header(source, path)
    return header


def _required_key(path, row_number, column, value):
    if value.strip().casefold() in _MISSING_KEY:
        _error(path, row_number, column, 'missing required identifier', value)
    if value != value.strip():
        _error(path, row_number, column, 'identifier contains surrounding whitespace', value)


def _date(path, row_number, value):
    try:
        # Do not set an arbitrary year cutoff: valid future/historical calendar
        # dates remain data, rather than being silently discarded by validation.
        parsed = datetime.date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError('date is not YYYY-MM-DD')
    except ValueError:
        _error(path, row_number, 'DATE', 'expected a valid YYYY-MM-DD calendar date', value)


def _count(path, row_number, column, value, *, allow_missing=False):
    text = value.strip()
    if allow_missing and text.casefold() in _MISSING_N:
        return False
    if not _NUMBER.fullmatch(text):
        _error(path, row_number, column, 'expected a finite non-negative integer count', value)
    try:
        count = Decimal(text)
    except InvalidOperation:
        _error(path, row_number, column, 'expected a finite non-negative integer count', value)
    if not count.is_finite() or count < 0 or count > _MAX_COUNT \
            or count != count.to_integral_value():
        _error(path, row_number, column, 'count must be finite, non-negative, integral and fit int64', value)
    return True


def validate_raw_tsv(path, required_columns, alternatives=(), kind=None):
    """Stream a raw TSV and return validation metadata, without changing it.

    ``required_columns`` retain their existing strict spellings; only explicit
    ``alternatives`` accept aliases. ``kind`` is Cat_Anc.tsv or Cat_Stud.tsv for
    scientific value checks. Other files receive the same structural checks.

    Duplicate study accessions are checked in a disposable disk-backed index,
    keeping memory bounded even for a large future catalogue. Identical rows are
    retained and counted in metadata; conflicting rows fail, never silently win.
    """
    kind = kind or os.path.basename(path)
    metadata = {'rowCount': 0, 'missingN': 0, 'duplicateStudyRows': 0}
    old_field_limit = csv.field_size_limit()
    csv.field_size_limit(max(old_field_limit, _MAX_FIELD_BYTES))
    try:
        with open(path, encoding='utf-8-sig', newline='') as source, \
                tempfile.TemporaryDirectory(prefix='gwas-upstream-validation-') as workspace:
            header, reader, tracked = _read_header(source, path)
            missing = set(required_columns) - set(header)
            if missing:
                _error(path, 1, '<header>', f'missing required columns {sorted(missing)}')
            for group in alternatives:
                matches = set(header).intersection(group)
                if not matches:
                    _error(path, 1, '<header>', f'missing one of required aliases {sorted(group)}')
                if len(matches) > 1:
                    _error(path, 1, '<header>', f'ambiguous alias columns {sorted(matches)}')

            semantic = kind in {'Cat_Anc.tsv', 'Cat_Stud.tsv'}
            positions = {name: index for index, name in enumerate(header)}
            if semantic:
                names = {'STUDY ACCESSION', 'DATE'}
                names.update({'STAGE', 'NUMBER OF INDIVDUALS'} if kind == 'Cat_Anc.tsv'
                             else {'ASSOCIATION COUNT'})
                missing = names - positions.keys()
                if missing:
                    _error(path, 1, '<header>', f'missing semantic columns {sorted(missing)}')
                pmid_columns = set(header).intersection(_PUBMED_HEADERS)
                if len(pmid_columns) != 1:
                    _error(path, 1, '<header>', 'expected exactly one PubMed identifier column')
                pmid_column = next(iter(pmid_columns))

            database = None
            try:
                if kind == 'Cat_Stud.tsv':
                    database = sqlite3.connect(os.path.join(workspace, 'study-rows.sqlite'))
                    database.execute('PRAGMA journal_mode=OFF')
                    database.execute('PRAGMA synchronous=OFF')
                    database.execute('PRAGMA cache_size=-2048')
                    database.execute('CREATE TABLE studies (accession TEXT PRIMARY KEY, '
                                     'digest BLOB NOT NULL, source_row INTEGER NOT NULL) WITHOUT ROWID')
                row_number = 1
                try:
                    for row_number, values in enumerate(reader, start=2):
                        blank_line = not values or (len(values) == 1 and not values[0].strip()
                                                    and not tracked.quoted)
                        tracked.validate_quotes(path, row_number)
                        if blank_line:
                            # Match pandas' skip_blank_lines=True; a tabbed
                            # record with missing cells is still a real record.
                            continue
                        if len(values) != len(header):
                            _error(path, row_number, '<record>',
                                   f'expected {len(header)} fields, found {len(values)}')
                        for column, value in zip(header, values):
                            if '\x00' in value:
                                _error(path, row_number, column, 'NUL character in value', value)
                        if values and _is_error_body(values[0], include_json=False):
                            _error(path, row_number, header[0], 'unexpected error/HTML/JSON/XML body', values[0])
                        metadata['rowCount'] += 1
                        if not semantic:
                            continue
                        accession = values[positions['STUDY ACCESSION']]
                        _required_key(path, row_number, 'STUDY ACCESSION', accession)
                        _required_key(path, row_number, pmid_column, values[positions[pmid_column]])
                        _date(path, row_number, values[positions['DATE']])
                        if kind == 'Cat_Anc.tsv':
                            stage = values[positions['STAGE']]
                            if stage not in {'initial', 'replication'}:
                                _error(path, row_number, 'STAGE', 'unsupported stage; expected initial or replication', stage)
                            if not _count(path, row_number, 'NUMBER OF INDIVDUALS',
                                          values[positions['NUMBER OF INDIVDUALS']], allow_missing=True):
                                metadata['missingN'] += 1
                        else:
                            _count(path, row_number, 'ASSOCIATION COUNT', values[positions['ASSOCIATION COUNT']])
                            digest = hashlib.sha256(json.dumps(values, ensure_ascii=False,
                                                               separators=(',', ':')).encode('utf-8')).digest()
                            inserted = database.execute('INSERT OR IGNORE INTO studies VALUES (?, ?, ?)',
                                                        (accession, digest, row_number))
                            if inserted.rowcount == 0:
                                previous_digest, previous_row = database.execute(
                                    'SELECT digest, source_row FROM studies WHERE accession = ?', (accession,)
                                ).fetchone()
                                if previous_digest != digest:
                                    _error(path, row_number, 'STUDY ACCESSION',
                                           f'conflicting duplicate of row {previous_row}', accession)
                                metadata['duplicateStudyRows'] += 1
                except (csv.Error, UnicodeDecodeError) as error:
                    _error(path, row_number + 1, '<record>', f'invalid UTF-8/TSV record: {error}')
            finally:
                if database is not None:
                    database.close()
            if metadata['rowCount'] == 0:
                _error(path, 1, '<record>', 'header-only TSV has no data records')
    finally:
        csv.field_size_limit(old_field_limit)
    return metadata
