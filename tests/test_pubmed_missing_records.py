"""No-network regression tests for omitted versus genuinely unavailable PMIDs."""

import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import requests

import funder_pipeline as pipeline


class PubMedMissingRecordTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.raw = self.root / 'catalog' / 'raw'
        self.raw.mkdir(parents=True)
        self.cache_path = self.root / 'pubmed_grants.json'
        patch = mock.patch('funder_pipeline.time.sleep')
        self.sleep = patch.start()
        self.addCleanup(patch.stop)

    def catalog(self, pmids):
        (self.raw / 'Cat_Stud.tsv').write_text('PUBMEDID\n' + '\n'.join(pmids) + '\n')

    @staticmethod
    def response(status=200, content=b''):
        response = requests.Response()
        response.status_code = status
        response.encoding = 'utf-8'
        if isinstance(content, dict):
            content = json.dumps(content).encode('utf-8')
            response.headers['Content-Type'] = 'application/json'
        response._content = content
        return response

    @staticmethod
    def articles(*pmids, funded=False):
        grant = '<GrantList><Grant><Agency>Agency A</Agency><GrantID>R01</GrantID></Grant></GrantList>' if funded else ''
        return ('<PubmedArticleSet>' + ''.join(
            f'<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID>'
            f'<Article>{grant}</Article></MedlineCitation></PubmedArticle>'
            for pmid in pmids
        ) + '</PubmedArticleSet>').encode('utf-8')

    @staticmethod
    def unavailable_summary(pmid):
        return {'header': {'type': 'esummary'}, 'result': {'uids': [pmid], pmid: {
            'uid': pmid, 'error': 'cannot get document summary',
        }}}

    def session(self, fetch, summary=None):
        def post(url, *, data, **kwargs):
            self.assertEqual(data['db'], 'pubmed')
            if url == pipeline.NCBI_EFETCH_URL:
                self.assertEqual(data['retmode'], 'xml')
                return fetch(data['id'])
            self.assertEqual(url, pipeline.NCBI_ESUMMARY_URL)
            self.assertEqual(data['retmode'], 'json')
            self.assertIsNotNone(summary, 'ESummary is unnecessary for a successfully returned article')
            return summary(data['id'])
        return mock.Mock(post=mock.Mock(side_effect=post))

    def collect(self, session, **kwargs):
        return pipeline.collect_pubmed_grants(
            str(self.root), self.cache_path, request_delay=0,
            max_retries=2, session=session, **kwargs,
        )

    @staticmethod
    def requests_to(session, url):
        return [call.kwargs['data']['id'] for call in session.post.call_args_list
                if call.args[0] == url]

    def test_complete_batch_needs_no_extra_requests_and_keeps_no_grants_distinct(self):
        self.catalog(['123', '456'])
        session = self.session(lambda ids: self.response(content=self.articles('123', '456')))
        cache = self.collect(session)
        self.assertEqual(pipeline.CACHE_VERSION, 3)
        self.assertEqual(cache['records'], {'123': {'grants': []}, '456': {'grants': []}})
        self.assertEqual(cache['retrievedPublicationCount'], 2)
        self.assertEqual(cache['unfundedPublicationCount'], 2)
        self.assertEqual(cache['unavailablePublicationCount'], 0)
        session.post.assert_called_once()
        session.post.reset_mock()
        self.assertEqual(self.collect(session), cache)
        session.post.assert_not_called()

    def test_partial_batch_retries_only_omitted_id_and_recovers_article(self):
        self.catalog(['123', '456', '789'])
        session = self.session(lambda ids: self.response(content=(
            self.articles('123', '789') if ids == '123,456,789'
            else self.articles('456', funded=True)
        )))
        cache = self.collect(session)
        self.assertEqual(self.requests_to(session, pipeline.NCBI_EFETCH_URL), ['123,456,789', '456'])
        self.assertEqual(cache['retrievedPublicationCount'], 3)
        self.assertEqual(cache['fundedPublicationCount'], 1)
        self.assertEqual(cache['unfundedPublicationCount'], 2)
        self.assertEqual(cache['unavailablePublicationCount'], 0)
        self.assertEqual(cache['records']['456']['grants'][0]['agency'], 'Agency A')

    def make_confirmed_unavailable_cache(self):
        self.catalog(['123', '456'])
        session = self.session(
            lambda ids: self.response(content=(self.articles('123') if ',' in ids else self.articles())),
            lambda pmid: self.response(content=self.unavailable_summary(pmid)),
        )
        return self.collect(session), session

    def test_only_single_empty_fetch_and_exact_summary_confirm_unavailable(self):
        cache, session = self.make_confirmed_unavailable_cache()
        self.assertEqual(self.requests_to(session, pipeline.NCBI_EFETCH_URL), ['123,456', '456'])
        self.assertEqual(self.requests_to(session, pipeline.NCBI_ESUMMARY_URL), ['456'])
        missing = cache['records']['456']
        self.assertEqual(missing['grants'], [])
        self.assertEqual(missing['retrievalStatus'], 'unavailable')
        self.assertEqual(missing['reason'], 'pubmed_record_unavailable')
        self.assertIsNotNone(datetime.datetime.fromisoformat(missing['checkedAt'].replace('Z', '+00:00')).utcoffset())
        self.assertTrue(pipeline.is_pubmed_record_unavailable(missing))
        self.assertFalse(pipeline.is_pubmed_record_unavailable(cache['records']['123']))
        self.assertEqual(cache['publicationCount'], 2)
        self.assertEqual(cache['retrievedPublicationCount'], 1)
        self.assertEqual(cache['fundedPublicationCount'], 0)
        self.assertEqual(cache['unfundedPublicationCount'], 1)
        self.assertEqual(cache['unavailablePublicationCount'], 1)
        self.assertEqual(cache['unavailablePublicationIds'], ['456'])
        self.assertEqual(json.loads(self.cache_path.read_text()), cache)

    def test_unavailable_is_retried_next_generation_and_can_recover_funding(self):
        self.make_confirmed_unavailable_cache()
        session = self.session(lambda ids: self.response(content=self.articles('456', funded=True)))
        cache = self.collect(session)
        self.assertEqual(self.requests_to(session, pipeline.NCBI_EFETCH_URL), ['456'])
        self.assertEqual(cache['unavailablePublicationCount'], 0)
        self.assertEqual(cache['retrievedPublicationCount'], 2)
        self.assertEqual(cache['fundedPublicationCount'], 1)
        self.assertEqual(cache['unfundedPublicationCount'], 1)
        self.assertNotIn('retrievalStatus', cache['records']['456'])

    def test_summary_must_match_uid_and_exact_document_missing_evidence(self):
        invalid = [
            {}, {'error': 'API rate limit exceeded'},
            {'result': {'uids': [], '456': {'uid': '456', 'error': 'cannot get document summary'}}},
            {'result': {'uids': ['999'], '456': {'uid': '456', 'error': 'cannot get document summary'}}},
            {'result': {'uids': ['456', '789'], '456': {'uid': '456', 'error': 'cannot get document summary'}}},
            {'result': {'uids': ['456'], '456': {'uid': '999', 'error': 'cannot get document summary'}}},
            {'result': {'uids': ['456'], '456': {'uid': '456', 'error': 'rate limit exceeded'}}},
            {'result': {'uids': ['456'], '456': {'uid': '456', 'title': 'A valid article'}}},
            {'result': {'uids': ['456'], '456': {'uid': '456', 'error': 'Cannot get document summary'}}},
            b'<html>Service unavailable</html>',
        ]
        self.catalog(['123', '456'])
        for body in invalid:
            if isinstance(body, dict) and 'result' in body:
                body = dict(body, header={'type': 'esummary'})
            with self.subTest(body=body):
                session = self.session(
                    lambda ids: self.response(content=(self.articles('123') if ',' in ids else self.articles())),
                    lambda pmid: self.response(content=body),
                )
                with self.assertRaises(pipeline.PubMedCollectionError):
                    self.collect(session)
                self.assertFalse(self.cache_path.exists(), 'Ambiguous evidence must not replace a funding cache')

    def test_http_failures_or_error_payloads_are_not_unknown_or_unfunded(self):
        self.catalog(['123', '456'])
        failures = [self.response(429, b'Rate limited'), self.response(503, b'Unavailable'),
                    self.response(200, b'<html>Unavailable</html>'),
                    self.response(200, b'<eFetchResult><ERROR>Invalid request</ERROR></eFetchResult>')]
        for response in failures:
            with self.subTest(status=response.status_code, body=response.content):
                session = self.session(lambda ids: (
                    self.response(content=self.articles('123')) if ',' in ids else response
                ))
                with self.assertRaises(pipeline.PubMedCollectionError):
                    self.collect(session)
                self.assertFalse(self.cache_path.exists())
                self.assertEqual(self.requests_to(session, pipeline.NCBI_ESUMMARY_URL), [])

    def test_summary_http_failures_never_confirm_even_with_matching_json(self):
        self.catalog(['123', '456'])
        for status in (201, 206, 429, 503):
            with self.subTest(status=status):
                self.cache_path.unlink(missing_ok=True)
                session = self.session(
                    lambda ids: self.response(content=(self.articles('123') if ',' in ids else self.articles())),
                    lambda pmid: self.response(status, self.unavailable_summary(pmid)),
                )
                with self.assertRaises(pipeline.PubMedCollectionError):
                    self.collect(session)
                self.assertFalse(self.cache_path.exists())

    def test_network_timeout_remains_a_collection_failure_not_missing_metadata(self):
        self.catalog(['123', '456'])
        def fetch(ids):
            if ',' in ids:
                return self.response(content=self.articles('123'))
            raise requests.Timeout('The upstream connection timed out')
        session = self.session(fetch)
        with self.assertRaises(pipeline.PubMedCollectionError):
            self.collect(session)
        self.assertFalse(self.cache_path.exists())
        self.assertEqual(self.requests_to(session, pipeline.NCBI_ESUMMARY_URL), [])

    def test_all_unavailable_fails_closed_even_when_each_id_is_confirmed(self):
        self.catalog(['456'])
        session = self.session(
            lambda ids: self.response(content=self.articles()),
            lambda pmid: self.response(content=self.unavailable_summary(pmid)),
        )
        with self.assertRaises(pipeline.PubMedCollectionError):
            self.collect(session)
        self.assertFalse(self.cache_path.exists())

    def test_unavailable_ceiling_prevents_broad_omissions_from_becoming_a_release(self):
        self.catalog(['123', '456', '789'])
        session = self.session(
            lambda ids: self.response(content=(self.articles('123') if ',' in ids else self.articles())),
            lambda pmid: self.response(content=self.unavailable_summary(pmid)),
        )
        with self.assertRaises(pipeline.PubMedCollectionError):
            self.collect(session)
        self.assertFalse(self.cache_path.exists())

    def test_one_percent_ceiling_allows_two_confirmed_ids_among_two_hundred(self):
        pmids = [str(pmid) for pmid in range(100, 300)]
        self.catalog(pmids)
        known = pmids[:-2]
        session = self.session(
            lambda ids: self.response(content=(self.articles(*known) if ',' in ids else self.articles())),
            lambda pmid: self.response(content=self.unavailable_summary(pmid)),
        )
        cache = self.collect(session, batch_size=200)
        self.assertEqual(cache['publicationCount'], 200)
        self.assertEqual(cache['unavailablePublicationCount'], 2)
        self.assertEqual(cache['retrievedPublicationCount'], 198)
        self.assertEqual(cache['unfundedPublicationCount'], 198)

    def test_failed_recovery_preserves_existing_cache_bytes(self):
        self.catalog(['123'])
        complete = self.session(lambda ids: self.response(content=self.articles('123', funded=True)))
        self.collect(complete)
        previous = self.cache_path.read_bytes()
        self.catalog(['123', '456', '789'])
        failing = self.session(lambda ids: (
            self.response(content=self.articles('456')) if ids == '456,789'
            else self.response(503, b'Unavailable')
        ))
        with self.assertRaises(pipeline.PubMedCollectionError):
            self.collect(failing)
        self.assertEqual(self.cache_path.read_bytes(), previous)

    def test_outage_guard_stops_before_probing_all_missing_ids(self):
        self.catalog(['100', '200', '300', '400', '500'])
        session = self.session(
            lambda ids: self.response(content=(self.articles('100') if ',' in ids else self.articles())),
            lambda pmid: self.response(content=self.unavailable_summary(pmid)),
        )
        with self.assertRaises(pipeline.PubMedCollectionError):
            self.collect(session)
        self.assertEqual(self.requests_to(session, pipeline.NCBI_EFETCH_URL),
                         ['100,200,300,400,500', '200', '300'])
        self.assertFalse(self.cache_path.exists())

    def test_old_unknowns_can_recover_later_without_premature_ceiling(self):
        self.catalog(['100', '200', '300'])
        cache = {
            'version': pipeline.CACHE_VERSION,
            'records': {
                '100': {'grants': []},
                '200': pipeline.make_unavailable_pubmed_record(),
                '300': pipeline.make_unavailable_pubmed_record(),
            },
        }
        pipeline._set_pubmed_cache_metadata(cache, ['100', '200', '300'])
        self.cache_path.write_text(json.dumps(cache))
        session = self.session(
            lambda ids: self.response(content=(self.articles('300') if ids == '300' else self.articles())),
            lambda pmid: self.response(content=self.unavailable_summary(pmid)),
        )
        result = self.collect(session, batch_size=1)
        self.assertEqual(self.requests_to(session, pipeline.NCBI_EFETCH_URL), ['200', '200', '300'])
        self.assertEqual(result['unavailablePublicationIds'], ['200'])
        self.assertEqual(result['retrievedPublicationCount'], 2)


if __name__ == '__main__':
    unittest.main()
