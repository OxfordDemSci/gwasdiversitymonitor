import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import pandas as pd

from app import app as flask_app
from app.FunderData import FunderDataStore, FunderDataUnavailable
from app.DashboardFilters import (
    DashboardFilterStore,
    FILTER_SCHEMA_VERSION,
    PRECOMPUTED_FILTER_ARCHIVE,
    PRECOMPUTED_FILTER_MANIFEST,
    normalize_cohort_name,
    split_cohorts,
    validate_precomputed_filter_archive,
)
from funder_pipeline import (
    ARTIFACT_VERSION,
    _promote_funder_artifacts,
    attach_funding_metadata,
    build_doughnut,
    build_funder_artifacts,
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

    def test_doughnut_grouping_preserves_recorded_share_denominators(self):
        merged = pd.DataFrame([
            {"Year": 2020, "parentterm": "Cancer", "STAGE": "initial",
             "Broader": "European", "N": 80, "ASSOCIATION COUNT": 8},
            {"Year": 2020, "parentterm": "Cancer", "STAGE": "initial",
             "Broader": "Asian", "N": 20, "ASSOCIATION COUNT": 2},
            {"Year": 2020, "parentterm": "Cancer", "STAGE": "initial",
             "Broader": "In Part Not Recorded", "N": 100,
             "ASSOCIATION COUNT": 0},
            {"Year": 2020, "parentterm": "Cancer",
             "STAGE": "replication", "Broader": "European", "N": 30,
             "ASSOCIATION COUNT": 0},
        ])

        result = build_doughnut(
            merged, ["European", "Asian"], ["Cancer"], 2020
        )

        self.assertEqual(
            result["doughnut_discovery_studies"]["2020"]["Cancer"]["1"]
            ["value"],
            33.33333333,
        )
        self.assertEqual(
            result["doughnut_discovery_participants"]["2020"]["Cancer"]["1"]
            ["value"],
            40.0,
        )
        self.assertEqual(
            result["doughnut_replication_studies"]["2020"]["Cancer"]["1"]
            ["value"],
            100.0,
        )
        self.assertEqual(
            result["doughnut_associations"]["2020"]["Cancer"]["2"]["value"],
            20.0,
        )

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
        self.assertEqual(report["journalCount"], 2)
        self.assertEqual(report["technologyCount"], 2)
        self.assertEqual(report["summaryStatisticsStudyCount"], 2)
        self.assertEqual(report["studiesWithCohortCount"], 2)
        self.assertEqual(report["studiesWithJournalCount"], 3)
        self.assertEqual(report["studiesWithTechnologyCount"], 3)
        self.assertEqual(report["recordedParticipantCount"], 225)
        self.assertEqual(report["ancestryReportingPercentage"], 90)
        self.assertEqual(report["fundedPublicationCount"], 2)
        self.assertEqual(report["topTraits"][0]["name"], "Trait X")
        self.assertEqual(report["topTraits"][0]["studies"], 2)
        self.assertEqual(report["topTraits"][0]["publications"], 2)
        self.assertEqual(report["topTraits"][0]["associations"], 7)
        self.assertEqual(report["topJournals"][0], {
            "name": "Journal One", "studies": 2, "publications": 1,
            "associations": 5, "studyPercentage": 66.67,
        })
        self.assertEqual(report["topCohorts"][0]["name"], "Cohort A")
        self.assertEqual(report["topCohorts"][0]["publications"], 1)
        self.assertEqual(report["topCohorts"][0]["associations"], 5)
        self.assertEqual(
            report["topTechnologies"][0]["name"],
            "Genome-wide genotyping array",
        )
        self.assertEqual(report["topTechnologies"][0]["publications"], 2)
        self.assertEqual(report["topTechnologies"][0]["associations"], 7)
        self.assertEqual(len(report["annualActivity"]), 2)

    def test_technology_parser_ignores_commas_inside_platform_brackets(self):
        studies = pd.DataFrame([{
            "STUDY ACCESSION": "A", "PUBMEDID": "1",
            "DATE": "2024-01-01", "ASSOCIATION COUNT": 4,
            "DISEASE/TRAIT": "Trait X", "JOURNAL": "Journal One",
            "COHORT": "Cohort A",
            "GENOTYPING TECHNOLOGY": (
                "Genome-wide genotyping array "
                "[Illumina HumanOmni2.5-8, Illumina 660W], "
                "Genome-wide sequencing"
            ),
            "FULL SUMMARY STATISTICS": "yes",
        }])
        ancestry = pd.DataFrame([{
            "STUDY ACCESSION": "A", "DATE": "2024-01-01", "N": 100,
            "STAGE": "initial", "Broader": "European",
            "COUNTRY OF RECRUITMENT": "United Kingdom",
        }])

        report = build_report("Safe", studies, ancestry, {})

        self.assertEqual(report["technologyCount"], 2)
        self.assertEqual(
            {row["name"] for row in report["topTechnologies"]},
            {
                "Genome-wide genotyping array",
                "Genome-wide sequencing",
            },
        )
        self.assertNotIn(
            "Illumina 660W]",
            {row["name"] for row in report["topTechnologies"]},
        )

    def test_recruitment_profile_counts_every_country_token_once_per_row(self):
        studies = pd.DataFrame([{
            "STUDY ACCESSION": "A", "PUBMEDID": "1",
            "DATE": "2024-01-01", "ASSOCIATION COUNT": 4,
            "DISEASE/TRAIT": "Trait X", "JOURNAL": "Journal One",
            "COHORT": "Cohort A",
            "GENOTYPING TECHNOLOGY": "Genome-wide genotyping array",
            "FULL SUMMARY STATISTICS": "yes",
        }])
        ancestry = pd.DataFrame([{
            "STUDY ACCESSION": "A", "DATE": "2024-01-01", "N": 100,
            "STAGE": "initial", "Broader": "European",
            "COUNTRY OF RECRUITMENT": (
                "UK; United States | NR, United Kingdom"
            ),
        }])

        report = build_report("Safe", studies, ancestry, {})
        countries = {row["name"]: row for row in report["topCountries"]}

        self.assertEqual(report["recruitmentCountryCount"], 2)
        self.assertEqual(set(countries), {"United Kingdom", "United States"})
        self.assertEqual(countries["United Kingdom"]["participants"], 100)
        self.assertEqual(countries["United Kingdom"]["records"], 1)
        self.assertEqual(countries["United States"]["participants"], 100)
        self.assertEqual(countries["United States"]["records"], 1)


class DatasetFilterTests(unittest.TestCase):
    @staticmethod
    def _store():
        store = DashboardFilterStore("/tmp/not-used")
        store._dataset_entries = [{
            "id": "large", "name": "Large", "studyCount": 3,
            "publicationCount": 3,
        }, {
            "id": "small", "name": "Small", "studyCount": 1,
            "publicationCount": 1,
        }]
        store._dataset_accessions = {
            "large": frozenset({"A", "B", "C"}),
            "small": frozenset({"D"}),
        }
        store._funder_pmids = {
            "wellcome": frozenset({"1", "2", "4"}),
            "another": frozenset({"3"}),
        }
        store._funder_entries = {
            "wellcome": {
                "slug": "wellcome", "name": "Wellcome",
                "studyCount": 3, "publicationCount": 3,
            },
            "another": {
                "slug": "another", "name": "Another",
                "studyCount": 1, "publicationCount": 1,
            },
        }
        store._funder_accessions = {}
        studies = pd.DataFrame([
            {"STUDY ACCESSION": accession, "PUBMEDID": pmid}
            for accession, pmid in (
                ("A", "1"), ("B", "2"), ("C", "3"), ("D", "4")
            )
        ])
        ancestry = pd.DataFrame([
            {"STUDY ACCESSION": "A", "STAGE": "initial"},
            {"STUDY ACCESSION": "B", "STAGE": "replication"},
            {"STUDY ACCESSION": "C", "STAGE": "initial"},
            {"STUDY ACCESSION": "D", "STAGE": "replication"},
        ])
        bubbles = pd.DataFrame([
            {"ACCESSION": accession} for accession in ("A", "B", "C", "D")
        ])
        store._sources = (
            studies, ancestry, pd.DataFrame(), bubbles, pd.DataFrame()
        )
        return store

    def test_cohort_values_are_split_into_individual_datasets(self):
        self.assertEqual(split_cohorts("UKB| CHIMGEN |"), ["UKB", "CHIMGEN"])
        self.assertEqual(split_cohorts(None), [])

    def test_dashboard_cache_survives_outside_temporary_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DashboardFilterStore(directory)

            self.assertEqual(
                Path(store._cache_root).parent,
                Path(directory) / ".dashboard-filter-cache",
            )

    def test_precomputed_individual_dashboard_is_loaded_from_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / PRECOMPUTED_FILTER_ARCHIVE
            archive_path.parent.mkdir(parents=True)
            payload = {
                "version": FILTER_SCHEMA_VERSION,
                "selection": {"studyCount": 3},
            }
            member = "funders/wellcome.json"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(member, json.dumps(payload))
                archive.writestr(PRECOMPUTED_FILTER_MANIFEST, json.dumps({
                    "version": FILTER_SCHEMA_VERSION,
                    "funderCount": 1,
                    "cohortCount": 0,
                    "members": [member],
                }))

            store = DashboardFilterStore(directory)
            loaded = store._load_precomputed_dashboard((), ("wellcome",))
            manifest = validate_precomputed_filter_archive(directory)

        self.assertEqual(loaded, payload)
        self.assertEqual(manifest["funderCount"], 1)
        self.assertIsNone(
            store._load_precomputed_dashboard(
                ("ukb",), ("wellcome",)
            )
        )

    def test_warm_loads_sources_and_filter_indexes(self):
        store = DashboardFilterStore("/tmp/not-used")
        with mock.patch.object(store, "_ensure_sources") as sources, \
                mock.patch.object(
                    store, "_ensure_dataset_index"
                ) as cohorts, \
                mock.patch.object(
                    store, "_ensure_funder_index"
                ) as funders:
            store.warm()

        sources.assert_called_once_with()
        cohorts.assert_called_once_with()
        funders.assert_called_once_with()

    def test_cohort_normalization_is_case_based_not_fuzzy(self):
        self.assertEqual(
            normalize_cohort_name(" 23ANDME "),
            normalize_cohort_name("23andme"),
        )
        self.assertEqual(len({
            normalize_cohort_name(value)
            for value in ("GRAD", "GRAAD", "GRADS")
        }), 3)

    def test_cohort_index_merges_case_variants_only(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "catalog" / "raw"
            raw.mkdir(parents=True)
            pd.DataFrame([
                {"PUBMED ID": "1", "STUDY ACCESSION": "A",
                 "COHORT": "23ANDME", "ASSOCIATION COUNT": "1"},
                {"PUBMED ID": "2", "STUDY ACCESSION": "B",
                 "COHORT": "23andMe", "ASSOCIATION COUNT": "1"},
                {"PUBMED ID": "3", "STUDY ACCESSION": "C",
                 "COHORT": "GRAD", "ASSOCIATION COUNT": "1"},
                {"PUBMED ID": "4", "STUDY ACCESSION": "D",
                 "COHORT": "GRAAD", "ASSOCIATION COUNT": "1"},
                {"PUBMED ID": "5", "STUDY ACCESSION": "E",
                 "COHORT": "GRADS", "ASSOCIATION COUNT": "1"},
            ]).to_csv(raw / "Cat_Stud.tsv", sep="\t", index=False)
            store = DashboardFilterStore(directory)
            store._ensure_dataset_index()

        names = [entry["name"] for entry in store._dataset_entries]
        case_matches = [
            entry for entry in store._dataset_entries
            if normalize_cohort_name(entry["name"]) == "23andme"
        ]
        self.assertEqual(len(case_matches), 1)
        self.assertEqual(case_matches[0]["studyCount"], 2)
        self.assertTrue({"grad", "graad", "grads"}.issubset(
            {normalize_cohort_name(name) for name in names}
        ))

    def test_multi_selection_unions_within_facets_and_intersects_between(self):
        store = self._store()

        _, _, studies, ancestry, bubbles, _, _ = store._selection(
            ["large", "small"], ["wellcome"]
        )

        self.assertEqual(
            set(studies["STUDY ACCESSION"]), {"A", "B", "D"}
        )
        self.assertEqual(
            set(ancestry["STUDY ACCESSION"]), {"A", "B", "D"}
        )
        self.assertEqual(set(bubbles["ACCESSION"]), {"A", "B", "D"})

    def test_option_counts_follow_opposite_facet_and_stage(self):
        store = self._store()

        discovery = store.cohorts("", ["wellcome"], "initial")
        replication = store.cohorts("", ["wellcome"], "replication")

        self.assertEqual(
            [(entry["id"], entry["studyCount"]) for entry in discovery],
            [("large", 1)],
        )
        self.assertEqual(
            {entry["id"]: entry["studyCount"] for entry in replication},
            {"large": 1, "small": 1},
        )
        self.assertEqual(
            store.funders("well", ["large"], "replication")[0]
            ["publicationCount"],
            1,
        )

    def test_cohort_list_is_ordered_by_study_count(self):
        store = DashboardFilterStore("/tmp/not-used")
        store._dataset_entries = [
            {"id": "small", "name": "Small", "studyCount": 1,
             "publicationCount": 1},
            {"id": "large", "name": "Large", "studyCount": 20,
             "publicationCount": 4},
        ]
        store._dataset_accessions = {}

        results = store.datasets()

        self.assertEqual([entry["id"] for entry in results], ["large", "small"])

    def test_funder_filter_includes_low_frequency_canonical_funders(self):
        with tempfile.TemporaryDirectory() as directory:
            funders = Path(directory) / "funders"
            funders.mkdir()
            cleaner_path = funders / "funder_cleaner.json"
            cleaner_path.write_text('{"Alias": "Canonical"}')
            (funders / "pubmed_grants.json").write_text(json.dumps({
                "records": {"123": {"grants": [{"agency": "Alias"}]}},
            }))

            store = DashboardFilterStore(directory)
            store._facet_studies = pd.DataFrame([{
                "STUDY ACCESSION": "A", "PUBMEDID": "123"
            }])
            store._all_accessions = frozenset({"A"})
            store._accession_pmids = {"A": frozenset({"123"})}
            store._stage_accessions = {
                "initial": frozenset({"A"}),
                "replication": frozenset(),
            }
            store._ensure_funder_index()

        self.assertEqual(store._funder_pmids["canonical"], frozenset({"123"}))
        self.assertEqual(store._funder_entries["canonical"]["studyCount"], 1)


class FunderArtifactTests(unittest.TestCase):
    @staticmethod
    def _complete_report():
        studies = pd.DataFrame([
            {
                "STUDY ACCESSION": "GCST000001", "PUBMEDID": "123",
                "DATE": "2020-01-01", "ASSOCIATION COUNT": 7,
                "DISEASE/TRAIT": "Trait A", "JOURNAL": "Journal A",
                "COHORT": "Cohort A",
                "GENOTYPING TECHNOLOGY": "Genome-wide genotyping array",
                "FULL SUMMARY STATISTICS": "yes",
            },
        ])
        ancestry = pd.DataFrame([
            {
                "STUDY ACCESSION": "GCST000001", "DATE": "2020-01-01",
                "N": 100, "STAGE": "initial", "Broader": "European",
                "COUNTRY OF RECRUITMENT": "United Kingdom",
            },
        ])
        return build_report(
            "Safe", studies, ancestry, {"123": ["Safe"]}
        )

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
            "version": ARTIFACT_VERSION,
            "funders": [{
                "slug": "safe", "name": "Safe", "studyCount": 1,
                "publicationCount": 1,
            }],
        }))
        (funders / "dashboards" / "safe.json").write_text(json.dumps({
            "version": ARTIFACT_VERSION,
            "funder": {
                "slug": "safe", "name": "Safe", "studyCount": 1,
                "publicationCount": 1,
            },
            "bubbleGraph": {}, "tsPlot": {}, "heatMap": {},
            "chloroMap": {}, "doughnutGraph": {}, "summary": {},
            "report": self._complete_report(),
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
                "version": ARTIFACT_VERSION,
                "funders": [{
                    "slug": "safe", "name": "Safe", "studyCount": 1,
                    "publicationCount": 1,
                }]
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

    def test_store_rejects_obsolete_index_version(self):
        with tempfile.TemporaryDirectory() as directory:
            funders = Path(directory) / "funders"
            funders.mkdir()
            (funders / "index.json").write_text(json.dumps({
                "version": ARTIFACT_VERSION - 1,
                "funders": [],
            }))

            with self.assertRaisesRegex(
                    FunderDataUnavailable, "obsolete schema"):
                FunderDataStore(directory).entries()

    def test_store_rejects_dashboard_with_incomplete_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_release(root)
            dashboard_path = (
                root / "funders" / "dashboards" / "safe.json"
            )
            dashboard = json.loads(dashboard_path.read_text())
            dashboard["report"].pop("journalCount")
            dashboard_path.write_text(json.dumps(dashboard))

            with self.assertRaisesRegex(
                    FunderDataUnavailable, "Report data are incomplete"):
                FunderDataStore(directory).dashboard("safe")

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

    def test_release_validator_rejects_legacy_nine_key_report_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_release(root)
            dashboard_path = (
                root / "funders" / "dashboards" / "safe.json"
            )
            dashboard = json.loads(dashboard_path.read_text())
            legacy_keys = {
                "funder", "studyCount", "ancestryRecordCount",
                "participantCount", "grantRecordCount", "firstStudyDate",
                "latestStudyDate", "topTraits", "ancestryPercentages",
            }
            dashboard["report"] = {
                key: value for key, value in dashboard["report"].items()
                if key in legacy_keys
            }
            self.assertEqual(set(dashboard["report"]), legacy_keys)
            dashboard_path.write_text(json.dumps(dashboard))

            with self.assertRaisesRegex(ValueError, "report"):
                validate_funder_artifacts(str(root))

    def test_generated_index_distinguishes_studies_from_publications(self):
        studies = pd.DataFrame([
            {
                "STUDY ACCESSION": accession, "PUBMEDID": pmid,
                "DATE": date, "ASSOCIATION COUNT": association_count,
                "DISEASE/TRAIT": trait, "JOURNAL": "Journal A",
                "COHORT": "Cohort A",
                "GENOTYPING TECHNOLOGY": "Genome-wide genotyping array",
                "FULL SUMMARY STATISTICS": "yes",
            }
            for accession, pmid, date, association_count, trait in (
                ("GCST000001", "123", "2020-01-01", 2, "Trait A"),
                ("GCST000002", "123", "2020-01-02", 3, "Trait B"),
                ("GCST000003", "456", "2021-01-01", 5, "Trait A"),
            )
        ])
        ancestry = pd.DataFrame([
            {
                "STUDY ACCESSION": row["STUDY ACCESSION"],
                "PUBMEDID": row["PUBMEDID"], "DATE": row["DATE"],
                "N": 100, "STAGE": "initial", "Broader": "European",
                "COUNTRY OF RECRUITMENT": "United Kingdom",
            }
            for row in studies.to_dict("records")
        ])
        mappings = pd.DataFrame([
            {"Disease trait": "Trait A", "Parent term": "Trait parent"},
            {"Disease trait": "Trait B", "Parent term": "Trait parent"},
        ])
        bubbles = pd.DataFrame([
            {"PUBMEDID": "123"}, {"PUBMEDID": "456"},
        ])
        cache = {
            "records": {
                "123": {"grants": [{"agency": "Safe"}]},
                "456": {"grants": [{"agency": "Safe"}]},
            }
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            (data / "funders").mkdir(parents=True)
            (data / "summary").mkdir()
            cleaner_path = data / "funders" / "funder_cleaner.json"
            cleaner_path.write_text('{"Safe": "Safe"}')
            (data / "summary" / "uniq_broader.txt").write_text(
                "European\n"
            )
            (data / "summary" / "uniq_parent.txt").write_text(
                "Trait parent\n"
            )
            sources = (
                studies, ancestry, mappings, bubbles, pd.DataFrame()
            )
            with mock.patch(
                    "funder_pipeline._ensure_support_and_synthetic"), \
                    mock.patch(
                        "funder_pipeline._load_sources",
                        return_value=sources), \
                    mock.patch(
                        "funder_pipeline.build_bubble_payload",
                        return_value={}), \
                    mock.patch(
                        "funder_pipeline.build_time_series",
                        return_value={}), \
                    mock.patch(
                        "funder_pipeline.build_heat_map",
                        return_value={}), \
                    mock.patch(
                        "funder_pipeline.build_country_map",
                        return_value={}), \
                    mock.patch(
                        "funder_pipeline.build_doughnut",
                        return_value={}), \
                    mock.patch("funder_pipeline._write_download"), \
                    mock.patch("funder_pipeline._promote_funder_artifacts"):
                index = build_funder_artifacts(
                    str(root), str(data), cache, str(cleaner_path),
                    min_studies=1,
                )

        self.assertEqual(len(index["funders"]), 1)
        self.assertEqual(index["funders"][0]["studyCount"], 3)
        self.assertEqual(index["funders"][0]["publicationCount"], 2)

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
                    return_value={"funders": [{"slug": "safe"}]}) as build, \
                    mock.patch.object(
                        generate_data, "build_precomputed_filter_archive"
                    ) as precompute, mock.patch.object(
                        generate_data, "validate_precomputed_filter_archive",
                        return_value={"funderCount": 1, "cohortCount": 1}
                    ):
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
            precompute.assert_called_once()


class FunderRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = flask_app.test_client()

    def test_index_dashboard_download_and_report_routes(self):
        report = FunderArtifactTests._complete_report()
        dashboard = {
            "funder": {
                "slug": "safe", "name": "Safe", "studyCount": 1,
                "publicationCount": 1,
            },
            "report": report,
        }
        with tempfile.TemporaryDirectory() as directory:
            dashboard_path = Path(directory) / "safe.json"
            dashboard_path.write_text(json.dumps(dashboard))
            download_path = Path(directory) / "safe.zip"
            download_path.write_bytes(b"download")
            store = mock.Mock()
            store.dashboard_path.return_value = str(dashboard_path)
            store.dashboard.return_value = dashboard
            store.entry.return_value = dashboard["funder"]
            store.download_path.return_value = str(download_path)
            with mock.patch(
                    "app.routes.FunderDataStore", return_value=store):
                response = self.client.get("/json/funders/safe.json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "application/json")
                response.close()

                response = self.client.get("/download/funders/safe.zip")
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    "gwas-funder-safe.zip",
                    response.headers["Content-Disposition"],
                )
                response.close()

                response = self.client.get("/reports/funders/safe")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Funding-linked diversity report", response.data)
        self.assertIn(b"funder-report__print-masthead", response.data)
        self.assertIn(b"logo_white_rect.png", response.data)
        self.assertIn(b"Print / save PDF", response.data)
        self.assertNotIn(b'<div id="header"', response.data)
        self.assertNotIn(b'<div id="footer"', response.data)

    def test_funder_report_renders_nonzero_catalog_metadata(self):
        report = FunderArtifactTests._complete_report()
        dashboard = {
            "funder": {"slug": "safe", "name": "Safe"},
            "report": report,
        }
        store = mock.Mock()
        store.dashboard.return_value = dashboard
        with mock.patch(
                "app.routes.FunderDataStore", return_value=store):
            response = self.client.get("/reports/funders/safe")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.data,
            br"<strong>7</strong>\s*<span>Associations</span>",
        )
        self.assertRegex(
            response.data,
            br"<strong>1</strong>\s*<span>Named cohorts</span>",
        )
        self.assertRegex(
            response.data,
            br"<strong>1</strong>\s*<span>Journals</span>",
        )
        self.assertIn(b'<th scope="row">Journal A</th>', response.data)
        self.assertIn(b'<th scope="row">Cohort A</th>', response.data)
        self.assertIn(
            b'<th scope="row">Genome-wide genotyping array</th>',
            response.data,
        )

    def test_unknown_funder_is_not_exposed(self):
        store = mock.Mock()
        store.dashboard_path.side_effect = KeyError("not-a-funder")
        with mock.patch(
                "app.routes.FunderDataStore", return_value=store):
            response = self.client.get(
                "/json/funders/not-a-funder.json"
            )

        self.assertEqual(response.status_code, 404)

    def test_funder_search_filters_names_case_insensitively(self):
        store = mock.Mock()
        store.funders.return_value = [{
            "slug": "wellcome-trust", "name": "Wellcome Trust",
            "studyCount": 10, "publicationCount": 5,
        }]
        with mock.patch(
                "app.routes.get_dashboard_filter_store",
                return_value=store):
            response = self.client.get("/api/funders?search=wellCOME")
        results = response.get_json()["results"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual([entry["text"] for entry in results], [
            "Wellcome Trust",
        ])
        self.assertEqual(results[0]["studyCount"], 10)
        self.assertEqual(results[0]["publicationCount"], 5)
        store.funders.assert_called_once_with("wellCOME", (), "")

    def test_funder_search_accepts_select2_term_parameter(self):
        store = mock.Mock()
        store.funders.return_value = [{
            "slug": "world-health-organization",
            "name": "World Health Organization", "studyCount": 10,
            "publicationCount": 5,
        }]
        with mock.patch(
                "app.routes.get_dashboard_filter_store",
                return_value=store):
            response = self.client.get(
                "/api/funders?term=world-health"
            )
        results = response.get_json()["results"]

        self.assertEqual(
            [entry["text"] for entry in results],
            ["World Health Organization"],
        )
        store.funders.assert_called_once_with("world-health", (), "")

    def test_cohort_search_forwards_funders_and_stage(self):
        store = mock.Mock()
        store.cohorts.return_value = [{
            "id": "chimgen", "name": "CHIMGEN",
            "studyCount": 3, "publicationCount": 2,
        }]
        with mock.patch(
                "app.routes.get_dashboard_filter_store",
                return_value=store):
            response = self.client.get(
                "/api/cohorts?search=CHIMGEN"
                "&funders=wellcome,mrc&stage=replication"
            )
        results = response.get_json()["results"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual([entry["text"] for entry in results], ["CHIMGEN"])
        self.assertEqual(results[0]["studyCount"], 3)
        self.assertEqual(results[0]["publicationCount"], 2)
        store.cohorts.assert_called_once_with(
            "CHIMGEN", ("wellcome", "mrc"), "replication"
        )

    def test_filtered_dashboard_route_passes_multiple_selections(self):
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
                    "?cohorts=ukb,23andme"
                    "&funders=wellcome-trust,mrc"
                )

            self.assertEqual(response.status_code, 200)
            store.dashboard_path.assert_called_once_with(
                ("ukb", "23andme"), ("wellcome-trust", "mrc")
            )
            response.close()

    def test_filtered_dashboard_route_keeps_legacy_query_names(self):
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
                ("ukb",), ("wellcome-trust",)
            )
            response.close()

    def test_cohort_download_and_report_keep_the_selection(self):
        report_payload = {
            "selection": {
                "cohorts": [{"id": "ukb", "name": "UKB"}],
                "funders": [],
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
            store.report.return_value = report_payload["report"]
            with mock.patch(
                    "app.routes.get_dashboard_filter_store",
                    return_value=store):
                download = self.client.get(
                    "/download/filtered-dashboard.zip?cohorts=ukb"
                )
                report = self.client.get(
                    "/reports/filtered-dashboard?cohorts=ukb"
                )

            self.assertEqual(download.status_code, 200)
            self.assertIn(
                "gwas-selection-ukb.zip",
                download.headers["Content-Disposition"],
            )
            self.assertEqual(report.status_code, 200)
            self.assertIn(b"Cohort diversity report", report.data)
            self.assertIn(b"UKB", report.data)
            self.assertIn(b"At a glance", report.data)
            self.assertIn(
                b"Participant and association profile", report.data
            )
            self.assertIn(b"Annual activity", report.data)
            store.download_path.assert_called_once_with(("ukb",), ())
            store.dashboard.assert_called_once_with(("ukb",), ())
            store.report.assert_called_once_with(("ukb",), ())
            download.close()


if __name__ == "__main__":
    unittest.main()
