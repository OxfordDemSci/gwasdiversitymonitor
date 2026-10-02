import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from app import app
from app.Comparison import build_comparison, validate_comparison
from app.DashboardFilters import DashboardFilterStore


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "catalog/raw").mkdir(parents=True)
        (root / "catalog/synthetic").mkdir(parents=True)
        (root / "funders").mkdir()
        studies = pd.DataFrame([
            {"STUDY ACCESSION": accession, "PUBMEDID": pmid, "COHORT": cohort,
             "DATE": date, "ASSOCIATION COUNT": 1}
            for accession, pmid, cohort, date in (
                ("A", "1", "Alpha|Beta", "2020-01-01"),
                ("B", "1", "Alpha", "2021-01-01"),
                ("C", "2", "Beta", "2022-01-01"),
                ("D", "3", "", "2022-01-01"),
                ("E", "4", "Alpha", ""),
            )
        ])
        studies.to_csv(root / "catalog/raw/Cat_Stud.tsv", sep="\t", index=False)
        pd.DataFrame([
            {"STUDY ACCESSION": accession, "STAGE": stage, "Broader": ancestry, "N": n}
            for accession, stage, ancestry, n in (
                ("A", "initial", "European", 60),
                ("A", "initial", "African", 40),
                ("A", "initial", "African", 20),
                ("A", "replication", "African", 10),
                ("B", "initial", "European", 80),
                ("C", "initial", "In Part Not Recorded", 50),
                ("D", "initial", "", 10),
                ("E", "initial", "European", 20),
            )
        ]).to_csv(root / "catalog/synthetic/Cat_Anc_wBroader.tsv", sep="\t", index=False)
        (root / "funders/pubmed_grants.json").write_text(json.dumps({
            "records": {
                "1": {"grants": [{"agency": "Funder One"}, {"agency": "Funder Two"}]},
                "2": {"grants": [{"agency": "Funder Two"}]},
                "3": {"grants": []}, "4": {"grants": []},
            },
        }))
        (root / "funders/funder_cleaner.json").write_text("{}")
        self.store = DashboardFilterStore(root, use_precomputed=False)
        self.cohorts = {entry["name"]: entry["id"] for entry in self.store.cohorts()}

    def settings(self, **overrides):
        value = {"left": {}, "right": {}, "stage": "initial", "metric": "participants"}
        value.update(overrides)
        return value

    def compare(self, **overrides):
        return build_comparison(self.store, self.settings(**overrides))

    def test_participant_denominator_and_missingness(self):
        result = self.compare()
        side = result["left"]
        self.assertEqual(side["studyCount"], 5)
        self.assertEqual(side["publicationCount"], 4)
        self.assertEqual(side["participantCount"], 280)
        self.assertEqual(side["denominator"], 220)
        self.assertEqual(side["unrecordedParticipantCount"], 60)
        self.assertEqual(side["fundingCoveragePercentage"], 50)
        self.assertEqual(side["cohortReportingPercentage"], 80)
        self.assertEqual(side["recordedStudyPercentage"], 60)
        self.assertEqual(result["overlap"]["studyCount"], 5)
        self.assertEqual(result["overlap"]["publicationCount"], 4)
        self.assertIsNone(result["overlap"]["uniqueParticipantCount"])
        self.assertIsNone(self.store._sources, "Comparison must not load the association/bubble data")

    def test_or_within_facets_and_across_facets_without_double_counting(self):
        result = self.compare(left={
            "funders": ["funder-one", "funder-two", "funder-one"],
            "cohorts": [self.cohorts["Alpha"], self.cohorts["Beta"]],
        }, right={"funders": ["funder-one"], "cohorts": [self.cohorts["Beta"]]})
        self.assertEqual(result["left"]["studyCount"], 3)
        self.assertEqual(result["left"]["participantCount"], 250)
        self.assertEqual(result["right"]["studyCount"], 1)
        self.assertEqual(result["right"]["participantCount"], 120)
        self.assertEqual(result["overlap"]["studyCount"], 1)
        self.assertEqual(result["overlap"]["publicationCount"], 1)

    def test_distinct_studies_are_not_ancestry_row_counts(self):
        side = self.compare(metric="studies")["left"]
        self.assertEqual(side["denominator"], 3)
        rows = {entry["name"]: entry for entry in side["ancestries"]}
        self.assertEqual(rows["African"]["count"], 1)
        self.assertEqual(rows["European"]["count"], 3)
        self.assertAlmostEqual(sum(entry["percentage"] for entry in rows.values()), 133.33)

    def test_shared_year_and_stage_restrictions_include_both_boundaries(self):
        result = self.compare(fromYear=2020, toYear=2021)
        self.assertEqual(result["left"]["studyCount"], 2)
        self.assertEqual(result["right"]["studyCount"], 2)
        self.assertEqual(result["overlap"]["studyCount"], 2)
        result = self.compare(stage="replication", fromYear=2020, toYear=2020)
        self.assertEqual(result["left"]["participantCount"], 10)
        self.assertEqual(result["left"]["studyCount"], 1)
        self.assertEqual(result["left"]["ancestries"][0]["name"], "African")

    def test_empty_and_unrecorded_are_distinct_from_zero_share(self):
        result = self.compare(fromYear=2022, toYear=2022,
                              left={"funders": ["funder-one"]})
        self.assertTrue(result["left"]["empty"])
        self.assertFalse(result["right"]["empty"])
        self.assertEqual(result["right"]["studyCount"], 2)
        self.assertEqual(result["right"]["participantCount"], 60)
        self.assertEqual(result["right"]["denominator"], 0)
        self.assertIsNone(result["left"]["ancestryReportingPercentage"])
        self.assertEqual(result["right"]["ancestryReportingPercentage"], 0)
        result = self.compare(left={"funders": ["funder-one"]},
                              right={"cohorts": [self.cohorts["Beta"]]}, fromYear=2022)
        self.assertEqual(result["overlap"]["studyCount"], 0)

    def test_unknown_entities_raise(self):
        with self.assertRaises(KeyError):
            self.compare(left={"cohorts": ["missing"]})

    def test_validation_rejects_ambiguous_types_and_oversized_lists(self):
        for overrides in (
            {"stage": "all"}, {"metric": "associations"}, {"fromYear": True},
            {"fromYear": "2020"}, {"toYear": 9000},
            {"fromYear": 2022, "toYear": 2021},
            {"left": {"funders": "funder-one"}},
            {"left": {"cohorts": ["unsafe/path"]}},
            {"right": {"funders": ["funder-one"] * 21}},
            {"left": {"unknown": []}}, {"unknown": "value"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_comparison(self.settings(**overrides))

    def test_route_uses_published_release_and_rejects_bad_requests(self):
        client = app.test_client()
        with mock.patch("app.routes.DataLoader.published_data_lock",
                        return_value=contextlib.nullcontext(self.directory.name)) as lock, \
                mock.patch("app.routes.get_dashboard_filter_store", return_value=self.store) as factory:
            response = client.post("/api/comparison", json=self.settings())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["left"]["studyCount"], 5)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            lock.assert_called_once_with()
            factory.assert_called_once_with(self.directory.name)
            response = client.post("/api/comparison", json=self.settings(left={"funders": ["missing"]}))
            self.assertEqual(response.status_code, 404)
        for payload, status in (("{broken", 400), (" " * 16385, 413), ("null", 400)):
            response = client.post("/api/comparison", data=payload, content_type="application/json")
            self.assertEqual(response.status_code, status)
        self.assertEqual(client.post("/api/comparison", data="{}").status_code, 415)


if __name__ == "__main__":
    unittest.main()
