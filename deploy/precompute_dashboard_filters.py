#!/usr/bin/env python3
import argparse
import os
import sys
import time

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, REPOSITORY_ROOT)

from app.DashboardFilters import (
    build_precomputed_filter_archive,
    validate_precomputed_filter_archive,
)


def main():
    parser = argparse.ArgumentParser(
        description="Precompute individual funder and cohort dashboards."
    )
    parser.add_argument("--data", default="data")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    started = time.monotonic()

    def progress(number, total, kind, identifier):
        if number == 1 or number == total or number % 50 == 0:
            elapsed = time.monotonic() - started
            print(
                f"Prepared {number}/{total} filters in {elapsed:.1f}s "
                f"({kind}/{identifier})",
                flush=True,
            )

    path = build_precomputed_filter_archive(
        os.path.abspath(args.data), progress=progress, limit=args.limit
    )
    manifest = validate_precomputed_filter_archive(path=path)
    print(
        f"Ready: {path} "
        f"({manifest['funderCount']} funders, "
        f"{manifest['cohortCount']} cohorts, "
        f"{time.monotonic() - started:.1f}s)",
        flush=True,
    )


if __name__ == "__main__":
    main()
