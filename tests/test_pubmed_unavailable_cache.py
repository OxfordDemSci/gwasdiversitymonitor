"""Confirmed PubMed absence is auditable, not a returned no-grant article."""

import json
from pathlib import Path
import tempfile
import unittest

import funder_pipeline as pipeline


class PubMedUnavailableCacheTests(unittest.TestCase):
    CHECKED_AT = "2026-10-03T12:00:00+00:00"

    def unavailable(self):
        return pipeline.make_unavailable_pubmed_record(self.CHECKED_AT)

    def cache(self, records):
        value = {"version": pipeline.CACHE_VERSION, "records": records}
        pipeline._set_pubmed_cache_metadata(value, sorted(records, key=int))
        return value

    def test_unavailable_record_has_explicit_reason_and_timezone_aware_evidence_time(self):
        record = self.unavailable()
        self.assertEqual(record, {
            "grants": [], "retrievalStatus": "unavailable",
            "reason": "pubmed_record_unavailable", "checkedAt": self.CHECKED_AT,
        })
        self.assertTrue(pipeline.is_pubmed_record_unavailable(record))
        self.assertFalse(pipeline.is_pubmed_record_unavailable({"grants": []}))
        self.assertTrue(pipeline._valid_pubmed_record(record))
        self.assertTrue(pipeline._valid_pubmed_record(pipeline.make_unavailable_pubmed_record()))
        for invalid in ("", "2026-10-03", "2026-10-03T12:00:00", "not a date", 123):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                pipeline.make_unavailable_pubmed_record(invalid)

    def test_unknown_is_neither_retrieved_nor_confirmed_unfunded(self):
        cache = self.cache({
            "100": {"grants": [{"agency": "New agency"}]},
            "200": {"grants": []}, "24513584": self.unavailable(),
        })
        self.assertEqual(pipeline.validate_pubmed_cache(cache, list(cache["records"])), cache)
        self.assertEqual(cache["publicationCount"], 3)
        self.assertEqual(cache["retrievedPublicationCount"], 2)
        self.assertEqual(cache["fundedPublicationCount"], 1)
        self.assertEqual(cache["unfundedPublicationCount"], 1)
        self.assertEqual(cache["unavailablePublicationCount"], 1)
        self.assertEqual(cache["unavailablePublicationIds"], ["24513584"])
        self.assertEqual(cache["grantCount"], 1)

    def test_partial_checkpoint_counts_only_current_catalog_ids(self):
        records = {"1": {"grants": []}, "2": self.unavailable(),
                   "10": self.unavailable(), "99": self.unavailable()}
        metadata = pipeline._pubmed_cache_metadata({"records": records}, ["10", "2", "1", "3"])
        self.assertEqual(metadata["publicationCount"], 4)
        self.assertEqual(metadata["retrievedPublicationCount"], 1)
        self.assertEqual(metadata["unavailablePublicationIds"], ["2", "10"])
        self.assertEqual(metadata["unfundedPublicationCount"], 1)

    def test_metadata_type_repairs_are_marked_for_persistence(self):
        cache = self.cache({"1": {"grants": []}})
        cache["retrievedPublicationCount"] = True
        self.assertTrue(pipeline._set_pubmed_cache_metadata(cache, ["1"]))
        self.assertIs(type(cache["retrievedPublicationCount"]), int)
        self.assertFalse(pipeline._set_pubmed_cache_metadata(cache, ["1"]))

    def test_invalid_unavailable_evidence_cannot_pass_cache_validation(self):
        variations = [dict(self.unavailable(), reason="probably_missing"),
                      dict(self.unavailable(), retrievalStatus="retrieved"),
                      dict(self.unavailable(), checkedAt="2026-10-03T12:00:00"),
                      dict(self.unavailable(), grants=[{"agency": "Invented agency"}])]
        without_timestamp = self.unavailable()
        del without_timestamp["checkedAt"]
        variations.append(without_timestamp)
        for record in variations:
            with self.subTest(record=record):
                self.assertFalse(pipeline._valid_pubmed_record(record))
                cache = self.cache({"1": {"grants": []}, "2": record})
                with self.assertRaisesRegex(ValueError, "invalid records"):
                    pipeline.validate_pubmed_cache(cache, ["1", "2"])

    def test_outage_circuit_breaker_requires_retrieved_peer_and_limits_missing_fraction(self):
        for total, unavailable_count, allowed in ((1, 1, False), (2, 1, True),
                                                   (100, 2, False), (199, 2, False),
                                                   (200, 2, True), (300, 3, True)):
            records = {str(index): self.unavailable() if index <= unavailable_count
                       else {"grants": []} for index in range(1, total + 1)}
            cache = self.cache(records)
            with self.subTest(total=total, unavailable=unavailable_count):
                if allowed:
                    pipeline.validate_pubmed_cache(cache, list(records))
                else:
                    with self.assertRaisesRegex(ValueError, "unavailable-only|safety limit"):
                        pipeline.validate_pubmed_cache(cache, list(records))

    def test_unavailable_metadata_is_checked_against_original_catalog_keys(self):
        correct = self.cache({"1": {"grants": []}, "24513584": self.unavailable()})
        for key, value in (("unavailablePublicationIds", ["24516880"]),
                           ("unavailablePublicationIds", ["24513584", "24513584"]),
                           ("unavailablePublicationIds", "24513584"),
                           ("unavailablePublicationCount", True),
                           ("unfundedPublicationCount", 2),
                           ("retrievedPublicationCount", 2)):
            cache = dict(correct, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, "inconsistent retrieval metadata"):
                pipeline.validate_pubmed_cache(cache, list(correct["records"]))

    def test_version_two_confirmed_empty_records_migrate_without_becoming_unknown(self):
        original = {"version": 2, "records": {
            "1": {"grants": []}, "2": {"grants": [{"agency": "Agency"}]},
        }, "updatedAt": self.CHECKED_AT}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text(json.dumps(original))
            cache, changed = pipeline._load_pubmed_cache_for_collection(path)
            self.assertTrue(changed)
            self.assertEqual(cache["version"], 3)
            self.assertEqual(cache["records"], original["records"])
            self.assertEqual(cache["updatedAt"], self.CHECKED_AT)
            pipeline._set_pubmed_cache_metadata(cache, ["1", "2"])
            self.assertEqual(cache["retrievedPublicationCount"], 2)
            self.assertEqual(cache["unavailablePublicationCount"], 0)
            pipeline.validate_pubmed_cache(cache, ["1", "2"])
            self.assertEqual(json.loads(path.read_text()), original)

    def test_legacy_ambiguous_empty_records_still_need_retrieval(self):
        original = {"version": 1, "records": {
            "1": {"grants": []}, "2": {"grants": [{"agency": "Agency"}]},
        }}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text(json.dumps(original))
            with self.assertLogs(pipeline.LOGGER, level="WARNING"):
                cache, changed = pipeline._load_pubmed_cache_for_collection(path)
            self.assertTrue(changed)
            self.assertEqual(set(cache["records"]), {"2"})
            self.assertEqual(cache["version"], 3)

    def test_current_unavailable_records_remain_explicit_and_old_schema_cannot_invent_them(self):
        current = self.cache({"1": {"grants": []}, "2": self.unavailable()})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text(json.dumps(current))
            loaded, changed = pipeline._load_pubmed_cache_for_collection(path)
            self.assertFalse(changed)
            self.assertEqual(loaded, current)
            path.write_text(json.dumps(dict(current, version=2)))
            with self.assertLogs(pipeline.LOGGER, level="WARNING"):
                migrated, changed = pipeline._load_pubmed_cache_for_collection(path)
            self.assertTrue(changed)
            self.assertEqual(set(migrated["records"]), {"1"})

    def test_normalization_audit_separates_unavailable_from_no_grants_and_keeps_catalog_identity(self):
        cache = self.cache({"1": {"grants": [{"agency": "Agency"}]},
                            "2": {"grants": []}, "24513584": self.unavailable()})
        audit = pipeline.build_funder_normalization_audit(cache, {})
        self.assertEqual(audit["version"], pipeline.FUNDER_NORMALIZATION_AUDIT_VERSION)
        self.assertEqual(audit["publicationCount"], 3)
        self.assertEqual(audit["retrievedPublicationCount"], 2)
        self.assertEqual(audit["publicationsWithGrantListCount"], 1)
        self.assertEqual(audit["publicationsWithoutGrantListCount"], 1)
        self.assertEqual(audit["unavailablePublicationCount"], 1)
        self.assertEqual(audit["unavailablePublicationIds"], ["24513584"])
        self.assertEqual(audit["grantRecordCount"], 1)
        names = pipeline.funding_names_by_publication(cache, {})
        self.assertEqual(names, {"1": ["Agency"], "2": [], "24513584": []})
        self.assertNotIn("24516880", names)


if __name__ == "__main__":
    unittest.main()
