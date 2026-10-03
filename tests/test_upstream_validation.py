import csv
import io
from pathlib import Path
import random
import tempfile
import unittest

from upstream_validation import read_tsv_header, validate_raw_tsv


class UpstreamValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, header, rows, name='input.tsv'):
        source = io.StringIO(newline='')
        writer = csv.writer(source, delimiter='\t')
        writer.writerow(header)
        writer.writerows(rows)
        path = self.root / name
        path.write_text(source.getvalue(), encoding='utf-8')
        return path

    def ancestry(self, **changes):
        row = {'STUDY ACCESSION': 'GCST000001', 'PUBMEDID': '12345',
               'DATE': '2024-02-29', 'STAGE': 'initial',
               'NUMBER OF INDIVDUALS': '123', 'NEW OPTIONAL COLUMN': 'Café'}
        row.update(changes)
        path = self.write(row.keys(), [row.values()], 'Cat_Anc.tsv')
        return path, set(row) - {'NEW OPTIONAL COLUMN', 'PUBMEDID'}

    def study(self, **changes):
        row = {'STUDY ACCESSION': 'GCST000001', 'PUBMED ID': '12345',
               'DATE': '2024-02-29', 'ASSOCIATION COUNT': '12',
               'OPTIONAL TRAIT': 'Trait'}
        row.update(changes)
        path = self.write(row.keys(), [row.values()], 'Cat_Stud.tsv')
        return path, set(row) - {'OPTIONAL TRAIT', 'PUBMED ID'}

    def test_bom_unicode_extra_columns_and_quoted_multiline_values_are_preserved(self):
        path = self.write(['Trait', 'Parent', 'New field'], [
            ['[A scientific label]', 'Parent', 'quoted\ttab\nand newline'],
            ['Café response', 'Other', 'text'],
        ])
        path.write_bytes(b'\xef\xbb\xbf' + path.read_bytes())
        original = path.read_bytes()
        self.assertEqual(read_tsv_header(path), ['Trait', 'Parent', 'New field'])
        self.assertEqual(validate_raw_tsv(path, {'Trait', 'Parent'})['rowCount'], 2)
        self.assertEqual(path.read_bytes(), original)

    def test_header_names_are_returned_verbatim_for_alias_readers(self):
        path = self.write([' STUDY_ACCESSION ', 'PUBMED ID'], [['GCST1', '1']])
        self.assertEqual(read_tsv_header(path), [' STUDY_ACCESSION ', 'PUBMED ID'])
        with self.assertRaisesRegex(ValueError, 'missing required columns'):
            validate_raw_tsv(path, {'STUDY ACCESSION'})

    def test_varied_valid_quoting_and_unicode_round_trip_without_rewriting(self):
        random_source = random.Random(122)
        characters = 'ab \t\r\n"éα[]'
        rows = [[
            ''.join(random_source.choices(characters, k=random_source.randrange(30)))
            for _ in range(3)
        ] for _ in range(1000)]
        path = self.write(['Trait', 'Parent', 'Optional'], rows)
        original = path.read_bytes()
        self.assertEqual(validate_raw_tsv(path, {'Trait', 'Parent'})['rowCount'], len(rows))
        self.assertEqual(path.read_bytes(), original)

    def test_duplicate_and_ambiguous_normalised_headers_are_rejected(self):
        for columns in (['DATE', 'DATE'], ['DATE', ' date '], ['PUBMED_ID', 'PUBMED ID']):
            with self.subTest(columns=columns):
                path = self.write(columns, [['a', 'b']])
                with self.assertRaisesRegex(ValueError, 'row 1.*duplicate/ambiguous'):
                    read_tsv_header(path)
        path = self.write(['PUBMEDID', 'PUBMED ID'], [['1', '1']])
        with self.assertRaisesRegex(ValueError, 'ambiguous alias'):
            validate_raw_tsv(path, set(), [{'PUBMEDID', 'PUBMED ID'}])

    def test_explicit_aliases_work_but_missing_alias_is_rejected(self):
        for alias in ('PUBMEDID', 'PUBMED ID', 'PUBMED_ID'):
            path = self.write([alias, 'DATE'], [['123', '2024-01-01']])
            self.assertEqual(validate_raw_tsv(path, {'DATE'}, [
                {'PUBMEDID', 'PUBMED ID', 'PUBMED_ID'},
            ])['rowCount'], 1)
        with self.assertRaisesRegex(ValueError, 'missing one of required aliases'):
            validate_raw_tsv(path, {'DATE'}, [{'another alias'}])

    def test_empty_header_only_and_error_bodies_are_rejected(self):
        path = self.root / 'error.tsv'
        for body in ('', 'A\tB\n', '<html>Error</html>', '<!DOCTYPE html>error',
                     '{"error":"try later"}', '[{"error":"try later"}]',
                     '<?xml version="1.0"?><Error/>'):
            with self.subTest(body=body):
                path.write_text(body)
                with self.assertRaises(ValueError):
                    validate_raw_tsv(path, set())

    def test_ragged_records_report_logical_row_even_with_multiline_fields(self):
        path = self.write(['A', 'B'], [['line one\nline two', 'ok'], ['missing']])
        with self.assertRaisesRegex(ValueError, 'row 3.*expected 2 fields, found 1'):
            validate_raw_tsv(path, {'A', 'B'})
        path = self.write(['A'], [['ok'], ['too', 'many']])
        with self.assertRaisesRegex(ValueError, 'row 3.*expected 1 fields, found 2'):
            validate_raw_tsv(path, {'A'})

    def test_harmless_blank_lines_do_not_create_data_rows_or_hide_missing_cells(self):
        path = self.root / 'blank-lines.tsv'
        path.write_text('A\tB\n\nfirst\tvalue\n  \nsecond\tvalue\n\n')
        self.assertEqual(validate_raw_tsv(path, {'A', 'B'})['rowCount'], 2)
        path.write_text('A\tB\n\n \n')
        with self.assertRaisesRegex(ValueError, 'header-only'):
            validate_raw_tsv(path, {'A', 'B'})
        path, required = self.ancestry()
        with path.open('a') as source:
            source.write('\t\t\t\t\t\n')
        with self.assertRaisesRegex(ValueError, 'row 3.*missing required identifier'):
            validate_raw_tsv(path, required)

    def test_invalid_utf8_nul_and_broken_quotes_are_rejected(self):
        path = self.root / 'invalid.tsv'
        for body in (b'A\n\xff\n', b'A\nnull\0character\n', b'A\0\nvalid\n',
                     b'A\nvalid\n"unfinished', b'A\t\nvalue\tvalue\n'):
            with self.subTest(body=body):
                path.write_bytes(body)
                with self.assertRaises(ValueError):
                    validate_raw_tsv(path, set())

    def test_current_catalogue_quotation_style_remains_supported(self):
        path = self.root / 'titles.tsv'
        path.write_text('Accession\tTitle\tExtra\n'
                        'GCST1\t"The Heidelberg Five" personality dimensions\tvalid\n')
        self.assertEqual(validate_raw_tsv(path, {'Accession', 'Title'})['rowCount'], 1)
        # A permissive quotation earlier in a row must not hide a genuinely
        # unclosed final field in that same row.
        path.write_text('Accession\tTitle\tExtra\n'
                        'GCST1\t"The Heidelberg Five" personality dimensions\t"unfinished')
        with self.assertRaisesRegex(ValueError, 'row 2.*unterminated'):
            validate_raw_tsv(path, {'Accession', 'Title'})

    def test_valid_scientific_counts_keep_existing_values(self):
        for value in ('0', '123', '1.0', '1e3', ' 42 ', str(2 ** 63 - 1)):
            with self.subTest(value=value):
                path, required = self.ancestry(**{'NUMBER OF INDIVDUALS': value})
                original = path.read_bytes()
                metadata = validate_raw_tsv(path, required)
                self.assertEqual(metadata, {'rowCount': 1, 'missingN': 0, 'duplicateStudyRows': 0})
                self.assertEqual(path.read_bytes(), original)

    def test_legacy_missing_participant_counts_are_reported_not_invented(self):
        for value in ('', 'NA', 'N/A', 'NR', ' na '):
            with self.subTest(value=value):
                path, required = self.ancestry(**{'NUMBER OF INDIVDUALS': value})
                self.assertEqual(validate_raw_tsv(path, required)['missingN'], 1)

    def test_supplied_invalid_participant_counts_fail_instead_of_disappearing(self):
        for value in ('NaN', 'nan', 'inf', 'Infinity', '-inf', '-1', '1.25',
                      '12 people', '1,000', '1_000', '١٢٣', str(2 ** 63), '1e1000000'):
            with self.subTest(value=value):
                path, required = self.ancestry(**{'NUMBER OF INDIVDUALS': value})
                with self.assertRaisesRegex(ValueError, 'row 2.*NUMBER OF INDIVDUALS'):
                    validate_raw_tsv(path, required)

    def test_association_counts_are_required_nonnegative_finite_integers(self):
        for value in ('', 'NA', 'NaN', 'Infinity', '-1', '1.2', 'text'):
            with self.subTest(value=value):
                path, required = self.study(**{'ASSOCIATION COUNT': value})
                with self.assertRaisesRegex(ValueError, 'row 2.*ASSOCIATION COUNT'):
                    validate_raw_tsv(path, required)

    def test_unsupported_stages_fail_without_guessing_or_dropping_rows(self):
        for value in ('', 'Initial', ' initial', 'discovery', 'meta-analysis'):
            with self.subTest(value=value):
                path, required = self.ancestry(STAGE=value)
                with self.assertRaisesRegex(ValueError, 'row 2.*STAGE'):
                    validate_raw_tsv(path, required)
        path, required = self.ancestry(STAGE='replication')
        self.assertEqual(validate_raw_tsv(path, required)['rowCount'], 1)

    def test_dates_need_valid_calendar_values_without_arbitrary_year_cutoff(self):
        for value in ('', '2023-02-29', 'NaT', 'not a date', '2024-13-01', '20240229'):
            with self.subTest(value=value):
                path, required = self.ancestry(DATE=value)
                with self.assertRaisesRegex(ValueError, 'row 2.*DATE'):
                    validate_raw_tsv(path, required)
        for value in ('1900-01-01', '2100-12-31'):
            path, required = self.ancestry(DATE=value)
            self.assertEqual(validate_raw_tsv(path, required)['rowCount'], 1)

    def test_missing_and_whitespace_identifiers_cannot_silently_miss_joins(self):
        for column in ('STUDY ACCESSION', 'PUBMEDID'):
            for value in ('', 'NA', 'NaN', ' GCST1 '):
                with self.subTest(column=column, value=value):
                    path, required = self.ancestry(**{column: value})
                    with self.assertRaisesRegex(ValueError, 'row 2.*identifier'):
                        validate_raw_tsv(path, required)

    def test_duplicate_study_accessions_are_checked_without_deduplicating(self):
        header = ['STUDY ACCESSION', 'PUBMED ID', 'DATE', 'ASSOCIATION COUNT', 'Extra']
        row = ['GCST1', '123', '2024-01-01', '1', 'same']
        path = self.write(header, [row, row], 'Cat_Stud.tsv')
        original = path.read_bytes()
        metadata = validate_raw_tsv(path, set(header))
        self.assertEqual(metadata['rowCount'], 2)
        self.assertEqual(metadata['duplicateStudyRows'], 1)
        self.assertEqual(path.read_bytes(), original)
        path = self.write(header, [row, row[:-1] + ['different']], 'Cat_Stud.tsv')
        with self.assertRaisesRegex(ValueError, 'row 3.*STUDY ACCESSION.*conflicting duplicate of row 2'):
            validate_raw_tsv(path, set(header))

    def test_repeated_ancestry_accessions_are_expected_not_duplicates(self):
        path, required = self.ancestry()
        with path.open(newline='') as source:
            rows = list(csv.reader(source, delimiter='\t'))
        self.write(rows[0], [rows[1], rows[1]], 'Cat_Anc.tsv')
        metadata = validate_raw_tsv(path, required)
        self.assertEqual(metadata['rowCount'], 2)
        self.assertEqual(metadata['duplicateStudyRows'], 0)

    def test_large_quoted_fields_are_allowed_and_parser_limit_is_restored(self):
        limit = csv.field_size_limit()
        path = self.write(['A', 'B'], [['x' * 150000, 'scientific annotation']])
        self.assertEqual(validate_raw_tsv(path, {'A', 'B'})['rowCount'], 1)
        self.assertEqual(csv.field_size_limit(), limit)

    def test_error_values_are_bounded_and_keep_filename_row_and_column(self):
        path, required = self.ancestry(STAGE='x' * 10000)
        with self.assertRaises(ValueError) as caught:
            validate_raw_tsv(path, required)
        message = str(caught.exception)
        self.assertIn(str(path), message)
        self.assertIn('row 2', message)
        self.assertIn('STAGE', message)
        self.assertLess(len(message), 400)


if __name__ == '__main__':
    unittest.main()
