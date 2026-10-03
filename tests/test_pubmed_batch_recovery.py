"""Offline regressions for transient PubMed batch failures and safe recovery."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import requests

import funder_pipeline as pipeline


class PubMedBatchRecoveryTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch('funder_pipeline.time.sleep')
        self.sleep = patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def response(status=200, content=b''):
        response = requests.Response()
        response.status_code = status
        response.encoding = 'utf-8'
        response._content = content
        return response

    @staticmethod
    def articles(*pmids):
        return ('<PubmedArticleSet>' + ''.join(
            '<PubmedArticle><MedlineCitation><PMID>' + pmid + '</PMID>'
            '<Article><GrantList><Grant><Agency>Agency ' + pmid + '</Agency>'
            '</Grant></GrantList></Article></MedlineCitation></PubmedArticle>'
            for pmid in pmids
        ) + '</PubmedArticleSet>').encode('utf-8')

    def session(self, fetch):
        def post(url, *, data, **kwargs):
            self.assertEqual(url, pipeline.NCBI_EFETCH_URL,
                             'HTTP failures do not establish unavailable records')
            self.assertEqual(data['db'], 'pubmed')
            self.assertEqual(data['retmode'], 'xml')
            return fetch(data['id'])
        return mock.Mock(post=mock.Mock(side_effect=post))

    @staticmethod
    def requested_ids(session):
        return [call.kwargs['data']['id'] for call in session.post.call_args_list]

    @staticmethod
    def fetch(session, pmids):
        return pipeline._fetch_pubmed_records(
            session, pmids, None, max_retries=2, request_delay=0,
        )

    def test_successful_batch_keeps_one_request_fast_path(self):
        session = self.session(lambda ids: self.response(content=self.articles(*ids.split(','))))
        result = self.fetch(session, ['100', '200', '300'])
        self.assertEqual(set(result), {'100', '200', '300'})
        self.assertEqual(self.requested_ids(session), ['100,200,300'])
        self.sleep.assert_not_called()

    def test_transient_http_400_retries_same_batch_then_recovers(self):
        responses = iter([
            self.response(400, b'<eFetchResult><ERROR>Temporary backend failure</ERROR></eFetchResult>'),
            self.response(content=self.articles('100', '200')),
        ])
        session = self.session(lambda ids: next(responses))
        result = self.fetch(session, ['100', '200'])
        self.assertEqual(set(result), {'100', '200'})
        self.assertEqual(result['200']['grants'][0]['agency'], 'Agency 200')
        self.assertEqual(self.requested_ids(session), ['100,200', '100,200'])
        self.sleep.assert_called_once_with(1)

    def test_persistent_batch_http_400_bisects_after_retries_and_recovers_all_records(self):
        session = self.session(lambda ids: (
            self.response(400, b'Batch temporarily unavailable') if ',' in ids
            else self.response(content=self.articles(ids))
        ))
        result = self.fetch(session, ['100', '200', '300', '400'])
        self.assertEqual(set(result), {'100', '200', '300', '400'})
        self.assertEqual(self.requested_ids(session), [
            '100,200,300,400', '100,200,300,400',
            '100,200', '100,200', '100', '200',
            '300,400', '300,400', '300', '400',
        ])
        for pmid, record in result.items():
            self.assertEqual(record['grants'][0]['agency'], 'Agency ' + pmid)
            self.assertNotIn('retrievalStatus', record)

    def test_split_preserves_unreturned_ids_for_individual_confirmation(self):
        def fetch(ids):
            if ids == '100,200,300,400':
                return self.response(400, b'Batch rejected')
            if ids == '100,200':
                return self.response(content=self.articles('100'))
            self.assertEqual(ids, '300,400')
            return self.response(content=self.articles('300', '400'))
        session = self.session(fetch)
        result = self.fetch(session, ['100', '200', '300', '400'])
        self.assertEqual(set(result), {'100', '300', '400'})
        self.assertNotIn('200', result, 'A batch omission is not confirmed unfunded or unavailable')
        self.assertEqual(self.requested_ids(session), [
            '100,200,300,400', '100,200,300,400', '100,200', '300,400',
        ])

    def test_permanent_authorization_failure_never_retries_or_splits(self):
        for status in (401, 403):
            with self.subTest(status=status):
                session = self.session(lambda ids: self.response(status, b'Permission denied'))
                with self.assertRaisesRegex(pipeline.PubMedCollectionError, f'HTTP {status}'):
                    self.fetch(session, ['100', '200'])
                self.assertEqual(self.requested_ids(session), ['100,200'])

    def test_rate_limits_and_server_outages_retry_without_splitting(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                session = self.session(lambda ids: self.response(status, b'Upstream unavailable'))
                with self.assertRaisesRegex(pipeline.PubMedCollectionError, f'HTTP {status}'):
                    self.fetch(session, ['100', '200'])
                self.assertEqual(self.requested_ids(session), ['100,200', '100,200'])

    def test_malformed_http_200_retries_without_splitting_or_fabricating_records(self):
        for content in (b'<PubmedArticleSet>', b'<html>Backend failed</html>'):
            with self.subTest(content=content):
                session = self.session(lambda ids: self.response(content=content))
                with self.assertRaises(pipeline.PubMedCollectionError):
                    self.fetch(session, ['100', '200'])
                self.assertEqual(self.requested_ids(session), ['100,200', '100,200'])

    def test_network_timeout_retries_without_splitting(self):
        def fetch(ids):
            raise requests.Timeout('No response from PubMed')
        session = self.session(fetch)
        with self.assertRaises(pipeline.PubMedCollectionError):
            self.fetch(session, ['100', '200'])
        self.assertEqual(self.requested_ids(session), ['100,200', '100,200'])

    def test_persistent_singleton_http_400_fails_even_with_structured_error(self):
        session = self.session(lambda ids: self.response(
            400, b'<eFetchResult><ERROR>Failed to retrieve document</ERROR></eFetchResult>',
        ))
        with self.assertRaises(pipeline.PubMedCollectionError):
            self.fetch(session, ['100'])
        self.assertEqual(self.requested_ids(session), ['100', '100'])

    def test_unresolved_singleton_preserves_existing_cache_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / 'catalog' / 'raw'
            raw.mkdir(parents=True)
            (raw / 'Cat_Stud.tsv').write_text('PUBMEDID\n100\n200\n')
            cache_path = root / 'pubmed_grants.json'
            cache = {'version': pipeline.CACHE_VERSION, 'records': {'100': {'grants': []}}}
            pipeline._set_pubmed_cache_metadata(cache, ['100', '200'])
            cache_path.write_text(json.dumps(cache))
            before = cache_path.read_bytes()
            session = self.session(lambda ids: self.response(400, b'Invalid upstream request'))
            with self.assertRaises(pipeline.PubMedCollectionError):
                pipeline.collect_pubmed_grants(
                    str(root), cache_path, request_delay=0, max_retries=2, session=session,
                )
            self.assertEqual(cache_path.read_bytes(), before)
            self.assertEqual(self.requested_ids(session), ['200', '200'])

    def test_xml_error_excerpt_exposes_actual_message_past_long_preamble(self):
        payload = (
            b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE eFetchResult PUBLIC "-//NLM//DTD EFetchResult//EN" '
            b'"https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20060628/efetch.dtd">\n'
            b'<!--' + b'padding ' * 50 + b'-->\n'
            b'<eFetchResult><ERROR>Failed to retrieve documents from PubMed</ERROR></eFetchResult>'
        )
        response = self.response(400, payload)
        excerpt = pipeline._response_excerpt(response)
        self.assertIn('Failed to retrieve documents from PubMed', excerpt)
        self.assertNotIn('<?xml', excerpt)
        self.assertIn('Failed to retrieve documents from PubMed',
                      str(pipeline._pubmed_http_error(response, ['100'])))

    def test_failed_split_sibling_does_not_checkpoint_an_incomplete_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / 'catalog' / 'raw'
            raw.mkdir(parents=True)
            (raw / 'Cat_Stud.tsv').write_text('PUBMEDID\n100\n200\n300\n')
            cache_path = root / 'pubmed_grants.json'
            cache = {'version': pipeline.CACHE_VERSION, 'records': {'100': {'grants': []}}}
            pipeline._set_pubmed_cache_metadata(cache, ['100', '200', '300'])
            cache_path.write_text(json.dumps(cache))
            before = cache_path.read_bytes()
            session = self.session(lambda ids: (
                self.response(content=self.articles('200')) if ids == '200'
                else self.response(400, b'Upstream failure')
            ))
            with self.assertRaises(pipeline.PubMedCollectionError):
                pipeline.collect_pubmed_grants(
                    str(root), cache_path, request_delay=0, max_retries=2, session=session,
                )
            self.assertEqual(cache_path.read_bytes(), before)
            self.assertEqual(self.requested_ids(session), ['200,300', '200,300', '200', '300', '300'])

    def test_all_batch_sizes_have_finite_singleton_split_bound(self):
        for count in (2, 3, 5, 8):
            with self.subTest(count=count):
                pmids = [str(100 + number) for number in range(count)]
                session = self.session(lambda ids: (
                    self.response(400, b'Batch rejected') if ',' in ids
                    else self.response(content=self.articles(ids))
                ))
                result = self.fetch(session, pmids)
                self.assertEqual(set(result), set(pmids))
                self.assertEqual(session.post.call_count, 2 * (count - 1) + count)


if __name__ == '__main__':
    unittest.main()
