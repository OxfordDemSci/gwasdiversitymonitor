import contextlib
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import generate_data
from app import DataLoader, app


class PrecompressedJsonGenerationTests(unittest.TestCase):
    def test_precompressed_json_is_deterministic_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "plot.json"
            source_bytes = b'{"unicode":"caf\xc3\xa9","values":[1,2,3]}\n'
            source_path.write_bytes(source_bytes)

            compressed_path = Path(
                generate_data._write_precompressed_json(source_path)
            )
            first_bytes = compressed_path.read_bytes()

            source_path.write_bytes(source_bytes)
            generate_data._write_precompressed_json(source_path)
            second_bytes = compressed_path.read_bytes()

        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(gzip.decompress(first_bytes), source_bytes)
        self.assertEqual(first_bytes[4:8], b"\0\0\0\0")

    def test_json_converter_writes_a_companion_for_every_plot_json(self):
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
            }],
            "bubblegraph_replication": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            loader = mock.Mock()
            loader.getAncestriesList.return_value = {"European": "european"}
            loader.getAncestriesListOrder.return_value = {1: "European"}
            loader.getTermsList.return_value = {"Example": "example"}
            loader.getTraitsList.return_value = {"Trait": "trait"}
            loader.getBubbleGraph.return_value = bubble_rows
            loader.getTSPlot.return_value = {"series": []}
            loader.getChloroMap.return_value = {"countries": []}
            loader.getHeatMap.return_value = {"cells": []}
            loader.getDoughnutGraph.return_value = {"slices": []}
            loader.getSummaryStatistics.return_value = {"studies": 1}

            with mock.patch.object(
                    generate_data, "DataLoader", return_value=loader), \
                    mock.patch.object(
                        generate_data, "diversity_logger", mock.Mock(),
                        create=True,
                    ):
                generate_data.json_converter(directory)

            plot_path = Path(directory) / "toplot"
            for filename in generate_data.TOPLOT_JSON_FILES:
                source_bytes = (plot_path / filename).read_bytes()
                compressed_bytes = (plot_path / f"{filename}.gz").read_bytes()
                self.assertEqual(
                    gzip.decompress(compressed_bytes), source_bytes, filename
                )

    def test_companions_are_published_but_not_required_for_legacy_startup(self):
        for filename in generate_data.TOPLOT_PRECOMPRESSED_JSON_FILES:
            relative_path = f"toplot/{filename}"
            self.assertIn(relative_path, generate_data.PUBLISHED_DATA_FILES)
            self.assertNotIn(relative_path, DataLoader.RUNTIME_DATA_FILES)


class PrecompressedJsonRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temporary_directory.name)
        self.plot_path = self.data_path / "toplot"
        self.plot_path.mkdir()
        self.source_path = self.plot_path / "summary.json"
        self.source_bytes = json.dumps(
            {"studies": 123, "participants": 456}, separators=(",", ":")
        ).encode("utf-8")
        self.source_path.write_bytes(self.source_bytes)
        self.compressed_path = Path(
            generate_data._write_precompressed_json(self.source_path)
        )
        self.data_lock = mock.patch(
            "app.routes.DataLoader.published_data_lock",
            return_value=contextlib.nullcontext(str(self.data_path)),
        )
        self.data_lock.start()

    def tearDown(self):
        self.data_lock.stop()
        self.temporary_directory.cleanup()

    def _versioned_url(self):
        version = self.source_path.stat().st_mtime_ns
        return f"/json/summary.json?v={version}"

    def test_route_serves_gzip_with_variant_cache_and_conditionals(self):
        response = self.client.get(
            self._versioned_url(), headers={"Accept-Encoding": "br, gzip"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertIn("Accept-Encoding", response.headers["Vary"])
        self.assertEqual(gzip.decompress(response.data), self.source_bytes)
        self.assertIn("max-age=31536000", response.headers["Cache-Control"])
        self.assertIn("immutable", response.headers["Cache-Control"])
        self.assertIn("ETag", response.headers)

        conditional = self.client.get(
            self._versioned_url(),
            headers={
                "Accept-Encoding": "gzip",
                "If-None-Match": response.headers["ETag"],
            },
        )
        self.assertEqual(conditional.status_code, 304)
        self.assertEqual(conditional.data, b"")
        self.assertIn("Accept-Encoding", conditional.headers["Vary"])
        response.close()
        conditional.close()

    def test_route_honours_gzip_q_zero(self):
        response = self.client.get(
            self._versioned_url(), headers={"Accept-Encoding": "gzip;q=0"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Content-Encoding", response.headers)
        self.assertIn("Accept-Encoding", response.headers["Vary"])
        self.assertEqual(response.data, self.source_bytes)
        response.close()

    def test_route_caches_compression_when_companion_is_missing_or_stale(self):
        self.compressed_path.unlink()
        missing = self.client.get(
            self._versioned_url(), headers={"Accept-Encoding": "gzip"}
        )
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(missing.data), self.source_bytes)
        missing.close()

        generate_data._write_precompressed_json(self.source_path)
        stale_timestamp = self.compressed_path.stat().st_mtime_ns
        self.source_bytes = b'{"studies":999}'
        self.source_path.write_bytes(self.source_bytes)
        os.utime(
            self.source_path,
            ns=(stale_timestamp + 1_000_000, stale_timestamp + 1_000_000),
        )
        stale = self.client.get(
            self._versioned_url(), headers={"Accept-Encoding": "gzip"}
        )
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(stale.data), self.source_bytes)
        stale.close()


if __name__ == "__main__":
    unittest.main()
