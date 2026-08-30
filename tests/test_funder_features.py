import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import pandas as pd

from app import app as flask_app
from app.FunderData import FunderDataStore, FunderDataUnavailable
from app.DashboardFilters import DashboardFilterStore, split_cohorts
from funder_pipeline import (
    _promote_funder_artifacts,
    attach_funding_metadata,
    build_heat_map,
    build_report,
    build_summary,
    canonical_agency,
    funding_names_by_publication,
    normalize_funding_records,
    parse_pubmed_grants,
    funder_artifact_files,
    validate_funder_artifacts,
)
import generate_data


class PubMedFundingTests(unittest.TestCase):
    def test_parser_preserves_requested_publications_and_deduplicates_grants(self):
        xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation>
          <PMID>123</PMID><Article><GrantList>
            <Grant><GrantID>R01</GrantID><Agency>NHLBI NIH HHS</Agency></Grant>
            <Grant><GrantID>R01</GrantID><Agency>NHLBI NIH HHS</Agency></Grant>
          </GrantList></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""

        records = parse_pubmed_grants(xml, ["123", "456"])

        self.assertEqual(len(records["123"]["grants"]), 1)
        self.assertEqual(records["456"], {"grants": []})

    def test_normalizer_handles_alias_cycles_and_groups_small_funders(self):
        cleaner = {"Agency A": "A", "A": "Agency A"}
        cache = {
            "records": {
                "1": {"grants": [{"agency": "Agency A"}]},
                "2": {"grants": [{"agency": "Agency A"}]},
                "3": {"grants": [{"agency": "Tiny fund"}]},
            }
        }

        records, counts = normalize_funding_records(cache, cleaner, 2)

        self.assertEqual(canonical_agency("Agency A", cleaner), "A")
        self.assertEqual(records["1"], ["A"])
        self.assertEqual(records["3"], ["Other funders"])
        self.assertEqual(counts["A"], 2)

    def test_bubble_metadata_keeps_all_canonical_funder_names(self):
        cache = {
            "records": {
                "123": {"grants": [
                    {"agency": "Wellcome"},
                    {"agency": "Medical Research Council"},
                    {"agency": "Wellcome"},
                ]},
                "456": {"grants": []},
            }
        }
        names = funding_names_by_publication(
            cache, {"Wellcome": "Wellcome Trust"}
        )

        bubbles = attach_funding_metadata(pd.DataFrame([
            {"PUBMEDID": 123.0}, {"PUBMEDID": "456"}
        ]), names)

        self.assertEqual(
            bubbles["FUNDER"].tolist(),
            ["Medical Research Council | Wellcome Trust", ""],
        )


class FunderSummaryTests(unittest.TestCase):
    def test_summary_tracks_metric_and_stage_modes(self):
        ancestry = pd.DataFrame([
            {"Broader": "European", "N": 90, "STAGE": "initial"},
            {"Broader": "Asian", "N": 10, "STAGE": "initial"},
            {"Broader": "Asian", "N": 20, "STAGE": "replication"},
            {"Broader": "In Part Not Recorded", "N": 999, "STAGE": "initial"},
        ])

        summary = build_summary(ancestry)

        self.assertEqual(summary["discoveryParticipants"]["european"], 90)
        self.assertEqual(summary["discoveryStudies"]["european"], 50)
        self.assertEqual(summary["replicationParticipants"]["asian"], 100)
        self.assertEqual(summary["overallParticipants"]["asian"], 25)

    def test_heatmap_uses_available_years_and_string_zero_sentinel(self):
        merged = pd.DataFrame([{
            "Year": 2024,
            "Broader": "European",
            "parentterm": "Cancer",
            "STAGE": "initial",
            "N": 100,
        }])

        heatmap = build_heat_map(
            merged, ["European", "Asian"], ["Cancer"], 2026
        )["heatmap_discovery_participants"]

        self.assertEqual(list(heatmap), ["2024"])
        self.assertEqual(heatmap["2024"]["1"]["value"], "0.0")

    def test_detailed_report_covers_output_participants_and_metadata(self):
        studies = pd.DataFrame([
            {
                "STUDY ACCESSION": "A", "PUBMEDID": "1",
                "DATE": "2020-01-10", "DISEASE/TRAIT": "Trait X",
                "ASSOCIATION COUNT": 2, "JOURNAL": "Journal One",
                "COHORT": "Cohort A|Cohort B",
                "GENOTYPING TECHNOLOGY": "Genome-wide genotyping array",
                "FULL SUMMARY STATISTICS": "yes",
            },
            {
                "STUDY ACCESSION": "B", "PUBMEDID": "1",
                "DATE": "2020-02-10", "DISEASE/TRAIT": "Trait Y",
                "ASSOCIATION COUNT": 3, "JOURNAL": "Journal One",
                "COHORT": "Cohort A",
                "GENOTYPING TECHNOLOGY": "Genome-wide sequencing",
                "FULL SUMMARY STATISTICS": "no",
            },
            {
                "STUDY ACCESSION": "C", "PUBMEDID": "2",
                "DATE": "2022-03-10", "DISEASE/TRAIT": "Trait X",
                "ASSOCIATION COUNT": 5, "JOURNAL": "Journal Two",
                "COHORT": "",
                "GENOTYPING TECHNOLOGY": "Genome-wide genotyping array",
                "FULL SUMMARY STATISTICS": "yes",
            },
        ])
        ancestry = pd.DataFrame([
            {
                "STUDY ACCESSION": "A", "DATE": "2020-01-10",
                "STAGE": "initial", "Broader": "European", "N": 100,
                "COUNTRY OF RECRUITMENT": "UK",
            },
            {
                "STUDY ACCESSION": "B", "DATE": "2020-02-10",
                "STAGE": "replication", "Broader": "Asian", "N": 50,
                "COUNTRY OF RECRUITMENT": "UK",
            },
            {
                "STUDY ACCESSION": "C", "DATE": "2022-03-10",
                "STAGE": "initial", "Broader": "African", "N": 75,
                "COUNTRY OF RECRUITMENT": "United States",
            },
            {
                "STUDY ACCESSION": "C", "DATE": "2022-03-10",
                "STAGE": "initial", "Broader": "In Part Not Recorded",
                "N": 25, "COUNTRY OF RECRUITMENT": "United States",
            },
        ])

        report = build_report(
            "Example Funder", studies, ancestry,
            {"1": ["Funder A"], "2": ["Funder B"]},
        )

        self.assertEqual(report["studyCount"], 3)
        self.assertEqual(report["publicationCount"], 2)
        self.assertEqual(report["participantCount"], 250)
        self.assertEqual(report["associationCount"], 10)
        self.assertEqual(report["traitCount"], 2)
        self.assertEqual(report["cohortCount"], 2)
        self.assertEqual(report["summaryStatisticsStudyCount"], 2)
        self.assertEqual(report["recordedParticipantCount"], 225)
        self.assertEqual(report["ancestryReportingPercentage"], 90)
        self.assertEqual(report["fundedPublicationCount"], 2)
        self.assertEqual(report["topTraits"][0]["name"], "Trait X")
        self.assertEqual(report["topTraits"][0]["studies"], 2)
        self.assertEqual(report["topCohorts"][0]["name"], "Cohort A")
        self.assertEqual(len(report["annualActivity"]), 2)


class DatasetFilterTests(unittest.TestCase):
    def test_cohort_values_are_split_into_individual_datasets(self):
        self.assertEqual(split_cohorts("UKB| CHIMGEN |"), ["UKB", "CHIMGEN"])
        self.assertEqual(split_cohorts(None), [])

    def test_dataset_and_funder_selection_is_an_intersection(self):
        store = DashboardFilterStore("/tmp/not-used")
        store._dataset_entries = [
            {"id": "ukb", "name": "UKB", "studyCount": 2}
        ]
        store._dataset_accessions = {"ukb": frozenset({"A", "B"})}
        store._funder_pmids = {"wellcome": frozenset({"2", "3"})}
        store._funder_entries = {
            "wellcome": {"slug": "wellcome", "name": "Wellcome"}
        }
        studies = pd.DataFrame([
            {"STUDY ACCESSION": "A", "PUBMEDID": "1"},
            {"STUDY ACCESSION": "B", "PUBMEDID": "2"},
            {"STUDY ACCESSION": "C", "PUBMEDID": "3"},
        ])
        ancestry = pd.DataFrame([
            {"STUDY ACCESSION": accession} for accession in ("A", "B", "C")
        ])
        bubbles = pd.DataFrame([
            {"ACCESSION": accession} for accession in ("A", "B", "C")
        ])
        store._sources = (studies, ancestry, pd.DataFrame(), bubbles,
                          pd.DataFrame())

        _, _, selected, selected_ancestry, selected_bubbles, _, _ = \
            store._selection("ukb", "wellcome")

        self.assertEqual(selected["STUDY ACCESSION"].tolist(), ["B"])
        self.assertEqual(selected_ancestry["STUDY ACCESSION"].tolist(), ["B"])
        self.assertEqual(selected_bubbles["ACCESSION"].tolist(), ["B"])

    def test_each_selector_can_be_constrained_by_the_other(self):
        store = DashboardFilterStore("/tmp/not-used")
        store._dataset_entries = [
            {"id": "ukb", "name": "UKB", "studyCount": 2},
            {"id": "other", "name": "Other", "studyCount": 1},
        ]
        store._dataset_accessions = {
            "ukb": frozenset({"A", "B"}),
            "other": frozenset({"C"}),
        }
        store._funder_pmids = {
            "wellcome": frozenset({"2"}), "another": frozenset({"3"})
        }
        store._funder_entries = {}
        studies = pd.DataFrame([
            {"STUDY ACCESSION": "A", "PUBMEDID": "1"},
            {"STUDY ACCESSION": "B", "PUBMEDID": "2"},
            {"STUDY ACCESSION": "C", "PUBMEDID": "3"},
        ])
        store._sources = (
            studies, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame()
        )

        self.assertEqual(
            [entry["id"] for entry in store.datasets("", "wellcome")],
            ["ukb"],
        )
        self.assertEqual(store.funders_for_dataset("ukb"), {"wellcome"})

    def test_dataset_list_remains_complete_and_alphabetical(self):
        store = DashboardFilterStore("/tmp/not-used")
        store._dataset_entries = [
            {"id": f"dataset-{index}", "name": f"Dataset {index}",
             "studyCount": index}
            for index in range(25)
        ]
        store._dataset_accessions = {}

        results = store.datasets()

        self.assertEqual(len(results), 25)
        self.assertEqual(results[0]["name"], "Dataset 0")
        self.assertEqual(results[-1]["name"], "Dataset 24")

    def test_funder_filter_reads_cleaner_from_the_funders_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            funders = Path(directory) / "funders"
            funders.mkdir()
            cleaner_path = funders / "funder_cleaner.json"
            cleaner_path.write_text('{"Alias": "Canonical"}')
            (funders / "index.json").write_text(json.dumps({
                "minimumPublicationCount": 1,
                "funders": [{"slug": "canonical", "name": "Canonical"}],
            }))
            (funders / "pubmed_grants.json").write_text(json.dumps({
                "records": {"123": {"grants": [{"agency": "Alias"}]}},
            }))

            store = DashboardFilterStore(directory)
            store._ensure_funder_index()

        self.assertEqual(store._funder_pmids["canonical"], frozenset({"123"}))


class FunderArtifactTests(unittest.TestCase):
    def _write_release(self, root):
        funders = root / "funders"
        (funders / "dashboards").mkdir(parents=True)
        (funders / "downloads").mkdir()
        (funders / "funder_cleaner.json").write_text(
            '{"Alias": "Canonical"}'
        )
        (funders / "pubmed_grants.json").write_text(json.dumps({
            "version": 1, "records": {"123": {"grants": []}}
        }))
        (funders / "index.json").write_text(json.dumps({
            "version": 1,
            "funders": [{"slug": "safe", "name": "Safe", "studyCount": 1}],
        }))
        (funders / "dashboards" / "safe.json").write_text(json.dumps({
            "version": 1,
            "funder": {"slug": "safe", "name": "Safe"},
            "bubbleGraph": {}, "tsPlot": {}, "heatMap": {},
            "chloroMap": {}, "doughnutGraph": {}, "summary": {},
            "report": {},
        }))
        with zipfile.ZipFile(funders / "downloads" / "safe.zip", "w") as archive:
            for name in ("studies.tsv", "ancestry.tsv", "bubble_df.csv", "funding.csv"):
                archive.writestr(name, "header\n")

    def test_store_only_resolves_indexed_slugs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "funders"
            (root / "dashboards").mkdir(parents=True)
            (root / "downloads").mkdir()
            (root / "index.json").write_text(json.dumps({
                "funders": [{"slug": "safe", "name": "Safe", "studyCount": 1}]
            }))
            (root / "dashboards" / "safe.json").write_text("{}")
            store = FunderDataStore(directory)

            self.assertEqual(store.entry("safe")["name"], "Safe")
            with self.assertRaises(KeyError):
                store.dashboard_path("../secret")

    def test_store_reports_missing_index_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FunderDataUnavailable):
                FunderDataStore(directory).entries()

    def test_staged_release_replaces_complete_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "funders"
            staging = root / ".funders-build"
            for base in (live, staging):
                (base / "dashboards").mkdir(parents=True)
                (base / "downloads").mkdir()
            (live / "dashboards" / "old.json").write_text("old")
            (staging / "dashboards" / "new.json").write_text("new")
            (staging / "downloads" / "new.zip").write_text("zip")
            (staging / "index.json").write_text('{"funders": []}')

            _promote_funder_artifacts(str(staging), str(live))

            self.assertFalse((live / "dashboards" / "old.json").exists())
            self.assertTrue((live / "dashboards" / "new.json").exists())
            self.assertTrue((live / "index.json").exists())

    def test_release_validator_covers_cache_dashboards_and_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_release(root)

            files = validate_funder_artifacts(str(root))

            self.assertEqual(files, funder_artifact_files(str(root)))
            self.assertIn("funders/funder_cleaner.json", files)
            self.assertIn("funders/pubmed_grants.json", files)
            self.assertIn("funders/dashboards/safe.json", files)
            self.assertIn("funders/downloads/safe.zip", files)

    def test_normal_pipeline_reuses_cache_before_building_funders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            previous = root / "previous"
            source_funders = root / "data" / "funders"
            source_funders.mkdir(parents=True)
            (source_funders / "funder_cleaner.json").write_text("{}")
            (previous / "funders").mkdir(parents=True)
            staged.mkdir()
            cache_payload = {"version": 1, "records": {"123": {"grants": []}}}
            (previous / "funders" / "pubmed_grants.json").write_text(
                json.dumps(cache_payload)
            )

            with mock.patch.object(
                    generate_data, "diversity_logger",
                    mock.Mock(), create=True), mock.patch.object(
                    generate_data.funder_pipeline, "collect_pubmed_grants",
                    return_value=cache_payload) as collect, mock.patch.object(
                    generate_data.funder_pipeline, "build_funder_artifacts",
                    return_value={"funders": [{"slug": "safe"}]}) as build:
                generate_data._run_funder_wrangling(
                    str(root), str(staged), str(previous)
                )

            self.assertTrue(
                (staged / "funders" / "pubmed_grants.json").is_file()
            )
            self.assertEqual(
                (staged / "funders" / "funder_cleaner.json").read_text(),
                "{}",
            )
            collect.assert_called_once_with(
                str(staged), str(staged / "funders" / "pubmed_grants.json")
            )
            build.assert_called_once_with(
                str(root),
                str(staged),
                cache_payload,
                str(staged / "funders" / "funder_cleaner.json"),
            )


class FunderRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = flask_app.test_client()

    def test_index_dashboard_download_and_report_routes(self):
        response = self.client.get("/api/funders")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["results"]), 39)

        response = self.client.get(
            "/json/funders/medical-research-council-uk.json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        response.close()

        response = self.client.get(
            "/download/funders/medical-research-council-uk.zip"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "gwas-funder-medical-research-council-uk.zip",
            response.headers["Content-Disposition"],
        )
        response.close()

        response = self.client.get(
            "/reports/funders/medical-research-council-uk"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Funding-linked diversity report", response.data)
        self.assertIn(b"funder-report__print-masthead", response.data)
        self.assertIn(b"logo_white_rect.png", response.data)
        self.assertIn(b"Print / save PDF", response.data)
        self.assertNotIn(b'<div id="header"', response.data)
        self.assertNotIn(b'<div id="footer"', response.data)

    def test_unknown_funder_is_not_exposed(self):
        self.assertEqual(
            self.client.get("/json/funders/not-a-funder.json").status_code,
            404,
        )

    def test_funder_search_filters_names_case_insensitively(self):
        response = self.client.get("/api/funders?search=wellCOME")
        results = response.get_json()["results"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry["text"] for entry in results], ["Wellcome Trust"]
        )

    def test_funder_search_accepts_select2_term_parameter(self):
        response = self.client.get("/api/funders?term=world-health")
        results = response.get_json()["results"]

        self.assertEqual(
            [entry["text"] for entry in results],
            ["World Health Organization"],
        )

    def test_dataset_search_splits_cohort_membership(self):
        response = self.client.get("/api/datasets?search=CHIMGEN")
        results = response.get_json()["results"]

        self.assertEqual(response.status_code, 200)
        self.assertIn("CHIMGEN", [entry["text"] for entry in results])

    def test_filtered_dashboard_route_passes_both_selections(self):
        with tempfile.TemporaryDirectory() as directory:
            dashboard_path = Path(directory) / "dashboard.json"
            dashboard_path.write_text('{"selection":{"studyCount":1}}')
            store = mock.Mock()
            store.dashboard_path.return_value = str(dashboard_path)
            with mock.patch(
                    "app.routes.get_dashboard_filter_store",
                    return_value=store):
                response = self.client.get(
                    "/json/filtered-dashboard.json"
                    "?dataset=ukb&funder=wellcome-trust"
                )

        self.assertEqual(response.status_code, 200)
        store.dashboard_path.assert_called_once_with(
            "ukb", "wellcome-trust"
        )
        response.close()

    def test_dataset_download_and_report_keep_the_selection(self):
        report_payload = {
            "selection": {
                "dataset": {"id": "ukb", "name": "UKB"},
                "funder": None,
                "studyCount": 1,
            },
            "report": {
                "studyCount": 1, "participantCount": 10,
                "ancestryRecordCount": 1, "firstStudyDate": "2020-01-01",
                "latestStudyDate": "2020-01-01", "topTraits": [],
            },
            "summary": {"overallParticipants": {
                "european": 100, "asian": 0, "african": 0,
                "afamafcam": 0, "hisorlatinam": 0, "othermixed": 0,
            }},
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "ukb.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("selection.json", "{}")
            store = mock.Mock()
            store.dataset.return_value = {"id": "ukb", "name": "UKB"}
            store.download_path.return_value = str(archive)
            store.dashboard.return_value = report_payload
            with mock.patch(
                    "app.routes.get_dashboard_filter_store",
                    return_value=store):
                download = self.client.get(
                    "/download/filtered-dashboard.zip?dataset=ukb"
                )
                report = self.client.get(
                    "/reports/filtered-dashboard?dataset=ukb"
                )

        self.assertEqual(download.status_code, 200)
        self.assertIn(
            "gwas-selection-ukb.zip",
            download.headers["Content-Disposition"],
        )
        self.assertEqual(report.status_code, 200)
        self.assertIn(b"Dataset diversity report", report.data)
        self.assertIn(b"UKB", report.data)
        self.assertIn(b"At a glance", report.data)
        self.assertIn(b"Participant and association profile", report.data)
        self.assertIn(b"Annual activity", report.data)
        download.close()


if __name__ == "__main__":
    unittest.main()
