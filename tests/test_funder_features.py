import ast
import json
import os
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pandas as pd
import requests

from app import app as flask_app
from app.FunderData import FunderDataStore, FunderDataUnavailable
from app.DashboardFilters import (
    DashboardFilterStore,
    FILTER_SCHEMA_VERSION,
    PRECOMPUTED_FILTER_ARCHIVE,
    PRECOMPUTED_FILTER_MANIFEST,
    PRECOMPUTED_FILTER_OPTION_MEMBERS,
    build_cohort_normalization_audit,
    canonical_cohort_name,
    load_cohort_cleaner,
    load_precomputed_facet_overview,
    normalize_cohort_name,
    split_cohorts,
    validate_precomputed_filter_archive,
)
from funder_pipeline import (
    ARTIFACT_VERSION,
    CACHE_VERSION,
    FUNDER_NORMALIZATION_AUDIT_VERSION,
    _promote_funder_artifacts,
    attach_funding_metadata,
    build_country_map,
    build_doughnut,
    build_funder_artifacts,
    build_funder_normalization_audit,
    build_heat_map,
    build_report,
    build_study_parent_map,
    build_summary,
    canonical_agency,
    collect_pubmed_grants,
    funding_names_by_publication,
    normalize_funding_records,
    parse_pubmed_grants,
    funder_artifact_files,
    load_funder_cleaner,
    validate_funder_artifacts,
)
import generate_data


class DataImagePackagingTests(unittest.TestCase):
    def test_data_image_copies_generate_data_app_dependencies(self):
        repository_root = Path(__file__).resolve().parents[1]
        source = (repository_root / "generate_data.py").read_text()
        dockerfile = (
            repository_root / "deploy" / "data.Dockerfile"
        ).read_text()
        app_modules = {
            node.module.split(".", 1)[1]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("app.")
        }

        for module in app_modules:
            copy_instruction = f"COPY app/{module}.py app/{module}.py"
            self.assertIn(copy_instruction, dockerfile)

        self.assertIn(
            "COPY data/support/cohort_cleaner.json "
            "data/support/cohort_cleaner.json",
            dockerfile,
        )


class PubMedFundingTests(unittest.TestCase):
    @staticmethod
    def _response(status, content=b"", headers=None):
        response = requests.Response()
        response.status_code = status
        response._content = content
        response.encoding = "utf-8"
        response.headers.update(headers or {})
        return response

    @staticmethod
    def _write_catalog(directory, pmids):
        raw = Path(directory) / "catalog" / "raw"
        raw.mkdir(parents=True)
        pd.DataFrame({"PUBMEDID": pmids}).to_csv(
            raw / "Cat_Stud.tsv", sep="\t", index=False
        )
        return Path(directory) / "pubmed_grants.json"

    def test_parser_distinguishes_no_grants_from_an_omitted_publication(self):
        xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation>
          <PMID>123</PMID><Article><GrantList>
            <Grant><GrantID>R01</GrantID><Agency>NHLBI NIH HHS</Agency></Grant>
            <Grant><GrantID>R01</GrantID><Agency>NHLBI NIH HHS</Agency></Grant>
          </GrantList></Article></MedlineCitation></PubmedArticle>
          <PubmedArticle><MedlineCitation><PMID>456</PMID><Article />
          </MedlineCitation></PubmedArticle></PubmedArticleSet>"""

        records = parse_pubmed_grants(xml, ["123", "456", "789"])

        self.assertEqual(len(records["123"]["grants"]), 1)
        self.assertEqual(records["456"], {"grants": []})
        self.assertNotIn("789", records)

    def test_parser_rejects_a_successful_ncbi_error_payload(self):
        with self.assertRaisesRegex(
                ValueError, "PubMed API returned an error"):
            parse_pubmed_grants(
                b"<eFetchResult><ERROR>Invalid uid 123</ERROR></eFetchResult>",
                ["123"],
            )

    def test_collector_honours_retry_after_for_rate_limits(self):
        xml = (
            b"<PubmedArticleSet><PubmedArticle><MedlineCitation>"
            b"<PMID>123</PMID><Article /></MedlineCitation>"
            b"</PubmedArticle></PubmedArticleSet>"
        )
        session = mock.Mock()
        session.post.side_effect = [
            self._response(429, b"rate limited", {"Retry-After": "3"}),
            self._response(200, xml),
        ]
        with tempfile.TemporaryDirectory() as directory:
            cache_path = self._write_catalog(directory, ["123"])
            with mock.patch("funder_pipeline.time.sleep") as sleep:
                cache = collect_pubmed_grants(
                    directory, cache_path, request_delay=0,
                    max_retries=2, session=session,
                )

            persisted = json.loads(cache_path.read_text())

        self.assertEqual(session.post.call_count, 2)
        sleep.assert_called_once_with(3.0)
        self.assertEqual(cache["version"], CACHE_VERSION)
        self.assertEqual(cache["retrievedPublicationCount"], 1)
        self.assertEqual(cache["unfundedPublicationCount"], 1)
        self.assertEqual(persisted, cache)

    def test_collector_retries_transient_server_errors(self):
        xml = (
            b"<PubmedArticleSet><PubmedArticle><MedlineCitation>"
            b"<PMID>123</PMID><Article /></MedlineCitation>"
            b"</PubmedArticle></PubmedArticleSet>"
        )
        session = mock.Mock()
        session.post.side_effect = [
            self._response(503, b"unavailable"),
            self._response(200, xml),
        ]
        with tempfile.TemporaryDirectory() as directory:
            cache_path = self._write_catalog(directory, ["123"])
            with mock.patch("funder_pipeline.time.sleep") as sleep:
                collect_pubmed_grants(
                    directory, cache_path, request_delay=0,
                    max_retries=2, session=session,
                )

        self.assertEqual(session.post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_collector_fails_permanent_client_errors_without_retrying(self):
        session = mock.Mock()
        session.post.return_value = self._response(400, b"invalid request")
        with tempfile.TemporaryDirectory() as directory:
            cache_path = self._write_catalog(directory, ["123"])
            with mock.patch("funder_pipeline.time.sleep") as sleep, \
                    self.assertRaisesRegex(
                        RuntimeError, "HTTP 400.*batch beginning 123"
                    ):
                collect_pubmed_grants(
                    directory, cache_path, request_delay=0,
                    max_retries=4, session=session,
                )

            self.assertFalse(cache_path.exists())

        session.post.assert_called_once()
        sleep.assert_not_called()

    def test_collector_never_caches_an_incomplete_success_response(self):
        partial = (
            b"<PubmedArticleSet><PubmedArticle><MedlineCitation>"
            b"<PMID>123</PMID><Article /></MedlineCitation>"
            b"</PubmedArticle></PubmedArticleSet>"
        )
        session = mock.Mock()
        def respond(url, *, data, **kwargs):
            if data['retmode'] == 'json':
                return self._response(200, b'{"header":{"type":"esummary"},"result":{"uids":[]}}')
            return self._response(200, partial if data['id'] == '123,456'
                                  else b'<PubmedArticleSet />')
        session.post.side_effect = respond
        with tempfile.TemporaryDirectory() as directory:
            cache_path = self._write_catalog(directory, ["123", "456"])
            with mock.patch("funder_pipeline.time.sleep") as sleep, \
                    self.assertRaisesRegex(
                        RuntimeError, "response omitted requested PMIDs: 456"
                    ):
                collect_pubmed_grants(
                    directory, cache_path, request_delay=0,
                    max_retries=2, session=session,
                )

            self.assertFalse(cache_path.exists())

        self.assertEqual(session.post.call_count, 5)
        sleep.assert_called_once_with(1)

    def test_collector_revalidates_ambiguous_empty_legacy_records(self):
        legacy = {
            "version": 1,
            "records": {
                "123": {"grants": [{"agency": "Agency A"}]},
                "456": {"grants": []},
            },
        }
        xml = (
            b"<PubmedArticleSet><PubmedArticle><MedlineCitation>"
            b"<PMID>456</PMID><Article /></MedlineCitation>"
            b"</PubmedArticle></PubmedArticleSet>"
        )
        session = mock.Mock()
        session.post.return_value = self._response(200, xml)
        with tempfile.TemporaryDirectory() as directory:
            cache_path = self._write_catalog(directory, ["123", "456"])
            cache_path.write_text(json.dumps(legacy))

            cache = collect_pubmed_grants(
                directory, cache_path, request_delay=0, session=session
            )

        requested = session.post.call_args.kwargs["data"]["id"]
        self.assertEqual(requested, "456")
        self.assertEqual(cache["version"], CACHE_VERSION)
        self.assertEqual(set(cache["records"]), {"123", "456"})
        self.assertEqual(cache["records"]["456"], {"grants": []})
        self.assertEqual(cache["fundedPublicationCount"], 1)
        self.assertEqual(cache["unfundedPublicationCount"], 1)

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

    def test_wellcome_aliases_resolve_to_one_canonical_funder(self):
        repository_root = Path(__file__).resolve().parents[1]
        cleaner = load_funder_cleaner(
            repository_root / "data" / "funders" / "funder_cleaner.json"
        )
        aliases = (
            "Wellcome Trust", "wellcome trust (wellcome)",
            "Wellcome Trust (WT)", "Wellcome Trust Discovery Award",
            "Wellcome Trust Investigator Award",
        )
        cache = {"records": {
            str(number): {"grants": [{"agency": alias}]}
            for number, alias in enumerate(aliases, 1)
        }}

        by_publication = funding_names_by_publication(cache, cleaner)

        self.assertEqual(
            {tuple(names) for names in by_publication.values()},
            {("Wellcome Trust",)},
        )

    def test_veterans_names_are_only_merged_by_reviewed_aliases(self):
        cleaner = {
            "U.S. Department of Veterans Affairs": "Veterans Affairs"
        }

        self.assertEqual(
            canonical_agency(
                "U.S. Department of Veterans Affairs", cleaner
            ),
            "Veterans Affairs",
        )
        self.assertEqual(
            canonical_agency("Taipei Veterans General Hospital", cleaner),
            "Taipei Veterans General Hospital",
        )

    def test_structured_nih_paths_resolve_to_their_component(self):
        self.assertEqual(
            canonical_agency(
                "U.S. Department of Health & Human Services | NIH | "
                "National Institute of Mental Health (NIMH)",
                {},
            ),
            "NIMH NIH HHS",
        )
        self.assertEqual(
            canonical_agency(
                "U.S. Department of Health & Human Services | National "
                "Institutes of Health (NIH)",
                {},
            ),
            "NIH (Other)",
        )
        repository_root = Path(__file__).resolve().parents[1]
        cleaner = load_funder_cleaner(
            repository_root / "data" / "funders" / "funder_cleaner.json"
        )
        self.assertEqual(
            canonical_agency(
                "Office of Extramural Research, National Institutes of "
                "Health",
                cleaner,
            ),
            "OER NIH HHS",
        )

    def test_funder_normalization_audit_lists_applied_merges(self):
        cache = {"records": {
            "1": {"grants": [{"agency": "Wellcome Trust"}]},
            "2": {"grants": [{"agency": "Wellcome Trust (WT)"}]},
            "3": {"grants": [{"agency": "Ambiguous source"}]},
            "4": {"grants": [{"grantId": "R01"}]},
            "5": {"grants": []},
        }}
        audit = build_funder_normalization_audit(
            cache, {
                "Wellcome Trust (WT)": "Wellcome Trust",
                "Ambiguous source": "Unclear",
            }
        )

        self.assertEqual(
            audit["version"], FUNDER_NORMALIZATION_AUDIT_VERSION
        )
        self.assertEqual(audit["canonicalNameCount"], 1)
        self.assertEqual(audit["publicationCount"], 5)
        self.assertEqual(audit["publicationsWithGrantListCount"], 4)
        self.assertEqual(audit["publicationsWithoutGrantListCount"], 1)
        self.assertEqual(audit["publicationsWithMappedAgencyCount"], 2)
        self.assertEqual(audit["grantRecordCount"], 4)
        self.assertEqual(audit["grantRecordsWithoutAgencyCount"], 1)
        self.assertEqual(
            audit["mergedGroups"][0]["sourceNames"],
            ["Wellcome Trust", "Wellcome Trust (WT)"],
        )
        self.assertEqual(
            {
                entry["source"]: entry["reason"]
                for entry in audit["excludedAgencySources"]
            },
            {
                "Ambiguous source": "excluded by reviewed normalization rule",
                "(missing agency)": "missing agency in PubMed GrantList",
            },
        )

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
    def test_parent_mapping_uses_mapped_trait_and_uri_fallbacks(self):
        studies = pd.DataFrame([
            {
                "STUDY ACCESSION": "A",
                "DISEASE/TRAIT": "Study-specific protein description",
                "MAPPED_TRAIT": "  BLOOD   PROTEIN amount ",
                "MAPPED_TRAIT_URI": "",
                "ASSOCIATION COUNT": 2,
            },
            {
                "STUDY ACCESSION": "B",
                "DISEASE/TRAIT": "Another new description",
                "MAPPED_TRAIT": "Unknown mapped label",
                "MAPPED_TRAIT_URI": " HTTP://EXAMPLE.ORG/EFO_2 ",
                "ASSOCIATION COUNT": 1,
            },
            {
                "STUDY ACCESSION": "C",
                "DISEASE/TRAIT": "No crosswalk available",
                "MAPPED_TRAIT": "",
                "MAPPED_TRAIT_URI": "",
                "ASSOCIATION COUNT": 0,
            },
            {
                "STUDY ACCESSION": "D",
                "DISEASE/TRAIT": "New multi-trait description",
                "MAPPED_TRAIT": "First trait, second trait",
                "MAPPED_TRAIT_URI": (
                    "http://example.org/efo_2, "
                    "http://example.org/efo_1"
                ),
                "ASSOCIATION COUNT": 3,
            },
        ])
        mappings = pd.DataFrame([
            {
                "Disease trait": "Catalog wording",
                "EFO term": "blood protein amount",
                "EFO URI": "http://example.org/efo_1",
                "Parent term": "Other measurement",
            },
            {
                "Disease trait": "Different catalog wording",
                "EFO term": "different EFO term",
                "EFO URI": "http://example.org/efo_2",
                "Parent term": "Hematological measurement",
            },
        ])

        result = build_study_parent_map(studies, mappings).set_index(
            "STUDY ACCESSION"
        )

        self.assertEqual(result.loc["A", "parentterm"], "Other measurement")
        self.assertEqual(
            result.loc["B", "parentterm"], "Hematological measurement"
        )
        self.assertTrue(pd.isna(result.loc["C", "parentterm"]))
        self.assertEqual(
            result.loc["D", "parentterm"], "Hematological measurement"
        )
        self.assertEqual((result.index == "D").sum(), 1)

    def test_country_map_separates_discovery_and_replication(self):
        ancestry = pd.DataFrame([
            {
                "DATE": "2024-01-01", "N": 100, "STAGE": "initial",
                "COUNTRY OF RECRUITMENT": "United Kingdom",
            },
            {
                "DATE": "2024-02-01", "N": 25,
                "STAGE": "replication",
                "COUNTRY OF RECRUITMENT": "United Kingdom",
            },
        ])
        countries = pd.DataFrame([{
            "Country": "United Kingdom", "2017population": 66000000,
        }])

        result = build_country_map(ancestry, countries, 2024)

        self.assertEqual(
            result["initial"]["2024"]["0"]["participants"], 100
        )
        self.assertEqual(
            result["replication"]["2024"]["0"]["participants"], 25
        )

    def test_country_map_only_emits_years_with_country_data(self):
        ancestry = pd.DataFrame([
            {
                "DATE": "2022-01-01", "N": 100, "STAGE": "initial",
                "COUNTRY OF RECRUITMENT": "U.S.",
            },
            {
                "DATE": "2025-01-01", "N": 50, "STAGE": "replication",
                "COUNTRY OF RECRUITMENT": "NR",
            },
        ])
        countries = pd.DataFrame([{
            "Country": "United States", "2017population": 325000000,
        }])

        result = build_country_map(ancestry, countries, 2026)

        self.assertEqual(list(result["initial"]), ["2022"])
        self.assertEqual(
            result["initial"]["2022"]["0"]["country"], "United States"
        )
        self.assertEqual(result["replication"], {})

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

    def test_heatmap_allows_valid_selections_without_plot_ready_rows(self):
        merged = pd.DataFrame(columns=[
            "Year", "Broader", "parentterm", "STAGE", "N",
        ])

        heatmap = build_heat_map(
            merged, ["European", "Asian"], ["Cancer"], 2026
        )

        self.assertTrue(heatmap)
        self.assertTrue(all(not years for years in heatmap.values()))

    def test_doughnut_allows_valid_selections_without_plot_ready_rows(self):
        merged = pd.DataFrame(columns=[
            "Year", "Broader", "parentterm", "STAGE", "N",
            "ASSOCIATION COUNT",
        ])

        doughnut = build_doughnut(
            merged, ["European", "Asian"], ["Cancer"], 2026
        )

        self.assertTrue(doughnut)
        self.assertTrue(all(not years for years in doughnut.values()))

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
    def _store(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="gwas-filter-unit-"))
        store = DashboardFilterStore(directory)
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
            {"STUDY ACCESSION": "A", "STAGE": "initial",
             "Broader": "European"},
            {"STUDY ACCESSION": "B", "STAGE": "replication",
             "Broader": "European"},
            {"STUDY ACCESSION": "C", "STAGE": "initial",
             "Broader": "Asian"},
            {"STUDY ACCESSION": "D", "STAGE": "replication",
             "Broader": "In Part Not Recorded"},
        ])
        bubbles = pd.DataFrame([
            {"ACCESSION": accession, "STAGE": stage,
             "DATE": "2020-01-01", "N": 100}
            for accession, stage in (
                ("A", "initial"), ("B", "replication"),
                ("C", "initial"),
            )
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
            option_members = list(
                PRECOMPUTED_FILTER_OPTION_MEMBERS.values()
            )
            conditional_members = [
                "options/cohorts-by-funder/initial/wellcome.json",
                "options/cohorts-by-funder/replication/wellcome.json",
            ]
            facet_overview = {
                "most_common_funder": {
                    "name": "Wellcome", "studyCount": 3,
                    "publicationCount": 1,
                },
                "most_common_cohort": None,
                "funder_count": 1,
                "cohort_count": 0,
                "study_count": 3,
                "funder_linked_study_count": 3,
                "cohort_linked_study_count": 0,
                "funder_linked_study_percentage": 100.0,
                "cohort_linked_study_percentage": 0.0,
            }
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(member, json.dumps(payload))
                for option_member in option_members:
                    archive.writestr(option_member, json.dumps({
                        "version": FILTER_SCHEMA_VERSION,
                        "entries": [{
                            "slug": "wellcome", "name": "Wellcome",
                            "studyCount": 3, "publicationCount": 1,
                        }],
                    }))
                for conditional_member in conditional_members:
                    archive.writestr(conditional_member, json.dumps({
                        "version": FILTER_SCHEMA_VERSION,
                        "entries": [{
                            "id": "ukb", "name": "UKB",
                            "studyCount": 2, "publicationCount": 1,
                        }],
                    }))
                archive.writestr(PRECOMPUTED_FILTER_MANIFEST, json.dumps({
                    "version": FILTER_SCHEMA_VERSION,
                    "funderCount": 1,
                    "cohortCount": 0,
                    "selectorFunderCount": 1,
                    "selectorCohortCount": 0,
                    "facetOverview": facet_overview,
                    "optionMembers": option_members,
                    "conditionalOptionMembers": conditional_members,
                    "members": [
                        member, *conditional_members, *option_members,
                    ],
                }))

            store = DashboardFilterStore(directory)
            loaded = store._load_precomputed_dashboard((), ("wellcome",))
            loaded_options = store._load_precomputed_options(
                "funders", "initial"
            )
            loaded_conditional = \
                store._load_precomputed_conditional_options(
                    "cohorts", "initial", ("wellcome",)
                )
            manifest = validate_precomputed_filter_archive(directory)
            loaded_overview = load_precomputed_facet_overview(directory)

        self.assertEqual(loaded, payload)
        self.assertEqual(manifest["funderCount"], 1)
        self.assertEqual(loaded_overview, facet_overview)
        self.assertEqual(loaded_options[0]["name"], "Wellcome")
        self.assertEqual(loaded_conditional[0]["name"], "UKB")
        self.assertIsNone(
            store._load_precomputed_dashboard(
                ("ukb",), ("wellcome",)
            )
        )

    def test_legacy_precomputed_archive_remains_usable_without_overview(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / PRECOMPUTED_FILTER_ARCHIVE
            archive_path.parent.mkdir(parents=True)
            payload = {
                "version": 7,
                "selection": {"studyCount": 3},
            }
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("funders/wellcome.json", json.dumps(payload))
                archive.writestr(PRECOMPUTED_FILTER_MANIFEST, json.dumps({
                    "version": 7,
                    "members": ["funders/wellcome.json"],
                }))

            store = DashboardFilterStore(directory)

            self.assertEqual(
                store._load_precomputed_dashboard((), ("wellcome",)),
                payload,
            )
            self.assertIsNone(load_precomputed_facet_overview(directory))

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

    def test_facet_overview_reports_top_entities_and_accession_coverage(self):
        store = self._store()
        store._funder_entries["another"]["studyCount"] = 4

        overview = store._build_facet_overview()

        self.assertEqual(overview["most_common_funder"]["name"], "Wellcome")
        self.assertEqual(
            overview["most_common_funder"]["publicationCount"], 3
        )
        self.assertEqual(overview["most_common_cohort"]["name"], "Large")
        self.assertEqual(overview["funder_count"], 2)
        self.assertEqual(overview["cohort_count"], 2)
        self.assertEqual(overview["study_count"], 4)
        self.assertEqual(overview["funder_linked_study_count"], 4)
        self.assertEqual(overview["cohort_linked_study_count"], 4)
        self.assertEqual(overview["funder_linked_study_percentage"], 100.0)
        self.assertEqual(overview["cohort_linked_study_percentage"], 100.0)

    def test_facet_overview_handles_empty_indexes(self):
        store = DashboardFilterStore("/tmp/not-used")
        store._all_accessions = frozenset()
        store._dataset_entries = []
        store._dataset_accessions = {}
        store._dataset_by_id = {}
        store._funder_entries = {}
        store._funder_pmids = {}
        store._funder_accessions = {}

        overview = store._build_facet_overview()

        self.assertIsNone(overview["most_common_funder"])
        self.assertIsNone(overview["most_common_cohort"])
        self.assertEqual(overview["funder_count"], 0)
        self.assertEqual(overview["cohort_count"], 0)
        self.assertEqual(overview["study_count"], 0)
        self.assertEqual(overview["funder_linked_study_percentage"], 0.0)
        self.assertEqual(overview["cohort_linked_study_percentage"], 0.0)

    def test_facet_overview_cache_avoids_index_loading_in_another_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store()
            store._cache_root = directory
            overview = store.facet_overview()
            other_worker = self._store()
            other_worker._cache_root = directory
            with mock.patch.object(
                other_worker, "_build_facet_overview",
                side_effect=AssertionError("Unexpected source scans"),
            ):
                self.assertEqual(other_worker.facet_overview(), overview)

    def test_invalid_overview_cache_is_recomputed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store()
            store._cache_root = directory
            cache_path = Path(directory) / "facet-overview.json"
            for invalid in ("{", '{"funder_count": -1}'):
                cache_path.write_text(invalid)
                overview = store.facet_overview()
                self.assertEqual(overview["funder_count"], 2)
                self.assertEqual(json.loads(cache_path.read_text()), overview)

    def test_overview_works_when_optional_cache_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store()
            store._cache_root = directory
            with mock.patch.object(
                store, "_atomic_json", side_effect=PermissionError,
            ):
                self.assertEqual(store.facet_overview()["funder_count"], 2)

    def test_overview_cache_key_changes_with_input_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "catalog" / "raw" / "Cat_Stud.tsv"
            source.parent.mkdir(parents=True)
            source.write_text("first release")
            before = DashboardFilterStore(directory)._cache_root
            source.write_text("updated release with new studies")
            after = DashboardFilterStore(directory)._cache_root
            self.assertNotEqual(before, after)

    def test_baseline_options_use_the_precomputed_archive(self):
        store = DashboardFilterStore("/tmp/not-used")
        funders = ({
            "slug": "wellcome", "name": "Wellcome Trust",
            "studyCount": 10, "publicationCount": 2,
        },)
        cohorts = ({
            "id": "ukb", "name": "UKB",
            "studyCount": 20, "publicationCount": 3,
        },)
        with mock.patch.object(
                store, "_load_precomputed_options",
                side_effect=[funders, cohorts]) as precomputed, \
                mock.patch.object(
                    store, "_ensure_funder_index"
                ) as funder_index, \
                mock.patch.object(
                    store, "_ensure_dataset_index"
                ) as cohort_index:
            funder_results = store.funders("well", (), "initial")
            cohort_results = store.cohorts("UK", (), "replication")

        self.assertEqual(funder_results[0]["slug"], "wellcome")
        self.assertEqual(cohort_results[0]["id"], "ukb")
        self.assertEqual(precomputed.call_count, 2)
        funder_index.assert_not_called()
        cohort_index.assert_not_called()

    def test_single_opposite_selection_uses_precomputed_options(self):
        store = DashboardFilterStore("/tmp/not-used")
        cohorts = ({
            "id": "ukb", "name": "UKB",
            "studyCount": 2, "publicationCount": 1,
        },)
        funders = ({
            "slug": "wellcome", "name": "Wellcome Trust",
            "studyCount": 2, "publicationCount": 1,
        },)
        with mock.patch.object(
                store, "_load_precomputed_conditional_options",
                side_effect=[cohorts, funders]) as precomputed, \
                mock.patch.object(
                    store, "_ensure_funder_index"
                ) as funder_index, \
                mock.patch.object(
                    store, "_ensure_dataset_index"
                ) as cohort_index:
            cohort_results = store.cohorts(
                "UK", ("wellcome",), "initial"
            )
            funder_results = store.funders(
                "well", ("ukb",), "replication"
            )

        self.assertEqual(cohort_results[0]["id"], "ukb")
        self.assertEqual(funder_results[0]["slug"], "wellcome")
        self.assertEqual(precomputed.call_count, 2)
        funder_index.assert_not_called()
        cohort_index.assert_not_called()

    def test_cohort_normalization_is_case_based_not_fuzzy(self):
        self.assertEqual(
            normalize_cohort_name(" 23ANDME "),
            normalize_cohort_name("23andme"),
        )
        self.assertEqual(len({
            normalize_cohort_name(value)
            for value in ("GRAD", "GRAAD", "GRADS")
        }), 3)

    def test_cohort_cleaner_supports_curated_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            support = Path(directory) / "support"
            support.mkdir()
            (support / "cohort_cleaner.json").write_text(json.dumps({
                "UKBB": "UKB", "UK Biobank": "UKB",
            }))
            cleaner = load_cohort_cleaner(directory)

        self.assertEqual(canonical_cohort_name("ukbb", cleaner), "UKB")
        self.assertEqual(
            canonical_cohort_name("UK Biobank", cleaner), "UKB"
        )
        self.assertEqual(canonical_cohort_name("UKB-PPP", cleaner), "UKB-PPP")

    def test_cohort_normalization_audit_lists_case_and_curated_merges(self):
        studies = pd.DataFrame([
            {"STUDY ACCESSION": "A", "COHORT": "UKB"},
            {"STUDY ACCESSION": "B", "COHORT": "ukb|UKBB"},
        ])
        with tempfile.TemporaryDirectory() as directory:
            support = Path(directory) / "support"
            support.mkdir()
            (support / "cohort_cleaner.json").write_text(json.dumps({
                "UKBB": "UKB",
            }))
            audit = build_cohort_normalization_audit(directory, studies)

        self.assertEqual(audit["canonicalNameCount"], 1)
        self.assertEqual(
            set(audit["mergedGroups"][0]["sourceNames"]),
            {"UKB", "ukb", "UKBB"},
        )

    def test_cohort_index_unions_curated_alias_accessions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "catalog" / "raw"
            support = root / "support"
            raw.mkdir(parents=True)
            support.mkdir()
            (support / "cohort_cleaner.json").write_text(
                '{"UKBB":"UKB","UK Biobank":"UKB"}'
            )
            pd.DataFrame([
                {"PUBMED ID": "1", "STUDY ACCESSION": "A",
                 "COHORT": "UKB", "ASSOCIATION COUNT": "1"},
                {"PUBMED ID": "2", "STUDY ACCESSION": "B",
                 "COHORT": "UKBB", "ASSOCIATION COUNT": "1"},
                {"PUBMED ID": "3", "STUDY ACCESSION": "C",
                 "COHORT": "UK Biobank", "ASSOCIATION COUNT": "1"},
            ]).to_csv(raw / "Cat_Stud.tsv", sep="\t", index=False)
            store = DashboardFilterStore(directory)
            store._ensure_dataset_index()

        self.assertEqual(len(store._dataset_entries), 1)
        self.assertEqual(store._dataset_entries[0]["id"], "ukb")
        self.assertEqual(store._dataset_entries[0]["studyCount"], 3)

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

    def test_cohort_index_resolves_repeated_tokens_once_without_changing_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DashboardFilterStore(directory)
            store._facet_studies = pd.DataFrame([
                {"STUDY ACCESSION": str(index), "PUBMEDID": str(index // 2),
                 "COHORT": value}
                for index, value in enumerate([
                    "A+B|UKBB", "A+B|UKBB", "A B|ukb", "A B|ukb",
                    "UKBB|UKBB", "", None,
                ])
            ])
            with mock.patch("app.DashboardFilters.load_cohort_cleaner",
                            return_value={"ukbb": "UKB"}), \
                    mock.patch("app.DashboardFilters.canonical_cohort_name",
                               wraps=canonical_cohort_name) as canonical:
                store._ensure_dataset_index()
            self.assertEqual(canonical.call_count, 4)
            rows = {row["id"]: row for row in store._dataset_entries}
            self.assertEqual(rows["a-b"]["name"], "A+B")
            self.assertEqual(rows["a-b-2"]["name"], "A B")
            self.assertEqual(rows["ukb"]["studyCount"], 5)
            self.assertEqual(rows["ukb"]["publicationCount"], 3)
            self.assertEqual(store._dataset_accessions["ukb"],
                             frozenset({"0", "1", "2", "3", "4"}))

    def test_study_loader_normalizes_each_distinct_pmid_once(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "catalog" / "raw"
            raw.mkdir(parents=True)
            pd.DataFrame({
                "PUBMED ID": ["123.0", "123.0", " 456 ", "invalid", None],
                "STUDY ACCESSION": list("ABCDE"),
                "ASSOCIATION COUNT": [1] * 5,
            }).to_csv(raw / "Cat_Stud.tsv", sep="\t", index=False)
            store = DashboardFilterStore(directory)
            from funder_pipeline import normalize_pmid
            with mock.patch("app.DashboardFilters.funder_pipeline.normalize_pmid",
                            wraps=normalize_pmid) as normalize:
                studies = store._load_studies()
            self.assertEqual(normalize.call_count, 3)
            self.assertEqual(studies["PUBMEDID"].tolist(),
                             ["123", "123", "456", "", ""])

    def test_facet_index_preserves_multiple_and_missing_publication_ids(self):
        store = DashboardFilterStore("/tmp/not-used")
        studies = pd.DataFrame({
            "STUDY ACCESSION": ["A", "A", "B", "C", "D", None],
            "PUBMEDID": ["123.0", "456", "123.0", "invalid", None, "789"],
        })
        ancestry = pd.DataFrame({"STUDY ACCESSION": list("ABCD")})
        store._set_facet_indexes(studies, ancestry)
        self.assertEqual(store._accession_pmids,
                         {"A": frozenset({"123", "456"}),
                          "B": frozenset({"123"})})
        self.assertEqual(store._counts({"A", "B", "C", "D"})["publicationCount"], 2)

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
        self.assertEqual(set(bubbles["ACCESSION"]), {"A", "B"})

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

    def test_stage_counts_explain_non_plottable_selected_studies(self):
        store = self._store()

        counts = store._dashboard_stage_counts({"D"})["replication"]

        self.assertEqual(counts["studyCount"], 1)
        self.assertEqual(counts["publicationCount"], 1)
        self.assertEqual(counts["recordedAncestryStudyCount"], 0)
        self.assertEqual(counts["bubbleStudyCount"], 0)

    def test_option_cache_reuses_complete_scope_across_search_and_returns_copies(self):
        store = self._store()
        with mock.patch.object(store, "_counts", wraps=store._counts) as counts:
            first = store.cohorts("Large", ["wellcome"], "replication")
            self.assertEqual(counts.call_count, 2)
            first[0]["studyCount"] = 999
            all_options = store.cohorts("", "wellcome,wellcome", "replication")
            self.assertEqual(counts.call_count, 2, "Typing/search must not recount accessions")
            self.assertEqual({row["id"]: row["studyCount"] for row in all_options},
                             {"large": 1, "small": 1})
            self.assertEqual(store.cohorts("small", ["wellcome"], "replication")[0]["id"], "small")
            self.assertEqual(counts.call_count, 2)
        cached = store._option_cache[("cohorts", "replication", ("wellcome",))]
        with self.assertRaises(TypeError):
            cached[0]["studyCount"] = 5
        self.assertEqual(store._dataset_entries[0]["studyCount"], 3)

    def test_option_cache_keys_keep_facets_stage_and_sorted_opposite_selections_separate(self):
        store = self._store()
        with mock.patch.object(store, "_build_option_entries", wraps=store._build_option_entries) as build:
            initial = store.cohorts("", ["wellcome", "another"], "discovery")
            again = store.cohorts("", ["another", "wellcome", "wellcome"], "initial")
            self.assertEqual(initial, again)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(initial[0]["studyCount"], 2)
            self.assertEqual(store.cohorts("", ["wellcome"], "initial")[0]["studyCount"], 1)
            self.assertEqual(len(store.cohorts("", ["wellcome"], "replication")), 2)
            self.assertEqual(store.cohorts("", (), "initial")[0]["studyCount"], 2)
            self.assertEqual(store.funders("", ["large"], "initial")[0]["studyCount"], 1)
            self.assertEqual(build.call_count, 5)
        self.assertEqual(len(store._option_cache), 5)

    def test_option_cache_includes_empty_results_and_evicts_least_recent_context(self):
        store = self._store()
        store.OPTION_CACHE_LIMIT = 2
        with mock.patch.object(store, "_build_option_entries", wraps=store._build_option_entries) as build:
            self.assertEqual(store.funders("", ["small"], "initial"), [])
            self.assertEqual(store.funders("well", ["small"], "initial"), [])
            self.assertEqual(build.call_count, 1)
            store.cohorts("", ["wellcome"], "initial")
            store.funders("", ["small"], "discovery")  # Touch the empty context.
            store.cohorts("", ["wellcome"], "replication")
            self.assertEqual(len(store._option_cache), 2)
            self.assertIn(("funders", "initial", ("small",)), store._option_cache)
            self.assertNotIn(("cohorts", "initial", ("wellcome",)), store._option_cache)
            store.cohorts("", ["wellcome"], "initial")
            self.assertEqual(build.call_count, 4)

    def test_precomputed_scoped_options_share_the_bounded_immutable_cache(self):
        store = DashboardFilterStore("/tmp/not-used")
        entries = ({"id": "ukb", "name": "UKB", "studyCount": 2, "publicationCount": 1},)
        with mock.patch.object(store, "_load_precomputed_conditional_options", return_value=entries) as load, \
                mock.patch.object(store, "_build_option_entries") as live:
            first = store.cohorts("UK", ["wellcome"], "initial")
            entries[0]["studyCount"] = 999
            first[0]["publicationCount"] = 999
            second = store.cohorts("", ["wellcome", "wellcome"], "discovery")
            self.assertEqual(second[0]["studyCount"], 2)
            self.assertEqual(second[0]["publicationCount"], 1)
            load.assert_called_once_with("cohorts", "initial", ("wellcome",))
            live.assert_not_called()

    def test_cached_options_do_not_wait_behind_other_scope_index_loading(self):
        store = self._store()
        expected = store.cohorts(stage="initial")
        started, release = threading.Event(), threading.Event()
        original = store._build_option_entries

        def slow_build(kind, opposite_ids, stage):
            # Index loading holds this lock. An unrelated warmed scope needs
            # only the independent, short-lived option-cache lock.
            with store._lock:
                started.set()
                if not release.wait(5):
                    raise AssertionError("Timed out waiting to release cold index build")
                return original(kind, opposite_ids, stage)

        with mock.patch.object(store, "_build_option_entries", side_effect=slow_build), \
                ThreadPoolExecutor(max_workers=2) as executor:
            cold = executor.submit(store.cohorts, "", ("wellcome",), "initial")
            try:
                self.assertTrue(started.wait(2))
                warm = executor.submit(store.cohorts, "", (), "initial")
                self.assertEqual(warm.result(timeout=2), expected)
            finally:
                release.set()
            self.assertEqual(cold.result(timeout=2)[0]["studyCount"], 1)

    def test_concurrent_same_scope_options_are_built_once(self):
        store = self._store()
        started, release = threading.Event(), threading.Event()
        original = store._build_option_entries

        def slow_build(*args):
            started.set()
            if not release.wait(5):
                raise AssertionError("Timed out waiting to release option build")
            return original(*args)

        with mock.patch.object(store, "_build_option_entries", side_effect=slow_build) as build, \
                ThreadPoolExecutor(max_workers=3) as executor:
            first = executor.submit(store.cohorts, "", ("wellcome",), "initial")
            try:
                self.assertTrue(started.wait(2))
                second = executor.submit(store.cohorts, "", ("wellcome",), "discovery")
                third = executor.submit(store.cohorts, "Large", ("wellcome",), "initial")
            finally:
                release.set()
            result = first.result(timeout=2)
            self.assertEqual(second.result(timeout=2), result)
            self.assertEqual(third.result(timeout=2), result)
            self.assertEqual(build.call_count, 1)
            self.assertIsNot(second.result()[0], result[0])
        self.assertEqual(store._option_building, set())

    def test_failed_option_build_releases_waiting_scope_for_retry(self):
        store = self._store()
        original = store._build_option_entries
        with mock.patch.object(store, "_build_option_entries",
                               side_effect=RuntimeError("temporary read failure")):
            with self.assertRaisesRegex(RuntimeError, "temporary read failure"):
                store.cohorts(stage="initial")
        self.assertEqual(store._option_building, set())
        with mock.patch.object(store, "_build_option_entries", wraps=original) as build:
            self.assertEqual(store.cohorts(stage="initial")[0]["studyCount"], 2)
            self.assertEqual(build.call_count, 1)

    def test_four_baseline_lists_are_reused_by_a_fresh_worker_without_indexes(self):
        store = self._store()
        expected = {}
        for kind in ("cohorts", "funders"):
            for stage in ("initial", "replication"):
                expected[(kind, stage)] = getattr(store, kind)(stage=stage)
        other = DashboardFilterStore(store.data_path)
        with mock.patch.object(other, "_build_option_entries",
                               side_effect=AssertionError("Must reuse complete baseline cache")), \
                mock.patch.object(other, "_ensure_facet_indexes",
                                  side_effect=AssertionError("Must not read source indexes")):
            for (kind, stage), rows in expected.items():
                self.assertEqual(getattr(other, kind)(stage=stage), rows)
        self.assertEqual(len(list(Path(store._cache_root).glob("baseline-options-v1/*.json"))), 4)
        self.assertIsNone(other._sources)
        self.assertIsNone(other._dataset_entries)
        self.assertIsNone(other._funder_entries)

    def test_disk_options_never_cache_arbitrary_selections_searches_or_all_stages(self):
        store = self._store()
        with mock.patch.object(store, "_atomic_json", wraps=store._atomic_json) as write:
            store.cohorts("Large", stage="discovery")
            store.cohorts("Small", stage="initial")
            self.assertEqual(write.call_count, 1)
            store.cohorts("", ["wellcome"], "initial")
            store.cohorts("", ["wellcome", "another"], "replication")
            store.funders("", ["large"], "initial")
            store.cohorts()
            self.assertEqual(write.call_count, 1)

    def test_corrupt_or_mismatched_baseline_options_are_recomputed(self):
        store = self._store()
        expected = store.cohorts(stage="initial")
        path = Path(store._baseline_option_cache_path("cohorts", (), "initial"))
        valid = json.loads(path.read_text())
        invalid = ["{", "null", "[]"]
        for key, value in (("version", -1), ("filterSchema", -1),
                           ("kind", "funders"), ("stage", "replication"),
                           ("entries", {})):
            changed = dict(valid, **{key: value})
            invalid.append(json.dumps(changed))
        for count in ("studyCount", "publicationCount", "recordedAncestryStudyCount"):
            changed = json.loads(json.dumps(valid))
            changed["entries"][0][count] = True
            invalid.append(json.dumps(changed))
        changed = json.loads(json.dumps(valid))
        changed["entries"][0]["recordedAncestryStudyCount"] = 999
        invalid.append(json.dumps(changed))
        changed = json.loads(json.dumps(valid))
        changed["entries"].append(changed["entries"][0])
        invalid.append(json.dumps(changed))
        for content in invalid:
            with self.subTest(content=content[:60]):
                path.write_text(content)
                store._option_cache.clear()
                with mock.patch.object(store, "_build_option_entries",
                                       wraps=store._build_option_entries) as build:
                    self.assertEqual(store.cohorts(stage="initial"), expected)
                    build.assert_called_once()
                self.assertEqual(json.loads(path.read_text()), valid)

    def test_baseline_cache_read_and_write_failures_do_not_break_options(self):
        store = self._store()
        path = store._baseline_option_cache_path("cohorts", (), "initial")
        with mock.patch("builtins.open", side_effect=PermissionError("read-only cache")):
            self.assertIsNone(store._read_baseline_options(path, "cohorts", "initial"))
        # Exercise both index-cache and option-cache writes, not just an
        # already-built index whose cache is no longer consulted.
        store._dataset_entries = None
        store._dataset_accessions = None
        store._facet_studies = store._sources[0].assign(COHORT="Same cohort")
        with mock.patch.object(store, "_atomic_json",
                               side_effect=PermissionError("read-only cache")) as write:
            rows = store.cohorts(stage="initial")
            self.assertEqual(rows[0]["studyCount"], 2)
            self.assertEqual(write.call_count, 2)
        self.assertEqual(store.cohorts(stage="initial"), rows)

    def test_baseline_disk_identity_tracks_atomic_replacements_with_preserved_mtime(self):
        inputs = (
            "catalog/raw/Cat_Stud.tsv", "catalog/synthetic/Cat_Anc_wBroader.tsv",
            "funders/pubmed_grants.json", "funders/funder_cleaner.json",
            "support/cohort_cleaner.json", ".generation_complete.json",
            PRECOMPUTED_FILTER_ARCHIVE,
        )
        for relative in inputs:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"old")
                previous_stat = path.stat()
                first = DashboardFilterStore(directory, use_precomputed=False)
                entries = [{"id": "a", "name": "A", "studyCount": 1,
                            "publicationCount": 1, "recordedAncestryStudyCount": 1}]
                with mock.patch.object(first, "_build_option_entries", return_value=entries):
                    first.cohorts(stage="initial")
                replacement = path.with_name(path.name + ".replacement")
                replacement.write_bytes(b"new")
                os.utime(replacement, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
                replacement.replace(path)
                second = DashboardFilterStore(directory, use_precomputed=False)
                self.assertNotEqual(second._cache_root, first._cache_root)
                updated = [dict(entries[0], studyCount=2)]
                with mock.patch.object(second, "_build_option_entries", return_value=updated) as build:
                    self.assertEqual(second.cohorts(stage="initial"), updated)
                    build.assert_called_once()

    def test_option_recorded_ancestry_counts_follow_the_selected_stage(self):
        store = self._store()
        sources = list(store._sources)
        ancestry = sources[1].copy()
        ancestry.loc[ancestry["STUDY ACCESSION"].eq("A"), "Broader"] = "In Part Not Recorded"
        sources[1] = pd.concat([ancestry, pd.DataFrame([{
            "STUDY ACCESSION": "A", "STAGE": "replication", "Broader": "European",
        }])], ignore_index=True)
        store._sources = tuple(sources)
        initial = store.cohorts("", ["wellcome"], "initial")
        self.assertEqual(initial[0]["studyCount"], 1)
        self.assertEqual(initial[0]["recordedAncestryStudyCount"], 0)
        replication = store.cohorts("large", ["wellcome"], "replication")
        self.assertEqual(replication[0]["studyCount"], 2)
        self.assertEqual(replication[0]["recordedAncestryStudyCount"], 2)
        self.assertEqual(store._counts({"A"})["recordedAncestryStudyCount"], 1)

    def test_recorded_accession_union_is_not_rebuilt_for_each_option_count(self):
        store = self._store()
        store._ensure_facet_indexes()
        class NoRepeatedUnion(dict):
            def values(self):
                raise AssertionError("Release-wide ancestry union must be calculated only once")
        store._recorded_stage_accessions = NoRepeatedUnion(store._recorded_stage_accessions)
        self.assertEqual(store._counts({"A", "D"})["recordedAncestryStudyCount"], 1)
        self.assertEqual(store._counts({"B", "C"})["recordedAncestryStudyCount"], 2)
        self.assertEqual(store._counts({"A", "D"}, "replication")["recordedAncestryStudyCount"], 0)

    def test_live_store_option_cache_invalidates_on_release_or_precomputed_archive_replacement(self):
        from app import DashboardFilters as filters
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(filters, "_stores", {}):
            first = filters.get_dashboard_filter_store(directory)
            self.assertIs(filters.get_dashboard_filter_store(directory), first)
            manifest = Path(directory) / ".generation_complete.json"
            manifest.write_text("{}")
            second = filters.get_dashboard_filter_store(directory)
            self.assertIsNot(second, first)
            archive = Path(directory) / PRECOMPUTED_FILTER_ARCHIVE
            archive.parent.mkdir()
            archive.write_bytes(b"fixture archive identity")
            third = filters.get_dashboard_filter_store(directory)
            self.assertIsNot(third, second)
            replacement = archive.with_suffix(".replacement")
            replacement.write_bytes(archive.read_bytes())
            replacement.replace(archive)
            self.assertIsNot(filters.get_dashboard_filter_store(directory), third)

    def test_small_bubble_subsets_are_embedded_without_large_payloads(self):
        store = self._store()

        empty = store._filtered_bubble_payload({"D"})
        small = store._filtered_bubble_payload({"A", "B"})
        with mock.patch(
                "app.DashboardFilters.BUBBLE_PAYLOAD_ROW_LIMIT", 1):
            large = store._filtered_bubble_payload({"A", "B"})

        self.assertEqual(
            empty["bubblegraph_initial"]["meta"]["rowCount"], 0
        )
        self.assertEqual(
            small["bubblegraph_initial"]["meta"]["rowCount"], 1
        )
        self.assertEqual(
            small["bubblegraph_replication"]["meta"]["rowCount"], 1
        )
        self.assertIsNone(large)

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

    def test_funder_publication_count_excludes_absent_pmids_and_deduplicates_accessions(self):
        with tempfile.TemporaryDirectory() as directory:
            funders = Path(directory) / "funders"
            funders.mkdir()
            (funders / "pubmed_grants.json").write_text(json.dumps({
                "records": {pmid: {"grants": [{"agency": "Test agency"}]}
                            for pmid in ["1", "2", "3", "999"]},
            }))
            store = DashboardFilterStore(directory)
            store._set_facet_indexes(pd.DataFrame({
                "STUDY ACCESSION": ["A", "A", "B", "C", "D"],
                "PUBMEDID": ["1", "2", "1", "3", "4"],
            }), pd.DataFrame({"STUDY ACCESSION": list("ABCD")}))
            store._ensure_funder_index()
            entry = store._funder_entries["test-agency"]
            self.assertEqual(entry["studyCount"], 3)
            self.assertEqual(entry["publicationCount"], 3)
            self.assertEqual(store._funder_accessions["test-agency"],
                             frozenset({"A", "B", "C"}))


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
        raw = root / "catalog" / "raw"
        raw.mkdir(parents=True)
        pd.DataFrame({"PUBMEDID": ["123"]}).to_csv(
            raw / "Cat_Stud.tsv", sep="\t", index=False
        )
        cleaner = {"Alias": "Canonical"}
        (funders / "funder_cleaner.json").write_text(json.dumps(cleaner))
        cache = {
            "version": CACHE_VERSION,
            "records": {"123": {"grants": []}},
            "publicationCount": 1,
            "retrievedPublicationCount": 1,
            "unavailablePublicationCount": 0,
            "unavailablePublicationIds": [],
            "fundedPublicationCount": 0,
            "unfundedPublicationCount": 1,
            "grantCount": 0,
        }
        (funders / "pubmed_grants.json").write_text(json.dumps(cache))
        audit = build_funder_normalization_audit(cache, cleaner)
        (funders / "normalization-audit.json").write_text(json.dumps(audit))
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
            (staging / "normalization-audit.json").write_text("{}")
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

    def test_release_validator_rejects_missing_pubmed_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_release(root)
            cache_path = root / "funders" / "pubmed_grants.json"
            cache = json.loads(cache_path.read_text())
            cache["records"] = {}
            cache["retrievedPublicationCount"] = 0
            cache["unfundedPublicationCount"] = 0
            cache_path.write_text(json.dumps(cache))

            with self.assertRaisesRegex(
                    ValueError, "missing Catalog PMIDs: 123"):
                validate_funder_artifacts(str(root))

    def test_release_accepts_audited_unavailability_without_dropping_study_rows(self):
        from funder_pipeline import make_unavailable_pubmed_record, _set_pubmed_cache_metadata

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_release(root)
            pd.DataFrame({'PUBMEDID': ['123', '24513584']}).to_csv(
                root / 'catalog/raw/Cat_Stud.tsv', sep='\t', index=False,
            )
            cache_path = root / 'funders/pubmed_grants.json'
            cache = json.loads(cache_path.read_text())
            cache['records']['24513584'] = make_unavailable_pubmed_record()
            _set_pubmed_cache_metadata(cache, ['123', '24513584'])
            cache_path.write_text(json.dumps(cache))
            audit = build_funder_normalization_audit(cache, {'Alias': 'Canonical'})
            (root / 'funders/normalization-audit.json').write_text(json.dumps(audit))

            self.assertEqual(validate_funder_artifacts(directory), funder_artifact_files(directory))
            rows = pd.DataFrame({'PUBMEDID': ['123', '24513584'], 'N': [100, 250]})
            result = attach_funding_metadata(rows, funding_names_by_publication(cache, {}))
            pd.testing.assert_frame_equal(result[['PUBMEDID', 'N']], rows)
            self.assertEqual(result.loc[1, 'FUNDER'], '')
            self.assertEqual(audit['unavailablePublicationIds'], ['24513584'])
            self.assertEqual(audit['publicationsWithoutGrantListCount'], 1)

    def test_release_validator_rejects_stale_normalization_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_release(root)
            audit_path = root / "funders" / "normalization-audit.json"
            audit = json.loads(audit_path.read_text())
            audit["publicationsWithMappedAgencyCount"] = 1
            audit_path.write_text(json.dumps(audit))

            with self.assertRaisesRegex(
                    ValueError, "normalization audit differs"):
                validate_funder_artifacts(str(root))

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
            source_support = root / "data" / "support"
            source_funders.mkdir(parents=True)
            source_support.mkdir(parents=True)
            (source_funders / "funder_cleaner.json").write_text("{}")
            (source_support / "cohort_cleaner.json").write_text("{}")
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
                        return_value={
                            "funderCount": 1, "cohortCount": 1,
                            "selectorFunderCount": 2,
                            "selectorCohortCount": 3,
                        }
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
            self.assertEqual(
                (staged / "support" / "cohort_cleaner.json").read_text(),
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

    def test_additional_information_renders_precomputed_facet_statistics(self):
        facet_overview = {
            "most_common_funder": {
                "name": "Example Research Council",
                "studyCount": 1234,
                "publicationCount": 321,
            },
            "most_common_cohort": {
                "name": "Example Cohort",
                "studyCount": 987,
                "publicationCount": 123,
            },
            "funder_count": 44,
            "cohort_count": 555,
            "study_count": 2000,
            "funder_linked_study_count": 1500,
            "cohort_linked_study_count": 1750,
            "funder_linked_study_percentage": 75.0,
            "cohort_linked_study_percentage": 87.5,
        }
        with mock.patch(
                "app.routes.load_precomputed_facet_overview",
                return_value=facet_overview) as load_overview, mock.patch(
                    "app.routes.get_dashboard_filter_store") as live_store:
            response = self.client.get("/additional-information")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Example Research Council", response.data)
        self.assertIn(
            b"321 publications covering 1,234 unique GWAS study accessions",
            response.data,
        )
        self.assertIn(b"Example Cohort", response.data)
        self.assertIn(b"44 canonical funders and 555 named cohorts", response.data)
        self.assertIn(b"1,500 (75.0%)", response.data)
        self.assertIn(b"1,750 (87.5%)", response.data)
        self.assertIn(b"Multiple choices within the same filter", response.data)
        self.assertIn(b"full rather than fractional counting", response.data)
        self.assertIn(b"ESRC Impact Acceleration Account", response.data)
        self.assertIn(
            b"Charles Rahal, Mingyue Liu, and Daniel Valdenegro",
            response.data,
        )
        self.assertNotIn(b"here</a> ).", response.data)
        load_overview.assert_called_once()
        live_store.assert_not_called()

    def test_additional_information_falls_back_for_legacy_archive(self):
        store = mock.Mock()
        store.facet_overview.return_value = {
            "most_common_funder": None,
            "most_common_cohort": None,
            "funder_count": 0,
            "cohort_count": 0,
            "study_count": 0,
            "funder_linked_study_count": 0,
            "cohort_linked_study_count": 0,
            "funder_linked_study_percentage": 0.0,
            "cohort_linked_study_percentage": 0.0,
        }
        with mock.patch(
                "app.routes.load_precomputed_facet_overview",
                return_value=None), mock.patch(
                    "app.routes.get_dashboard_filter_store",
                    return_value=store):
            response = self.client.get("/additional-information")

        self.assertEqual(response.status_code, 200)
        store.facet_overview.assert_called_once_with()

    def test_wsgi_does_not_eagerly_warm_dashboard_filters(self):
        source = (
            Path(__file__).resolve().parents[1] / "deploy" / "wsgi.py"
        ).read_text()

        self.assertIn("_check_required_data()", source)
        self.assertNotIn("get_dashboard_filter_store", source)
        self.assertNotIn(".warm()", source)

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

    def test_baseline_funder_list_is_returned_without_pagination(self):
        store = mock.Mock()
        store.funders.return_value = [{
            "slug": f"funder-{number}", "name": f"Funder {number}",
            "studyCount": number, "publicationCount": 1,
        } for number in range(60)]
        with mock.patch(
                "app.routes.get_dashboard_filter_store",
                return_value=store):
            response = self.client.get(
                "/api/funders?stage=initial&page=1"
            )

        payload = response.get_json()
        self.assertEqual(len(payload["results"]), 60)
        self.assertFalse(payload["pagination"]["more"])

    def test_single_cohort_funder_list_is_returned_without_pagination(self):
        store = mock.Mock()
        store.funders.return_value = [{
            "slug": f"funder-{number}", "name": f"Funder {number}",
            "studyCount": number, "publicationCount": 1,
        } for number in range(60)]
        with mock.patch(
                "app.routes.get_dashboard_filter_store",
                return_value=store):
            response = self.client.get(
                "/api/funders?cohorts=ukb&stage=initial&page=1"
            )

        payload = response.get_json()
        self.assertEqual(len(payload["results"]), 60)
        self.assertFalse(payload["pagination"]["more"])

    def test_multi_cohort_funder_list_remains_paginated(self):
        store = mock.Mock()
        store.funders.return_value = [{
            "slug": f"funder-{number}", "name": f"Funder {number}",
            "studyCount": number, "publicationCount": 1,
        } for number in range(60)]
        with mock.patch(
                "app.routes.get_dashboard_filter_store",
                return_value=store):
            response = self.client.get(
                "/api/funders?cohorts=ukb,clsa&stage=initial&page=1"
            )

        payload = response.get_json()
        self.assertEqual(len(payload["results"]), 50)
        self.assertTrue(payload["pagination"]["more"])

    def test_baseline_cohort_list_is_returned_without_pagination(self):
        store = mock.Mock()
        store.cohorts.return_value = [{
            "id": f"cohort-{number}", "name": f"Cohort {number}",
            "studyCount": number, "publicationCount": 1,
        } for number in range(60)]
        with mock.patch(
                "app.routes.get_dashboard_filter_store",
                return_value=store):
            response = self.client.get(
                "/api/cohorts?stage=replication&page=1"
            )

        payload = response.get_json()
        self.assertEqual(len(payload["results"]), 60)
        self.assertFalse(payload["pagination"]["more"])

    def test_single_funder_cohort_list_is_returned_without_pagination(self):
        store = mock.Mock()
        store.cohorts.return_value = [{
            "id": f"cohort-{number}", "name": f"Cohort {number}",
            "studyCount": number, "publicationCount": 1,
        } for number in range(60)]
        with mock.patch(
                "app.routes.get_dashboard_filter_store",
                return_value=store):
            response = self.client.get(
                "/api/cohorts?funders=wellcome&stage=initial&page=1"
            )

        payload = response.get_json()
        self.assertEqual(len(payload["results"]), 60)
        self.assertFalse(payload["pagination"]["more"])

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
