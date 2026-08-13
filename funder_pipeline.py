"""Build isolated funder data without changing the main dashboard artifacts."""

import argparse
import csv
import datetime
import html
import json
import math
import os
import re
import shutil
import tempfile
import time
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import requests


NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
CACHE_VERSION = 1
ARTIFACT_VERSION = 1
DEFAULT_MIN_STUDIES = 50
DEFAULT_BATCH_SIZE = 100
FUNDER_DOWNLOAD_MEMBERS = {
    "studies.tsv", "ancestry.tsv", "bubble_df.csv", "funding.csv"
}


def normalize_pmid(value):
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text if text.isdigit() else ""


def slugify(value):
    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "funder"


def _atomic_json(path, payload, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            if compact:
                json.dump(
                    payload, output, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False
                )
            else:
                json.dump(
                    payload, output, ensure_ascii=False, indent=2,
                    sort_keys=True, allow_nan=False
                )
                output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(source)
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def _study_path(data_path):
    return os.path.join(data_path, "catalog", "raw", "Cat_Stud.tsv")


def read_publication_ids(data_path):
    header = pd.read_csv(_study_path(data_path), sep="\t", nrows=0).columns
    column = "PUBMEDID" if "PUBMEDID" in header else "PUBMED ID"
    values = pd.read_csv(
        _study_path(data_path), sep="\t", usecols=[column], dtype=str
    )[column]
    return sorted({pmid for pmid in values.map(normalize_pmid) if pmid}, key=int)


def parse_pubmed_grants(xml_content, requested_ids):
    records = {pmid: {"grants": []} for pmid in requested_ids}
    root = ElementTree.fromstring(xml_content)
    for article in root.findall(".//PubmedArticle"):
        pmid_node = article.find("./MedlineCitation/PMID")
        if pmid_node is None:
            continue
        pmid = normalize_pmid(pmid_node.text)
        if not pmid:
            continue
        grants = []
        seen = set()
        for grant in article.findall("./MedlineCitation/Article/GrantList/Grant"):
            item = {
                "agency": (grant.findtext("Agency") or "").strip(),
                "acronym": (grant.findtext("Acronym") or "").strip(),
                "country": (grant.findtext("Country") or "").strip(),
                "grantId": (grant.findtext("GrantID") or "").strip(),
            }
            signature = tuple(item.values())
            if signature not in seen and any(item.values()):
                grants.append(item)
                seen.add(signature)
        records[pmid] = {"grants": grants}
    return records


def collect_pubmed_grants(
        data_path, output_path, email=None, batch_size=DEFAULT_BATCH_SIZE,
        request_delay=0.36, max_retries=4, session=None):
    publication_ids = read_publication_ids(data_path)
    cache = _load_json(output_path, {"version": CACHE_VERSION, "records": {}})
    if cache.get("version") != CACHE_VERSION:
        cache = {"version": CACHE_VERSION, "records": {}}
    records = cache.setdefault("records", {})
    missing = [pmid for pmid in publication_ids if pmid not in records]
    session = session or requests.Session()

    for offset in range(0, len(missing), batch_size):
        batch = missing[offset:offset + batch_size]
        last_error = None
        for attempt in range(max_retries):
            try:
                request_data = {
                        "db": "pubmed",
                        "retmode": "xml",
                        "id": ",".join(batch),
                        "tool": "gwas-diversity-monitor",
                }
                if email:
                    request_data["email"] = email
                response = session.post(
                    NCBI_EFETCH_URL,
                    data=request_data,
                    timeout=90,
                )
                response.raise_for_status()
                records.update(parse_pubmed_grants(response.content, batch))
                cache["updatedAt"] = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
                cache["publicationCount"] = len(publication_ids)
                _atomic_json(output_path, cache)
                print(
                    f"Collected PubMed funding data: "
                    f"{min(offset + len(batch), len(missing))}/{len(missing)}"
                )
                last_error = None
                break
            except (requests.RequestException, ElementTree.ParseError) as error:
                last_error = error
                if attempt + 1 < max_retries:
                    time.sleep(min(2 ** attempt, 8))
        if last_error is not None:
            raise RuntimeError(
                f"PubMed funding request failed for batch beginning {batch[0]}"
            ) from last_error
        time.sleep(request_delay)

    return cache


def _clean_agency_text(value):
    value = html.unescape(str(value or ""))
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[\[\]]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ;,")
    return value


def load_funder_cleaner(path):
    raw = _load_json(path, {})
    return {_clean_agency_text(key): _clean_agency_text(value)
            for key, value in raw.items()}


def canonical_agency(value, cleaner):
    agency = _clean_agency_text(value)
    if not agency:
        return ""
    if "veteran" in agency.casefold():
        agency = "Veterans Affairs"

    visited = set()
    while agency in cleaner and agency not in visited:
        visited.add(agency)
        agency = cleaner[agency]
    if agency in visited:
        return sorted(visited, key=lambda item: (len(item), item))[0]
    return agency


def normalize_funding_records(cache, cleaner, min_studies):
    raw_by_publication = {}
    counts = Counter()
    for pmid, record in cache.get("records", {}).items():
        names = {
            canonical_agency(grant.get("agency"), cleaner)
            for grant in record.get("grants", [])
        }
        names.discard("")
        names.discard("Unclear")
        raw_by_publication[normalize_pmid(pmid)] = names
        counts.update(names)

    retained = {name for name, count in counts.items()
                if count >= min_studies}
    by_publication = {}
    display_counts = Counter()
    for pmid, names in raw_by_publication.items():
        normalized = {name for name in names if name in retained}
        if names - retained:
            normalized.add("Other funders")
        by_publication[pmid] = sorted(normalized)
        display_counts.update(normalized)

    return by_publication, display_counts


def _json_value(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _broader_class(value):
    return str(value or "").replace(" ", "-").replace("/", "-").lower()


def _parent_class(value):
    return str(value or "").replace(", ", ",").replace(" ", "-").replace(",", " ").lower()


def _trait_class(value):
    return (str(value or "").replace(" ", "-")
            .replace(">", "more than").replace("<", "less than")
            .replace("(", "").replace(")", "").lower())


def _encode_bubble_stage(frame):
    frame = frame.copy()
    parsed_dates = pd.to_datetime(frame["DATE"], errors="coerce", utc=True)
    date_milliseconds = [
        None if pd.isna(value) else int(value.timestamp() * 1000)
        for value in parsed_dates
    ]
    rows = []
    for source, date_milliseconds_value in zip(
            frame.to_dict(orient="records"), date_milliseconds):
        row = {key: _json_value(value) for key, value in source.items()}
        n_value = float(row.get("N") or 0)
        row["__Nnum"] = n_value
        row["__dateMS"] = date_milliseconds_value
        row["__class"] = (
            _broader_class(row.get("Broader")) + " "
            + _parent_class(row.get("parentterm"))
        )
        row["__trait"] = _trait_class(row.get("DiseaseOrTrait"))
        row["__DiseaseOrTraitClean"] = str(
            row.get("DiseaseOrTrait") or ""
        ).replace(">", "more than").replace("<", "less than")
        row["__BroaderClass"] = _broader_class(row.get("Broader"))
        row["__ParentTermClass"] = _parent_class(row.get("parentterm"))
        rows.append(row)

    columns = sorted({column for row in rows for column in row})
    dictionaries = {}
    codes = {}
    for column in columns:
        values = []
        lookup = {}
        column_codes = []
        for row in rows:
            value = row.get(column)
            key = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if key not in lookup:
                lookup[key] = len(values)
                values.append(value)
            column_codes.append(lookup[key])
        dictionaries[column] = values
        codes[column] = column_codes

    dates = [row["__dateMS"] for row in rows if row["__dateMS"] is not None]
    numbers = [row["__Nnum"] for row in rows]
    return {
        "columns": columns,
        "dicts": dictionaries,
        "codes": codes,
        "meta": {
            "rowCount": len(rows),
            "maxN": max(numbers) if numbers else 0,
            "minDateMS": min(dates) if dates else None,
            "maxDateMS": max(dates) if dates else None,
            "minDate": datetime.datetime.fromtimestamp(
                min(dates) / 1000, datetime.timezone.utc
            ).strftime("%Y-%m-%d") if dates else None,
            "maxDate": datetime.datetime.fromtimestamp(
                max(dates) / 1000, datetime.timezone.utc
            ).strftime("%Y-%m-%d") if dates else None,
            "includePrecomputed": True,
        },
    }


def build_bubble_payload(frame):
    return {
        "__format": "dict_columnar_v2",
        "bubblegraph_initial": _encode_bubble_stage(
            frame[frame["STAGE"] == "initial"]
        ),
        "bubblegraph_replication": _encode_bubble_stage(
            frame[frame["STAGE"] == "replication"]
        ),
    }


def _percentage_series(frame, ancestries, stage, metric, years):
    stage_frame = frame[frame["STAGE"] == stage].copy()
    if metric == "participants":
        grouped = stage_frame.groupby(["Year", "Broader"])["N"].sum()
    else:
        grouped = stage_frame.groupby(["Year", "Broader"]).size()

    result = {ancestry: {} for ancestry in ancestries}
    for index, year in enumerate(years):
        values = {ancestry: float(grouped.get((year, ancestry), 0))
                  for ancestry in ancestries}
        total = sum(values.values())
        for ancestry, value in values.items():
            result[ancestry][str(index)] = {
                "year": str(year),
                "value": round(value / total * 100, 8) if total else 0.0,
            }
    return result


def build_time_series(ancestry, ancestry_order, final_year):
    frame = ancestry.copy()
    frame["Year"] = pd.to_datetime(frame["DATE"], errors="coerce").dt.year
    frame["N"] = pd.to_numeric(frame["N"], errors="coerce").fillna(0)
    years = list(range(2007, final_year + 1))
    result = {}
    for prefix, source in (
        ("notrecorded", frame),
        ("recorded", frame[frame["Broader"] != "In Part Not Recorded"]),
    ):
        for stage_name, stage in (
            ("discovery", "initial"), ("replication", "replication")
        ):
            for metric in ("studies", "participants"):
                key = f"ts_{prefix}_{stage_name}_{metric}"
                result[key] = _percentage_series(
                    source, ancestry_order, stage, metric, years
                )
    return result


def build_study_parent_map(studies, mappings):
    study_columns = ["STUDY ACCESSION", "DISEASE/TRAIT", "ASSOCIATION COUNT"]
    mapped = studies[study_columns].merge(
        mappings[["Disease trait", "Parent term"]], how="left",
        left_on="DISEASE/TRAIT", right_on="Disease trait"
    )
    mapped = mapped.rename(columns={"Parent term": "parentterm"})
    mapped["ASSOCIATION COUNT"] = pd.to_numeric(
        mapped["ASSOCIATION COUNT"], errors="coerce"
    ).fillna(0)
    return mapped[
        ["STUDY ACCESSION", "DISEASE/TRAIT", "ASSOCIATION COUNT", "parentterm"]
    ].drop_duplicates()


def _heat_values(merged, ancestry_order, parent_terms, stage, metric, years):
    source = merged[merged["STAGE"] == stage]
    if metric == "participants":
        grouped = source.groupby(["Year", "Broader", "parentterm"])["N"].sum()
    else:
        grouped = source.groupby(["Year", "Broader", "parentterm"]).size()
    result = {}
    for year in years:
        rows = {}
        index = 0
        for ancestry in ancestry_order:
            for parent in parent_terms:
                rows[str(index)] = {
                    "ancestry": ancestry,
                    "term": parent,
                    # The existing D3 heatmap treats the string "0" as its
                    # empty-cell sentinel before applying a logarithmic scale.
                    "value": str(round(
                        float(grouped.get((year, ancestry, parent), 0)), 2
                    )),
                }
                index += 1
        result[str(year)] = rows
    return result


def build_heat_map(merged, ancestry_order, parent_terms, final_year):
    available_years = pd.to_numeric(
        merged["Year"], errors="coerce"
    ).dropna().astype(int)
    years = sorted({year for year in available_years if 2008 <= year <= final_year})
    return {
        "heatmap_discovery_studies": _heat_values(
            merged, ancestry_order, parent_terms, "initial", "studies", years
        ),
        "heatmap_replication_studies": _heat_values(
            merged, ancestry_order, parent_terms, "replication", "studies", years
        ),
        "heatmap_discovery_participants": _heat_values(
            merged, ancestry_order, parent_terms, "initial", "participants", years
        ),
        "heatmap_replication_participants": _heat_values(
            merged, ancestry_order, parent_terms, "replication", "participants", years
        ),
    }


def _percentage(numerator, denominator):
    return round(float(numerator) / float(denominator) * 100, 8) \
        if denominator else 0.0


def build_doughnut(merged, ancestry_order, parent_terms, final_year):
    keys = {
        "doughnut_discovery_studies": {},
        "doughnut_discovery_participants": {},
        "doughnut_replication_studies": {},
        "doughnut_replication_participants": {},
        "doughnut_associations": {},
    }
    for year in range(2008, final_year + 1):
        year_frame = merged[merged["Year"] == year]
        if year_frame.empty:
            continue
        for container in keys.values():
            container[str(year)] = {}
        for term in ["All"] + parent_terms:
            term_frame = year_frame if term == "All" else year_frame[
                year_frame["parentterm"] == term
            ]
            for container in keys.values():
                container[str(year)][term] = {}
            initial = term_frame[term_frame["STAGE"] == "initial"]
            replication = term_frame[term_frame["STAGE"] == "replication"]
            totals = {
                "initialN": initial["N"].sum(),
                "initialCount": len(initial),
                "replicationN": replication["N"].sum(),
                "replicationCount": len(replication),
                "associations": initial["ASSOCIATION COUNT"].sum(),
            }
            for order, ancestry in enumerate(ancestry_order, 1):
                initial_ancestry = initial[initial["Broader"] == ancestry]
                replication_ancestry = replication[
                    replication["Broader"] == ancestry
                ]
                values = {
                    "doughnut_discovery_studies": _percentage(
                        len(initial_ancestry), totals["initialCount"]
                    ),
                    "doughnut_discovery_participants": _percentage(
                        initial_ancestry["N"].sum(), totals["initialN"]
                    ),
                    "doughnut_replication_studies": _percentage(
                        len(replication_ancestry), totals["replicationCount"]
                    ),
                    "doughnut_replication_participants": _percentage(
                        replication_ancestry["N"].sum(), totals["replicationN"]
                    ),
                    "doughnut_associations": _percentage(
                        initial_ancestry["ASSOCIATION COUNT"].sum(),
                        totals["associations"]
                    ),
                }
                for key, value in values.items():
                    keys[key][str(year)][term][str(order)] = {
                        "ancestry": ancestry,
                        "value": value,
                    }
    return keys


COUNTRY_REPLACEMENTS = {
    "U.S.": "United States",
    "U.K.": "United Kingdom",
    "Gambia": "Gambia, The",
    "Republic of Korea": "Korea, South",
    "Czech Republic": "Czechia",
    "Russian Federation": "Russia",
    "Iran (Islamic Republic of)": "Iran",
    "Viet Nam": "Vietnam",
    "United Republic of Tanzania": "Tanzania",
    "Republic of Ireland": "Ireland",
    "Micronesia (Federated States of)": "Micronesia, Federated States of",
}


def _first_country(value):
    for token in re.split(r"[,;|]", str(value or "")):
        token = token.strip()
        if token and token != "NR":
            return COUNTRY_REPLACEMENTS.get(token, token)
    return ""


def build_country_map(ancestry, country_lookup, final_year):
    frame = ancestry.copy()
    frame["country"] = frame["COUNTRY OF RECRUITMENT"].map(_first_country)
    frame = frame[frame["country"] != ""]
    frame["Year"] = pd.to_datetime(frame["DATE"], errors="coerce").dt.year
    frame["N"] = pd.to_numeric(frame["N"], errors="coerce").fillna(0)
    populations = country_lookup.set_index("Country")["2017population"].to_dict()
    result = {}
    for year in range(2008, final_year + 1):
        current = frame[frame["Year"] == year]
        if current.empty:
            continue
        grouped = current.groupby("country")["N"].agg(["sum", "count"])
        total_n = grouped["sum"].sum()
        total_count = grouped["count"].sum()
        rows = {}
        for index, (country, values) in enumerate(grouped.iterrows()):
            rows[str(index)] = {
                "country": country,
                "population": _json_value(populations.get(country, 0)) or 0,
                "studies": int(values["count"]),
                "studiesPercentage": _percentage(values["count"], total_count),
                "participants": float(values["sum"]),
                "participantsPercentage": _percentage(values["sum"], total_n),
            }
        result[str(year)] = rows
    return result


SUMMARY_KEYS = {
    "European": "european",
    "Asian": "asian",
    "African": "african",
    "African American or Afro-Caribbean": "afamafcam",
    "Hispanic or Latin American": "hisorlatinam",
    "Other/Mixed": "othermixed",
}


def build_summary(ancestry):
    recorded = ancestry[ancestry["Broader"] != "In Part Not Recorded"].copy()
    recorded["N"] = pd.to_numeric(recorded["N"], errors="coerce").fillna(0)

    def percentages(source, metric):
        if metric == "participants":
            values = source.groupby("Broader")["N"].sum()
        else:
            values = source.groupby("Broader").size()
        total = values.sum()
        return {
            key: _percentage(values.get(ancestry_name, 0), total)
            for ancestry_name, key in SUMMARY_KEYS.items()
        }

    result = {"overallParticipants": percentages(recorded, "participants")}
    for stage_name, stage in (
            ("discovery", "initial"), ("replication", "replication")):
        stage_frame = recorded[recorded["STAGE"] == stage]
        result[f"{stage_name}Participants"] = percentages(
            stage_frame, "participants"
        )
        result[f"{stage_name}Studies"] = percentages(stage_frame, "studies")
    return result


def build_report(funder, studies, ancestry, normalized_records):
    dates = pd.to_datetime(studies["DATE"], errors="coerce").dropna()
    top_traits = studies["DISEASE/TRAIT"].dropna().value_counts().head(8)
    summary = build_summary(ancestry)["overallParticipants"]
    pmids = {normalize_pmid(value) for value in studies["PUBMEDID"]}
    grant_count = sum(
        len(normalized_records.get(pmid, [])) for pmid in pmids if pmid
    )
    return {
        "funder": funder,
        "studyCount": int(studies["PUBMEDID"].map(normalize_pmid).nunique()),
        "ancestryRecordCount": int(len(ancestry)),
        "participantCount": int(pd.to_numeric(
            ancestry["N"], errors="coerce"
        ).fillna(0).sum()),
        "grantRecordCount": int(grant_count),
        "firstStudyDate": dates.min().strftime("%Y-%m-%d") if not dates.empty else None,
        "latestStudyDate": dates.max().strftime("%Y-%m-%d") if not dates.empty else None,
        "topTraits": [
            {"name": str(name), "studies": int(count)}
            for name, count in top_traits.items()
        ],
        "ancestryPercentages": summary,
    }


def _safe_zip_write(archive_path, files):
    archive_path = Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{archive_path.name}.", dir=str(archive_path.parent)
    )
    os.close(descriptor)
    try:
        with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, name in files:
                archive.write(source, name)
        os.replace(temporary, archive_path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _promote_funder_artifacts(staging_root, live_root):
    """Publish a complete funder release only after every file is ready."""
    os.makedirs(live_root, exist_ok=True)
    backups = []
    try:
        for name in ("dashboards", "downloads"):
            live_path = os.path.join(live_root, name)
            staged_path = os.path.join(staging_root, name)
            backup_path = os.path.join(live_root, f".{name}.previous")
            if os.path.exists(backup_path):
                shutil.rmtree(backup_path)
            if os.path.exists(live_path):
                os.replace(live_path, backup_path)
                backups.append((live_path, backup_path))
            os.replace(staged_path, live_path)

        os.replace(
            os.path.join(staging_root, "index.json"),
            os.path.join(live_root, "index.json"),
        )
    except Exception:
        for live_path, backup_path in reversed(backups):
            if os.path.exists(live_path):
                shutil.rmtree(live_path)
            if os.path.exists(backup_path):
                os.replace(backup_path, live_path)
        raise
    finally:
        for _, backup_path in backups:
            if os.path.exists(backup_path):
                shutil.rmtree(backup_path)
        if os.path.exists(staging_root):
            shutil.rmtree(staging_root)


def _write_download(root, slug, funder, studies, ancestry, bubbles, records):
    with tempfile.TemporaryDirectory(prefix="gwas-funder-download-") as temporary:
        temporary = Path(temporary)
        studies_path = temporary / "studies.tsv"
        ancestry_path = temporary / "ancestry.tsv"
        bubbles_path = temporary / "bubble_df.csv"
        funding_path = temporary / "funding.csv"

        studies.to_csv(studies_path, sep="\t", index=False)
        ancestry.to_csv(ancestry_path, sep="\t", index=False)
        bubble_output = bubbles.copy()
        bubble_output["Selected Funder"] = funder
        bubble_output.to_csv(bubbles_path, index=False)

        with open(funding_path, "w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["PUBMEDID", "Funders"])
            for pmid in sorted(records, key=lambda value: int(value)):
                writer.writerow([pmid, "; ".join(records[pmid])])

        _safe_zip_write(
            Path(root) / "downloads" / f"{slug}.zip",
            [
                (studies_path, "studies.tsv"),
                (ancestry_path, "ancestry.tsv"),
                (bubbles_path, "bubble_df.csv"),
                (funding_path, "funding.csv"),
            ],
        )


def _ensure_support_and_synthetic(repository_path, data_path):
    required_support = ["Country_Lookup.csv", "dict_replacer_broad.tsv"]
    with zipfile.ZipFile(os.path.join(repository_path, "data_static.zip")) as bundle:
        for name in required_support:
            destination = os.path.join(data_path, "support", name)
            if not os.path.isfile(destination):
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with bundle.open(f"support/{name}") as source, \
                        open(destination, "wb") as output:
                    output.write(source.read())

    synthetic = os.path.join(
        data_path, "catalog", "synthetic", "Cat_Anc_wBroader.tsv"
    )
    if not os.path.isfile(synthetic):
        ancestry = pd.read_csv(
            os.path.join(data_path, "catalog", "raw", "Cat_Anc.tsv"),
            sep="\t", low_memory=False
        )
        ancestry = ancestry.rename(columns={
            "BROAD ANCESTRAL CATEGORY": "BROAD ANCESTRAL",
            "NUMBER OF INDIVDUALS": "N",
        })
        dictionary = pd.read_csv(
            os.path.join(data_path, "support", "dict_replacer_broad.tsv"),
            sep="\t", dtype=str
        )
        for column in ("BROAD ANCESTRAL", "Broader"):
            dictionary[column] = dictionary[column].astype(str).str.strip()
        conflicts = dictionary.groupby("BROAD ANCESTRAL")["Broader"].nunique()
        conflicts = conflicts[conflicts > 1]
        if not conflicts.empty:
            raise ValueError(
                "The ancestry dictionary contains conflicting exact mappings: "
                + ", ".join(conflicts.index[:10])
            )
        ancestry["BROAD ANCESTRAL"] = (
            ancestry["BROAD ANCESTRAL"].astype(str).str.strip()
        )
        lookup = dictionary.drop_duplicates("BROAD ANCESTRAL").set_index(
            "BROAD ANCESTRAL"
        )["Broader"]
        ancestry["Broader"] = ancestry["BROAD ANCESTRAL"].map(lookup)
        missing = sorted(ancestry.loc[
            ancestry["Broader"].isna(), "BROAD ANCESTRAL"
        ].dropna().unique())
        if missing:
            raise ValueError(
                "The funder build found unmapped ancestry values: "
                + ", ".join(missing[:10])
            )
        ancestry["DATE"] = pd.to_datetime(
            ancestry["DATE"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        ancestry["N"] = pd.to_numeric(
            ancestry["N"], errors="coerce"
        )
        ancestry = ancestry[
            ancestry["DATE"].notna() & ancestry["N"].notna()
        ].sort_values("DATE")
        os.makedirs(os.path.dirname(synthetic), exist_ok=True)
        os.makedirs(os.path.join(data_path, "unmapped"), exist_ok=True)
        ancestry.to_csv(synthetic, sep="\t", index=False)


def _load_sources(data_path):
    studies = pd.read_csv(_study_path(data_path), sep="\t", dtype=str)
    if "PUBMED ID" in studies.columns:
        studies = studies.rename(columns={"PUBMED ID": "PUBMEDID"})
    studies["PUBMEDID"] = studies["PUBMEDID"].map(normalize_pmid)
    studies["ASSOCIATION COUNT"] = pd.to_numeric(
        studies["ASSOCIATION COUNT"], errors="coerce"
    ).fillna(0)

    ancestry = pd.read_csv(
        os.path.join(data_path, "catalog", "synthetic", "Cat_Anc_wBroader.tsv"),
        sep="\t", dtype={"PUBMEDID": str}, low_memory=False
    )
    ancestry["PUBMEDID"] = ancestry["PUBMEDID"].map(normalize_pmid)
    ancestry["N"] = pd.to_numeric(ancestry["N"], errors="coerce").fillna(0)

    mappings = pd.read_csv(
        os.path.join(data_path, "catalog", "raw", "Cat_Map.tsv"),
        sep="\t", dtype=str
    )
    bubbles = pd.read_csv(
        os.path.join(data_path, "toplot", "bubble_df.csv"), low_memory=False
    )
    bubbles = bubbles.loc[:, ~bubbles.columns.str.startswith("Unnamed:")]
    bubbles["PUBMEDID"] = bubbles["PUBMEDID"].map(normalize_pmid)
    bubbles["DATE"] = pd.to_datetime(
        bubbles["DATE"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    countries = pd.read_csv(
        os.path.join(data_path, "support", "Country_Lookup.csv")
    )
    return studies, ancestry, mappings, bubbles, countries


def build_funder_artifacts(
        repository_path, data_path, cache, cleaner_path,
        min_studies=DEFAULT_MIN_STUDIES):
    _ensure_support_and_synthetic(repository_path, data_path)
    cleaner = load_funder_cleaner(cleaner_path)
    by_publication, counts = normalize_funding_records(
        cache, cleaner, min_studies
    )
    studies, ancestry, mappings, bubbles, countries = _load_sources(data_path)

    with open(os.path.join(data_path, "summary", "uniq_broader.txt")) as source:
        all_ancestries = [line.strip() for line in source if line.strip()]
    recorded_ancestries = [
        value for value in all_ancestries if value != "In Part Not Recorded"
    ]
    with open(os.path.join(data_path, "summary", "uniq_parent.txt")) as source:
        parent_terms = [line.strip() for line in source if line.strip()]

    dates = pd.to_datetime(ancestry["DATE"], errors="coerce")
    final_year = int(dates.dt.year.max())
    live_root = os.path.join(data_path, "funders")
    root = os.path.join(data_path, ".funders-build")
    if os.path.exists(root):
        shutil.rmtree(root)
    os.makedirs(os.path.join(root, "dashboards"), exist_ok=True)
    os.makedirs(os.path.join(root, "downloads"), exist_ok=True)

    entries = []
    used_slugs = set()
    normalized_records = {
        pmid: funders for pmid, funders in by_publication.items() if funders
    }
    for funder, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0].casefold())):
        # The legacy dashboard deliberately kept its low-frequency catch-all
        # out of the selector. It is too broad to be a useful funder filter.
        if funder == "Other funders":
            continue
        slug = slugify(funder)
        suffix = 2
        while slug in used_slugs:
            slug = f"{slugify(funder)}-{suffix}"
            suffix += 1
        used_slugs.add(slug)

        pmids = {pmid for pmid, funders in by_publication.items()
                 if funder in funders}
        selected_studies = studies[studies["PUBMEDID"].isin(pmids)].copy()
        selected_ancestry = ancestry[ancestry["PUBMEDID"].isin(pmids)].copy()
        selected_bubbles = bubbles[bubbles["PUBMEDID"].isin(pmids)].copy()
        if selected_studies.empty or selected_ancestry.empty:
            continue

        study_parent_map = build_study_parent_map(selected_studies, mappings)
        merged = study_parent_map.merge(
            selected_ancestry, how="inner", on="STUDY ACCESSION"
        )
        merged = merged[
            merged["Broader"].notna() & merged["parentterm"].notna()
        ].copy()
        merged["Year"] = pd.to_datetime(
            merged["DATE"], errors="coerce"
        ).dt.year
        merged["N"] = pd.to_numeric(merged["N"], errors="coerce").fillna(0)

        report = build_report(
            funder, selected_studies, selected_ancestry, normalized_records
        )
        payload = {
            "version": ARTIFACT_VERSION,
            "funder": {"name": funder, "slug": slug, "studyCount": int(count)},
            "bubbleGraph": build_bubble_payload(selected_bubbles),
            "tsPlot": build_time_series(
                selected_ancestry, all_ancestries, final_year
            ),
            "heatMap": build_heat_map(
                merged, recorded_ancestries, parent_terms, final_year
            ),
            "chloroMap": build_country_map(
                selected_ancestry, countries, final_year
            ),
            "doughnutGraph": build_doughnut(
                merged, recorded_ancestries, parent_terms, final_year
            ),
            "summary": build_summary(selected_ancestry),
            "report": report,
        }
        _atomic_json(
            os.path.join(root, "dashboards", f"{slug}.json"),
            payload, compact=True
        )
        _write_download(
            root, slug, funder, selected_studies, selected_ancestry,
            selected_bubbles, {pmid: normalized_records[pmid]
                               for pmid in pmids if pmid in normalized_records}
        )
        entries.append({
            "name": funder,
            "slug": slug,
            "studyCount": int(count),
            "participantCount": report["participantCount"],
        })
        print(f"Built funder dashboard: {funder} ({count} publications)")

    index = {
        "version": ARTIFACT_VERSION,
        "generatedAt": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "minimumPublicationCount": min_studies,
        "funders": entries,
    }
    _atomic_json(os.path.join(root, "index.json"), index)
    _promote_funder_artifacts(root, live_root)
    return index


def funder_artifact_files(data_path):
    """Return the funder files named by a valid, path-safe index."""
    index_path = os.path.join(data_path, "funders", "index.json")
    index = _load_json(index_path, None)
    if not isinstance(index, dict) or index.get("version") != ARTIFACT_VERSION:
        raise ValueError("The funder index is missing or has an invalid version")
    entries = index.get("funders")
    if not isinstance(entries, list) or not entries:
        raise ValueError("The funder index contains no dashboards")

    slugs = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("The funder index contains an invalid entry")
        slug = entry.get("slug")
        if not isinstance(slug, str) or slugify(slug) != slug:
            raise ValueError(f"Unsafe funder slug in index: {slug!r}")
        if slug in slugs:
            raise ValueError(f"Duplicate funder slug in index: {slug}")
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError(f"Missing funder name for slug: {slug}")
        slugs.append(slug)

    return (
        "funders/pubmed_grants.json",
        *(f"funders/dashboards/{slug}.json" for slug in slugs),
        *(f"funders/downloads/{slug}.zip" for slug in slugs),
        "funders/index.json",
    )


def validate_funder_artifacts(data_path):
    """Validate all generated funder artifacts before release publication."""
    relative_paths = funder_artifact_files(data_path)
    for relative_path in relative_paths:
        path = os.path.join(data_path, relative_path)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise FileNotFoundError(
                f"Missing or empty funder artifact: {path}"
            )

    cache = _load_json(
        os.path.join(data_path, "funders", "pubmed_grants.json"), None
    )
    if not isinstance(cache, dict) or cache.get("version") != CACHE_VERSION \
            or not isinstance(cache.get("records"), dict):
        raise ValueError("The PubMed funding cache is invalid")

    index = _load_json(
        os.path.join(data_path, "funders", "index.json"), None
    )
    for entry in index["funders"]:
        slug = entry["slug"]
        dashboard_path = os.path.join(
            data_path, "funders", "dashboards", f"{slug}.json"
        )
        dashboard = _load_json(dashboard_path, None)
        required = {
            "version", "funder", "bubbleGraph", "tsPlot", "heatMap",
            "chloroMap", "doughnutGraph", "summary", "report",
        }
        if not isinstance(dashboard, dict) or required - set(dashboard):
            raise ValueError(f"Invalid funder dashboard: {slug}")
        if dashboard.get("version") != ARTIFACT_VERSION \
                or dashboard.get("funder", {}).get("slug") != slug \
                or dashboard.get("funder", {}).get("name") != entry["name"]:
            raise ValueError(f"Funder dashboard metadata differs: {slug}")

        archive_path = os.path.join(
            data_path, "funders", "downloads", f"{slug}.zip"
        )
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None \
                    or set(archive.namelist()) != FUNDER_DOWNLOAD_MEMBERS:
                raise ValueError(f"Invalid funder download archive: {slug}")

    return relative_paths


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.getcwd())
    parser.add_argument("--email")
    parser.add_argument("--min-studies", type=int, default=DEFAULT_MIN_STUDIES)
    parser.add_argument("--skip-fetch", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    repository = os.path.abspath(args.repository)
    data_path = os.path.join(repository, "data")
    funder_root = os.path.join(data_path, "funders")
    cache_path = os.path.join(funder_root, "pubmed_grants.json")
    cleaner_path = os.path.join(repository, "funder_data", "funder_cleaner.json")
    if args.skip_fetch:
        cache = _load_json(cache_path, None)
        if not cache:
            raise RuntimeError("No cached PubMed funding data are available")
    else:
        cache = collect_pubmed_grants(
            data_path, cache_path, email=args.email
        )
    index = build_funder_artifacts(
        repository, data_path, cache, cleaner_path,
        min_studies=args.min_studies
    )
    print(f"Generated {len(index['funders'])} funder dashboards")


if __name__ == "__main__":
    main()
