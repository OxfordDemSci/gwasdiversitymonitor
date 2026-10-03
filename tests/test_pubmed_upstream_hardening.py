"""Generation-only PubMed guards; all requests and inputs are synthetic."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import requests

import funder_pipeline as pipeline


class PubMedUpstreamHardeningTests(unittest.TestCase):
    @staticmethod
    def _article(pmid="123", body="<Article />"):
        return (f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID>"
                f"{body}</MedlineCitation></PubmedArticle>")

    @classmethod
    def _xml(cls, pmid="123", body="<Article />"):
        return ("<PubmedArticleSet>" + cls._article(pmid, body)
                + "</PubmedArticleSet>").encode()

    @staticmethod
    def _response(content, status=200, headers=None):
        response = requests.Response()
        response.status_code = status
        response._content = content
        response.headers.update(headers or {})
        return response

    @staticmethod
    def _catalog(directory, contents="PUBMEDID\n123\n"):
        path = Path(directory) / "catalog/raw/Cat_Stud.tsv"
        path.parent.mkdir(parents=True)
        path.write_text(contents, encoding="utf-8")
        return Path(directory) / "pubmed_grants.json"

    @staticmethod
    def _cache(records):
        value = {"version": pipeline.CACHE_VERSION, "records": records}
        pipeline._set_pubmed_cache_metadata(value, list(records))
        return value

    def test_catalog_header_aliases_keep_actual_spelling_and_normalize_legacy_numbers(self):
        for header in ("PUBMEDID", " PUBMED ID ", "\ufeff PUBMED_ID "):
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                self._catalog(directory, header + "\n123.0\n 456 \n123\n\n")
                self.assertEqual(pipeline.read_publication_ids(directory), ["123", "456"])

    def test_missing_duplicate_empty_or_malformed_catalog_ids_fail_before_any_request(self):
        contents = [
            "OTHER\n123\n", "PUBMEDID\tPUBMED ID\n123\t456\n",
            "PUBMEDID\tPUBMEDID\n123\t456\n", "PUBMEDID\n", "PUBMEDID\n \n",
        ]
        contents.extend("PUBMEDID\n" + value + "\n" for value in (
            "unknown", "NA", "0", "-1", "123.5", "１２３", "000123",
        ))
        for content in contents:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = self._catalog(directory, content)
                original = b'{"version":2,"records":{"123":{"grants":[]}}}'
                path.write_bytes(original)
                session = mock.Mock()
                with self.assertRaises(ValueError):
                    pipeline.collect_pubmed_grants(directory, path, session=session)
                session.post.assert_not_called()
                self.assertEqual(path.read_bytes(), original)

    def test_optional_grants_new_names_partial_metadata_and_book_records_remain_valid(self):
        body = ("<Article><GrantList><Grant><Agency>New <i>Research</i> Fund α</Agency>"
                "</Grant><Grant><GrantID>future-42</GrantID></Grant></GrantList></Article>")
        records = pipeline.parse_pubmed_grants(self._xml(body=body), ["123"])
        self.assertEqual(records["123"]["grants"][0]["agency"], "New Research Fund α")
        self.assertEqual(records["123"]["grants"][1]["grantId"], "future-42")
        self.assertEqual(pipeline.parse_pubmed_grants(self._xml(), ["123"]),
                         {"123": {"grants": []}})
        book = (b"<PubmedBookArticleSet><PubmedBookArticle><BookDocument>"
                b"<PMID>456</PMID><Book><BookTitle>New book</BookTitle></Book>"
                b"</BookDocument></PubmedBookArticle></PubmedBookArticleSet>")
        self.assertEqual(pipeline.parse_pubmed_grants(book, ["456"]),
                         {"456": {"grants": []}})

    def test_missing_body_duplicate_identity_and_unrequested_records_are_not_unfunded(self):
        invalid = [
            self._xml(body=""), self._xml(pmid="unknown"), self._xml(pmid="456"),
            self._xml(body="<Article /><Article />"),
            self._xml(body="<PMID>123</PMID><Article />"),
            ("<PubmedArticleSet>" + self._article() * 2 + "</PubmedArticleSet>").encode(),
            b"<html>" + self._article().encode() + b"</html>",
            b"<PubmedArticleSet><NewArticle><PMID>123</PMID></NewArticle></PubmedArticleSet>",
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(pipeline.PubMedResponseError):
                pipeline.parse_pubmed_grants(payload, ["123"])

    def test_xml_errors_empty_grants_and_unsupported_structural_fields_fail_explicitly(self):
        malformed_bodies = [
            "<Article><GrantList><Grant /></GrantList></Article>",
            "<Article><GrantList><Unexpected>Agency</Unexpected></GrantList></Article>",
            "<Article><GrantList><Grant><Agency>A</Agency><Agency>B</Agency></Grant></GrantList></Article>",
            "<Article><grantList><Grant><Agency>A</Agency></Grant></grantList></Article>",
            '<Article><x:GrantList xmlns:x="urn:changed-schema" /></Article>',
        ]
        for body in malformed_bodies:
            with self.subTest(body=body), self.assertRaises(pipeline.PubMedResponseError):
                pipeline.parse_pubmed_grants(self._xml(body=body), ["123"])
        for error in (b"<ERROR />", b"<error>unavailable</error>",
                      b'<n:ERROR xmlns:n="urn:ncbi">unavailable</n:ERROR>'):
            with self.subTest(error=error), self.assertRaisesRegex(
                    pipeline.PubMedResponseError, "API returned an error"):
                pipeline.parse_pubmed_grants(b"<eFetchResult>" + error + b"</eFetchResult>", ["123"])

    def test_external_doctype_is_allowed_but_custom_entity_declarations_are_rejected(self):
        doctype = b'<!DOCTYPE PubmedArticleSet PUBLIC "NLM PubMed" "https://example.invalid/pubmed.dtd">'
        self.assertEqual(pipeline.parse_pubmed_grants(doctype + self._xml(), ["123"]),
                         {"123": {"grants": []}})
        declaration = b'<!DOCTYPE PubmedArticleSet [<!ENTITY agency "Injected">]>'
        with self.assertRaisesRegex(pipeline.PubMedResponseError, "entity declaration"):
            pipeline.parse_pubmed_grants(declaration + self._xml(), ["123"])

    def test_parser_rejects_invalid_requested_id_instead_of_dropping_it(self):
        with self.assertRaisesRegex(pipeline.PubMedResponseError, "invalid PubMed ID"):
            pipeline.parse_pubmed_grants(self._xml(), ["123", "bad-id"])

    def test_truncated_and_structurally_incomplete_successes_retry_before_cache_write(self):
        for bad in (self._xml()[:-12], self._xml(body="")):
            with self.subTest(payload=bad), tempfile.TemporaryDirectory() as directory:
                path = self._catalog(directory)
                session = mock.Mock()
                session.post.side_effect = [self._response(bad), self._response(self._xml())]
                with mock.patch("funder_pipeline.time.sleep") as sleep:
                    result = pipeline.collect_pubmed_grants(directory, path, session=session,
                                                            request_delay=0, max_retries=2)
                self.assertEqual(session.post.call_count, 2)
                sleep.assert_called_once_with(1)
                self.assertEqual(result["records"], {"123": {"grants": []}})
                self.assertEqual(json.loads(path.read_text()), result)

    def test_unreadable_unknown_or_ambiguous_cache_is_preserved_without_network(self):
        invalid = [
            '{"version":2,"records":', "[]", '{"version":2,"records":[]}',
            '{"version":999,"records":{}}', '{"version":true,"records":{}}',
            '{"version":2,"records":{"123":{"grants":[]},"123":{"grants":[]}}}',
            '{"version":2,"records":{"123":{"grants":[]},"123.0":{"grants":[]}}}',
            '{"version":2,"records":{"bad-id":{"grants":[]}}}',
            '{"version":2,"records":{"123":{"grants":[{"agency":"A","agency":"B"}]}}}',
            '{"version":2,"records":{},"metadata":NaN}',
        ]
        for content in invalid:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = self._catalog(directory)
                path.write_text(content)
                session = mock.Mock()
                with self.assertRaises(ValueError):
                    pipeline.collect_pubmed_grants(directory, path, session=session)
                session.post.assert_not_called()
                self.assertEqual(path.read_text(), content)
        with mock.patch("funder_pipeline.open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(ValueError, "Cannot safely read PubMed funding cache"):
                pipeline._load_pubmed_cache_for_collection("unused")

    def test_invalid_grant_field_types_are_retrieved_again_not_used_as_funder_names(self):
        for invalid in ([], {}, None, 123, True):
            with self.subTest(value=invalid), tempfile.TemporaryDirectory() as directory:
                path = self._catalog(directory)
                cache = self._cache({"123": {"grants": [{"agency": invalid}]}})
                path.write_text(json.dumps(cache))
                session = mock.Mock()
                session.post.return_value = self._response(self._xml())
                with self.assertLogs(pipeline.LOGGER, level="WARNING") as logs:
                    result = pipeline.collect_pubmed_grants(directory, path, session=session, request_delay=0)
                self.assertIn("Revalidating 1 invalid", " ".join(logs.output))
                self.assertEqual(session.post.call_args.kwargs["data"]["id"], "123")
                self.assertEqual(result["records"]["123"], {"grants": []})

    def test_failed_record_revalidation_does_not_replace_existing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._catalog(directory)
            original = json.dumps(self._cache({"123": {"grants": [{}]}})).encode()
            path.write_bytes(original)
            session = mock.Mock()
            session.post.return_value = self._response(self._xml(body=""))
            with mock.patch("funder_pipeline.time.sleep"), self.assertRaises(RuntimeError):
                pipeline.collect_pubmed_grants(directory, path, session=session,
                                               request_delay=0, max_retries=2)
            self.assertEqual(path.read_bytes(), original)

    def test_batch_checkpoints_remain_incomplete_until_all_requested_records_arrive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._catalog(directory, "PUBMEDID\n123\n456\n")
            session = mock.Mock()
            session.post.side_effect = [self._response(self._xml()), self._response(b"<error />")]
            with self.assertRaises(RuntimeError):
                pipeline.collect_pubmed_grants(directory, path, session=session,
                                               request_delay=0, batch_size=1, max_retries=1)
            checkpoint = json.loads(path.read_text())
            self.assertEqual(checkpoint["retrievedPublicationCount"], 1)
            with self.assertRaisesRegex(ValueError, "missing Catalog PMIDs: 456"):
                pipeline.validate_pubmed_cache(checkpoint, ["123", "456"])
            session.post.reset_mock()
            session.post.side_effect = None
            session.post.return_value = self._response(self._xml("456"))
            result = pipeline.collect_pubmed_grants(directory, path, session=session, request_delay=0)
            self.assertEqual(session.post.call_args.kwargs["data"]["id"], "456")
            self.assertEqual(result["retrievedPublicationCount"], 2)

    def test_retrieval_metadata_rejects_boolean_counts(self):
        cache = self._cache({"123": {"grants": []}})
        cache["publicationCount"] = True
        with self.assertRaisesRegex(ValueError, "inconsistent retrieval metadata"):
            pipeline.validate_pubmed_cache(cache, ["123"])

    def test_retry_settings_and_headers_are_finite_and_bounded(self):
        for kwargs in ({"batch_size": True}, {"batch_size": 1.5},
                       {"max_retries": 0}, {"max_retries": float("inf")},
                       {"request_delay": float("nan")}, {"request_delay": float("inf")},
                       {"request_delay": True}, {"request_delay": "1"}):
            with self.subTest(kwargs=kwargs), mock.patch("funder_pipeline.read_publication_ids") as read:
                with self.assertRaises(ValueError):
                    pipeline.collect_pubmed_grants("unused", "unused", **kwargs)
                read.assert_not_called()
        for value in ("NaN", "inf", "-inf", "invalid-date"):
            self.assertIsNone(pipeline._retry_after_seconds(self._response(b"", headers={"Retry-After": value})))
        with tempfile.TemporaryDirectory() as directory:
            path = self._catalog(directory)
            session = mock.Mock()
            session.post.side_effect = [self._response(b"wait", 429, {"Retry-After": "99999"}),
                                        self._response(self._xml())]
            with mock.patch("funder_pipeline.time.sleep") as sleep:
                pipeline.collect_pubmed_grants(directory, path, session=session,
                                               request_delay=0, max_retries=2)
            sleep.assert_called_once_with(pipeline.PUBMED_MAX_RETRY_DELAY)

    def test_atomic_writer_rejects_nested_nonfinite_values_without_replacing_a_good_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "published.json"
            original = b'{"good":true}\n'
            path.write_bytes(original)
            for compact in (False, True):
                for value in (float("nan"), float("inf"), -float("inf")):
                    with self.subTest(compact=compact, value=value), self.assertRaises(ValueError):
                        pipeline._atomic_json(path, {"rows": [{"value": value}]}, compact=compact)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_funder_publication_gate_uses_strict_json_without_runtime_helper_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "funders"
            root.mkdir()
            path = root / "funder_cleaner.json"
            for invalid in ('{"Agency":"A","Agency":"B"}', '{"Agency":NaN}'):
                path.write_text(invalid)
                with mock.patch.object(pipeline, "funder_artifact_files",
                                       return_value=("funders/funder_cleaner.json",)), \
                        self.assertRaises(ValueError):
                    pipeline.validate_funder_artifacts(directory)
                self.assertEqual(path.read_text(), invalid)

    def test_cache_validator_reports_invalid_keys_without_incidental_sort_errors(self):
        for key in ("bad", "１２３", "0", "000123", 123):
            cache = {"version": pipeline.CACHE_VERSION, "records": {key: {"grants": []}}}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "invalid PMID keys"):
                pipeline.validate_pubmed_cache(cache, ["123"])
        for version in (True, False, "2", 2.0):
            cache = self._cache({"123": {"grants": []}})
            cache["version"] = version
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "invalid version"):
                pipeline.validate_pubmed_cache(cache, ["123"])


if __name__ == "__main__":
    unittest.main()
