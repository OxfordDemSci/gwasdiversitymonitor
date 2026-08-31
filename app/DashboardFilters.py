import json
import hashlib
import os
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import pandas as pd

import funder_pipeline


class DashboardSelectionUnavailable(RuntimeError):
    pass


def split_cohorts(value):
    if value is None or pd.isna(value):
        return []
    return [token.strip() for token in str(value).split("|") if token.strip()]


class DashboardFilterStore:
    """Build and cache dashboards for dataset and funder intersections."""

    CACHE_LIMIT = 4
    def __init__(self, data_path="data"):
        self.data_path = os.path.abspath(data_path)
        self._lock = threading.RLock()
        self._sources = None
        self._dataset_entries = None
        self._dataset_accessions = None
        self._funder_pmids = None
        self._funder_entries = None
        self._funding_by_publication = None
        self._source_indexes = None
        self._payload_cache = OrderedDict()
        self._cache_root = self._build_cache_root()

    def _build_cache_root(self):
        source_paths = (
            os.path.join(self.data_path, "catalog", "raw", "Cat_Stud.tsv"),
            os.path.join(self.data_path, "catalog", "raw", "Cat_Map.tsv"),
            os.path.join(
                self.data_path, "catalog", "synthetic",
                "Cat_Anc_wBroader.tsv",
            ),
            os.path.join(self.data_path, "toplot", "bubble_df.csv"),
            os.path.join(self.data_path, "funders", "index.json"),
            os.path.join(
                self.data_path, "funders", "pubmed_grants.json"
            ),
            funder_pipeline.funder_cleaner_path(self.data_path),
        )
        signature = [
            self.data_path,
            f"report-schema:{funder_pipeline.REPORT_SCHEMA_VERSION}",
        ]
        for path in source_paths:
            try:
                stat = os.stat(path)
                signature.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")
            except OSError:
                signature.append(f"{path}:missing")
        digest = hashlib.sha256("\n".join(signature).encode()).hexdigest()[:20]
        return os.path.join(
            tempfile.gettempdir(), "gwas-dashboard-filter-cache", digest
        )

    def _cache_path(self, directory, dataset_id, funder_slug, suffix):
        selection = dataset_id
        if funder_slug:
            selection = f"{selection}--{funder_slug}"
        return os.path.join(
            self._cache_root, directory, f"{selection}.{suffix}"
        )

    @staticmethod
    def _atomic_json(path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=str(path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, separators=(",", ":"))
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _load_studies(self):
        studies = pd.read_csv(
            os.path.join(self.data_path, "catalog", "raw", "Cat_Stud.tsv"),
            sep="\t", dtype=str, usecols=lambda column: column in {
                "PUBMED ID", "PUBMEDID", "STUDY ACCESSION", "COHORT",
                "DISEASE/TRAIT", "ASSOCIATION COUNT", "DATE", "JOURNAL",
                "GENOTYPING TECHNOLOGY", "FULL SUMMARY STATISTICS",
            }
        )
        if "PUBMED ID" in studies.columns:
            studies = studies.rename(columns={"PUBMED ID": "PUBMEDID"})
        studies["PUBMEDID"] = studies["PUBMEDID"].map(
            funder_pipeline.normalize_pmid
        )
        studies["ASSOCIATION COUNT"] = pd.to_numeric(
            studies["ASSOCIATION COUNT"], errors="coerce"
        ).fillna(0)
        return studies

    def _ensure_dataset_index(self):
        with self._lock:
            if self._dataset_entries is not None:
                return
            cache_path = os.path.join(self._cache_root, "datasets.json")
            try:
                with open(cache_path, encoding="utf-8") as source:
                    cached = json.load(source)
                self._dataset_entries = cached["entries"]
                self._dataset_accessions = {
                    key: frozenset(values)
                    for key, values in cached["accessions"].items()
                }
                return
            except (KeyError, OSError, TypeError, ValueError,
                    json.JSONDecodeError):
                pass

            studies = self._load_studies()
            memberships = studies[["STUDY ACCESSION", "COHORT"]].copy()
            memberships["dataset"] = memberships["COHORT"].map(split_cohorts)
            memberships = memberships.explode("dataset")
            memberships = memberships[
                memberships["dataset"].notna()
                & memberships["dataset"].ne("")
            ].drop_duplicates(["STUDY ACCESSION", "dataset"])

            accession_groups = memberships.groupby(
                "dataset", sort=False
            )["STUDY ACCESSION"].agg(
                lambda values: frozenset(values.dropna().astype(str))
            )
            used_ids = set()
            entries = []
            accessions = {}
            for name in sorted(
                    accession_groups.index,
                    key=lambda value: value.casefold()):
                base = funder_pipeline.slugify(name)
                dataset_id = base
                suffix = 2
                while dataset_id in used_ids:
                    dataset_id = f"{base}-{suffix}"
                    suffix += 1
                used_ids.add(dataset_id)
                entry = {
                    "id": dataset_id,
                    "name": name,
                    "studyCount": len(accession_groups[name]),
                }
                entries.append(entry)
                accessions[dataset_id] = accession_groups[name]
            self._dataset_entries = entries
            self._dataset_accessions = accessions
            self._atomic_json(cache_path, {
                "version": 1,
                "entries": entries,
                "accessions": {
                    key: sorted(values) for key, values in accessions.items()
                },
            })

    def datasets(self, search="", funder_slug=None):
        self._ensure_dataset_index()
        needle = search.strip().casefold()
        entries = [
            entry for entry in self._dataset_entries
            if not needle
            or needle in entry["name"].casefold()
            or needle in entry["id"].casefold()
        ]
        if funder_slug:
            self._ensure_sources()
            self._ensure_funder_index()
            if funder_slug not in self._funder_pmids:
                raise KeyError(funder_slug)
            studies = self._sources[0]
            funder_accessions = frozenset(studies.loc[
                studies["PUBMEDID"].isin(self._funder_pmids[funder_slug]),
                "STUDY ACCESSION"
            ].dropna().astype(str))
            results = []
            for entry in entries:
                count = len(
                    self._dataset_accessions[entry["id"]] & funder_accessions
                )
                if count:
                    result = entry.copy()
                    result["studyCount"] = count
                    results.append(result)
            entries = results

        return entries

    def dataset(self, dataset_id):
        self._ensure_dataset_index()
        for entry in self._dataset_entries:
            if entry["id"] == dataset_id:
                return entry
        raise KeyError(dataset_id)

    def funders_for_dataset(self, dataset_id):
        self._ensure_dataset_index()
        self._ensure_sources()
        self._ensure_funder_index()
        if dataset_id not in self._dataset_accessions:
            raise KeyError(dataset_id)
        studies = self._sources[0]
        dataset_pmids = frozenset(studies.loc[
            studies["STUDY ACCESSION"].isin(
                self._dataset_accessions[dataset_id]
            ),
            "PUBMEDID"
        ].dropna().astype(str))
        return {
            slug for slug, pmids in self._funder_pmids.items()
            if pmids & dataset_pmids
        }

    def _ensure_sources(self):
        with self._lock:
            if self._sources is not None:
                return
            self._sources = funder_pipeline._load_sources(self.data_path)
            studies, ancestry, _, bubbles, _ = self._sources
            self._source_indexes = (
                studies.set_index("STUDY ACCESSION", drop=False),
                ancestry.set_index("STUDY ACCESSION", drop=False),
                bubbles.set_index("ACCESSION", drop=False),
            )

    def _select_accessions(self, source_number, accessions):
        if self._source_indexes is None:
            source = self._sources[(0, 1, 3)[source_number]]
            column = ("STUDY ACCESSION", "STUDY ACCESSION", "ACCESSION")[
                source_number
            ]
            return source[source[column].isin(accessions)].copy()
        indexed = self._source_indexes[source_number]
        available = indexed.index.unique().intersection(
            list(accessions), sort=False
        )
        if available.empty:
            return indexed.iloc[0:0].reset_index(drop=True)
        return indexed.loc[available].reset_index(drop=True)

    def _ensure_funder_index(self):
        with self._lock:
            if self._funder_pmids is not None:
                return
            with open(
                os.path.join(self.data_path, "funders", "index.json"),
                encoding="utf-8"
            ) as source:
                index = json.load(source)
            with open(
                os.path.join(self.data_path, "funders", "pubmed_grants.json"),
                encoding="utf-8"
            ) as source:
                cache = json.load(source)
            cleaner = funder_pipeline.load_funder_cleaner(
                funder_pipeline.funder_cleaner_path(self.data_path)
            )
            by_publication, _ = funder_pipeline.normalize_funding_records(
                cache, cleaner, index.get("minimumPublicationCount", 50)
            )
            names_by_slug = {
                entry["slug"]: entry["name"] for entry in index["funders"]
            }
            self._funder_entries = {
                entry["slug"]: entry for entry in index["funders"]
            }
            self._funding_by_publication = by_publication
            self._funder_pmids = {
                slug: frozenset(
                    pmid for pmid, names in by_publication.items()
                    if name in names
                )
                for slug, name in names_by_slug.items()
            }

    def _selection(self, dataset_id, funder_slug=None):
        self._ensure_dataset_index()
        self._ensure_sources()
        studies, ancestry, mappings, bubbles, countries = self._sources

        dataset = self.dataset(dataset_id)
        accessions = self._dataset_accessions[dataset_id]
        selected_studies = self._select_accessions(0, accessions)

        funder = None
        if funder_slug:
            self._ensure_funder_index()
            if funder_slug not in self._funder_pmids:
                raise KeyError(funder_slug)
            selected_studies = selected_studies[
                selected_studies["PUBMEDID"].isin(
                    self._funder_pmids[funder_slug]
                )
            ].copy()
            funder = self._funder_entries[funder_slug]

        selected_accessions = frozenset(
            selected_studies["STUDY ACCESSION"].dropna().astype(str)
        )
        selected_ancestry = self._select_accessions(1, selected_accessions)
        selected_bubbles = self._select_accessions(2, selected_accessions)
        if selected_studies.empty or selected_ancestry.empty:
            raise DashboardSelectionUnavailable(
                "No published GWAS match this funder and dataset selection"
            )
        return (
            dataset, funder, selected_studies, selected_ancestry,
            selected_bubbles, mappings, countries,
        )

    @staticmethod
    def _build_report(studies, ancestry, funding_records=None, funder=None):
        return funder_pipeline.build_report(
            funder, studies, ancestry, funding_records or {}
        )

    def dashboard(self, dataset_id, funder_slug=None):
        cache_key = (dataset_id, funder_slug or "")
        with self._lock:
            cached = self._payload_cache.get(cache_key)
            if cached is not None:
                self._payload_cache.move_to_end(cache_key)
                return cached

        cache_path = self._cache_path(
            "dashboards", dataset_id, funder_slug, "json"
        )
        try:
            with open(cache_path, encoding="utf-8") as source:
                cached = json.load(source)
            with self._lock:
                self._payload_cache[cache_key] = cached
            return cached
        except (OSError, ValueError, json.JSONDecodeError):
            pass

        (dataset, funder, studies, ancestry, bubbles, mappings,
         countries) = self._selection(dataset_id, funder_slug)
        study_parent_map = funder_pipeline.build_study_parent_map(
            studies, mappings
        )
        merged = study_parent_map.merge(
            ancestry, how="inner", on="STUDY ACCESSION"
        )
        merged = merged[
            merged["Broader"].notna() & merged["parentterm"].notna()
        ].copy()
        merged["Year"] = pd.to_datetime(
            merged["DATE"], errors="coerce"
        ).dt.year
        merged["N"] = pd.to_numeric(
            merged["N"], errors="coerce"
        ).fillna(0)

        with open(
            os.path.join(self.data_path, "summary", "uniq_broader.txt")
        ) as source:
            all_ancestries = [line.strip() for line in source if line.strip()]
        recorded_ancestries = [
            value for value in all_ancestries
            if value != "In Part Not Recorded"
        ]
        with open(
            os.path.join(self.data_path, "summary", "uniq_parent.txt")
        ) as source:
            parent_terms = [line.strip() for line in source if line.strip()]
        final_year = int(pd.to_datetime(
            self._sources[1]["DATE"], errors="coerce"
        ).dt.year.max())

        funding_records = {}
        funder_index_path = os.path.join(
            self.data_path, "funders", "index.json"
        )
        funding_cache_path = os.path.join(
            self.data_path, "funders", "pubmed_grants.json"
        )
        if os.path.isfile(funder_index_path) and os.path.isfile(
                funding_cache_path):
            self._ensure_funder_index()
            funding_records = self._funding_by_publication

        payload = {
            "version": 1,
            "selection": {
                "dataset": dataset,
                "funder": funder,
                "studyCount": int(
                    studies["STUDY ACCESSION"].nunique()
                ),
            },
            "bubbleGraph": funder_pipeline.build_bubble_payload(bubbles),
            "tsPlot": funder_pipeline.build_time_series(
                ancestry, all_ancestries, final_year
            ),
            "heatMap": funder_pipeline.build_heat_map(
                merged, recorded_ancestries, parent_terms, final_year
            ),
            "chloroMap": funder_pipeline.build_country_map(
                ancestry, countries, final_year
            ),
            "doughnutGraph": funder_pipeline.build_doughnut(
                merged, recorded_ancestries, parent_terms, final_year
            ),
            "summary": funder_pipeline.build_summary(ancestry),
            "report": self._build_report(
                studies, ancestry, funding_records,
                funder["name"] if funder else None
            ),
        }
        self._atomic_json(cache_path, payload)
        with self._lock:
            self._payload_cache[cache_key] = payload
            self._payload_cache.move_to_end(cache_key)
            while len(self._payload_cache) > self.CACHE_LIMIT:
                self._payload_cache.popitem(last=False)
        return payload

    def dashboard_path(self, dataset_id, funder_slug=None):
        self.dataset(dataset_id)
        if funder_slug:
            self._ensure_funder_index()
            if funder_slug not in self._funder_pmids:
                raise KeyError(funder_slug)
        path = self._cache_path(
            "dashboards", dataset_id, funder_slug, "json"
        )
        if not os.path.isfile(path):
            self.dashboard(dataset_id, funder_slug)
        return path

    def download_path(self, dataset_id, funder_slug=None):
        path = self._cache_path(
            "downloads", dataset_id, funder_slug, "zip"
        )
        if os.path.isfile(path):
            return path

        (dataset, funder, studies, ancestry, bubbles, _, _) = \
            self._selection(dataset_id, funder_slug)
        with tempfile.TemporaryDirectory(
                prefix="gwas-dashboard-download-") as temporary:
            temporary = Path(temporary)
            studies_path = temporary / "studies.tsv"
            ancestry_path = temporary / "ancestry.tsv"
            bubbles_path = temporary / "bubble_df.csv"
            selection_path = temporary / "selection.json"

            studies.to_csv(studies_path, sep="\t", index=False)
            ancestry.to_csv(ancestry_path, sep="\t", index=False)
            bubble_output = bubbles.copy()
            bubble_output["Selected Dataset"] = dataset["name"]
            if funder:
                bubble_output["Selected Funder"] = funder["name"]
            bubble_output.to_csv(bubbles_path, index=False)
            with open(selection_path, "w", encoding="utf-8") as output:
                json.dump({
                    "dataset": dataset,
                    "funder": funder,
                    "studyCount": int(
                        studies["STUDY ACCESSION"].nunique()
                    ),
                }, output, indent=2)

            files = [
                (studies_path, "studies.tsv"),
                (ancestry_path, "ancestry.tsv"),
                (bubbles_path, "bubble_df.csv"),
                (selection_path, "selection.json"),
            ]
            if funder:
                self._ensure_funder_index()
                funding_path = temporary / "funding.json"
                selected_pmids = {
                    funder_pipeline.normalize_pmid(value)
                    for value in studies["PUBMEDID"]
                }
                with open(funding_path, "w", encoding="utf-8") as output:
                    json.dump({
                        pmid: self._funding_by_publication.get(pmid, [])
                        for pmid in sorted(selected_pmids)
                        if pmid
                    }, output, indent=2)
                files.append((funding_path, "funding.json"))

            funder_pipeline._safe_zip_write(path, files)
        return path


_stores = {}
_stores_lock = threading.Lock()


def get_dashboard_filter_store(data_path="data"):
    absolute_path = os.path.abspath(data_path)
    study_path = os.path.join(
        absolute_path, "catalog", "raw", "Cat_Stud.tsv"
    )
    signature = (absolute_path, os.path.getmtime(study_path))
    with _stores_lock:
        store = _stores.get(signature)
        if store is None:
            _stores.clear()
            store = DashboardFilterStore(absolute_path)
            _stores[signature] = store
        return store
