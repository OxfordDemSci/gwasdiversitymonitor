"""Disposable browser representations of published plots, including old releases.

These files are not publication artifacts: source data and its manifest are never
modified. A source-versioned cache lets existing installations use the smaller
wire format without downloading or regenerating the scientific dataset.
"""

import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from funder_pipeline import BUBBLE_PAYLOAD_COLUMNS


BUBBLE_BROWSER_FORMAT = "compact-v1"
_STAGES = ("bubblegraph_initial", "bubblegraph_replication")


def compact_bubble_payload(payload):
    """Omit redundant derived columns without dropping observations or metadata."""
    if payload.get("__format") != "dict_columnar_v2":
        # Older row-oriented exports remain readable by the chart. Compression
        # still helps them; don't reinterpret an unknown representation.
        return payload
    compact = dict(payload)
    for stage_name in _STAGES:
        source = payload[stage_name]
        columns = [
            name for name in BUBBLE_PAYLOAD_COLUMNS
            if name in source["columns"]
        ]
        compact[stage_name] = {
            "columns": columns,
            "dicts": {name: source["dicts"][name] for name in columns},
            "codes": {name: source["codes"][name] for name in columns},
            "meta": dict(source.get("meta", {}), includePrecomputed=False),
        }
    return compact


def _atomic_output(path, write):
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=".browser-plot-"
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            write(output)
            # These are public browser responses; a server master may prepare
            # them before its workers drop privileges.
            os.fchmod(output.fileno(), 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _cache_key(source, representation):
    source_stat = source.stat()
    signature = "\0".join(map(str, (
        source.resolve(), source_stat.st_dev, source_stat.st_ino,
        source_stat.st_mtime_ns, source_stat.st_size, representation,
    )))
    return hashlib.sha256(signature.encode()).hexdigest()[:24]


def _write_gzip(source, destination, source_key=None):
    def write(output):
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=output, compresslevel=6, mtime=0,
        ) as compressed:
            with source.open("rb") as identity:
                shutil.copyfileobj(identity, compressed)
        if source_key is not None and _cache_key(source, "gzip-v1") != source_key:
            raise OSError("Source changed while preparing browser compression")
    _atomic_output(destination, write)


def prepare_cached_gzip(source, directory):
    """Return a source-versioned gzip copy of any public file in a safe cache."""
    source = Path(source)
    directory = Path(directory)
    source_key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:12]
    prefix = f"gzip-v1-{source.stem}-{source_key}-"
    version = _cache_key(source, "gzip-v1")
    destination = directory / f"{prefix}{version}.json.gz"
    if destination.is_file():
        return destination
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "cache.lock").open("a+") as cache_lock:
        fcntl.flock(cache_lock.fileno(), fcntl.LOCK_EX)
        if not destination.is_file():
            _write_gzip(source, destination, source_key=version)
        previous = sorted(
            (path for path in directory.glob(prefix + "*.json.gz")
             if path != destination),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for obsolete in previous[1:]:
            obsolete.unlink(missing_ok=True)
    return destination


def prepare_plot_gzip(published_path, filename):
    """Compress an older published JSON once, without editing its manifest."""
    return prepare_cached_gzip(
        Path(published_path) / "toplot" / filename,
        Path(published_path) / ".generate_data" / "browser-cache",
    )


def prepare_browser_plot_caches(published_path):
    """Move one-time conversion off the first visitor's critical path."""
    prepare_bubble_browser_cache(published_path)
    for filename in (
        "tsPlot.json", "heatMap.json", "ancestriesOrdered.json",
        "chloroMap.json", "doughnutGraph.json", "summary.json",
    ):
        source = Path(published_path) / "toplot" / filename
        companion = Path(str(source) + ".gz")
        if source.is_file() and (
            not companion.is_file()
            or companion.stat().st_mtime_ns < source.stat().st_mtime_ns
        ):
            prepare_plot_gzip(published_path, filename)


def prepare_bubble_browser_cache(published_path):
    """Return (JSON, gzip) paths; serialize cold creation across server workers.

    The caller holds published_data_lock, so the source cannot change while it
    is read. Keeping two revisions bounds disk use and tolerates active readers
    across a publication. Open response file descriptors survive later pruning.
    """
    source = Path(published_path) / "toplot" / "bubbleGraph.json"
    key = _cache_key(source, BUBBLE_BROWSER_FORMAT)
    directory = Path(published_path) / ".generate_data" / "browser-cache"
    filename = f"bubble-{BUBBLE_BROWSER_FORMAT}-{key}.json"
    identity_path = directory / filename
    gzip_path = directory / (filename + ".gz")
    if identity_path.is_file() and gzip_path.is_file():
        return identity_path, gzip_path

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "cache.lock").open("a+") as cache_lock:
        fcntl.flock(cache_lock.fileno(), fcntl.LOCK_EX)
        if identity_path.is_file() and gzip_path.is_file():
            return identity_path, gzip_path

        with source.open(encoding="utf-8") as input_file:
            payload = compact_bubble_payload(json.load(input_file))
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        _atomic_output(identity_path, lambda output: output.write(encoded))

        _write_gzip(identity_path, gzip_path)
        previous = sorted(
            (path for path in directory.glob("bubble-*.json")
             if path != identity_path),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for obsolete in previous[1:]:
            obsolete.unlink(missing_ok=True)
            Path(str(obsolete) + ".gz").unlink(missing_ok=True)
    return identity_path, gzip_path
