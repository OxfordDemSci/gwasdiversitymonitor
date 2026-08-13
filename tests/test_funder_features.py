import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import pandas as pd

from app import app as flask_app
from app.FunderData import FunderDataStore, FunderDataUnavailable
from funder_pipeline import (
    _promote_funder_artifacts,
    build_heat_map,
    build_summary,
    canonical_agency,
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


class FunderArtifactTests(unittest.TestCase):
    def _write_release(self, root):
        funders = root / "funders"
        (funders / "dashboards").mkdir(parents=True)
        (funders / "downloads").mkdir()
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
            self.assertIn("funders/pubmed_grants.json", files)
            self.assertIn("funders/dashboards/safe.json", files)
            self.assertIn("funders/downloads/safe.zip", files)

    def test_normal_pipeline_reuses_cache_before_building_funders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            previous = root / "previous"
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
            collect.assert_called_once_with(
                str(staged), str(staged / "funders" / "pubmed_grants.json")
            )
            build.assert_called_once()


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


if __name__ == "__main__":
    unittest.main()
