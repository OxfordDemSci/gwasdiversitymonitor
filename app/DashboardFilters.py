import hashlib
import datetime
import json
import os
import re
import tempfile
import threading
import unicodedata
import zipfile
from collections import OrderedDict, defaultdict
from pathlib import Path

import pandas as pd

import funder_pipeline


FILTER_SCHEMA_VERSION = 5
PRECOMPUTED_FILTER_ARCHIVE = os.path.join(
    "filter-cache", "individual-dashboards.zip"
)
PRECOMPUTED_FILTER_MANIFEST = "manifest.json"
PRECOMPUTED_FILTER_OPTION_MEMBERS = {
    ("funders", "initial"): "options/funders-initial.json",
    ("funders", "replication"): "options/funders-replication.json",
    ("cohorts", "initial"): "options/cohorts-initial.json",
    ("cohorts", "replication"): "options/cohorts-replication.json",
}
COHORT_CLEANER_FILE = os.path.join("support", "cohort_cleaner.json")
BUBBLE_PAYLOAD_ROW_LIMIT = 5000


class DashboardSelectionUnavailable(RuntimeError):
    pass


def split_cohorts(value):
    if value is None or pd.isna(value):
        return []
    return [token.strip() for token in str(value).split("|") if token.strip()]


def normalize_cohort_name(value):
    """Return a conservative key without fuzzy or plural matching."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip().casefold()


def cohort_cleaner_path(data_path="data"):
    return os.path.join(data_path, COHORT_CLEANER_FILE)


def load_cohort_cleaner(data_path="data"):
    try:
        with open(cohort_cleaner_path(data_path), encoding="utf-8") as source:
            raw = json.load(source)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return {
        normalize_cohort_name(alias): re.sub(
            r"\s+", " ", unicodedata.normalize("NFKC", str(canonical))
        ).strip()
        for alias, canonical in raw.items()
        if normalize_cohort_name(alias) and str(canonical).strip()
    }


def canonical_cohort_name(value, cleaner):
    name = re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))
    ).strip()
    visited = set()
    while name:
        key = normalize_cohort_name(name)
        if key in visited or key not in cleaner:
            break
        visited.add(key)
        name = cleaner[key]
    return name


def _selection_ids(values):
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]

    result = []
    seen = set()
    for value in values:
        for token in str(value or "").split(","):
            token = token.strip()
            if token and token not in seen:
                result.append(token)
                seen.add(token)
    return tuple(sorted(result))


class DashboardFilterStore:
    """Build and cache dashboards for cohort and funder selections."""

    CACHE_LIMIT = 4

    def __init__(self, data_path="data", use_precomputed=True):
        self.data_path = os.path.abspath(data_path)
        self._lock = threading.RLock()
        self._sources = None
        self._source_indexes = None
        self._facet_studies = None
        self._all_accessions = None
        self._accession_pmids = None
        self._stage_accessions = None
        self._recorded_stage_accessions = None
        self._bubble_stage_accessions = None
        self._bubble_accession_row_counts = None
        self._dataset_entries = None
        self._dataset_accessions = None
        self._dataset_by_id = None
        self._funder_pmids = None
        self._funder_accessions = None
        self._funder_entries = None
        self._funding_by_publication = None
        self._payload_cache = OrderedDict()
        self._cache_root = self._build_cache_root()
        self._precomputed_archive = os.path.join(
            self.data_path, PRECOMPUTED_FILTER_ARCHIVE
        ) if use_precomputed else None
        self._precomputed_options = {}

    def _build_cache_root(self):
        source_paths = (
            os.path.join(self.data_path, "catalog", "raw", "Cat_Stud.tsv"),
            os.path.join(self.data_path, "catalog", "raw", "Cat_Map.tsv"),
            os.path.join(
                self.data_path, "catalog", "synthetic",
                "Cat_Anc_wBroader.tsv",
            ),
            os.path.join(self.data_path, "toplot", "bubble_df.csv"),
            os.path.join(
                self.data_path, "funders", "pubmed_grants.json"
            ),
            funder_pipeline.funder_cleaner_path(self.data_path),
            cohort_cleaner_path(self.data_path),
        )
        signature = [
            self.data_path,
            f"filter-schema:{FILTER_SCHEMA_VERSION}",
            f"report-schema:{funder_pipeline.REPORT_SCHEMA_VERSION}",
        ]
        for path in source_paths:
            try:
                stat = os.stat(path)
                signature.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")
            except OSError:
                signature.append(f"{path}:missing")
        digest = hashlib.sha256("\n".join(signature).encode()).hexdigest()[:20]
        cache_base = os.environ.get("GWAS_DASHBOARD_FILTER_CACHE", "").strip()
        if not cache_base:
            cache_base = os.path.join(
                self.data_path, ".dashboard-filter-cache"
            )
        return os.path.join(
            os.path.abspath(cache_base), digest
        )

    def _cache_path(self, directory, cohort_ids, funder_slugs, suffix):
        cohort_ids = _selection_ids(cohort_ids)
        funder_slugs = _selection_ids(funder_slugs)
        encoded = json.dumps(
            {"cohorts": cohort_ids, "funders": funder_slugs},
            separators=(",", ":"), sort_keys=True,
        )
        digest = hashlib.sha256(encoded.encode()).hexdigest()[:20]
        hint = cohort_ids[0] if cohort_ids else "all-cohorts"
        if funder_slugs:
            hint = f"{hint}--{funder_slugs[0]}"
        hint = re.sub(r"[^a-zA-Z0-9._-]+", "-", hint)[:70]
        return os.path.join(
            self._cache_root, directory, f"{hint}-{digest}.{suffix}"
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

    @staticmethod
    def _precomputed_member(cohort_ids, funder_slugs):
        if len(cohort_ids) == 1 and not funder_slugs:
            return f"cohorts/{cohort_ids[0]}.json"
        if len(funder_slugs) == 1 and not cohort_ids:
            return f"funders/{funder_slugs[0]}.json"
        return None

    def _load_precomputed_dashboard(self, cohort_ids, funder_slugs):
        member = self._precomputed_member(cohort_ids, funder_slugs)
        if not member or not self._precomputed_archive \
                or not os.path.isfile(self._precomputed_archive):
            return None
        try:
            with zipfile.ZipFile(self._precomputed_archive) as archive:
                payload = json.loads(archive.read(member))
            if payload.get("version") != FILTER_SCHEMA_VERSION:
                return None
            return payload
        except (KeyError, OSError, TypeError, ValueError,
                zipfile.BadZipFile, json.JSONDecodeError):
            return None

    def _load_precomputed_options(self, kind, stage):
        stage = self._normalise_stage(stage)
        member = PRECOMPUTED_FILTER_OPTION_MEMBERS.get((kind, stage))
        if not member or not self._precomputed_archive \
                or not os.path.isfile(self._precomputed_archive):
            return None
        cache_key = (kind, stage)
        with self._lock:
            cached = self._precomputed_options.get(cache_key)
            if cached is not None:
                return cached
        try:
            with zipfile.ZipFile(self._precomputed_archive) as archive:
                payload = json.loads(archive.read(member))
            if payload.get("version") != FILTER_SCHEMA_VERSION \
                    or not isinstance(payload.get("entries"), list):
                return None
            entries = tuple(payload["entries"])
        except (KeyError, OSError, TypeError, ValueError,
                zipfile.BadZipFile, json.JSONDecodeError):
            return None
        with self._lock:
            self._precomputed_options[cache_key] = entries
        return entries

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

    @staticmethod
    def _entry_sort_key(entry):
        return (
            -entry.get("studyCount", 0),
            -entry.get("publicationCount", 0),
            entry.get("name", "").casefold(),
            entry.get("name", ""),
        )

    def _ensure_dataset_index(self):
        with self._lock:
            if self._dataset_entries is not None:
                if self._dataset_by_id is None:
                    self._dataset_by_id = {
                        entry["id"]: entry for entry in self._dataset_entries
                    }
                return

            cache_path = os.path.join(self._cache_root, "cohorts.json")
            try:
                with open(cache_path, encoding="utf-8") as source:
                    cached = json.load(source)
                if cached.get("version") != FILTER_SCHEMA_VERSION:
                    raise ValueError("obsolete cohort cache")
                self._dataset_entries = cached["entries"]
                self._dataset_accessions = {
                    key: frozenset(values)
                    for key, values in cached["accessions"].items()
                }
                self._dataset_by_id = {
                    entry["id"]: entry for entry in self._dataset_entries
                }
                return
            except (KeyError, OSError, TypeError, ValueError,
                    json.JSONDecodeError):
                pass

            studies = (
                self._facet_studies
                if self._facet_studies is not None
                else self._load_studies()
            )
            self._facet_studies = studies
            accession_pmids = defaultdict(set)
            variants = defaultdict(lambda: defaultdict(set))
            cohort_cleaner = load_cohort_cleaner(self.data_path)
            for row in studies[
                    ["STUDY ACCESSION", "PUBMEDID", "COHORT"]
            ].itertuples(index=False, name=None):
                accession, pmid, cohort_value = row
                if pd.isna(accession):
                    continue
                accession = str(accession)
                if pmid:
                    accession_pmids[accession].add(str(pmid))
                for cohort_name in split_cohorts(cohort_value):
                    cohort_name = canonical_cohort_name(
                        cohort_name, cohort_cleaner
                    )
                    key = normalize_cohort_name(cohort_name)
                    if key:
                        variants[key][cohort_name].add(accession)

            used_ids = set()
            entries = []
            accessions = {}
            for cohort_variants in variants.values():
                name = sorted(
                    cohort_variants,
                    key=lambda value: (
                        -len(cohort_variants[value]),
                        value.casefold(), value,
                    ),
                )[0]
                cohort_accessions = frozenset().union(
                    *cohort_variants.values()
                )
                base = funder_pipeline.slugify(name) or "cohort"
                cohort_id = base
                suffix = 2
                while cohort_id in used_ids:
                    cohort_id = f"{base}-{suffix}"
                    suffix += 1
                used_ids.add(cohort_id)
                publications = {
                    pmid for accession in cohort_accessions
                    for pmid in accession_pmids.get(accession, ()) if pmid
                }
                entries.append({
                    "id": cohort_id,
                    "name": name,
                    "studyCount": len(cohort_accessions),
                    "publicationCount": len(publications),
                })
                accessions[cohort_id] = cohort_accessions

            entries.sort(key=self._entry_sort_key)
            self._dataset_entries = entries
            self._dataset_accessions = accessions
            self._dataset_by_id = {entry["id"]: entry for entry in entries}
            self._atomic_json(cache_path, {
                "version": FILTER_SCHEMA_VERSION,
                "entries": entries,
                "accessions": {
                    key: sorted(values) for key, values in accessions.items()
                },
            })

    def _set_facet_indexes(self, studies, ancestry):
        self._facet_studies = studies
        self._all_accessions = frozenset(
            studies["STUDY ACCESSION"].dropna().astype(str)
        )
        accession_pmids = defaultdict(set)
        for accession, pmid in studies[
                ["STUDY ACCESSION", "PUBMEDID"]
        ].itertuples(index=False, name=None):
            if pd.isna(accession):
                continue
            pmid = funder_pipeline.normalize_pmid(pmid)
            if pmid:
                accession_pmids[str(accession)].add(pmid)
        self._accession_pmids = {
            accession: frozenset(pmids)
            for accession, pmids in accession_pmids.items()
        }
        if "STAGE" in ancestry.columns:
            stage_values = ancestry["STAGE"].fillna("").astype(str).str.casefold()
        else:
            stage_values = pd.Series("initial", index=ancestry.index)
        self._stage_accessions = {
            "initial": frozenset(
                ancestry.loc[
                    stage_values.eq("initial"), "STUDY ACCESSION"
                ].dropna().astype(str)
            ),
            "replication": frozenset(
                ancestry.loc[
                    stage_values.eq("replication"), "STUDY ACCESSION"
                ].dropna().astype(str)
            ),
        }
        if "Broader" in ancestry.columns:
            broader = ancestry["Broader"].fillna("").astype(str).str.strip()
            recorded = broader.ne("") & broader.str.casefold().ne(
                "in part not recorded"
            )
            self._recorded_stage_accessions = {
                stage: frozenset(
                    ancestry.loc[
                        stage_values.eq(stage) & recorded,
                        "STUDY ACCESSION",
                    ].dropna().astype(str)
                )
                for stage in ("initial", "replication")
            }
        else:
            self._recorded_stage_accessions = dict(self._stage_accessions)

    def _ensure_facet_indexes(self):
        with self._lock:
            if self._all_accessions is not None:
                return
            studies = self._facet_studies
            if studies is None and self._sources is not None:
                studies = self._sources[0]
            if studies is None:
                studies = self._load_studies()
            if self._sources is not None:
                ancestry = self._sources[1]
            else:
                ancestry = pd.read_csv(
                    os.path.join(
                        self.data_path, "catalog", "synthetic",
                        "Cat_Anc_wBroader.tsv",
                    ),
                    sep="\t", dtype=str,
                    usecols=["STUDY ACCESSION", "STAGE", "Broader"],
                )
            self._set_facet_indexes(studies, ancestry)

    def _ensure_sources(self):
        with self._lock:
            if self._sources is None:
                self._sources = funder_pipeline._load_sources(self.data_path)
            if self._source_indexes is not None:
                return

            studies, ancestry, _, bubbles, _ = self._sources
            self._source_indexes = (
                studies.set_index("STUDY ACCESSION", drop=False),
                ancestry.set_index("STUDY ACCESSION", drop=False),
                bubbles.set_index("ACCESSION", drop=False),
            )
            bubble_stages = bubbles["STAGE"].fillna("").astype(
                str
            ).str.casefold() if "STAGE" in bubbles.columns else pd.Series(
                "initial", index=bubbles.index
            )
            self._bubble_stage_accessions = {
                stage: frozenset(
                    bubbles.loc[
                        bubble_stages.eq(stage), "ACCESSION"
                    ].dropna().astype(str)
                )
                for stage in ("initial", "replication")
            }
            self._bubble_accession_row_counts = {
                str(accession): int(count)
                for accession, count in bubbles[
                    "ACCESSION"
                ].dropna().astype(str).value_counts().items()
            }
            if self._all_accessions is None:
                self._set_facet_indexes(studies, ancestry)

    def warm(self):
        """Load immutable filter sources and indexes before serving requests."""
        self._ensure_sources()
        self._ensure_dataset_index()
        self._ensure_funder_index()

    def _select_accessions(self, source_number, accessions):
        self._ensure_sources()
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
                if self._funder_accessions is None:
                    self._funder_accessions = {}
                return

            self._ensure_facet_indexes()
            with open(
                os.path.join(self.data_path, "funders", "pubmed_grants.json"),
                encoding="utf-8"
            ) as source:
                cache = json.load(source)
            cleaner = funder_pipeline.load_funder_cleaner(
                funder_pipeline.funder_cleaner_path(self.data_path)
            )
            by_publication = funder_pipeline.funding_names_by_publication(
                cache, cleaner
            )
            pmid_accessions = defaultdict(set)
            for accession, pmids in self._accession_pmids.items():
                for pmid in pmids:
                    pmid_accessions[pmid].add(accession)

            name_pmids = defaultdict(set)
            for pmid, names in by_publication.items():
                for name in names:
                    name_pmids[name].add(pmid)

            entries = {}
            funder_pmids = {}
            funder_accessions = {}
            used_slugs = set()
            for name in sorted(name_pmids, key=str.casefold):
                pmids = frozenset(name_pmids[name])
                accessions = frozenset().union(
                    *(pmid_accessions.get(pmid, set()) for pmid in pmids)
                ) if pmids else frozenset()
                if not accessions:
                    continue
                base = funder_pipeline.slugify(name) or "funder"
                slug = base
                suffix = 2
                while slug in used_slugs:
                    slug = f"{base}-{suffix}"
                    suffix += 1
                used_slugs.add(slug)
                matched_pmids = {
                    pmid for accession in accessions
                    for pmid in self._accession_pmids.get(accession, ())
                    if pmid in pmids
                }
                entry = {
                    "slug": slug,
                    "name": name,
                    "studyCount": len(accessions),
                    "publicationCount": len(matched_pmids),
                }
                entries[slug] = entry
                funder_pmids[slug] = pmids
                funder_accessions[slug] = accessions

            self._funder_entries = entries
            self._funder_pmids = funder_pmids
            self._funder_accessions = funder_accessions
            self._funding_by_publication = by_publication

    @staticmethod
    def _normalise_stage(stage):
        value = str(stage or "").strip().casefold()
        if value in ("", "all"):
            return None
        if value in ("initial", "discovery"):
            return "initial"
        if value == "replication":
            return "replication"
        raise ValueError(f"Unknown stage: {stage}")

    def _publication_count(self, accessions):
        self._ensure_facet_indexes()
        publications = {
            pmid for accession in accessions
            for pmid in self._accession_pmids.get(accession, ()) if pmid
        }
        return len(publications)

    def _counts(self, accessions, stage=None):
        self._ensure_facet_indexes()
        stage = self._normalise_stage(stage)
        matching = frozenset(accessions)
        if stage:
            matching &= self._stage_accessions[stage]
        return {
            "studyCount": len(matching),
            "publicationCount": self._publication_count(matching),
            "recordedAncestryStudyCount": len(
                matching & self._recorded_stage_accessions[stage]
            ) if stage else len(
                matching & frozenset().union(
                    *self._recorded_stage_accessions.values()
                )
            ),
        }

    def _dashboard_stage_counts(self, accessions):
        self._ensure_sources()
        selected = frozenset(accessions)
        result = {}
        for stage in ("initial", "replication"):
            counts = self._counts(selected, stage)
            counts["bubbleStudyCount"] = len(
                selected & self._bubble_stage_accessions[stage]
            )
            result[stage] = counts
        return result

    def _filtered_bubble_payload(self, accessions):
        self._ensure_sources()
        selected = frozenset(accessions)
        row_count = sum(
            self._bubble_accession_row_counts.get(accession, 0)
            for accession in selected
        )
        if row_count > BUBBLE_PAYLOAD_ROW_LIMIT:
            return None
        return funder_pipeline.build_bubble_payload(
            self._select_accessions(2, selected)
        )

    def _cohort_union(self, cohort_ids):
        self._ensure_dataset_index()
        cohort_ids = _selection_ids(cohort_ids)
        if not cohort_ids:
            self._ensure_facet_indexes()
            return self._all_accessions
        unknown = [
            cohort_id for cohort_id in cohort_ids
            if cohort_id not in self._dataset_accessions
        ]
        if unknown:
            raise KeyError(unknown[0])
        return frozenset().union(*(
            self._dataset_accessions[cohort_id]
            for cohort_id in cohort_ids
        ))

    def _funder_union(self, funder_slugs):
        funder_slugs = _selection_ids(funder_slugs)
        self._ensure_facet_indexes()
        if not funder_slugs:
            return self._all_accessions
        self._ensure_funder_index()
        unknown = [
            slug for slug in funder_slugs if slug not in self._funder_pmids
        ]
        if unknown:
            raise KeyError(unknown[0])
        for slug in funder_slugs:
            if slug not in self._funder_accessions:
                pmids = self._funder_pmids[slug]
                studies = self._facet_studies
                self._funder_accessions[slug] = frozenset(
                    studies.loc[
                        studies["PUBMEDID"].isin(pmids), "STUDY ACCESSION"
                    ].dropna().astype(str)
                )
        return frozenset().union(*(
            self._funder_accessions[slug] for slug in funder_slugs
        ))

    def _filtered_accessions(
            self, cohort_ids=None, funder_slugs=None, stage=None):
        self._ensure_facet_indexes()
        accessions = self._all_accessions
        cohort_ids = _selection_ids(cohort_ids)
        funder_slugs = _selection_ids(funder_slugs)
        if cohort_ids:
            accessions &= self._cohort_union(cohort_ids)
        if funder_slugs:
            accessions &= self._funder_union(funder_slugs)
        stage = self._normalise_stage(stage)
        if stage:
            accessions &= self._stage_accessions[stage]
        return accessions

    def cohorts(self, search="", funder_slugs=None, stage=None):
        funder_slugs = _selection_ids(funder_slugs)
        stage_name = self._normalise_stage(stage)
        needle = normalize_cohort_name(search)
        if not funder_slugs and stage_name:
            precomputed = self._load_precomputed_options(
                "cohorts", stage_name
            )
            if precomputed is not None:
                return [
                    entry.copy() for entry in precomputed
                    if not needle
                    or needle in normalize_cohort_name(entry["name"])
                    or needle in entry["id"].casefold()
                ]
        self._ensure_dataset_index()
        if not funder_slugs and not stage_name:
            results = [
                entry.copy() for entry in self._dataset_entries
                if not needle
                or needle in normalize_cohort_name(entry["name"])
                or needle in entry["id"].casefold()
            ]
            results.sort(key=self._entry_sort_key)
            return results
        self._ensure_facet_indexes()
        opposite = self._funder_union(funder_slugs)
        if stage_name:
            opposite &= self._stage_accessions[stage_name]

        results = []
        for entry in self._dataset_entries:
            if needle and needle not in normalize_cohort_name(entry["name"]) \
                    and needle not in entry["id"].casefold():
                continue
            matching = self._dataset_accessions[entry["id"]] & opposite
            if not matching:
                continue
            result = entry.copy()
            result.update(self._counts(matching))
            results.append(result)
        results.sort(key=self._entry_sort_key)
        return results

    def datasets(self, search="", funder_slug=None, stage=None):
        return self.cohorts(search, funder_slug, stage)

    def cohort(self, cohort_id):
        self._ensure_dataset_index()
        try:
            return self._dataset_by_id[cohort_id]
        except KeyError:
            raise KeyError(cohort_id) from None

    def dataset(self, dataset_id):
        return self.cohort(dataset_id)

    def funders(self, search="", cohort_ids=None, stage=None):
        cohort_ids = _selection_ids(cohort_ids)
        stage_name = self._normalise_stage(stage)
        needle = str(search or "").strip().casefold()
        if not cohort_ids and stage_name:
            precomputed = self._load_precomputed_options(
                "funders", stage_name
            )
            if precomputed is not None:
                return [
                    entry.copy() for entry in precomputed
                    if not needle
                    or needle in entry["name"].casefold()
                    or needle in entry["slug"].casefold()
                ]
        self._ensure_funder_index()
        opposite = self._cohort_union(cohort_ids)
        if stage_name:
            opposite &= self._stage_accessions[stage_name]

        results = []
        for slug, entry in self._funder_entries.items():
            if needle and needle not in entry["name"].casefold() \
                    and needle not in slug.casefold():
                continue
            matching = self._funder_accessions[slug] & opposite
            if not matching:
                continue
            result = entry.copy()
            result.update(self._counts(matching))
            results.append(result)
        results.sort(key=self._entry_sort_key)
        return results

    def funders_for_dataset(self, dataset_id, stage=None):
        return {
            entry["slug"]
            for entry in self.funders(cohort_ids=dataset_id, stage=stage)
        }

    def _selection(
            self, cohort_ids=None, funder_slugs=None,
            include_bubbles=True):
        cohort_ids = _selection_ids(cohort_ids)
        funder_slugs = _selection_ids(funder_slugs)
        if not cohort_ids and not funder_slugs:
            raise DashboardSelectionUnavailable("Choose a cohort or funder")

        self._ensure_dataset_index()
        self._ensure_sources()
        self._ensure_funder_index()
        _, _, mappings, _, countries = self._sources
        cohorts = [self.cohort(cohort_id) for cohort_id in cohort_ids]
        funders = []
        for slug in funder_slugs:
            if slug not in self._funder_entries:
                raise KeyError(slug)
            funders.append(self._funder_entries[slug])

        accessions = self._filtered_accessions(cohort_ids, funder_slugs)
        selected_studies = self._select_accessions(0, accessions)
        selected_ancestry = self._select_accessions(1, accessions)
        selected_bubbles = self._select_accessions(2, accessions) \
            if include_bubbles else None
        if selected_studies.empty or selected_ancestry.empty:
            raise DashboardSelectionUnavailable(
                "No published GWAS match this cohort and funder selection"
            )
        return (
            cohorts, funders, selected_studies, selected_ancestry,
            selected_bubbles, mappings, countries,
        )

    @staticmethod
    def _build_report(studies, ancestry, funding_records=None, funder=None):
        return funder_pipeline.build_report(
            funder, studies, ancestry, funding_records or {}
        )

    def dashboard(self, cohort_ids=None, funder_slugs=None):
        cohort_ids = _selection_ids(cohort_ids)
        funder_slugs = _selection_ids(funder_slugs)
        cache_key = (cohort_ids, funder_slugs)
        with self._lock:
            cached = self._payload_cache.get(cache_key)
            if cached is not None:
                self._payload_cache.move_to_end(cache_key)
                return cached

        cache_path = self._cache_path(
            "dashboards", cohort_ids, funder_slugs, "json"
        )
        try:
            with open(cache_path, encoding="utf-8") as source:
                cached = json.load(source)
            if cached.get("version") != FILTER_SCHEMA_VERSION:
                raise ValueError("obsolete dashboard cache")
            with self._lock:
                self._payload_cache[cache_key] = cached
            return cached
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

        cached = self._load_precomputed_dashboard(cohort_ids, funder_slugs)
        if cached is not None:
            self._atomic_json(cache_path, cached)
            with self._lock:
                self._payload_cache[cache_key] = cached
                self._payload_cache.move_to_end(cache_key)
                while len(self._payload_cache) > self.CACHE_LIMIT:
                    self._payload_cache.popitem(last=False)
            return cached

        (cohorts, funders, studies, ancestry, _, mappings,
         countries) = self._selection(
             cohort_ids, funder_slugs, include_bubbles=False
         )
        selected_accessions = frozenset(
            studies["STUDY ACCESSION"].dropna().astype(str)
        )
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

        overall_counts = self._counts(selected_accessions)
        stage_counts = self._dashboard_stage_counts(selected_accessions)
        selection = {
            "cohorts": cohorts,
            "funders": funders,
            "dataset": cohorts[0] if len(cohorts) == 1 else None,
            "funder": funders[0] if len(funders) == 1 else None,
            "studyCount": overall_counts["studyCount"],
            "publicationCount": overall_counts["publicationCount"],
            "stageCounts": stage_counts,
            "accessions": sorted(selected_accessions),
        }
        payload = {
            "version": FILTER_SCHEMA_VERSION,
            "selection": selection,
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
        }
        bubble_payload = self._filtered_bubble_payload(selected_accessions)
        if bubble_payload is not None:
            payload["bubbleGraph"] = bubble_payload
        self._atomic_json(cache_path, payload)
        with self._lock:
            self._payload_cache[cache_key] = payload
            self._payload_cache.move_to_end(cache_key)
            while len(self._payload_cache) > self.CACHE_LIMIT:
                self._payload_cache.popitem(last=False)
        return payload

    def report(self, cohort_ids=None, funder_slugs=None):
        cohort_ids = _selection_ids(cohort_ids)
        funder_slugs = _selection_ids(funder_slugs)
        path = self._cache_path(
            "reports", cohort_ids, funder_slugs, "json"
        )
        try:
            with open(path, encoding="utf-8") as source:
                cached = json.load(source)
            if cached.get("version") != FILTER_SCHEMA_VERSION:
                raise ValueError("obsolete report cache")
            return cached["report"]
        except (KeyError, OSError, TypeError, ValueError,
                json.JSONDecodeError):
            pass

        (_, funders, studies, ancestry, _, _, _) = self._selection(
            cohort_ids, funder_slugs, include_bubbles=False
        )
        funder_label = ", ".join(
            entry["name"] for entry in funders
        ) or None
        report = self._build_report(
            studies, ancestry, self._funding_by_publication or {},
            funder_label,
        )
        self._atomic_json(path, {
            "version": FILTER_SCHEMA_VERSION,
            "report": report,
        })
        return report

    def dashboard_path(self, cohort_ids=None, funder_slugs=None):
        cohort_ids = _selection_ids(cohort_ids)
        funder_slugs = _selection_ids(funder_slugs)
        if not cohort_ids and not funder_slugs:
            raise DashboardSelectionUnavailable("Choose a cohort or funder")
        for cohort_id in cohort_ids:
            self.cohort(cohort_id)
        self._ensure_funder_index()
        for slug in funder_slugs:
            if slug not in self._funder_entries:
                raise KeyError(slug)
        path = self._cache_path(
            "dashboards", cohort_ids, funder_slugs, "json"
        )
        if not os.path.isfile(path):
            self.dashboard(cohort_ids, funder_slugs)
        return path

    def download_path(self, cohort_ids=None, funder_slugs=None):
        cohort_ids = _selection_ids(cohort_ids)
        funder_slugs = _selection_ids(funder_slugs)
        path = self._cache_path(
            "downloads", cohort_ids, funder_slugs, "zip"
        )
        if os.path.isfile(path):
            return path

        (cohorts, funders, studies, ancestry, bubbles, _, _) = \
            self._selection(cohort_ids, funder_slugs)
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
            bubble_output["Selected Cohorts"] = " | ".join(
                entry["name"] for entry in cohorts
            )
            bubble_output["Selected Funders"] = " | ".join(
                entry["name"] for entry in funders
            )
            bubble_output.to_csv(bubbles_path, index=False)
            selected_accessions = frozenset(
                studies["STUDY ACCESSION"].dropna().astype(str)
            )
            selection = {
                "cohorts": cohorts,
                "funders": funders,
                "studyCount": len(selected_accessions),
                "publicationCount": self._publication_count(
                    selected_accessions
                ),
            }
            with open(selection_path, "w", encoding="utf-8") as output:
                json.dump(selection, output, indent=2)

            files = [
                (studies_path, "studies.tsv"),
                (ancestry_path, "ancestry.tsv"),
                (bubbles_path, "bubble_df.csv"),
                (selection_path, "selection.json"),
            ]
            if funders:
                funding_path = temporary / "funding.json"
                selected_pmids = {
                    funder_pipeline.normalize_pmid(value)
                    for value in studies["PUBMEDID"]
                }
                with open(funding_path, "w", encoding="utf-8") as output:
                    json.dump({
                        pmid: self._funding_by_publication.get(pmid, [])
                        for pmid in sorted(selected_pmids) if pmid
                    }, output, indent=2)
                files.append((funding_path, "funding.json"))

            funder_pipeline._safe_zip_write(path, files)
        return path


def build_precomputed_filter_archive(data_path="data", output_path=None,
                                     progress=None, limit=None):
    """Prepare compact dashboards for every individual cohort and funder."""
    data_path = os.path.abspath(data_path)
    output_path = output_path or os.path.join(
        data_path, PRECOMPUTED_FILTER_ARCHIVE
    )
    store = DashboardFilterStore(data_path, use_precomputed=False)
    store.warm()

    selections = [
        ("funders", slug, (), (slug,))
        for slug in store._funder_entries
    ] + [
        ("cohorts", entry["id"], (entry["id"],), ())
        for entry in store._dataset_entries
    ]
    if limit is not None:
        selections = selections[:max(0, int(limit))]

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=str(output.parent)
    )
    os.close(descriptor)
    members = []
    option_members = []
    try:
        with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=6) as archive:
            option_builders = {
                "funders": store.funders,
                "cohorts": store.cohorts,
            }
            for (kind, stage), member in \
                    PRECOMPUTED_FILTER_OPTION_MEMBERS.items():
                entries = option_builders[kind]("", (), stage)
                archive.writestr(member, json.dumps({
                    "version": FILTER_SCHEMA_VERSION,
                    "entries": entries,
                }, separators=(",", ":")))
                option_members.append(member)
                members.append(member)

            total = len(selections)
            for number, (kind, identifier, cohorts, funders) in enumerate(
                    selections, 1):
                payload = store.dashboard(cohorts, funders)
                member = f"{kind}/{identifier}.json"
                archive.writestr(
                    member,
                    json.dumps(payload, separators=(",", ":")),
                )
                members.append(member)
                if progress:
                    progress(number, total, kind, identifier)

            archive.writestr(PRECOMPUTED_FILTER_MANIFEST, json.dumps({
                "version": FILTER_SCHEMA_VERSION,
                "generatedAt": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
                "funderCount": sum(
                    member.startswith("funders/") for member in members
                ),
                "cohortCount": sum(
                    member.startswith("cohorts/") for member in members
                ),
                "optionMembers": option_members,
                "members": members,
            }, separators=(",", ":")))
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return str(output)


def validate_precomputed_filter_archive(data_path="data", path=None):
    path = path or os.path.join(
        os.path.abspath(data_path), PRECOMPUTED_FILTER_ARCHIVE
    )
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("The precomputed filter archive is corrupt")
        manifest = json.loads(archive.read(PRECOMPUTED_FILTER_MANIFEST))
        members = manifest.get("members")
        option_members = manifest.get("optionMembers")
        if manifest.get("version") != FILTER_SCHEMA_VERSION \
                or not isinstance(members, list) \
                or not isinstance(option_members, list):
            raise ValueError("The precomputed filter manifest is invalid")
        expected_option_members = set(
            PRECOMPUTED_FILTER_OPTION_MEMBERS.values()
        )
        if set(option_members) != expected_option_members \
                or not expected_option_members.issubset(members):
            raise ValueError("The precomputed filter options differ")
        expected = set(members) | {PRECOMPUTED_FILTER_MANIFEST}
        if len(expected) != len(members) + 1 \
                or set(archive.namelist()) != expected:
            raise ValueError("The precomputed filter members differ")
        if any(not (
                re.fullmatch(
                    r"(?:funders|cohorts)/[a-z0-9._-]+\.json", member
                ) or member in expected_option_members
                ) for member in members):
            raise ValueError("The precomputed filter archive has unsafe names")
        if manifest.get("funderCount") != sum(
                member.startswith("funders/") for member in members) \
                or manifest.get("cohortCount") != sum(
                    member.startswith("cohorts/") for member in members
                ):
            raise ValueError("The precomputed filter counts differ")
        for member in expected_option_members:
            payload = json.loads(archive.read(member))
            if payload.get("version") != FILTER_SCHEMA_VERSION \
                    or not isinstance(payload.get("entries"), list):
                raise ValueError("The precomputed filter options are invalid")
    return manifest


_stores = {}
_stores_lock = threading.Lock()


def get_dashboard_filter_store(data_path="data"):
    absolute_path = os.path.abspath(data_path)
    source_paths = (
        os.path.join(absolute_path, "catalog", "raw", "Cat_Stud.tsv"),
        os.path.join(
            absolute_path, "catalog", "synthetic", "Cat_Anc_wBroader.tsv"
        ),
        os.path.join(absolute_path, "funders", "pubmed_grants.json"),
        funder_pipeline.funder_cleaner_path(absolute_path),
        cohort_cleaner_path(absolute_path),
    )
    signature = []
    for path in source_paths:
        try:
            modified = os.path.getmtime(path)
        except OSError:
            modified = None
        signature.append((path, modified))
    signature = tuple(signature)
    with _stores_lock:
        store = _stores.get(signature)
        if store is None:
            _stores.clear()
            store = DashboardFilterStore(absolute_path)
            _stores[signature] = store
        return store
