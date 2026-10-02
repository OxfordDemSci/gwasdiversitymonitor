import gzip
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app import app
from app import BrowserPlotCache


class StaticCompressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.static = self.root / "static"
        self.static.mkdir()
        self.cache = self.root / "cache"
        self.source = self.static / "bundle.js"
        self.contents = b"window.example = 'compressible static asset';\n" * 200
        self.source.write_bytes(self.contents)
        self.static_patch = mock.patch.object(
            app, "_static_folder", str(self.static),
        )
        self.static_patch.start()
        self.config_patch = mock.patch.dict(app.config, {
            "STATIC_GZIP_CACHE_DIRECTORY": str(self.cache),
        })
        self.config_patch.start()
        self.client = app.test_client()

    def tearDown(self):
        self.config_patch.stop()
        self.static_patch.stop()
        self.directory.cleanup()

    def get(self, filename="bundle.js", **headers):
        response = self.client.get("/static/" + filename, headers=headers)
        self.addCleanup(response.close)
        return response

    def test_gzip_round_trip_distinct_validators_and_conditional_requests(self):
        identity = self.get()
        compressed = self.get(**{"Accept-Encoding": "br, gzip"})

        self.assertEqual(compressed.status_code, 200)
        self.assertEqual(compressed.headers["Content-Encoding"], "gzip")
        self.assertEqual(compressed.mimetype, identity.mimetype)
        self.assertEqual(gzip.decompress(compressed.data), self.contents)
        self.assertLess(len(compressed.data), len(self.contents))
        self.assertEqual(
            compressed.headers["Last-Modified"],
            identity.headers["Last-Modified"],
        )
        self.assertNotEqual(
            compressed.headers["ETag"], identity.headers["ETag"],
        )
        for response in (identity, compressed):
            self.assertIn("Accept-Encoding", response.vary)

        for encoding, etag in (("gzip", compressed.headers["ETag"]),
                               ("identity", identity.headers["ETag"])):
            conditional = self.get(**{
                "Accept-Encoding": encoding, "If-None-Match": etag,
            })
            self.assertEqual(conditional.status_code, 304)
            self.assertEqual(conditional.data, b"")
            self.assertIn("Accept-Encoding", conditional.vary)
        cross_variant = self.get(**{
            "Accept-Encoding": "identity",
            "If-None-Match": compressed.headers["ETag"],
        })
        self.assertEqual(cross_variant.status_code, 200)

    def test_explicit_gzip_refusal_and_range_requests_keep_identity(self):
        for encoding in ("gzip;q=0", "*;q=1, gzip;q=0", "identity"):
            response = self.get(**{"Accept-Encoding": encoding})
            self.assertEqual(response.data, self.contents)
            self.assertNotIn("Content-Encoding", response.headers)
            self.assertIn("Accept-Encoding", response.vary)
        partial = self.get(**{
            "Accept-Encoding": "gzip", "Range": "bytes=0-9",
        })
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.data, self.contents[:10])
        self.assertNotIn("Content-Encoding", partial.headers)
        self.assertIn("Accept-Encoding", partial.vary)

    def test_head_and_versioned_cache_headers_match_compressed_get(self):
        url = "/static/bundle.js?v=" + str(self.source.stat().st_mtime_ns)
        response = self.client.get(url, headers={"Accept-Encoding": "gzip"})
        self.addCleanup(response.close)
        head = self.client.head(url, headers={"Accept-Encoding": "gzip"})
        self.addCleanup(head.close)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.data, b"")
        for header in (
            "Content-Length", "Content-Encoding", "ETag", "Cache-Control",
        ):
            self.assertEqual(head.headers[header], response.headers[header])
        self.assertIn("immutable", response.headers["Cache-Control"])
        conditional = self.client.get(url, headers={
            "Accept-Encoding": "gzip",
            "If-None-Match": response.headers["ETag"],
        })
        self.addCleanup(conditional.close)
        self.assertEqual(conditional.status_code, 304)
        self.assertEqual(
            conditional.headers["Cache-Control"],
            response.headers["Cache-Control"],
        )

    def test_cache_reuses_compression_and_tracks_source_changes(self):
        with mock.patch.object(
            BrowserPlotCache, "_write_gzip", wraps=BrowserPlotCache._write_gzip,
        ) as compress:
            first = self.get(**{"Accept-Encoding": "gzip"})
            second = self.get(**{"Accept-Encoding": "gzip"})
            self.assertEqual(compress.call_count, 1)
            self.assertEqual(first.headers["ETag"], second.headers["ETag"])
            previous_mtime = self.source.stat().st_mtime_ns
            changed = self.contents.replace(b"window", b"global")
            self.source.write_bytes(changed)
            next_mtime = previous_mtime + 1_000_000
            os.utime(self.source, ns=(next_mtime, next_mtime))
            updated = self.get(**{"Accept-Encoding": "gzip"})
            self.assertEqual(compress.call_count, 2)
            self.assertEqual(gzip.decompress(updated.data), changed)
            self.assertNotEqual(
                first.headers["ETag"], updated.headers["ETag"],
            )
        self.assertEqual(self.source.read_bytes(), changed)
        self.assertEqual(list(self.static.iterdir()), [self.source])

    def test_small_binary_missing_and_unsafe_paths_are_not_compressed(self):
        (self.static / "small.css").write_bytes(b"body{}")
        (self.static / "image.png").write_bytes(self.contents)
        (self.static / "font.woff2").write_bytes(self.contents)
        outside = self.root / "outside.js"
        outside.write_bytes(self.contents)
        (self.static / "outside.js").symlink_to(outside)
        with mock.patch.object(
            BrowserPlotCache, "prepare_cached_gzip",
        ) as prepare:
            for filename in (
                "small.css", "image.png", "font.woff2", "missing.js",
                "../outside.js", "outside.js",
            ):
                response = self.get(filename, **{"Accept-Encoding": "gzip"})
                self.assertNotIn("Content-Encoding", response.headers)
            prepare.assert_not_called()

    def test_unwritable_cache_falls_back_to_original_asset(self):
        with mock.patch.object(
            BrowserPlotCache, "prepare_cached_gzip", side_effect=PermissionError,
        ):
            response = self.get(**{"Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.contents)
        self.assertNotIn("Content-Encoding", response.headers)
        self.assertIn("Accept-Encoding", response.vary)


if __name__ == "__main__":
    unittest.main()
