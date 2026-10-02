"""On-demand comparisons with explicit units and shared publication windows."""

import os
import re

import pandas as pd


MAX_COMPARISON_BYTES = 16384
MAX_ENTITIES_PER_FACET = 20
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")


def validate_comparison(value):
    """Reject ambiguous or oversized selections before any data are loaded."""
    if not isinstance(value, dict) or set(value) - {
            "left", "right", "stage", "metric", "fromYear", "toYear"}:
        raise ValueError("Provide two selections and shared comparison settings.")
    result = {}
    for side in ("left", "right"):
        selection = value.get(side)
        if not isinstance(selection, dict) or set(selection) - {"funders", "cohorts"}:
            raise ValueError("Each selection must contain funders and cohorts.")
        result[side] = {}
        for facet in ("funders", "cohorts"):
            identifiers = selection.get(facet, [])
            if not isinstance(identifiers, list) or len(identifiers) > MAX_ENTITIES_PER_FACET:
                raise ValueError("Choose at most 20 funders and 20 cohorts per side.")
            if any(not isinstance(item, str) or not _IDENTIFIER.fullmatch(item)
                   for item in identifiers):
                raise ValueError("A selected funder or cohort identifier is invalid.")
            result[side][facet] = sorted(set(identifiers))
    result["stage"] = value.get("stage", "initial")
    result["metric"] = value.get("metric", "participants")
    if result["stage"] not in ("initial", "replication"):
        raise ValueError("Choose discovery or replication.")
    if result["metric"] not in ("participants", "studies"):
        raise ValueError("Choose participant instances or distinct studies.")
    for field in ("fromYear", "toYear"):
        year = value.get(field)
        if year is not None and (type(year) is not int or not 1900 <= year <= 2100):
            raise ValueError("Publication years must be between 1900 and 2100.")
        result[field] = year
    if result["fromYear"] is not None and result["toYear"] is not None \
            and result["fromYear"] > result["toYear"]:
        raise ValueError("The first publication year must not follow the last.")
    return result


def _percentage(count, total):
    return round(float(count) * 100 / float(total), 2) if total else None


def _indexes(store):
    """Reuse entity indexes, without loading associations or chart payloads."""
    with store._lock:
        store._ensure_facet_indexes()
        store._ensure_funder_index()
        cached = getattr(store, "_comparison_indexes", None)
        if cached is not None:
            return cached
        studies = store._facet_studies.copy()
        studies["STUDY ACCESSION"] = studies["STUDY ACCESSION"].astype(str)
        studies["__year"] = pd.to_datetime(studies["DATE"], errors="coerce").dt.year
        if store._sources is not None:
            ancestry = store._sources[1][["STUDY ACCESSION", "STAGE", "Broader", "N"]].copy()
        else:
            ancestry = pd.read_csv(
                os.path.join(store.data_path, "catalog", "synthetic", "Cat_Anc_wBroader.tsv"),
                sep="\t", dtype={"STUDY ACCESSION": str},
                usecols=["STUDY ACCESSION", "STAGE", "Broader", "N"],
            )
        ancestry["STUDY ACCESSION"] = ancestry["STUDY ACCESSION"].astype(str)
        ancestry["STAGE"] = ancestry["STAGE"].fillna("").astype(str).str.casefold()
        ancestry["Broader"] = ancestry["Broader"].fillna("").astype(str).str.strip()
        ancestry["N"] = pd.to_numeric(ancestry["N"], errors="coerce").fillna(0).clip(lower=0)
        cached = (studies.set_index("STUDY ACCESSION", drop=False),
                  ancestry.set_index("STUDY ACCESSION", drop=False))
        store._comparison_indexes = cached
        return cached


def _rows(indexed, accessions):
    available = indexed.index.unique().intersection(list(accessions), sort=False)
    return indexed.loc[available].reset_index(drop=True)


def _side(store, indexed, selection, settings):
    accessions = store._filtered_accessions(
        selection["cohorts"], selection["funders"], settings["stage"]
    )
    studies = _rows(indexed[0], accessions)
    if settings["fromYear"] is not None:
        studies = studies[studies["__year"] >= settings["fromYear"]]
    if settings["toYear"] is not None:
        studies = studies[studies["__year"] <= settings["toYear"]]
    accessions = frozenset(studies["STUDY ACCESSION"])
    publications = frozenset(
        pmid for accession in accessions
        for pmid in store._accession_pmids.get(accession, ()) if pmid
    )
    ancestry = _rows(indexed[1], accessions)
    ancestry = ancestry[ancestry["STAGE"] == settings["stage"]]
    recorded = ancestry[
        ancestry["Broader"].ne("")
        & ancestry["Broader"].str.casefold().ne("in part not recorded")
    ]
    participant_count = int(round(ancestry["N"].sum()))
    recorded_participants = int(round(recorded["N"].sum()))
    recorded_studies = int(recorded["STUDY ACCESSION"].nunique())
    if settings["metric"] == "participants":
        counts = recorded.groupby("Broader")["N"].sum()
        denominator = recorded_participants
    else:
        counts = recorded.groupby("Broader")["STUDY ACCESSION"].nunique()
        denominator = recorded_studies
    cohort_count = int(studies.loc[
        studies["COHORT"].fillna("").astype(str).str.strip().ne(""),
        "STUDY ACCESSION",
    ].nunique())
    funded_count = sum(bool((store._funding_by_publication or {}).get(pmid)) for pmid in publications)
    labels = []
    if selection["funders"]:
        labels.append(", ".join(store._funder_entries[slug]["name"] for slug in selection["funders"]))
    if selection["cohorts"]:
        labels.append(", ".join(store.cohort(identifier)["name"] for identifier in selection["cohorts"]))
    return {
        "label": " / ".join(labels) or "All published GWAS",
        "selection": selection,
        "studyCount": len(accessions),
        "publicationCount": len(publications),
        "participantCount": participant_count,
        "recordedParticipantCount": recorded_participants,
        "unrecordedParticipantCount": participant_count - recorded_participants,
        "recordedStudyCount": recorded_studies,
        "ancestryReportingPercentage": _percentage(recorded_participants, participant_count),
        "recordedStudyPercentage": _percentage(recorded_studies, len(accessions)),
        "cohortReportingPercentage": _percentage(cohort_count, len(accessions)),
        "fundingCoveragePercentage": _percentage(funded_count, len(publications)),
        "denominator": denominator,
        "ancestries": [{"name": name, "count": int(round(count)),
                         "percentage": _percentage(count, denominator)}
                        for name, count in counts.items()],
        "empty": not accessions,
    }, accessions, publications


def build_comparison(store, value):
    settings = validate_comparison(value)
    indexed = _indexes(store)
    left, left_studies, left_publications = _side(store, indexed, settings["left"], settings)
    right, right_studies, right_publications = _side(store, indexed, settings["right"], settings)
    names = sorted({row["name"] for side in (left, right) for row in side["ancestries"]}, key=str.casefold)
    for side in (left, right):
        rows = {row["name"]: row for row in side["ancestries"]}
        side["ancestries"] = [rows.get(name, {
            "name": name, "count": 0,
            "percentage": 0.0 if side["denominator"] else None,
        }) for name in names]
    studies_metric = settings["metric"] == "studies"
    return {
        "settings": settings,
        "left": left,
        "right": right,
        "overlap": {
            "studyCount": len(left_studies & right_studies),
            "publicationCount": len(left_publications & right_publications),
            "uniqueParticipantCount": None,
        },
        "methodology": {
            "unit": "Distinct study accessions" if studies_metric else "Participant instances",
            "denominator": (
                "Distinct studies with recorded ancestry in each selection"
                if studies_metric else "Participant instances with recorded ancestry in each selection"
            ),
            "note": (
                "Each ancestry share uses distinct study accessions. A study can report several ancestries, "
                "so shares can sum to more than 100%. Dashboard ancestry charts use ancestry-record counts; "
                "their study proportions can differ."
                if studies_metric else
                "Participant instances are not deduplicated people. Ancestry proportions exclude records "
                "whose ancestry is missing or In Part Not Recorded."
            ),
            "dateBasis": "Inclusive publication years from the GWAS Catalog study record; "
                         "undated studies are excluded when a year limit is applied.",
        },
    }
