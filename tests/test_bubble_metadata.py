import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import generate_data
from app.DataLoader import DataLoader
from funder_pipeline import build_bubble_payload


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
            "STAGE": "initial",
            "DATE": "2024-01-02",
            "N": 500,
            "Broader": "European",
            "parentterm": "Example parent",
            "DiseaseOrTrait": "Example trait",
            "COHORT": "Example Cohort",
            "JOURNAL": "Example Journal",
            "FUNDER": "Example Funder",
        }])

        stage = build_bubble_payload(frame)["bubblegraph_initial"]

        self.assertTrue(
            {"DATE", "COHORT", "JOURNAL", "FUNDER"}.issubset(
                stage["columns"]
            )
        )


if __name__ == "__main__":
    unittest.main()
