"""Download guards operate in the generator, never in dashboard requests."""
import io
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import generate_data


class DownloadResponse:
    def __init__(self, content, headers=None):
        self.content = content
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.content[:7]
        yield b''
        yield self.content[7:]


class CatalogDownloadHardeningTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.raw = self.root / 'catalog' / 'raw'
        self.raw.mkdir(parents=True)
        self.old = self.raw / 'Cat_Stud.tsv'
        self.old.write_bytes(b'previous staged input\n')
        self.enterContext(mock.patch.object(
            generate_data, 'diversity_logger', logging.getLogger('download-test'), create=True,
        ))

    def download_first(self, response):
        with mock.patch.object(generate_data.requests, 'get', return_value=response):
            return generate_data.download_cat(str(self.root), 'https://example.invalid/')

    def assert_previous_untouched(self):
        self.assertEqual(self.old.read_bytes(), b'previous staged input\n')
        self.assertEqual(sorted(path.name for path in self.raw.iterdir()), ['Cat_Stud.tsv'])

    def test_stream_limits_check_wire_length_without_writing_an_oversize_chunk(self):
        output = io.BytesIO()
        self.assertEqual(generate_data._write_bounded_download([b'ab', b'', b'cd'], output, 4, '4'), 4)
        self.assertEqual(output.getvalue(), b'abcd')
        output = io.BytesIO()
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            generate_data._write_bounded_download([b'ab', b'cde'], output, 4)
        self.assertEqual(output.getvalue(), b'ab')
        for length in ('invalid', '-1', '999'):
            with self.subTest(length=length), self.assertRaises(ValueError):
                generate_data._write_bounded_download([b'ab'], io.BytesIO(), 4, length)
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            generate_data._write_bounded_download([b'ab'], io.BytesIO(), 4, '4')
        with self.assertRaisesRegex(ValueError, 'empty'):
            generate_data._write_bounded_download([b''], io.BytesIO(), 4)

    def test_configurable_limit_is_positive_and_has_a_generous_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(generate_data._catalog_download_limit(), 8 * 1024 ** 3)
        with mock.patch.dict(os.environ, GWAS_CATALOG_MAX_DOWNLOAD_BYTES='123'):
            self.assertEqual(generate_data._catalog_download_limit(), 123)
        for raw in ('', '-1', '0', 'nan', '1.2'):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, GWAS_CATALOG_MAX_DOWNLOAD_BYTES=raw):
                with self.assertRaisesRegex(ValueError, 'positive integer'):
                    generate_data._catalog_download_limit()

    def test_truncated_response_never_replaces_existing_input(self):
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            self.download_first(DownloadResponse(b'STUDY ACCESSION\nA\n', {'Content-Length': '999'}))
        self.assert_previous_untouched()

    def test_duplicate_header_and_html_error_do_not_replace_existing_input(self):
        for payload in (b'STUDY ACCESSION\tSTUDY ACCESSION\nA\tB\n', b'<html>Upstream temporarily unavailable</html>'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.download_first(DownloadResponse(payload))
            self.assert_previous_untouched()

    def test_zip_expansion_and_multiple_candidates_do_not_replace_existing_input(self):
        def archive_bytes(entries):
            content = io.BytesIO()
            with zipfile.ZipFile(content, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, body in entries:
                    archive.writestr(name, body)
            return content.getvalue()

        payload = archive_bytes([('studies.tsv', b'STUDY ACCESSION\n' + b'A\n' * 3000)])
        with mock.patch.dict(os.environ, GWAS_CATALOG_MAX_DOWNLOAD_BYTES='1024'):
            with self.assertRaisesRegex(ValueError, 'expanded TSV'):
                self.download_first(DownloadResponse(payload))
        self.assert_previous_untouched()
        payload = archive_bytes([('one.tsv', b'STUDY ACCESSION\nA\n'), ('two.tsv', b'STUDY ACCESSION\nB\n')])
        with self.assertRaisesRegex(ValueError, 'expected one TSV'):
            self.download_first(DownloadResponse(payload))
        self.assert_previous_untouched()

    def test_encoded_http_body_is_not_compared_to_compressed_content_length(self):
        responses = [
            DownloadResponse(b'STUDY ACCESSION\nA\n', {'Content-Length': '3', 'Content-Encoding': 'gzip'}),
            DownloadResponse(b'BROAD ANCESTRAL CATEGORY\nEuropean\n'),
            DownloadResponse(b'P-VALUE\n1e-8\n'),
        ]
        mapping = b'Disease trait\tParent term\nHeight\tBody measurement\n'
        ftp = mock.Mock()
        ftp.retrbinary.side_effect = lambda command, callback, **kwargs: callback(mapping)
        with mock.patch.object(generate_data.requests, 'get', side_effect=responses), \
                mock.patch.object(generate_data.ftplib, 'FTP', return_value=ftp):
            sources = generate_data.download_cat(str(self.root), 'https://example.invalid/')
        self.assertEqual(len(sources), 4)
        self.assertEqual(self.old.read_bytes(), responses[0].content)
        self.assertFalse(list(self.raw.glob('.gwas_*')))

    def download_with_ftp(self, ftp):
        responses = [
            DownloadResponse(b'STUDY ACCESSION\nA\n'),
            DownloadResponse(b'BROAD ANCESTRAL CATEGORY\nEuropean\n'),
            DownloadResponse(b'P-VALUE\n1e-8\n', {'ETag': 'http-only-etag'}),
        ]
        with mock.patch.object(generate_data.requests, 'get', side_effect=responses), \
                mock.patch.object(generate_data.ftplib, 'FTP', return_value=ftp) as constructor:
            sources = generate_data.download_cat(str(self.root), 'https://example.invalid/')
        constructor.assert_called_once_with(timeout=60)
        return sources

    def test_mapping_ftp_streams_chunks_and_keeps_original_provenance_without_http_headers(self):
        ftp = mock.Mock()
        chunks = [b'Disease trait\tParent term\n', b'Height\tBody measurement\n']

        def transfer(command, callback, blocksize):
            self.assertEqual(command, 'RETR /pub/databases/gwas/releases/latest/gwas-efo-trait-mappings.tsv')
            self.assertEqual(blocksize, 1024 * 1024)
            for chunk in chunks:
                callback(chunk)

        ftp.retrbinary.side_effect = transfer
        sources = self.download_with_ftp(ftp)
        ftp.connect.assert_called_once_with('ftp.ebi.ac.uk')
        ftp.login.assert_called_once_with()
        ftp.close.assert_called_once_with()
        ftp.size.assert_not_called()
        self.assertEqual((self.raw / 'Cat_Map.tsv').read_bytes(), b''.join(chunks))
        self.assertEqual(sources[-1]['url'], 'ftp://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest/gwas-efo-trait-mappings.tsv')
        self.assertEqual(sources[-1]['filename'], 'gwas-efo-trait-mappings.tsv')
        self.assertIsNone(sources[-1]['etag'])
        self.assertIsNone(sources[-1]['lastModified'])
        self.assertFalse(list(self.raw.glob('.gwas_*')))

    def test_mapping_ftp_limit_aborts_transfer_immediately_and_preserves_previous_file(self):
        mapping = self.raw / 'Cat_Map.tsv'
        mapping.write_bytes(b'previous mapping\n')
        ftp = mock.Mock()
        callbacks = []

        def transfer(command, callback, blocksize):
            for chunk in (b'Disease trait\tParent term\n', b'x' * 64, b'unreachable'):
                callbacks.append(chunk)
                callback(chunk)

        ftp.retrbinary.side_effect = transfer
        with mock.patch.dict(os.environ, GWAS_CATALOG_MAX_DOWNLOAD_BYTES='64'), \
                self.assertRaisesRegex(ValueError, 'exceeds'):
            self.download_with_ftp(ftp)
        self.assertEqual(len(callbacks), 2)
        ftp.close.assert_called_once_with()
        self.assertEqual(mapping.read_bytes(), b'previous mapping\n')
        self.assertFalse(list(self.raw.glob('.gwas_*')))

    def test_mapping_ftp_empty_bad_header_and_failed_transfer_close_and_clean_up(self):
        mapping = self.raw / 'Cat_Map.tsv'
        for failure in ('empty', 'header', 'connect', 'login', 'transfer'):
            with self.subTest(failure=failure):
                mapping.write_bytes(b'previous mapping\n')
                ftp = mock.Mock()
                if failure in ('connect', 'login'):
                    getattr(ftp, failure).side_effect = TimeoutError('upstream unavailable')

                def transfer(command, callback, blocksize):
                    if failure == 'empty':
                        return
                    if failure == 'header':
                        callback(b'<html>service unavailable</html>')
                    else:
                        callback(b'Disease trait\tParent term\n')
                        raise OSError('transfer interrupted')

                ftp.retrbinary.side_effect = transfer
                with self.assertRaises((ValueError, OSError)):
                    self.download_with_ftp(ftp)
                ftp.close.assert_called_once_with()
                self.assertEqual(mapping.read_bytes(), b'previous mapping\n')
                self.assertFalse(list(self.raw.glob('.gwas_*')))

    def test_alias_reader_preserves_real_header_spelling_and_rejects_ambiguity(self):
        source = self.root / 'aliases.tsv'
        source.write_text('\ufeff PUBMED_ID \tFIRST_AUTHOR\n123\tExample\n', encoding='utf-8')
        frame = generate_data.read_tsv_with_aliases(source, ['PUBMEDID', 'FIRST AUTHOR'])
        self.assertEqual(list(frame.columns), ['PUBMEDID', 'FIRST AUTHOR'])
        self.assertEqual(frame.iloc[0].to_dict(), {'PUBMEDID': '123', 'FIRST AUTHOR': 'Example'})
        source.write_text('PUBMEDID\tPUBMED ID\n123\t456\n')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            generate_data.read_tsv_with_aliases(source, ['PUBMEDID'])

    def test_generator_image_and_implementation_identity_include_validators(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / 'deploy' / 'data.Dockerfile').read_text()
        for name in ('upstream_validation.py', 'generated_data_validation.py'):
            self.assertIn(f'COPY {name} {name}', dockerfile)
        with mock.patch.object(generate_data, '_fingerprint_files', return_value={}) as fingerprint, \
                mock.patch.object(generate_data, '_file_fingerprint', return_value={}):
            generate_data._implementation_fingerprints(str(root))
        files = fingerprint.call_args.args[1]
        self.assertIn('upstream_validation.py', files)
        self.assertIn('generated_data_validation.py', files)


if __name__ == '__main__':
    unittest.main()
