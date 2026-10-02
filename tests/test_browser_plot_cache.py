import contextlib
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app import app
from app.BrowserPlotCache import (
    compact_bubble_payload, prepare_bubble_browser_cache, prepare_cached_gzip,
)


def legacy_payload():
    stage = {
        "columns": ["N", "DATE", "AUTHOR", "FUNDER", "COHORT", "__Nnum", "__class"],
        "dicts": {
            "N": ["50", "100"], "DATE": ["2025-01-01"],
            "AUTHOR": ["Example A"], "FUNDER": ["Funder A|Funder B"],
            "COHORT": ["Cohort A"], "__Nnum": [50, 100],
            "__class": ["european height"],
        },
        "codes": {
            "N": [0, 1], "DATE": [0, 0], "AUTHOR": [0, 0],
            "FUNDER": [0, 0], "COHORT": [0, 0], "__Nnum": [0, 1],
            "__class": [0, 0],
        },
        "meta": {"rowCount": 2, "maxN": 100, "includePrecomputed": True},
    }
    return {
        "__format": "dict_columnar_v2",
        "bubblegraph_initial": stage,
        "bubblegraph_replication": json.loads(json.dumps(stage)),
    }


class BrowserPlotCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temporary.name)
        (self.data_path / "toplot").mkdir()
        self.source = self.data_path / "toplot" / "bubbleGraph.json"
        self.original = json.dumps(legacy_payload()).encode()
        self.source.write_bytes(self.original)

    def tearDown(self):
        self.temporary.cleanup()

    def test_compaction_preserves_every_raw_value_and_both_stages(self):
        original = legacy_payload()
        compact = compact_bubble_payload(original)
        for name in ("bubblegraph_initial", "bubblegraph_replication"):
            stage = compact[name]
            self.assertEqual(stage["meta"]["rowCount"], 2)
            self.assertEqual(stage["meta"]["maxN"], 100)
            self.assertFalse(stage["meta"]["includePrecomputed"])
            self.assertNotIn("__class", stage["columns"])
            self.assertNotIn("__Nnum", stage["columns"])
            for column in ("N", "DATE", "AUTHOR", "FUNDER", "COHORT"):
                self.assertEqual(stage["dicts"][column], original[name]["dicts"][column])
                self.assertEqual(stage["codes"][column], original[name]["codes"][column])
        self.assertTrue(original["bubblegraph_initial"]["meta"]["includePrecomputed"])
        self.assertIn("__class", original["bubblegraph_initial"]["columns"])

    def test_cache_does_not_modify_published_source_or_manifest(self):
        manifest = self.data_path / ".generation_complete.json"
        manifest.write_bytes(b'{"release":"original"}')
        before = (self.source.stat(), manifest.read_bytes())
        identity, compressed = prepare_bubble_browser_cache(self.data_path)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.source.stat().st_mtime_ns, before[0].st_mtime_ns)
        self.assertEqual(manifest.read_bytes(), before[1])
        self.assertEqual(gzip.decompress(compressed.read_bytes()), identity.read_bytes())
        self.assertEqual(identity.stat().st_mode & 0o777, 0o644)
        self.assertEqual(compressed.stat().st_mode & 0o777, 0o644)
        written = compressed.stat().st_mtime_ns
        self.assertEqual(prepare_bubble_browser_cache(self.data_path), (identity, compressed))
        self.assertEqual(compressed.stat().st_mtime_ns, written)

    def test_source_changes_invalidate_cache_and_keep_at_most_two_releases(self):
        paths = []
        for number in range(4):
            payload = legacy_payload()
            payload["bubblegraph_initial"]["dicts"]["AUTHOR"] = [f"Author {number}"]
            self.source.write_text(json.dumps(payload))
            identity, compressed = prepare_bubble_browser_cache(self.data_path)
            paths.append(identity)
            self.assertEqual(json.loads(gzip.decompress(compressed.read_bytes()))[
                "bubblegraph_initial"]["dicts"]["AUTHOR"], [f"Author {number}"])
        self.assertEqual(len(set(paths)), 4)
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[1].exists())
        self.assertTrue(paths[2].exists())
        self.assertTrue(paths[3].exists())

    def test_concurrent_cold_requests_share_a_complete_cache(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(
                lambda _: prepare_bubble_browser_cache(self.data_path), range(8)
            ))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(gzip.decompress(results[0][1].read_bytes()), results[0][0].read_bytes())

    def test_row_oriented_legacy_data_is_compressed_without_reinterpretation(self):
        payload = {"bubblegraph_initial": [{"N": 42}], "bubblegraph_replication": []}
        self.source.write_text(json.dumps(payload))
        identity, compressed = prepare_bubble_browser_cache(self.data_path)
        self.assertEqual(json.loads(identity.read_bytes()), payload)
        self.assertEqual(json.loads(gzip.decompress(compressed.read_bytes())), payload)

    def test_generic_gzip_tracks_source_changes(self):
        directory = self.data_path / "cache"
        first = prepare_cached_gzip(self.source, directory)
        self.source.write_bytes(b'{"new":"source"}')
        second = prepare_cached_gzip(self.source, directory)
        self.assertNotEqual(first, second)
        self.assertEqual(gzip.decompress(second.read_bytes()), self.source.read_bytes())

    def test_route_negotiates_variants_and_conditional_requests(self):
        version = self.source.stat().st_mtime_ns
        url = f"/json/bubbleGraph.json?v={version}&format=compact-v1"
        with mock.patch("app.routes.DataLoader.published_data_lock", return_value=(
            contextlib.nullcontext(str(self.data_path))
        )):
            client = app.test_client()
            compressed = client.get(url, headers={"Accept-Encoding": "gzip"})
            identity = client.get(url, headers={"Accept-Encoding": "gzip;q=0"})
            conditional = client.get(url, headers={
                "Accept-Encoding": "gzip", "If-None-Match": compressed.headers["ETag"],
            })
            original = client.get(f"/json/bubbleGraph.json?v={version}")
        self.assertEqual(compressed.status_code, 200)
        self.assertEqual(compressed.headers["Content-Encoding"], "gzip")
        self.assertIn("Accept-Encoding", identity.headers["Vary"])
        self.assertNotIn("Content-Encoding", identity.headers)
        self.assertEqual(gzip.decompress(compressed.data), identity.data)
        self.assertNotEqual(compressed.headers["ETag"], identity.headers["ETag"])
        self.assertIn("immutable", compressed.headers["Cache-Control"])
        self.assertEqual(conditional.status_code, 304)
        self.assertEqual(original.data, self.original)
        for response in (compressed, identity, conditional, original):
            response.close()

    def test_unwritable_cache_keeps_complete_legacy_response_available(self):
        with mock.patch("app.routes.DataLoader.published_data_lock", return_value=(
            contextlib.nullcontext(str(self.data_path))
        )), mock.patch("app.routes.prepare_bubble_browser_cache", side_effect=PermissionError), \
                mock.patch("app.routes.prepare_plot_gzip", side_effect=PermissionError):
            response = app.test_client().get(
                "/json/bubbleGraph.json?format=compact-v1", headers={"Accept-Encoding": "gzip"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.original)
        self.assertNotIn("immutable", response.headers["Cache-Control"])
        response.close()


if __name__ == "__main__":
    unittest.main()
