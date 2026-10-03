"""Build isolated funder data without changing the main dashboard artifacts."""

import argparse
import csv
import datetime
import html
import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
import unicodedata
import zipfile
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import requests


LOGGER = logging.getLogger("diversity_logger")


NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
NCBI_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
CACHE_VERSION = 3
ARTIFACT_VERSION = 2
REPORT_SCHEMA_VERSION = 3
DEFAULT_MIN_STUDIES = 50
DEFAULT_BATCH_SIZE = 100
BUBBLE_PAYLOAD_COLUMNS = (
    "ACCESSION",
    "AUTHOR",
    "Broader",
    "COHORT",
    "DATE",
    "DiseaseOrTrait",
    "FUNDER",
    "JOURNAL",
    "N",
    "PUBMEDID",
    "STAGE",
    "parentterm",
)
PUBMED_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429})
PUBMED_MAX_RETRY_DELAY = 120
PUBMED_GRANT_FIELDS = ("agency", "acronym", "country", "grantId")
FUNDER_DOWNLOAD_MEMBERS = {
    "studies.tsv", "ancestry.tsv", "bubble_df.csv", "funding.csv"
}
FUNDER_DIRECTORY = "funders"
FUNDER_CLEANER_FILE = "funder_cleaner.json"
FUNDER_NORMALIZATION_AUDIT_FILE = "normalization-audit.json"
FUNDER_NORMALIZATION_AUDIT_VERSION = 3
NIH_COMPONENT_ALIASES = (
    ("NHLBI", "NHLBI NIH HHS"),
    ("NHGRI", "NHGRI NIH HHS"),
    ("NIDDK", "NIDDK NIH HHS"),
    ("NIEHS", "NIEHS NIH HHS"),
    ("NICHD", "NICHD NIH HHS"),
    ("NCATS", "NCATS NIH HHS"),
    ("NIAMS", "NIAMS NIH HHS"),
    ("NIAAA", "NIAAA NIH HHS"),
    ("NIMHD", "NIMHD NIH HHS"),
    ("NIGMS", "NIGMS NIH HHS"),
    ("NINDS", "NINDS NIH HHS"),
    ("NIAID", "NIAID NIH HHS"),
    ("NIDCR", "NIDCR NIH HHS"),
    ("NIBIB", "NIBIB NIH HHS"),
    ("NINR", "NINR NIH HHS"),
    ("NIMH", "NIMH NIH HHS"),
    ("NIDA", "NIDA NIH HHS"),
    ("NLM", "NLM NIH HHS"),
    ("FIC", "FIC NIH HHS"),
    ("CSR", "CSR NIH HHS"),
    ("OER", "OER NIH HHS"),
    ("NCI", "NCI NIH HHS"),
)

REPORT_REQUIRED_KEYS = frozenset({
    "schemaVersion", "funder", "studyCount", "publicationCount",
    "participantCount", "ancestryRecordCount", "associationCount",
    "traitCount", "journalCount", "cohortCount", "technologyCount",
    "recruitmentCountryCount", "ancestryGroupCount", "firstStudyDate",
    "latestStudyDate", "yearSpan", "medianPublicationYear",
    "recentPublicationCount", "averageStudiesPerPublication",
    "averageParticipantsPerStudy", "medianParticipantsPerStudy",
    "largestStudyParticipantCount", "largestStudyAccession",
    "averageAssociationsPerStudy", "medianAssociationsPerStudy",
    "maximumAssociationsPerStudy", "recordedParticipantCount",
    "unrecordedParticipantCount", "ancestryReportingPercentage",
    "nonEuropeanParticipantCount", "nonEuropeanParticipantPercentage",
    "summaryStatisticsStudyCount", "summaryStatisticsPercentage",
    "studiesWithCohortCount", "cohortReportingPercentage",
    "studiesWithJournalCount", "journalReportingPercentage",
    "studiesWithTechnologyCount", "technologyReportingPercentage",
    "fundedPublicationCount", "fundingCoveragePercentage", "funderCount",
    "grantRecordCount", "stageBreakdown", "ancestryBreakdown",
    "topTraits", "topJournals", "topCohorts", "topTechnologies",
    "topCountries", "topFunders", "annualActivity",
    "ancestryPercentages",
})

REPORT_NUMERIC_KEYS = frozenset({
    "studyCount", "publicationCount", "participantCount",
    "ancestryRecordCount", "associationCount", "traitCount",
    "journalCount", "cohortCount", "technologyCount",
    "recruitmentCountryCount", "ancestryGroupCount", "yearSpan",
    "recentPublicationCount", "averageStudiesPerPublication",
    "averageParticipantsPerStudy", "medianParticipantsPerStudy",
    "largestStudyParticipantCount", "averageAssociationsPerStudy",
    "medianAssociationsPerStudy", "maximumAssociationsPerStudy",
    "recordedParticipantCount", "unrecordedParticipantCount",
    "ancestryReportingPercentage", "nonEuropeanParticipantCount",
    "nonEuropeanParticipantPercentage", "summaryStatisticsStudyCount",
    "summaryStatisticsPercentage", "studiesWithCohortCount",
    "cohortReportingPercentage", "studiesWithJournalCount",
    "journalReportingPercentage", "studiesWithTechnologyCount",
    "technologyReportingPercentage", "fundedPublicationCount",
    "fundingCoveragePercentage", "funderCount", "grantRecordCount",
})

REPORT_ROW_SCHEMAS = {
    "stageBreakdown": frozenset({
        "name", "studyCount", "recordCount", "participantCount",
        "participantPercentage",
    }),
    "ancestryBreakdown": frozenset({
        "name", "participantCount", "participantPercentage", "recordCount",
        "recordPercentage", "studyCount", "discoveryParticipantCount",
        "replicationParticipantCount",
    }),
    "topTraits": frozenset({
        "name", "studies", "publications", "associations",
        "studyPercentage",
    }),
    "topJournals": frozenset({
        "name", "studies", "publications", "associations",
        "studyPercentage",
    }),
    "topCohorts": frozenset({
        "name", "studies", "publications", "associations",
        "studyPercentage",
    }),
    "topTechnologies": frozenset({
        "name", "studies", "publications", "associations",
        "studyPercentage",
    }),
    "topCountries": frozenset({
        "name", "participants", "participantPercentage", "records",
        "studies",
    }),
    "topFunders": frozenset({
        "name", "publications", "publicationPercentage",
    }),
    "annualActivity": frozenset({
        "year", "studyCount", "publicationCount", "associationCount",
        "participantCount",
    }),
}


def funder_cleaner_path(data_path):
    """Return the canonical funder-normalization configuration path."""
    return os.path.join(data_path, FUNDER_DIRECTORY, FUNDER_CLEANER_FILE)


def funder_normalization_audit_path(data_path):
    return os.path.join(
        data_path, FUNDER_DIRECTORY, FUNDER_NORMALIZATION_AUDIT_FILE
    )


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
    # Inspect the actual header before pandas can rename duplicate columns.
    # This runs during collection/validation, never while serving filters.
    with open(_study_path(data_path), encoding="utf-8-sig", newline="") as source:
        header = next(csv.reader(source, delimiter="\t"), [])
    columns = [name for name in header if name.strip() in {
        "PUBMEDID", "PUBMED ID", "PUBMED_ID",
    }]
    if len(columns) != 1:
        raise ValueError("The study Catalog must have exactly one PubMed ID column")
    column = columns[0]
    values = pd.read_csv(
        _study_path(data_path), sep="\t", usecols=[column], dtype=str,
        encoding="utf-8-sig", keep_default_na=False,
    )[column]
    publications = set()
    for row_number, value in enumerate(values, 2):
        if not value.strip():
            continue
        pmid = normalize_pmid(value)
        if not re.fullmatch(r"[1-9][0-9]*", pmid):
            raise ValueError(
                f"The study Catalog has an invalid PubMed ID at row {row_number}: {value!r}"
            )
        publications.add(pmid)
    if not publications:
        raise ValueError("The study Catalog contains no valid PubMed IDs")
    return sorted(publications, key=int)


class PubMedResponseError(ValueError):
    """Raised when PubMed returns parseable but unusable response data."""


class PubMedCollectionError(RuntimeError):
    """Funding lookup failed; this is not evidence of a bad Catalog snapshot."""


def parse_pubmed_grants(xml_content, requested_ids):
    """Parse records actually returned by PubMed.

    A returned article without a GrantList is represented by an empty list.
    A requested PMID absent from the response is deliberately not represented,
    allowing the collector to retry rather than cache an ambiguous empty row.
    """
    requested = set()
    for value in requested_ids:
        pmid = normalize_pmid(value)
        if not re.fullmatch(r"[1-9][0-9]*", pmid):
            raise PubMedResponseError(f"Cannot request an invalid PubMed ID: {value!r}")
        requested.add(pmid)
    # PubMed's normal external DOCTYPE is harmless here (ElementTree does not
    # fetch it), but custom entity declarations are not publication metadata.
    entity_pattern = rb"<!ENTITY\s" if isinstance(xml_content, bytes) else r"<!ENTITY\s"
    if re.search(entity_pattern, xml_content, flags=re.IGNORECASE):
        raise PubMedResponseError("PubMed response contains an unexpected XML entity declaration")
    root = ElementTree.fromstring(xml_content)

    error_nodes = [node for node in root.iter()
                   if node.tag.rsplit("}", 1)[-1].casefold() == "error"]
    errors = [
        re.sub(r"\s+", " ", "".join(node.itertext())).strip()
        for node in error_nodes
    ]
    if error_nodes:
        raise PubMedResponseError(
            "PubMed API returned an error: " + "; ".join(
                message or "unspecified error" for message in errors[:3]
            )
        )
    if root.tag not in ("PubmedArticleSet", "PubmedBookArticleSet"):
        raise PubMedResponseError(f"PubMed response has an unexpected root element: {root.tag}")
    critical_elements = {
        name.casefold(): name for name in (
            "PubmedArticle", "PubmedBookArticle", "MedlineCitation",
            "BookDocument", "PMID", "Article", "Book", "GrantList",
            "Grant", "Agency", "Acronym", "Country", "GrantID",
        )
    }
    for node in root.iter():
        expected_name = critical_elements.get(node.tag.rsplit("}", 1)[-1].casefold())
        if expected_name and node.tag != expected_name:
            raise PubMedResponseError(
                f"PubMed response has an unsupported namespace or spelling for {expected_name}"
            )

    records = {}
    for article in root:
        if article.tag not in ("PubmedArticle", "PubmedBookArticle"):
            raise PubMedResponseError(f"PubMed response has an unexpected record element: {article.tag}")
        is_book = article.tag == "PubmedBookArticle"
        citation_name = "BookDocument" if is_book else "MedlineCitation"
        citations = article.findall(citation_name)
        if len(citations) != 1:
            raise PubMedResponseError(f"PubMed record must contain one {citation_name}")
        citation = citations[0]
        pmid_nodes = citation.findall("PMID")
        body_name = "Book" if is_book else "Article"
        if len(pmid_nodes) != 1 or len(citation.findall(body_name)) != 1:
            raise PubMedResponseError(f"PubMed {citation_name} is missing or duplicates its PMID/{body_name} body")
        pmid_node = pmid_nodes[0]
        pmid = normalize_pmid(pmid_node.text)
        if list(pmid_node) or not re.fullmatch(r"[1-9][0-9]*", pmid):
            raise PubMedResponseError("PubMed record contains an invalid PMID")
        if pmid not in requested:
            raise PubMedResponseError(f"PubMed response returned an unrequested PMID: {pmid}")
        if pmid in records:
            raise PubMedResponseError(f"PubMed response returned a duplicate PMID: {pmid}")
        record = {"grants": []}
        seen = set()
        for grant_list in article.findall(".//GrantList"):
            for grant in grant_list:
                if grant.tag != "Grant":
                    raise PubMedResponseError(f"PubMed GrantList for {pmid} has an unexpected element: {grant.tag}")
                item = {}
                for key, element_name in zip(PUBMED_GRANT_FIELDS, ("Agency", "Acronym", "Country", "GrantID")):
                    elements = grant.findall(element_name)
                    if len(elements) > 1:
                        raise PubMedResponseError(f"PubMed grant for {pmid} duplicates {element_name}")
                    item[key] = "".join(elements[0].itertext()).strip() if elements else ""
                if not any(item.values()):
                    raise PubMedResponseError(f"PubMed grant for {pmid} contains no recognised grant information")
                signature = tuple(item.values())
                if signature not in seen:
                    record["grants"].append(item)
                    seen.add(signature)
        records[pmid] = record
    return records


def is_pubmed_record_unavailable(record):
    """Identify explicit upstream absence, never infer it from empty grants."""
    return isinstance(record, dict) and record.get("retrievalStatus") == "unavailable"


def make_unavailable_pubmed_record(checked_at=None):
    """Record separately verified upstream absence under the Catalog's PMID."""
    record = {
        "grants": [], "retrievalStatus": "unavailable",
        "reason": "pubmed_record_unavailable",
        "checkedAt": checked_at if checked_at is not None else
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if not _valid_pubmed_record(record):
        raise ValueError("Unavailable PubMed evidence needs a timezone-aware checkedAt timestamp")
    return record


def pubmed_unavailable_limit(publication_count):
    """Largest count strictly below 5% of distinct current Catalog PMIDs."""
    return max(0, (publication_count - 1) // 20)


def _valid_pubmed_record(record):
    if not isinstance(record, dict) or not isinstance(record.get("grants"), list):
        return False
    if "retrievalStatus" in record:
        if not is_pubmed_record_unavailable(record) or record["grants"] \
                or record.get("reason") != "pubmed_record_unavailable" \
                or not isinstance(record.get("checkedAt"), str):
            return False
        try:
            checked_at = datetime.datetime.fromisoformat(record["checkedAt"].replace("Z", "+00:00"))
            return checked_at.utcoffset() is not None
        except (ValueError, OverflowError):
            return False
    return all(
            isinstance(grant, dict)
            and all(isinstance(grant.get(key, ""), str) for key in PUBMED_GRANT_FIELDS)
            and any(grant.get(key, "").strip() for key in PUBMED_GRANT_FIELDS)
            for grant in record["grants"]
        )


def _pubmed_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate PubMed cache JSON key: {key!r}")
        result[key] = value
    return result


def _invalid_pubmed_json_constant(value):
    raise ValueError(f"Invalid PubMed cache JSON constant: {value}")


def _load_pubmed_cache_for_collection(path):
    try:
        with open(path, encoding="utf-8") as source:
            raw = json.load(source, object_pairs_hook=_pubmed_json_object,
                            parse_constant=_invalid_pubmed_json_constant)
    except FileNotFoundError:
        return {"version": CACHE_VERSION, "records": {}}, True
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot safely read PubMed funding cache {path}: {error}") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("records"), dict):
        raise ValueError("The PubMed funding cache records are invalid; the existing file was not replaced")
    raw_records = raw["records"]
    version = raw.get("version")
    if type(version) is not int or version not in (1, 2, CACHE_VERSION):
        raise ValueError(f"Unsupported PubMed funding cache version {version!r}; the existing file was not replaced")
    records = {}
    unverified_empty_count = 0
    invalid_records = []
    seen_pmids = set()
    for raw_pmid, record in raw_records.items():
        pmid = normalize_pmid(raw_pmid)
        if not re.fullmatch(r"[1-9][0-9]*", pmid):
            raise ValueError(f"The PubMed funding cache has an invalid PMID key: {raw_pmid!r}")
        if pmid in seen_pmids:
            raise ValueError(f"The PubMed funding cache contains colliding PMID aliases: {pmid}")
        seen_pmids.add(pmid)
        if not _valid_pubmed_record(record) \
                or (version < 3 and "retrievalStatus" in record):
            invalid_records.append(pmid)
            continue
        if version == 1 and not record["grants"]:
            # Version 1 could not distinguish a returned no-grant article from
            # a PMID omitted by a partial response, so revalidate empty rows.
            unverified_empty_count += 1
            continue
        records[pmid] = record

    if version == 1:
        LOGGER.warning(
            "Migrating PubMed funding cache to version %d; revalidating "
            "%d ambiguous empty records",
            CACHE_VERSION,
            unverified_empty_count,
        )
    if invalid_records:
        LOGGER.warning(
            "Revalidating %d invalid PubMed funding cache records (PMIDs: %s)",
            len(invalid_records), ", ".join(invalid_records[:10]),
        )

    cache = {"version": CACHE_VERSION, "records": records}
    changed = version != CACHE_VERSION or records != raw_records
    if version in (2, CACHE_VERSION):
        for key, value in raw.items():
            if key not in ("version", "records"):
                cache[key] = value
    return cache, changed


def _pubmed_cache_metadata(cache, publication_ids):
    records = cache["records"]
    unavailable = sorted({
        pmid for pmid in publication_ids if pmid in records
        and is_pubmed_record_unavailable(records[pmid])
    }, key=int)
    current = [records[pmid] for pmid in publication_ids if pmid in records
               and not is_pubmed_record_unavailable(records[pmid])]
    funded = sum(bool(record["grants"]) for record in current)
    return {
        "publicationCount": len(publication_ids),
        "retrievedPublicationCount": len(current),
        "unavailablePublicationCount": len(unavailable),
        "unavailablePublicationIds": unavailable,
        "fundedPublicationCount": funded,
        "unfundedPublicationCount": len(current) - funded,
        "grantCount": sum(len(record["grants"]) for record in current),
    }


def _set_pubmed_cache_metadata(cache, publication_ids):
    metadata = _pubmed_cache_metadata(cache, publication_ids)
    changed = any(type(cache.get(key)) is not type(value) or cache.get(key) != value
                  for key, value in metadata.items())
    cache.update(metadata)
    return changed


def validate_pubmed_cache(cache, publication_ids):
    """Require returned records or bounded, individually verified absence."""
    if not isinstance(cache, dict) or type(cache.get("version")) is not int \
            or cache.get("version") != CACHE_VERSION:
        raise ValueError("The PubMed funding cache has an invalid version")
    records = cache.get("records")
    if not isinstance(records, dict):
        raise ValueError("The PubMed funding cache records are invalid")
    invalid_keys = [
        str(pmid) for pmid in records
        if not isinstance(pmid, str) or not re.fullmatch(r"[1-9][0-9]*", pmid)
    ]
    if invalid_keys:
        raise ValueError(
            "The PubMed funding cache has invalid PMID keys: "
            + ", ".join(invalid_keys[:10])
        )

    expected = {
        pmid for pmid in map(normalize_pmid, publication_ids) if pmid
    }
    missing = sorted(expected - set(records), key=int)
    if missing:
        raise ValueError(
            "The PubMed funding cache is missing Catalog PMIDs: "
            + ", ".join(missing[:10])
        )
    unexpected = sorted(set(records) - expected, key=int)
    if unexpected:
        raise ValueError(
            "The PubMed funding cache contains obsolete PMIDs: "
            + ", ".join(unexpected[:10])
        )
    invalid = [
        pmid for pmid in expected if not _valid_pubmed_record(records[pmid])
    ]
    if invalid:
        raise ValueError(
            "The PubMed funding cache has invalid records for PMIDs: "
            + ", ".join(sorted(invalid, key=int)[:10])
        )

    metadata = _pubmed_cache_metadata(cache, sorted(expected, key=int))
    mismatched = [
        key for key, value in metadata.items()
        if type(cache.get(key)) is not type(value) or cache.get(key) != value
    ]
    if mismatched:
        raise ValueError(
            "The PubMed funding cache has inconsistent retrieval metadata: "
            + ", ".join(mismatched)
        )
    if not metadata["retrievedPublicationCount"]:
        raise ValueError("The PubMed funding cache has no retrieved publications; refusing an unavailable-only release")
    limit = pubmed_unavailable_limit(len(expected))
    if metadata["unavailablePublicationCount"] > limit:
        raise ValueError(
            "The PubMed funding cache exceeds its unavailable-publication safety limit "
            f"({metadata['unavailablePublicationCount']} > {limit}); fewer than 5% "
            "of distinct Catalog PMIDs may have confirmed unavailable metadata"
        )
    return cache


def _response_excerpt(response, limit=200):
    text = getattr(response, "text", "") or ""
    # EFetch errors put the useful explanation after a long XML declaration
    # and DOCTYPE. Keep that explanation rather than truncating at the header.
    if not re.search(r"<!ENTITY\s", text, re.IGNORECASE):
        try:
            root = ElementTree.fromstring(text)
            errors = ["".join(node.itertext()).strip() for node in root.iter()
                      if node.tag.rsplit("}", 1)[-1].casefold() == "error"]
            if any(errors):
                text = "; ".join(error for error in errors if error)
        except (ElementTree.ParseError, ValueError):
            pass
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _retry_after_seconds(response):
    value = getattr(response, "headers", {}).get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            return max(0.0, (retry_at - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _retryable_pubmed_status(status_code):
    # PubMed can intermittently reject a valid ID batch with HTTP 400; the
    # same request may succeed on retry. Never interpret that as absent IDs.
    return status_code == 400 or status_code in PUBMED_RETRYABLE_STATUS_CODES \
        or 500 <= status_code <= 599


def _pubmed_http_error(response, batch):
    status = int(getattr(response, "status_code", 0))
    detail = _response_excerpt(response)
    message = (
        f"PubMed funding request received HTTP {status} for batch "
        f"beginning {batch[0]}"
    )
    if detail:
        message += f": {detail}"
    return PubMedCollectionError(message)


def _summary_confirms_unavailable(response, pmid):
    """Require a specific, matching upstream answer, not a generic API error."""
    payload = json.loads(response.content, object_pairs_hook=_pubmed_json_object,
                         parse_constant=_invalid_pubmed_json_constant)
    if not isinstance(payload, dict) or payload.get("error") \
            or not isinstance(payload.get("header"), dict) \
            or payload["header"].get("type") != "esummary":
        return False
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("uids") != [pmid]:
        return False
    item = result.get(pmid)
    return isinstance(item, dict) and item.get("uid") == pmid \
        and item.get("error") == "cannot get document summary"


def _fetch_pubmed_records(session, batch, email, max_retries,
                          request_delay=0.36, confirm_unavailable=False):
    """Fetch a batch, or independently resolve one omitted PMID off-line.

    Only a valid empty single-ID EFetch followed by an explicit matching
    ESummary answer can establish unavailable metadata. HTTP errors, malformed
    bodies, wrong IDs and ordinary batch omissions never establish no funding.
    Rejected HTTP-400 batches get bounded retries, then smaller requests;
    persistent single-ID failures still stop collection without inventing data.
    """
    if confirm_unavailable and len(batch) != 1:
        raise ValueError("Unavailable-record confirmation requires exactly one PMID")
    request_data = {"db": "pubmed", "retmode": "xml", "id": ",".join(batch),
                    "tool": "gwas-diversity-monitor"}
    if email:
        request_data["email"] = email
    last_error = None
    for attempt in range(max_retries):
        response = None
        try:
            response = session.post(NCBI_EFETCH_URL, data=request_data, timeout=90)
            status = int(getattr(response, "status_code", 0))
            if status == 200:
                parsed = parse_pubmed_grants(response.content, batch)
                if parsed or not confirm_unavailable:
                    return parsed
                # NCBI rate limits apply across EFetch and ESummary alike.
                if request_delay:
                    time.sleep(request_delay)
                response = session.post(
                    NCBI_ESUMMARY_URL, data=dict(request_data, retmode="json"), timeout=90,
                )
                status = int(getattr(response, "status_code", 0))
                if status == 200:
                    if _summary_confirms_unavailable(response, batch[0]):
                        LOGGER.warning(
                            "PubMed record %s is unavailable in both single-ID EFetch "
                            "and ESummary; retaining its Catalog studies with funding "
                            "metadata explicitly unknown (not confirmed unfunded). "
                            "This ID will be checked again on the next PubMed collection.", batch[0],
                        )
                        return {batch[0]: make_unavailable_pubmed_record()}
                    raise PubMedResponseError(
                        "PubMed response omitted requested PMIDs: " + batch[0]
                        + "; ESummary did not confirm an unavailable record"
                    )
            last_error = _pubmed_http_error(response, batch)
            if not _retryable_pubmed_status(status):
                raise last_error
        except (requests.RequestException, ElementTree.ParseError, ValueError) as error:
            last_error = error

        if attempt + 1 < max_retries:
            retry_after = _retry_after_seconds(response) if response is not None else None
            delay = retry_after if retry_after is not None else min(2 ** attempt, 8)
            delay = min(delay, PUBMED_MAX_RETRY_DELAY)
            LOGGER.warning(
                "PubMed funding request attempt %d/%d for batch beginning %s "
                "failed (%s); retrying in %.1f seconds",
                attempt + 1, max_retries, batch[0], last_error, delay,
            )
            time.sleep(delay)
    if response is not None and response.status_code == 400 \
            and len(batch) > 1 and not confirm_unavailable:
        split = len(batch) // 2
        LOGGER.warning(
            "PubMed rejected a %d-PMID batch beginning %s with HTTP 400 "
            "after %d attempts (%s); retrying as batches of %d and %d. "
            "No PMID has been marked unavailable because of this error.",
            len(batch), batch[0], max_retries, last_error, split, len(batch) - split,
        )
        recovered = {}
        for smaller_batch in (batch[:split], batch[split:]):
            if request_delay:
                time.sleep(request_delay)
            recovered.update(_fetch_pubmed_records(
                session, smaller_batch, email, max_retries, request_delay,
            ))
        return recovered
    raise PubMedCollectionError(
        f"PubMed funding request failed after {max_retries} attempts for batch "
        f"beginning {batch[0]}: {last_error}"
    ) from last_error


def collect_pubmed_grants(
        data_path, output_path, email=None, batch_size=DEFAULT_BATCH_SIZE,
        request_delay=0.36, max_retries=4, session=None):
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("PubMed batch_size must be at least 1")
    if type(max_retries) is not int or max_retries < 1:
        raise ValueError("PubMed max_retries must be at least 1")
    if isinstance(request_delay, bool) or not isinstance(request_delay, (int, float)) \
            or not math.isfinite(request_delay) or request_delay < 0:
        raise ValueError("PubMed request_delay must be finite and nonnegative")

    publication_ids = read_publication_ids(data_path)
    cache, cache_changed = _load_pubmed_cache_for_collection(output_path)
    records = cache["records"]
    active_publications = set(publication_ids)
    obsolete = set(records) - active_publications
    if obsolete:
        for pmid in obsolete:
            records.pop(pmid)
        cache_changed = True
        LOGGER.info(
            "Removed %d obsolete publications from the PubMed funding cache",
            len(obsolete),
        )
    missing = [pmid for pmid in publication_ids
               if pmid not in records or is_pubmed_record_unavailable(records[pmid])]
    session = session or requests.Session()
    confirmed_unavailable = set()
    unavailable_limit = pubmed_unavailable_limit(len(publication_ids))

    for offset in range(0, len(missing), batch_size):
        batch = missing[offset:offset + batch_size]
        parsed = _fetch_pubmed_records(session, batch, email, max_retries, request_delay)
        absent = [pmid for pmid in batch if pmid not in parsed]
        if absent:
            LOGGER.warning("PubMed batch omitted %d PMID(s); checking each missing "
                           "record individually: %s", len(absent), ", ".join(absent[:10]))
        for pmid in absent:
            if request_delay:
                time.sleep(request_delay)
            parsed.update(_fetch_pubmed_records(
                session, [pmid], email, max_retries, request_delay, confirm_unavailable=True,
            ))
            if is_pubmed_record_unavailable(parsed[pmid]):
                confirmed_unavailable.add(pmid)
            # Stop a possible outage promptly, not after probing every omitted
            # ID. Old unavailable entries still awaiting recheck may recover;
            # count only this run's evidence until final cache validation.
            if len(confirmed_unavailable) > unavailable_limit:
                raise PubMedCollectionError(
                    f"Confirmed unavailable PubMed records reached "
                    f"{len(confirmed_unavailable)}/{len(publication_ids)}; "
                    f"publication requires strictly less than 5% "
                    f"(at most {unavailable_limit} IDs). "
                    "The current batch was not saved."
                )
        has_retrieved = any(not is_pubmed_record_unavailable(record) for record in parsed.values()) \
            or any(not is_pubmed_record_unavailable(record)
                   for pmid, record in records.items() if pmid not in parsed)
        if offset + len(batch) >= len(missing) and not has_retrieved:
            raise PubMedCollectionError(
                "No PubMed records were retrieved; refusing to publish an entirely "
                "unavailable funding dataset."
            )
        records.update(parsed)
        _set_pubmed_cache_metadata(cache, publication_ids)
        cache["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _atomic_json(output_path, cache)
        cache_changed = False
        LOGGER.info("Collected PubMed funding data: %d/%d",
                    min(offset + len(batch), len(missing)), len(missing))
        if request_delay and offset + len(batch) < len(missing):
            time.sleep(request_delay)

    if _set_pubmed_cache_metadata(cache, publication_ids) or cache_changed:
        cache["updatedAt"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        _atomic_json(output_path, cache)

    validate_pubmed_cache(cache, publication_ids)
    LOGGER.info(
        "PubMed funding coverage: %d/%d publications retrieved; %d/%d "
        "(%.3f%%) confirmed unavailable. Publication requires strictly "
        "less than 5%% unavailable; unknown funding is not counted as unfunded.",
        cache["retrievedPublicationCount"], len(publication_ids),
        cache["unavailablePublicationCount"], len(publication_ids),
        100.0 * cache["unavailablePublicationCount"] / len(publication_ids),
    )
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
    cleaner = {}
    for key, value in raw.items():
        key = _clean_agency_text(key)
        value = _clean_agency_text(value)
        if not key or not value:
            continue
        cleaner[key] = value
        cleaner.setdefault(key.casefold(), value)
    return cleaner


def canonical_agency(value, cleaner):
    agency = _clean_agency_text(value)
    if not agency:
        return ""

    visited = []
    visited_keys = set()
    while agency:
        identity = agency.casefold()
        if identity in visited_keys:
            return sorted(
                visited, key=lambda item: (len(item), item.casefold(), item)
            )[0]
        target = cleaner.get(agency)
        if target is None:
            target = cleaner.get(identity)
        if target is None:
            break
        visited.append(agency)
        visited_keys.add(identity)
        agency = target
    normalized = agency.casefold()
    is_hhs_nih_path = (
        "department of health" in normalized
        and (
            "| nih |" in normalized
            or "national institutes of health" in normalized
        )
    )
    if is_hhs_nih_path:
        for acronym, canonical in NIH_COMPONENT_ALIASES:
            if re.search(rf"\b{re.escape(acronym)}\b", agency, re.I):
                return canonical
        for phrase, canonical in (
            ("center for scientific review", "CSR NIH HHS"),
            ("office of extramural research", "OER NIH HHS"),
            ("national institute on aging", "NIA NIH HHS"),
            ("national institute of diabetes", "NIDDK NIH HHS"),
        ):
            if phrase in normalized:
                return canonical
        return "NIH (Other)"
    return agency


def build_funder_normalization_audit(cache, cleaner):
    """Describe every source-to-canonical decision without fuzzy merging."""
    source_publications = defaultdict(set)
    canonical_publications = defaultdict(set)
    source_to_canonical = {}
    excluded_publications = defaultdict(set)
    excluded_grants = Counter()
    publication_ids = set()
    publications_with_grants = set()
    unavailable_publications = set()
    publications_with_mapped_agency = set()
    grant_record_count = 0
    grant_records_without_agency = 0
    for pmid, record in cache.get("records", {}).items():
        pmid = normalize_pmid(pmid)
        if not pmid or not _valid_pubmed_record(record):
            continue
        publication_ids.add(pmid)
        if is_pubmed_record_unavailable(record):
            unavailable_publications.add(pmid)
            continue
        grants = record["grants"]
        if grants:
            publications_with_grants.add(pmid)
        for grant in grants:
            grant_record_count += 1
            source = _clean_agency_text(grant.get("agency"))
            if not source:
                grant_records_without_agency += 1
                label = "(missing agency)"
                excluded_publications[label].add(pmid)
                excluded_grants[label] += 1
                continue
            canonical = canonical_agency(source, cleaner)
            if not canonical or canonical == "Unclear":
                excluded_publications[source].add(pmid)
                excluded_grants[source] += 1
                continue
            source_publications[source].add(pmid)
            canonical_publications[canonical].add(pmid)
            source_to_canonical[source] = canonical
            publications_with_mapped_agency.add(pmid)

    grouped_sources = defaultdict(list)
    for source, canonical in source_to_canonical.items():
        grouped_sources[canonical].append(source)

    merged_groups = []
    for canonical, sources in grouped_sources.items():
        aliases = sorted(set(sources), key=str.casefold)
        if len(aliases) > 1 or aliases[0] != canonical:
            merged_groups.append({
                "canonical": canonical,
                "sourceNames": aliases,
                "publicationCount": len(canonical_publications[canonical]),
            })
    merged_groups.sort(
        key=lambda entry: (
            -entry["publicationCount"], entry["canonical"].casefold()
        )
    )

    unaliased = [
        {
            "source": source,
            "publicationCount": len(publications),
        }
        for source, publications in source_publications.items()
        if source_to_canonical[source] == source
    ]
    unaliased.sort(
        key=lambda entry: (
            -entry["publicationCount"], entry["source"].casefold()
        )
    )
    excluded = [
        {
            "source": source,
            "reason": (
                "missing agency in PubMed GrantList"
                if source == "(missing agency)"
                else "excluded by reviewed normalization rule"
            ),
            "publicationCount": len(publications),
            "grantRecordCount": excluded_grants[source],
        }
        for source, publications in excluded_publications.items()
    ]
    excluded.sort(
        key=lambda entry: (
            -entry["publicationCount"], entry["source"].casefold()
        )
    )
    return {
        "version": FUNDER_NORMALIZATION_AUDIT_VERSION,
        "generatedAt": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "method": (
            "Unicode/whitespace normalization, case-insensitive exact "
            "matching, and reviewed aliases only; no fuzzy merging"
        ),
        "sourceNameCount": len(source_publications),
        "canonicalNameCount": len(canonical_publications),
        "publicationCount": len(publication_ids),
        "retrievedPublicationCount": len(publication_ids - unavailable_publications),
        "unavailablePublicationCount": len(unavailable_publications),
        "unavailablePublicationIds": sorted(unavailable_publications, key=int),
        "publicationsWithGrantListCount": len(publications_with_grants),
        "publicationsWithoutGrantListCount": (
            len(publication_ids - publications_with_grants - unavailable_publications)
        ),
        "publicationsWithMappedAgencyCount": len(
            publications_with_mapped_agency
        ),
        "grantRecordCount": grant_record_count,
        "grantRecordsWithoutAgencyCount": grant_records_without_agency,
        "mergedGroups": merged_groups,
        "sourceNamesWithoutExplicitAlias": unaliased,
        "excludedAgencySources": excluded,
    }


def _canonical_funding_names(cache, cleaner):
    raw_by_publication = {}
    variants = defaultdict(Counter)
    for pmid, record in cache.get("records", {}).items():
        names = {
            canonical_agency(grant.get("agency"), cleaner)
            for grant in record.get("grants", [])
        }
        names.discard("")
        names.discard("Unclear")
        raw_by_publication[normalize_pmid(pmid)] = names
        for name in names:
            variants[name.casefold()][name] += 1

    display_names = {
        identity: sorted(
            spellings,
            key=lambda name: (
                -spellings[name], len(name), name.casefold(), name
            ),
        )[0]
        for identity, spellings in variants.items()
    }
    return {
        pmid: {
            display_names[name.casefold()] for name in names
        }
        for pmid, names in raw_by_publication.items()
    }


def funding_names_by_publication(cache, cleaner):
    """Return every canonical funding agency associated with each PMID."""
    return {
        pmid: sorted(names, key=str.casefold)
        for pmid, names in _canonical_funding_names(
            cache, cleaner
        ).items()
    }


def attach_funding_metadata(frame, funding_by_publication):
    """Add display-ready funder names to bubble rows without dropping rows."""
    result = frame.copy()
    result["FUNDER"] = result["PUBMEDID"].map(
        lambda value: " | ".join(
            funding_by_publication.get(normalize_pmid(value), [])
        )
    )
    return result


def write_bubble_funding_metadata(data_path, cache, cleaner):
    """Persist funding metadata for the main dashboard's bubble dataset."""
    path = os.path.join(data_path, "toplot", "bubble_df.csv")
    frame = pd.read_csv(path, low_memory=False)
    frame = frame.loc[:, ~frame.columns.str.startswith("Unnamed:")]
    frame = attach_funding_metadata(
        frame, funding_names_by_publication(cache, cleaner)
    )
    frame.to_csv(path, index=True)


def normalize_funding_records(cache, cleaner, min_studies):
    raw_by_publication = _canonical_funding_names(cache, cleaner)
    counts = Counter()
    for names in raw_by_publication.values():
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


def _encode_bubble_stage(frame):
    frame = frame.copy()
    parsed_dates = pd.to_datetime(
        frame.get("DATE", pd.Series(index=frame.index, dtype=object)),
        errors="coerce",
        utc=True,
    )
    date_milliseconds = [
        None if pd.isna(value) else int(value.timestamp() * 1000)
        for value in parsed_dates
    ]
    columns = [
        column for column in BUBBLE_PAYLOAD_COLUMNS
        if column in frame.columns
    ]
    rows = [
        {column: _json_value(source.get(column)) for column in columns}
        for source in frame.to_dict(orient="records")
    ]
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

    dates = [value for value in date_milliseconds if value is not None]
    numbers = pd.to_numeric(
        frame.get("N", pd.Series(index=frame.index, dtype=float)),
        errors="coerce",
    ).fillna(0).astype(float).tolist()
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
            "includePrecomputed": False,
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


def _normalise_mapping_text(values):
    return values.fillna("").astype(str).str.strip().str.replace(
        r"\s+", " ", regex=True
    ).str.casefold()


def _normalise_mapping_uri(values):
    return values.fillna("").astype(str).str.strip().str.casefold()


def _parent_lookup(mappings, column, normaliser):
    if column not in mappings.columns or "Parent term" not in mappings.columns:
        return pd.Series(dtype=object)
    values = mappings[[column, "Parent term"]].copy()
    values["__key"] = normaliser(values[column])
    values["Parent term"] = values["Parent term"].fillna("").astype(
        str
    ).str.strip()
    values = values[
        values["__key"].ne("") & values["Parent term"].ne("")
    ]
    return values.drop_duplicates("__key").set_index(
        "__key"
    )["Parent term"]


def build_parent_term_lookups(mappings):
    return {
        "disease": _parent_lookup(
            mappings, "Disease trait", _normalise_mapping_text
        ),
        "efo_term": _parent_lookup(
            mappings, "EFO term", _normalise_mapping_text
        ),
        "efo_uri": _parent_lookup(
            mappings, "EFO URI", _normalise_mapping_uri
        ),
    }


def _map_first_trait_uri(values, lookup):
    token_lists = values.fillna("").astype(str).reset_index(
        drop=True
    ).str.split(r"\s*,\s*", regex=True)
    tokens = token_lists.explode()
    matches = _normalise_mapping_uri(tokens).map(lookup).dropna()
    first_matches = matches.groupby(level=0, sort=False).first()
    result = pd.Series(index=range(len(values)), dtype=object)
    result.loc[first_matches.index] = first_matches
    result.index = values.index
    return result


def build_study_parent_map(studies, mappings=None, parent_lookups=None):
    required = {"STUDY ACCESSION", "DISEASE/TRAIT"}
    missing = required - set(studies.columns)
    if missing:
        raise KeyError(f"Study data missing required columns: {sorted(missing)}")

    mapped = studies[["STUDY ACCESSION", "DISEASE/TRAIT"]].copy()
    if "ASSOCIATION COUNT" in studies.columns:
        mapped["ASSOCIATION COUNT"] = studies["ASSOCIATION COUNT"]
    else:
        mapped["ASSOCIATION COUNT"] = 0

    if parent_lookups is None:
        if mappings is None:
            raise ValueError("Mappings or prepared parent lookups are required")
        parent_lookups = build_parent_term_lookups(mappings)
    disease_lookup = parent_lookups["disease"]
    efo_term_lookup = parent_lookups["efo_term"]
    efo_uri_lookup = parent_lookups["efo_uri"]

    disease_keys = _normalise_mapping_text(studies["DISEASE/TRAIT"])
    parent = disease_keys.map(disease_lookup)
    parent = parent.combine_first(disease_keys.map(efo_term_lookup))
    if "MAPPED_TRAIT" in studies.columns:
        mapped_trait_keys = _normalise_mapping_text(
            studies["MAPPED_TRAIT"]
        )
        parent = parent.combine_first(mapped_trait_keys.map(disease_lookup))
        parent = parent.combine_first(mapped_trait_keys.map(efo_term_lookup))
    if "MAPPED_TRAIT_URI" in studies.columns:
        mapped_uri_keys = _normalise_mapping_uri(
            studies["MAPPED_TRAIT_URI"]
        )
        parent = parent.combine_first(mapped_uri_keys.map(efo_uri_lookup))
        parent = parent.combine_first(_map_first_trait_uri(
            studies["MAPPED_TRAIT_URI"], efo_uri_lookup
        ))

    mapped["parentterm"] = parent
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
    source = merged[
        pd.to_numeric(merged["Year"], errors="coerce").between(
            2008, final_year
        )
    ].copy()
    if source.empty:
        return keys

    source["Year"] = pd.to_numeric(
        source["Year"], errors="coerce"
    ).astype(int)
    source["__row_count"] = 1
    all_terms = source.copy()
    all_terms["parentterm"] = "All"
    expanded = pd.concat([source, all_terms], ignore_index=True)

    grouped = expanded.groupby(
        ["Year", "parentterm", "STAGE", "Broader"], sort=False
    ).agg(
        N=("N", "sum"),
        Count=("__row_count", "sum"),
        Associations=("ASSOCIATION COUNT", "sum"),
    ).to_dict(orient="index")
    totals = expanded.groupby(
        ["Year", "parentterm", "STAGE"], sort=False
    ).agg(
        N=("N", "sum"),
        Count=("__row_count", "sum"),
        Associations=("ASSOCIATION COUNT", "sum"),
    ).to_dict(orient="index")

    for year in sorted(source["Year"].unique()):
        for container in keys.values():
            container[str(year)] = {}
        for term in ["All"] + parent_terms:
            for container in keys.values():
                container[str(year)][term] = {}
            initial_total = totals.get(
                (year, term, "initial"), {}
            )
            replication_total = totals.get(
                (year, term, "replication"), {}
            )
            for order, ancestry in enumerate(ancestry_order, 1):
                initial = grouped.get(
                    (year, term, "initial", ancestry), {}
                )
                replication = grouped.get(
                    (year, term, "replication", ancestry), {}
                )
                values = {
                    "doughnut_discovery_studies": _percentage(
                        initial.get("Count", 0),
                        initial_total.get("Count", 0),
                    ),
                    "doughnut_discovery_participants": _percentage(
                        initial.get("N", 0), initial_total.get("N", 0),
                    ),
                    "doughnut_replication_studies": _percentage(
                        replication.get("Count", 0),
                        replication_total.get("Count", 0),
                    ),
                    "doughnut_replication_participants": _percentage(
                        replication.get("N", 0),
                        replication_total.get("N", 0),
                    ),
                    "doughnut_associations": _percentage(
                        initial.get("Associations", 0),
                        initial_total.get("Associations", 0),
                    ),
                }
                for key, value in values.items():
                    keys[key][str(year)][term][str(order)] = {
                        "ancestry": ancestry,
                        "value": value,
                    }
    return keys


COUNTRY_REPLACEMENTS = {
    "US": "United States",
    "USA": "United States",
    "U.S.": "United States",
    "U.S.A.": "United States",
    "UK": "United Kingdom",
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


def _countries(value):
    countries = []
    seen = set()
    for token in re.split(r"[,;|]", str(value or "")):
        token = token.strip()
        if not token or token.casefold() in {
                "nr", "na", "n/a", "not reported", "none"}:
            continue
        country = COUNTRY_REPLACEMENTS.get(token, token)
        if country not in seen:
            countries.append(country)
            seen.add(country)
    return countries


def _first_country(value):
    countries = _countries(value)
    return countries[0] if countries else ""


def _split_technologies(value):
    """Split top-level technology categories, not commas in brackets."""
    tokens = []
    current = []
    depth = 0
    for character in str(value or ""):
        if character == "[":
            depth += 1
        elif character == "]" and depth:
            depth -= 1
        if character == "," and depth == 0:
            tokens.append("".join(current))
            current = []
        else:
            current.append(character)
    tokens.append("".join(current))

    technologies = []
    seen = set()
    for token in tokens:
        technology = re.sub(r"\s*\[[^]]*]\s*$", "", token).strip()
        if not technology or technology.casefold() in {
                "nr", "na", "n/a", "not reported", "none"}:
            continue
        if technology not in seen:
            technologies.append(technology)
            seen.add(technology)
    return technologies


def build_country_map(ancestry, country_lookup, final_year):
    frame = ancestry.copy()
    frame["country"] = frame["COUNTRY OF RECRUITMENT"].map(_first_country)
    frame = frame[frame["country"] != ""]
    frame["Year"] = pd.to_datetime(frame["DATE"], errors="coerce").dt.year
    frame["N"] = pd.to_numeric(frame["N"], errors="coerce").fillna(0)
    if "STAGE" in frame:
        frame["__stage"] = (
            frame["STAGE"].fillna("").astype(str).str.casefold()
            .replace({"discovery": "initial"})
        )
    else:
        frame["__stage"] = "initial"
    populations = country_lookup.set_index("Country")["2017population"].to_dict()

    def stage_map(stage):
        stage_frame = frame[frame["__stage"] == stage]
        result = {}
        for year in range(2008, final_year + 1):
            current = stage_frame[stage_frame["Year"] == year]
            if current.empty:
                continue
            grouped = current.groupby("country")["N"].agg(["sum", "count"])
            total_n = grouped["sum"].sum()
            total_count = grouped["count"].sum()
            rows = {}
            for index, (country, values) in enumerate(grouped.iterrows()):
                rows[str(index)] = {
                    "country": country,
                    "population": _json_value(
                        populations.get(country, 0)
                    ) or 0,
                    "studies": int(values["count"]),
                    "studiesPercentage": _percentage(
                        values["count"], total_count
                    ),
                    "participants": float(values["sum"]),
                    "participantsPercentage": _percentage(
                        values["sum"], total_n
                    ),
                }
            if rows:
                result[str(year)] = rows
        return result

    return {
        "initial": stage_map("initial"),
        "replication": stage_map("replication"),
    }


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


def validate_report(report, require_content=False):
    """Reject incomplete report payloads before missing data become zeros."""
    if not isinstance(report, dict):
        raise ValueError("The report payload is not an object")
    if report.get("schemaVersion") != REPORT_SCHEMA_VERSION:
        raise ValueError(
            "The report schema is stale or unsupported; expected version "
            f"{REPORT_SCHEMA_VERSION}"
        )
    missing = REPORT_REQUIRED_KEYS - set(report)
    if missing:
        raise ValueError(
            "The report payload is missing required fields: "
            + ", ".join(sorted(missing))
        )
    for field in REPORT_NUMERIC_KEYS:
        value = report[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or value < 0:
            raise ValueError(
                f"The report field {field} is not a non-negative number"
            )
    median_year = report["medianPublicationYear"]
    if median_year is not None and (
            isinstance(median_year, bool)
            or not isinstance(median_year, (int, float))
            or not math.isfinite(median_year)):
        raise ValueError("The report median publication year is invalid")

    for field, required_row_keys in REPORT_ROW_SCHEMAS.items():
        rows = report[field]
        if not isinstance(rows, list):
            raise ValueError(f"The report field {field} is not a list")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(
                    f"The report field {field}[{index}] is not an object"
                )
            missing_row_keys = required_row_keys - set(row)
            if missing_row_keys:
                raise ValueError(
                    f"The report field {field}[{index}] is missing: "
                    + ", ".join(sorted(missing_row_keys))
                )

    if not isinstance(report["ancestryPercentages"], dict):
        raise ValueError("The report ancestry percentages are invalid")
    if report["studyCount"] < report["publicationCount"]:
        raise ValueError(
            "A report cannot contain fewer studies than publications"
        )
    if report["recordedParticipantCount"] \
            + report["unrecordedParticipantCount"] \
            != report["participantCount"]:
        raise ValueError(
            "Recorded and unrecorded participants do not match the total"
        )

    if require_content:
        positive_fields = (
            "studyCount", "publicationCount", "participantCount",
            "ancestryRecordCount", "associationCount", "traitCount",
            "journalCount",
        )
        missing_content = [
            field for field in positive_fields if report[field] <= 0
        ]
        nonempty_fields = (
            "stageBreakdown", "ancestryBreakdown", "topTraits",
            "topJournals", "annualActivity",
        )
        missing_content.extend(
            field for field in nonempty_fields if not report[field]
        )
        if report["cohortCount"] > 0 and not report["topCohorts"]:
            missing_content.append("topCohorts")
        if report["technologyCount"] > 0 and not report["topTechnologies"]:
            missing_content.append("topTechnologies")
        if report["recruitmentCountryCount"] > 0 \
                and not report["topCountries"]:
            missing_content.append("topCountries")
        if missing_content:
            raise ValueError(
                "The generated report has empty source-backed content: "
                + ", ".join(missing_content)
            )
    return report


def build_report(funder, studies, ancestry, normalized_records):
    """Build the detailed metrics used by funder and dataset reports."""
    study_columns = [
        "STUDY ACCESSION", "PUBMEDID", "DATE", "ASSOCIATION COUNT",
        "DISEASE/TRAIT", "JOURNAL", "COHORT", "GENOTYPING TECHNOLOGY",
        "FULL SUMMARY STATISTICS",
    ]
    ancestry_columns = [
        "STUDY ACCESSION", "DATE", "N", "STAGE", "Broader",
        "COUNTRY OF RECRUITMENT",
    ]
    missing_study_columns = set(study_columns) - set(studies.columns)
    missing_ancestry_columns = set(ancestry_columns) - set(ancestry.columns)
    if missing_study_columns:
        raise ValueError(
            "Report study data are missing required columns: "
            + ", ".join(sorted(missing_study_columns))
        )
    if missing_ancestry_columns:
        raise ValueError(
            "Report ancestry data are missing required columns: "
            + ", ".join(sorted(missing_ancestry_columns))
        )
    studies = studies.loc[:, study_columns].copy()
    ancestry = ancestry.loc[:, ancestry_columns].copy()
    normalized_records = normalized_records or {}

    def text_column(frame, name):
        if name not in frame:
            return pd.Series("", index=frame.index, dtype=object)
        return frame[name].fillna("").astype(str).str.strip()

    def percentage(numerator, denominator):
        return round(float(numerator) / float(denominator) * 100, 2) \
            if denominator else 0.0

    studies["__accession"] = text_column(studies, "STUDY ACCESSION")
    studies["__pmid"] = text_column(studies, "PUBMEDID").map(normalize_pmid)
    studies["__date"] = pd.to_datetime(
        text_column(studies, "DATE"), errors="coerce"
    )
    association_values = studies["ASSOCIATION COUNT"] \
        if "ASSOCIATION COUNT" in studies \
        else pd.Series(0, index=studies.index, dtype=float)
    studies["__association"] = pd.to_numeric(
        association_values, errors="coerce"
    ).fillna(0)
    studies["__trait"] = text_column(studies, "DISEASE/TRAIT")
    studies["__journal"] = text_column(studies, "JOURNAL")
    studies["__cohort"] = text_column(studies, "COHORT")
    studies["__technology"] = text_column(
        studies, "GENOTYPING TECHNOLOGY"
    )

    participant_values = ancestry["N"] if "N" in ancestry \
        else pd.Series(0, index=ancestry.index, dtype=float)
    ancestry["__N"] = pd.to_numeric(
        participant_values, errors="coerce"
    ).fillna(0)
    ancestry["__stage"] = text_column(ancestry, "STAGE").str.casefold()
    ancestry["__broader"] = text_column(ancestry, "Broader")
    ancestry["__accession"] = text_column(ancestry, "STUDY ACCESSION")
    ancestry["__date"] = pd.to_datetime(
        text_column(ancestry, "DATE"), errors="coerce"
    )

    accessions = studies.loc[studies["__accession"] != "", "__accession"]
    publication_ids = studies.loc[studies["__pmid"] != "", "__pmid"]
    study_count = int(accessions.nunique())
    publication_count = int(publication_ids.nunique())
    participant_count = int(round(ancestry["__N"].sum()))
    ancestry_record_count = int(len(ancestry))
    association_count = int(round(studies["__association"].sum()))
    valid_dates = studies["__date"].dropna()

    per_study_participants = ancestry[
        ancestry["__accession"] != ""
    ].groupby("__accession")["__N"].sum()
    per_study_associations = studies[
        studies["__accession"] != ""
    ].groupby("__accession")["__association"].sum()

    recorded = ancestry[
        ancestry["__broader"].ne("")
        & ancestry["__broader"].ne("In Part Not Recorded")
    ].copy()
    recorded_participants = int(round(recorded["__N"].sum()))
    unrecorded_participants = participant_count - recorded_participants
    non_european_participants = int(round(recorded.loc[
        recorded["__broader"].ne("European"), "__N"
    ].sum()))

    stage_breakdown = []
    for label, stage in (("Discovery", "initial"),
                         ("Replication", "replication")):
        stage_rows = ancestry[ancestry["__stage"] == stage]
        stage_participants = int(round(stage_rows["__N"].sum()))
        stage_breakdown.append({
            "name": label,
            "studyCount": int(stage_rows.loc[
                stage_rows["__accession"] != "", "__accession"
            ].nunique()),
            "recordCount": int(len(stage_rows)),
            "participantCount": stage_participants,
            "participantPercentage": percentage(
                stage_participants, participant_count
            ),
        })

    recorded_total = recorded["__N"].sum()
    recorded_records = len(recorded)
    ancestry_breakdown = []
    preferred_ancestries = list(SUMMARY_KEYS)
    observed_ancestries = sorted(
        set(recorded["__broader"]) - set(preferred_ancestries),
        key=str.casefold
    )
    for name in preferred_ancestries + observed_ancestries:
        rows = recorded[recorded["__broader"] == name]
        if rows.empty and name not in SUMMARY_KEYS:
            continue
        participants = int(round(rows["__N"].sum()))
        discovery_participants = int(round(rows.loc[
            rows["__stage"] == "initial", "__N"
        ].sum()))
        replication_participants = int(round(rows.loc[
            rows["__stage"] == "replication", "__N"
        ].sum()))
        ancestry_breakdown.append({
            "name": name,
            "participantCount": participants,
            "participantPercentage": percentage(
                participants, recorded_total
            ),
            "recordCount": int(len(rows)),
            "recordPercentage": percentage(len(rows), recorded_records),
            "studyCount": int(rows.loc[
                rows["__accession"] != "", "__accession"
            ].nunique()),
            "discoveryParticipantCount": discovery_participants,
            "replicationParticipantCount": replication_participants,
        })

    def grouped_study_metrics(frame, group_column, limit=10):
        frame = frame[frame[group_column] != ""]
        if frame.empty:
            return []
        grouped = frame.groupby(group_column, sort=False).agg(
            studyCount=("__accession", "nunique"),
            publicationCount=("__pmid", "nunique"),
            associationCount=("__association", "sum"),
        ).reset_index().rename(columns={group_column: "name"})
        grouped = grouped.sort_values(
            ["studyCount", "publicationCount", "name"],
            ascending=[False, False, True]
        ).head(limit)
        return [{
            "name": str(row["name"]),
            "studies": int(row["studyCount"]),
            "publications": int(row["publicationCount"]),
            "associations": int(round(row["associationCount"])),
            "studyPercentage": percentage(row["studyCount"], study_count),
        } for _, row in grouped.iterrows()]

    top_traits = grouped_study_metrics(studies, "__trait", 12)
    top_journals = grouped_study_metrics(studies, "__journal", 10)

    cohort_frame = studies[
        ["__accession", "__pmid", "__association", "__cohort"]
    ].copy()
    cohort_frame["name"] = cohort_frame["__cohort"].str.split("|")
    cohort_frame = cohort_frame.explode("name")
    cohort_frame["name"] = cohort_frame["name"].fillna("").str.strip()
    cohort_frame = cohort_frame[cohort_frame["name"] != ""]
    if cohort_frame.empty:
        top_cohorts = []
        cohort_count = 0
    else:
        cohort_count = int(cohort_frame["name"].nunique())
        top_cohorts = grouped_study_metrics(
            cohort_frame.rename(columns={"name": "__cohort_name"}),
            "__cohort_name", 10
        )

    technology_frame = studies[
        ["__accession", "__pmid", "__association", "__technology"]
    ].copy()
    technology_frame["name"] = technology_frame[
        "__technology"
    ].map(_split_technologies)
    technology_frame = technology_frame.explode("name")
    technology_frame["name"] = technology_frame["name"].fillna("").str.strip()
    technology_frame = technology_frame[technology_frame["name"] != ""]
    if technology_frame.empty:
        top_technologies = []
        technology_count = 0
    else:
        technology_count = int(technology_frame["name"].nunique())
        top_technologies = grouped_study_metrics(
            technology_frame.rename(columns={"name": "__technology_name"}),
            "__technology_name", 10
        )

    country_source = ancestry.copy()
    country_source["__country"] = text_column(
        country_source, "COUNTRY OF RECRUITMENT"
    ).map(_countries)
    country_source = country_source.explode("__country")
    country_source["__country"] = country_source[
        "__country"
    ].fillna("").str.strip()
    country_source = country_source[country_source["__country"] != ""]
    if country_source.empty:
        top_countries = []
        country_count = 0
    else:
        country_count = int(country_source["__country"].nunique())
        country_groups = country_source.groupby("__country").agg(
            participantCount=("__N", "sum"),
            recordCount=("__country", "size"),
            studyCount=("__accession", "nunique"),
        ).reset_index().sort_values(
            ["participantCount", "recordCount", "__country"],
            ascending=[False, False, True]
        ).head(12)
        top_countries = [{
            "name": str(row["__country"]),
            "participants": int(round(row["participantCount"])),
            "participantPercentage": percentage(
                row["participantCount"], participant_count
            ),
            "records": int(row["recordCount"]),
            "studies": int(row["studyCount"]),
        } for _, row in country_groups.iterrows()]

    pmids = {pmid for pmid in publication_ids if pmid}
    publication_funders = {
        pmid: normalized_records.get(pmid, []) for pmid in pmids
        if normalized_records.get(pmid, [])
    }
    funder_counts = Counter(
        name for names in publication_funders.values() for name in names
    )
    top_funders = [{
        "name": name,
        "publications": int(count),
        "publicationPercentage": percentage(count, publication_count),
    } for name, count in sorted(
        funder_counts.items(), key=lambda item: (-item[1], item[0].casefold())
    )[:12]]

    study_years = studies.assign(
        __year=studies["__date"].dt.year
    ).dropna(subset=["__year"])
    ancestry_years = ancestry.assign(
        __year=ancestry["__date"].dt.year
    ).dropna(subset=["__year"])
    years = sorted(set(study_years["__year"].astype(int))
                   | set(ancestry_years["__year"].astype(int)))
    annual_activity = []
    for year in years:
        year_studies = study_years[study_years["__year"] == year]
        year_ancestry = ancestry_years[ancestry_years["__year"] == year]
        annual_activity.append({
            "year": int(year),
            "studyCount": int(year_studies["__accession"].nunique()),
            "publicationCount": int(year_studies["__pmid"].nunique()),
            "associationCount": int(round(
                year_studies["__association"].sum()
            )),
            "participantCount": int(round(year_ancestry["__N"].sum())),
        })

    summary_stats_values = text_column(
        studies, "FULL SUMMARY STATISTICS"
    ).str.casefold()
    summary_stats_studies = int(studies.loc[
        summary_stats_values.isin({"yes", "y", "true", "1"}),
        "__accession"
    ].nunique())
    studies_with_cohort = int(studies.loc[
        studies["__cohort"] != "", "__accession"
    ].nunique())
    studies_with_journal = int(studies.loc[
        studies["__journal"] != "", "__accession"
    ].nunique())
    studies_with_technology = int(studies.loc[
        studies["__technology"] != "", "__accession"
    ].nunique())

    first_date = valid_dates.min() if not valid_dates.empty else None
    latest_date = valid_dates.max() if not valid_dates.empty else None
    recent_publications = 0
    median_publication_year = None
    if latest_date is not None:
        recent = studies[
            studies["__date"] >= pd.Timestamp(latest_date.year - 4, 1, 1)
        ]
        recent_publications = int(recent["__pmid"].nunique())
        publication_dates = studies[
            studies["__pmid"] != ""
        ].drop_duplicates("__pmid")["__date"].dropna()
        if not publication_dates.empty:
            median_publication_year = int(
                round(float(publication_dates.dt.year.median()))
            )

    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "funder": funder,
        "studyCount": study_count,
        "publicationCount": publication_count,
        "participantCount": participant_count,
        "ancestryRecordCount": ancestry_record_count,
        "associationCount": association_count,
        "traitCount": int(studies.loc[
            studies["__trait"] != "", "__trait"
        ].nunique()),
        "journalCount": int(studies.loc[
            studies["__journal"] != "", "__journal"
        ].nunique()),
        "cohortCount": cohort_count,
        "technologyCount": technology_count,
        "recruitmentCountryCount": country_count,
        "ancestryGroupCount": int(recorded["__broader"].nunique()),
        "firstStudyDate": first_date.strftime("%Y-%m-%d")
        if first_date is not None else None,
        "latestStudyDate": latest_date.strftime("%Y-%m-%d")
        if latest_date is not None else None,
        "yearSpan": int(latest_date.year - first_date.year + 1)
        if first_date is not None and latest_date is not None else 0,
        "medianPublicationYear": median_publication_year,
        "recentPublicationCount": recent_publications,
        "averageStudiesPerPublication": round(
            float(study_count) / publication_count, 2
        ) if publication_count else 0.0,
        "averageParticipantsPerStudy": round(
            float(per_study_participants.mean()), 2
        ) if not per_study_participants.empty else 0.0,
        "medianParticipantsPerStudy": int(round(
            per_study_participants.median()
        )) if not per_study_participants.empty else 0,
        "largestStudyParticipantCount": int(round(
            per_study_participants.max()
        )) if not per_study_participants.empty else 0,
        "largestStudyAccession": str(per_study_participants.idxmax())
        if not per_study_participants.empty else None,
        "averageAssociationsPerStudy": round(
            float(per_study_associations.mean()), 2
        ) if not per_study_associations.empty else 0.0,
        "medianAssociationsPerStudy": round(
            float(per_study_associations.median()), 2
        ) if not per_study_associations.empty else 0.0,
        "maximumAssociationsPerStudy": int(round(
            per_study_associations.max()
        )) if not per_study_associations.empty else 0,
        "recordedParticipantCount": recorded_participants,
        "unrecordedParticipantCount": unrecorded_participants,
        "ancestryReportingPercentage": percentage(
            recorded_participants, participant_count
        ),
        "nonEuropeanParticipantCount": non_european_participants,
        "nonEuropeanParticipantPercentage": percentage(
            non_european_participants, recorded_participants
        ),
        "summaryStatisticsStudyCount": summary_stats_studies,
        "summaryStatisticsPercentage": percentage(
            summary_stats_studies, study_count
        ),
        "studiesWithCohortCount": studies_with_cohort,
        "cohortReportingPercentage": percentage(
            studies_with_cohort, study_count
        ),
        "studiesWithJournalCount": studies_with_journal,
        "journalReportingPercentage": percentage(
            studies_with_journal, study_count
        ),
        "studiesWithTechnologyCount": studies_with_technology,
        "technologyReportingPercentage": percentage(
            studies_with_technology, study_count
        ),
        "fundedPublicationCount": int(len(publication_funders)),
        "fundingCoveragePercentage": percentage(
            len(publication_funders), publication_count
        ),
        "funderCount": int(len(funder_counts)),
        "grantRecordCount": int(sum(funder_counts.values())),
        "stageBreakdown": stage_breakdown,
        "ancestryBreakdown": ancestry_breakdown,
        "topTraits": top_traits,
        "topJournals": top_journals,
        "topCohorts": top_cohorts,
        "topTechnologies": top_technologies,
        "topCountries": top_countries,
        "topFunders": top_funders,
        "annualActivity": annual_activity,
        "ancestryPercentages": build_summary(ancestry)[
            "overallParticipants"
        ],
    }
    return validate_report(report)


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
            os.path.join(staging_root, FUNDER_NORMALIZATION_AUDIT_FILE),
            os.path.join(live_root, FUNDER_NORMALIZATION_AUDIT_FILE),
        )
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
    parent_term_lookups = build_parent_term_lookups(mappings)
    bubbles = attach_funding_metadata(
        bubbles, funding_names_by_publication(cache, cleaner)
    )

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
    _atomic_json(
        os.path.join(root, FUNDER_NORMALIZATION_AUDIT_FILE),
        build_funder_normalization_audit(cache, cleaner),
    )

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

        study_parent_map = build_study_parent_map(
            selected_studies, parent_lookups=parent_term_lookups
        )
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
        validate_report(report, require_content=True)
        payload = {
            "version": ARTIFACT_VERSION,
            "funder": {
                "name": funder,
                "slug": slug,
                "studyCount": report["studyCount"],
                "publicationCount": report["publicationCount"],
            },
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
            "studyCount": report["studyCount"],
            "publicationCount": report["publicationCount"],
            "participantCount": report["participantCount"],
        })
        LOGGER.info(
            "Built funder dashboard: %s (%d publications)", funder, count
        )

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
        "funders/funder_cleaner.json",
        f"funders/{FUNDER_NORMALIZATION_AUDIT_FILE}",
        "funders/pubmed_grants.json",
        *(f"funders/dashboards/{slug}.json" for slug in slugs),
        *(f"funders/downloads/{slug}.zip" for slug in slugs),
        "funders/index.json",
    )


def validate_funder_artifacts(data_path):
    """Validate all generated funder artifacts before release publication."""
    # Keep deep decoding checks out of the runtime report/filter helpers.
    from generated_data_validation import read_generated_json

    relative_paths = funder_artifact_files(data_path)
    for relative_path in relative_paths:
        path = os.path.join(data_path, relative_path)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise FileNotFoundError(
                f"Missing or empty funder artifact: {path}"
            )

    cleaner = read_generated_json(funder_cleaner_path(data_path))
    if not isinstance(cleaner, dict) or not cleaner or not all(
            isinstance(alias, str) and isinstance(canonical, str)
            for alias, canonical in cleaner.items()):
        raise ValueError("The funder normalization configuration is invalid")

    audit = read_generated_json(funder_normalization_audit_path(data_path))
    if not isinstance(audit, dict) \
            or audit.get("version") != FUNDER_NORMALIZATION_AUDIT_VERSION \
            or not isinstance(audit.get("generatedAt"), str) \
            or not audit["generatedAt"].strip() \
            or not isinstance(audit.get("mergedGroups"), list) \
            or not isinstance(
                audit.get("sourceNamesWithoutExplicitAlias"), list
            ) \
            or not isinstance(audit.get("excludedAgencySources"), list) \
            or any(
                not isinstance(audit.get(key), int)
                for key in (
                    "publicationCount",
                    "retrievedPublicationCount",
                    "unavailablePublicationCount",
                    "publicationsWithGrantListCount",
                    "publicationsWithoutGrantListCount",
                    "publicationsWithMappedAgencyCount",
                    "grantRecordCount",
                    "grantRecordsWithoutAgencyCount",
                )
            ):
        raise ValueError("The funder normalization audit is invalid")

    cache = read_generated_json(
        os.path.join(data_path, "funders", "pubmed_grants.json")
    )
    publication_ids = read_publication_ids(data_path)
    validate_pubmed_cache(cache, publication_ids)
    expected_audit = build_funder_normalization_audit(cache, cleaner)
    comparable_audit = {
        key: value for key, value in audit.items() if key != "generatedAt"
    }
    comparable_expected = {
        key: value for key, value in expected_audit.items()
        if key != "generatedAt"
    }
    if comparable_audit != comparable_expected:
        raise ValueError(
            "The funder normalization audit differs from the PubMed cache"
        )

    index = read_generated_json(
        os.path.join(data_path, "funders", "index.json")
    )
    for entry in index["funders"]:
        slug = entry["slug"]
        dashboard_path = os.path.join(
            data_path, "funders", "dashboards", f"{slug}.json"
        )
        dashboard = read_generated_json(dashboard_path)
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
        try:
            report = validate_report(
                dashboard.get("report"), require_content=True
            )
        except ValueError as error:
            raise ValueError(
                f"Invalid funder report for {slug}: {error}"
            ) from error
        count_fields = ("studyCount", "publicationCount")
        if any(
            entry.get(field) != report[field]
            or dashboard["funder"].get(field) != report[field]
            for field in count_fields
        ):
            raise ValueError(
                f"Funder study/publication counts differ for {slug}"
            )

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


def _configure_standalone_logging(repository):
    """Write standalone pipeline progress to the generator's log file."""
    log_directory = os.path.join(repository, "app", "logging")
    os.makedirs(log_directory, exist_ok=True)
    log_path = os.path.abspath(
        os.path.join(log_directory, "diversity_logger.log")
    )

    LOGGER.setLevel(logging.DEBUG)
    LOGGER.propagate = False
    for handler in LOGGER.handlers[:]:
        LOGGER.removeHandler(handler)
        handler.close()

    handler = logging.FileHandler(log_path)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    LOGGER.addHandler(handler)


def main():
    args = parse_args()
    repository = os.path.abspath(args.repository)
    _configure_standalone_logging(repository)
    data_path = os.path.join(repository, "data")
    funder_root = os.path.join(data_path, "funders")
    cache_path = os.path.join(funder_root, "pubmed_grants.json")
    cleaner_path = funder_cleaner_path(data_path)
    if args.skip_fetch:
        cache = _load_json(cache_path, None)
        if not cache:
            raise RuntimeError("No cached PubMed funding data are available")
        validate_pubmed_cache(cache, read_publication_ids(data_path))
    else:
        cache = collect_pubmed_grants(
            data_path, cache_path, email=args.email
        )
    index = build_funder_artifacts(
        repository, data_path, cache, cleaner_path,
        min_studies=args.min_studies
    )
    LOGGER.info("Generated %d funder dashboards", len(index["funders"]))


if __name__ == "__main__":
    main()
