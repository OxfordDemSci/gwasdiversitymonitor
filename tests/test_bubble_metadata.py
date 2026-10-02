import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import generate_data
from app.DataLoader import DataLoader
from funder_pipeline import BUBBLE_PAYLOAD_COLUMNS, build_bubble_payload


class BubbleMetadataGenerationTests(unittest.TestCase):
    def test_bubble_rows_include_study_and_publication_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog" / "raw").mkdir(parents=True)
            (root / "catalog" / "synthetic").mkdir(parents=True)
            (root / "summary").mkdir()
            (root / "toplot").mkdir()

            pd.DataFrame([{
                "STUDY ACCESSION": "GCST000001",
                "DISEASE/TRAIT": "Example trait",
                "COHORT": "UK Biobank|Example Cohort",
                "JOURNAL": "Example Journal",
            }]).to_csv(
                root / "catalog" / "raw" / "Cat_Stud.tsv",
                sep="\t", index=False,
            )
            pd.DataFrame([{
                "Disease trait": "Example trait",
                "Parent term": "Example parent",
            }]).to_csv(
                root / "catalog" / "raw" / "Cat_Map.tsv",
                sep="\t", index=False,
            )
            pd.DataFrame([{
                "STUDY ACCESSION": "GCST000001",
                "PUBMEDID": "12345678",
                "FIRST AUTHOR": "Example A",
                "DATE": "2024-01-02",
                "STAGE": "initial",
                "N": 500,
                "Broader": "European",
            }]).to_csv(
                root / "catalog" / "synthetic" / "Cat_Anc_wBroader.tsv",
                sep="\t", index=False,
            )

            with mock.patch.object(
                generate_data, "diversity_logger", mock.Mock(), create=True
            ):
                generate_data.make_bubbleplot_df(str(root))
            bubbles = pd.read_csv(root / "toplot" / "bubble_df.csv")

            self.assertEqual(bubbles.loc[0, "DATE"], "2024-01-02")
            self.assertEqual(
                bubbles.loc[0, "COHORT"], "UK Biobank | Example Cohort"
            )
            self.assertEqual(bubbles.loc[0, "JOURNAL"], "Example Journal")

    def test_bubble_rows_use_mapped_trait_when_study_text_is_new(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog" / "raw").mkdir(parents=True)
            (root / "catalog" / "synthetic").mkdir(parents=True)
            (root / "summary").mkdir()
            (root / "toplot").mkdir()

            pd.DataFrame([{
                "STUDY ACCESSION": "GCST000002",
                "DISEASE/TRAIT": "Study-specific protein description",
                "MAPPED_TRAIT": "Blood protein amount",
                "MAPPED_TRAIT_URI": "http://example.org/efo_1",
                "COHORT": "MGBB",
                "JOURNAL": "Example Journal",
            }]).to_csv(
                root / "catalog" / "raw" / "Cat_Stud.tsv",
                sep="\t", index=False,
            )
            pd.DataFrame([{
                "Disease trait": "Different catalog wording",
                "EFO term": "blood protein amount",
                "EFO URI": "http://example.org/efo_1",
                "Parent term": "Other measurement",
            }]).to_csv(
                root / "catalog" / "raw" / "Cat_Map.tsv",
                sep="\t", index=False,
            )
            pd.DataFrame([{
                "STUDY ACCESSION": "GCST000002",
                "PUBMEDID": "41310232",
                "FIRST AUTHOR": "Example A",
                "DATE": "2025-11-27",
                "STAGE": "initial",
                "N": 500,
                "Broader": "Other/Mixed",
            }]).to_csv(
                root / "catalog" / "synthetic" / "Cat_Anc_wBroader.tsv",
                sep="\t", index=False,
            )

            with mock.patch.object(
                generate_data, "diversity_logger", mock.Mock(), create=True
            ):
                generate_data.make_bubbleplot_df(str(root))
            bubbles = pd.read_csv(root / "toplot" / "bubble_df.csv")

            self.assertEqual(len(bubbles), 1)
            self.assertEqual(bubbles.loc[0, "parentterm"], "Other measurement")

    def test_loader_uses_column_names_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "toplot").mkdir()
            pd.DataFrame([{
                "JOURNAL": "Example Journal",
                "DATE": "2024-01-02",
                "STAGE": "initial",
                "COHORT": "Example Cohort",
                "FUNDER": "Example Funder",
                "PUBMEDID": "12345678",
            }]).to_csv(root / "toplot" / "bubble_df.csv", index=False)

            payload = DataLoader(str(root)).getBubbleGraph()
            row = payload["bubblegraph_initial"][0]

            self.assertEqual(row["DATE"], "2024-01-02")
            self.assertEqual(row["JOURNAL"], "Example Journal")
            self.assertEqual(row["COHORT"], "Example Cohort")
            self.assertEqual(row["FUNDER"], "Example Funder")

    def test_filtered_payload_keeps_active_area_metadata(self):
        frame = pd.DataFrame([{
            "ACCESSION": "GCST000001",
            "AUTHOR": "Example A",
            "STAGE": "initial",
            "DATE": "2024-01-02",
            "N": 500,
            "PUBMEDID": "12345678",
            "Broader": "European",
            "parentterm": "Example parent",
            "DiseaseOrTrait": "Example trait",
            "COHORT": "Example Cohort",
            "JOURNAL": "Example Journal",
            "FUNDER": "Example Funder",
            "cssclassname": "redundant-class",
            "trait": "redundant-trait",
            "__class": "redundant-derived-class",
            "__trait": "redundant-derived-trait",
            "__Nnum": 500,
            "__dateMS": 1704153600000,
            "__DiseaseOrTraitClean": "redundant-clean-trait",
            "__BroaderClass": "redundant-broader-class",
            "__ParentTermClass": "redundant-parent-class",
        }])

        stage = build_bubble_payload(frame)["bubblegraph_initial"]

        self.assertEqual(set(stage["columns"]), set(BUBBLE_PAYLOAD_COLUMNS))
        self.assertFalse(stage["meta"]["includePrecomputed"])
        self.assertTrue(
            {
                "cssclassname", "trait", "__class", "__trait", "__Nnum",
                "__dateMS", "__DiseaseOrTraitClean", "__BroaderClass",
                "__ParentTermClass",
            }.isdisjoint(stage["columns"])
        )

        decoded = {
            column: stage["dicts"][column][stage["codes"][column][0]]
            for column in stage["columns"]
        }
        self.assertEqual(decoded["ACCESSION"], "GCST000001")
        self.assertEqual(decoded["STAGE"], "initial")
        self.assertEqual(decoded["FUNDER"], "Example Funder")
        self.assertEqual(stage["meta"]["maxN"], 500)
        self.assertEqual(stage["meta"]["minDate"], "2024-01-02")

    def test_main_json_converter_uses_compact_bubble_payload(self):
        bubble_rows = {
            "bubblegraph_initial": [{
                "ACCESSION": "GCST000001",
                "AUTHOR": "Example A",
                "Broader": "European",
                "COHORT": "Example Cohort",
                "DATE": "2024-01-02",
                "DiseaseOrTrait": "Example trait",
                "FUNDER": "Example Funder",
                "JOURNAL": "Example Journal",
                "N": "500",
                "PUBMEDID": "12345678",
                "STAGE": "initial",
                "parentterm": "Example parent",
                "cssclassname": "redundant-class",
                "trait": "redundant-trait",
            }],
            "bubblegraph_replication": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            loader = mock.Mock()
            loader.getAncestriesList.return_value = {}
            loader.getAncestriesListOrder.return_value = {}
            loader.getTermsList.return_value = {}
            loader.getTraitsList.return_value = {}
            loader.getBubbleGraph.return_value = bubble_rows
            loader.getTSPlot.return_value = {}
            loader.getChloroMap.return_value = {}
            loader.getHeatMap.return_value = {}
            loader.getDoughnutGraph.return_value = {}
            loader.getSummaryStatistics.return_value = {}

            with mock.patch.object(generate_data, "DataLoader", return_value=loader), \
                    mock.patch.object(
                        generate_data, "diversity_logger", mock.Mock(),
                        create=True,
                    ):
                generate_data.json_converter(directory)

            with open(Path(directory) / "toplot" / "bubbleGraph.json") as fp:
                payload = json.load(fp)

        stage = payload["bubblegraph_initial"]
        self.assertEqual(set(stage["columns"]), set(BUBBLE_PAYLOAD_COLUMNS))
        self.assertNotIn("cssclassname", stage["columns"])
        self.assertNotIn("trait", stage["columns"])
        self.assertFalse(stage["meta"]["includePrecomputed"])


if __name__ == "__main__":
    unittest.main()
