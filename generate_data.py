# generate data: python script that does the daily GWAS data collection

import pandas as pd
import traceback
import smtplib
import socket
import json
import numpy as np
import logging
import datetime
import requests
import requests_ftp
import os
import csv
import contextlib
import fcntl
import hashlib
import shutil
import sys
import warnings
import zipfile
import math
import re
import tempfile
from email.message import EmailMessage
from app.DataLoader import (
    DataLoader,
    RUNTIME_DATA_FILES,
    TOPLOT_RUNTIME_FILES,
    published_data_lock,
)
import funder_pipeline

warnings.filterwarnings("ignore")


GENERATION_STATE_VERSION = 3
GENERATION_STATE_FILE = '.generation_complete.json'
GENERATION_CONTROL_DIRECTORY = '.generate_data'
GENERATION_WORKSPACE_DIRECTORY = 'workspace'
GENERATION_RAW_STATE_FILE = 'raw-inputs-complete.json'
GENERATION_STAGED_STATE_FILE = 'generation-complete.json'
GENERATION_PUBLICATION_FILE = 'publication-in-progress.json'
GENERATION_LOCK_FILE = 'generate_data.lock'
GENERATION_FALLBACK_DIRECTORY = 'previous-release'
GENERATION_RAW_FAILURE_LIMIT = 2
FAILURE_NOTIFICATION_STATE_FILE = 'failure-email-notification.json'
FAILURE_EMAIL_RECIPIENT = 'contact@gwasdiversitymonitor.com'
FAILURE_EMAIL_SENDER = 'alerts@mail.gwasdiversitymonitor.com'
FAILURE_EMAIL_COOLDOWN_SECONDS = 6 * 60 * 60
FAILURE_EMAIL_PRODUCTION_DOMAIN = 'gwasdiversitymonitor.com'
FAILURE_EMAIL_LOCAL_RELAY = '127.0.0.1'
FAILURE_EMAIL_LOCAL_RELAY_PORT = 25

RAW_INPUT_FILES = (
    'catalog/raw/Cat_Anc.tsv',
    'catalog/raw/Cat_Full.tsv',
    'catalog/raw/Cat_Map.tsv',
    'catalog/raw/Cat_Stud.tsv',
)

STATIC_DATA_FILES = (
    'summary/uniq_broader.txt',
    'support/Country_Lookup.csv',
    'support/dict_replacer_broad.tsv',
)

SYNTHETIC_OUTPUT_FILES = (
    'catalog/synthetic/Cat_Anc_wBroader.tsv',
    'catalog/synthetic/Cat_Anc_wBroader_withParents.tsv',
    'catalog/synthetic/Disease_to_Parent_Mappings.tsv',
    'catalog/synthetic/GWAScatalogue_CleanedCountry.tsv',
    'catalog/synthetic/ancestry_CoR.csv',
)

SUMMARY_OUTPUT_FILES = (
    'summary/summary.json',
    'summary/uniq_dis_trait.txt',
    'summary/uniq_parent.txt',
)

UNMAPPED_OUTPUT_FILES = (
    'unmapped/unmapped_broader.txt',
    'unmapped/unmapped_diseases.txt',
)

TOPLOT_OUTPUT_FILES = TOPLOT_RUNTIME_FILES

TOPLOT_JSON_FILES = tuple(
    file_name for file_name in TOPLOT_OUTPUT_FILES
    if file_name.endswith('.json')
)

DOWNLOAD_OUTPUT_FILES = (
    'todownload/gwasdiversitymonitor_download.zip',
    'todownload/heatmap.zip',
    'todownload/timeseries.zip',
)

PUBLISHED_DATA_FILES = (
    RAW_INPUT_FILES
    + STATIC_DATA_FILES
    + SYNTHETIC_OUTPUT_FILES
    + SUMMARY_OUTPUT_FILES
    + UNMAPPED_OUTPUT_FILES
    + tuple(f'toplot/{file_name}' for file_name in TOPLOT_OUTPUT_FILES)
    + DOWNLOAD_OUTPUT_FILES
)

RAW_REQUIRED_COLUMNS = {
    'catalog/raw/Cat_Anc.tsv': {
        'STUDY ACCESSION', 'DATE', 'STAGE', 'NUMBER OF INDIVDUALS',
        'BROAD ANCESTRAL CATEGORY', 'COUNTRY OF RECRUITMENT',
    },
    'catalog/raw/Cat_Full.tsv': {'P-VALUE'},
    'catalog/raw/Cat_Map.tsv': {'Disease trait', 'Parent term'},
    'catalog/raw/Cat_Stud.tsv': {
        'STUDY ACCESSION', 'DATE', 'ASSOCIATION COUNT', 'DISEASE/TRAIT',
        'JOURNAL', 'COHORT', 'GENOTYPING TECHNOLOGY',
        'FULL SUMMARY STATISTICS',
    },
}

RAW_REQUIRED_COLUMN_ALTERNATIVES = {
    'catalog/raw/Cat_Anc.tsv': (
        {'PUBMEDID', 'PUBMED ID', 'PUBMED_ID'},
    ),
    'catalog/raw/Cat_Stud.tsv': (
        {'PUBMEDID', 'PUBMED ID', 'PUBMED_ID'},
    ),
}


def read_tsv_with_aliases(path, required, optional=None, logger=None):
    """
    Read a TSV where headers may have changed (e.g., 'PUBMEDID' -> 'PUBMED ID').
    Returns a DataFrame with canonical column names as given in `required`/`optional`.
    Only those columns are read.
    """
    optional = optional or []
    # Canonical -> acceptable variants
    ALIASES = {
        'PUBMEDID': {'PUBMEDID', 'PUBMED ID', 'PUBMED_ID'},
        'STUDY ACCESSION': {'STUDY ACCESSION', 'STUDY_ACCESSION', 'STUDY ACCESSSION'},
        'FIRST AUTHOR': {'FIRST AUTHOR', 'FIRST_AUTHOR', 'FIRST AUTHOR(S)'},
        'DISEASE/TRAIT': {'DISEASE/TRAIT', 'DISEASE / TRAIT', 'DISEASE_TRAIT'},
        'MAPPED_TRAIT': {'MAPPED_TRAIT', 'MAPPED TRAIT'},
        'ASSOCIATION COUNT': {'ASSOCIATION COUNT', 'ASSOCIATION_COUNT'},
        'JOURNAL': {'JOURNAL'},
        'DATE': {'DATE'},
    }

    def norm(s: str) -> str:
        return s.replace('\ufeff', '').strip().replace('_', ' ').casefold()

    # 1) sniff the header
    sniff = pd.read_csv(path, sep='\t', nrows=0)
    raw_cols = [c.replace('\ufeff','').strip() for c in sniff.columns]

    # 2) find the actual column name for each canonical
    found = {}  # canonical -> actual
    for canon in required + optional:
        variants = {canon} | ALIASES.get(canon, set())
        variants_norm = {norm(v) for v in variants}
        for c in raw_cols:
            if norm(c) in variants_norm:
                found[canon] = c
                break

    missing = [c for c in required if c not in found]
    if missing:
        if logger:
            logger.debug(f"Header sniff for {path}: {raw_cols}")
        raise KeyError(f"{path}: missing required columns (after alias matching): {missing}")

    # 3) read only what we found
    usecols_actual = [found[c] for c in (required + [c for c in optional if c in found])]
    df = pd.read_csv(path, sep='\t', usecols=usecols_actual, dtype=str, low_memory=False)

    # 4) rename to canonical
    rename_map = {v: k for k, v in found.items()}
    df = df.rename(columns=rename_map)
    return df


def json_converter(data_path):
    """Convert the .csvs to .jsons to bypass dataLoader."""
    dl = DataLoader(data_path)
    plot_path = os.path.join(data_path, 'toplot')
    os.makedirs(plot_path, exist_ok=True)

    def broader_class(value):
        return str(value or "").replace(" ", "-").replace("/", "-").replace(" ", "-").replace(" ", "-").lower()

    def parentterm_class(value):
        return str(value or "").replace(", ", ",").replace(" ", "-").replace(",", " ").lower()

    def disease_clean(value):
        return str(value or "").replace(">", "more than").replace("<", "less than")

    def trait_clean(value):
        return str(value or "").replace(" ", "-").replace(">", "more than").replace("<", "less than").replace("(", "").replace(")", "").lower()

    def date_ms(value):
        try:
            return int(pd.to_datetime(value).timestamp() * 1000)
        except Exception:
            return None

    def rows_from_stage(stage_obj):
        if isinstance(stage_obj, list):
            return stage_obj
        return [stage_obj[key] for key in stage_obj.keys()]

    def enrich_bubble_row(row):
        enriched = dict(row)

        try:
            nnum = float(enriched.get("N", 0))
        except Exception:
            nnum = 0

        enriched["__Nnum"] = nnum
        enriched["__dateMS"] = date_ms(enriched.get("DATE"))
        enriched["__class"] = broader_class(enriched.get("Broader")) + " " + parentterm_class(enriched.get("parentterm"))
        enriched["__trait"] = trait_clean(enriched.get("DiseaseOrTrait"))
        enriched["__DiseaseOrTraitClean"] = disease_clean(enriched.get("DiseaseOrTrait"))
        enriched["__BroaderClass"] = broader_class(enriched.get("Broader"))
        enriched["__ParentTermClass"] = parentterm_class(enriched.get("parentterm"))

        return enriched

    def encode_bubble_stage(rows, columns):
        dicts = {}
        codes = {}

        for column in columns:
            values = []
            value_to_code = {}
            column_codes = []

            for row in rows:
                value = row.get(column)
                if isinstance(value, float) and math.isnan(value):
                    value = None

                key = json.dumps(value, ensure_ascii=False, sort_keys=True)

                if key not in value_to_code:
                    value_to_code[key] = len(values)
                    values.append(value)

                column_codes.append(value_to_code[key])

            dicts[column] = values
            codes[column] = column_codes

        ns = [float(row.get("__Nnum") or 0) for row in rows]
        dms = [row.get("__dateMS") for row in rows if row.get("__dateMS") is not None]

        meta = {
            "rowCount": len(rows),
            "maxN": max(ns) if ns else 0,
            "minDateMS": min(dms) if dms else None,
            "maxDateMS": max(dms) if dms else None,
            "minDate": datetime.datetime.utcfromtimestamp(min(dms) / 1000).strftime("%Y-%m-%d") if dms else None,
            "maxDate": datetime.datetime.utcfromtimestamp(max(dms) / 1000).strftime("%Y-%m-%d") if dms else None,
            "includePrecomputed": True,
        }

        return {
            "columns": columns,
            "dicts": dicts,
            "codes": codes,
            "meta": meta,
        }

    def encode_bubble_graph(data):
        initial_rows = [enrich_bubble_row(row) for row in rows_from_stage(data["bubblegraph_initial"])]
        replication_rows = [enrich_bubble_row(row) for row in rows_from_stage(data["bubblegraph_replication"])]
        columns = sorted(set().union(*(row.keys() for row in initial_rows + replication_rows)))

        return {
            "__format": "dict_columnar_v2",
            "bubblegraph_initial": encode_bubble_stage(initial_rows, columns),
            "bubblegraph_replication": encode_bubble_stage(replication_rows, columns),
        }

    # filename -> function (do NOT call here)
    tasks = [
        ('ancestries.json',         dl.getAncestriesList),
        ('ancestriesOrdered.json',  dl.getAncestriesListOrder),
        ('parentTerms.json',        dl.getTermsList),
        ('traits.json',             dl.getTraitsList),
        ('bubbleGraph.json',        dl.getBubbleGraph),
        ('tsPlot.json',             dl.getTSPlot),
        ('chloroMap.json',          dl.getChloroMap),
        ('heatMap.json',            dl.getHeatMap),
        ('doughnutGraph.json',      lambda: dl.getDoughnutGraph(dl.getAncestriesListOrder())),
        ('summary.json',            dl.getSummaryStatistics),
    ]

    for filename, func in tasks:
        try:
            diversity_logger.info(f'json_converter: building {filename}')
            data = func()  # evaluate lazily here
            if filename == 'bubbleGraph.json':
                data = encode_bubble_graph(data)
                with open(os.path.join(plot_path, filename), 'w') as fp:
                    json.dump(data, fp, ensure_ascii=False, separators=(',', ':'))
            else:
                with open(os.path.join(plot_path, filename), 'w') as fp:
                    json.dump(data, fp)
        except Exception as e:
            diversity_logger.exception(f'json_converter: failed {filename}: {e}')
            raise

def setup_logging(logpath):
    """Configure the generator's logger to write only to its log file."""
    os.makedirs(logpath, exist_ok=True)
    logger = logging.getLogger('diversity_logger')
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # setup_logging can be called more than once by maintenance commands. Keep
    # exactly one handler so records are neither duplicated nor sent to a stream.
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(os.path.abspath(
        os.path.join(logpath, 'diversity_logger.log')
    ))
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def _failure_notification_state_path(repository_path):
    return os.path.join(
        repository_path,
        'data',
        GENERATION_CONTROL_DIRECTORY,
        FAILURE_NOTIFICATION_STATE_FILE,
    )


def _failure_email_recipients(value):
    recipients = [address.strip() for address in value.split(',')]
    return [address for address in recipients if address]


def _failure_signature(error):
    description = '{}.{}\n{}'.format(
        type(error).__module__, type(error).__name__, str(error)
    )
    return hashlib.sha256(description.encode('utf-8')).hexdigest()


def _failure_notification_is_throttled(state_path, signature, timestamp,
                                       cooldown_seconds):
    if cooldown_seconds <= 0 or not os.path.isfile(state_path):
        return False
    try:
        state = _read_json(state_path)
        return state.get('signature') == signature and \
            timestamp - float(state['sent_timestamp']) < cooldown_seconds
    except (KeyError, OSError, TypeError, ValueError,
            json.JSONDecodeError):
        return False


def send_failure_notification(error, traceback_text, repository_path,
                              exit_status=1, environ=None, now=None,
                              logger=None):
    """Email a bounded failure report through the host's local MTA.

    Returns ``sent``, ``suppressed``, or ``not-production``.
    Alerts fail closed unless this process is explicitly marked as the
    canonical production deployment. Identical consecutive failures are
    throttled using state stored in the persistent data volume so Docker's
    restart policy cannot flood the recipient. The loopback relay is
    deliberately unauthenticated: it must be a local, loopback-only MTA on
    the production Lightsail host, not a public SMTP service.
    """
    environ = os.environ if environ is None else environ
    deployment_domain = environ.get(
        'GWAS_DEPLOYMENT_DOMAIN', ''
    ).strip().lower().rstrip('.')
    if deployment_domain != FAILURE_EMAIL_PRODUCTION_DOMAIN:
        return 'not-production'

    smtp_host = environ.get(
        'GWAS_LOCAL_MAIL_HOST', FAILURE_EMAIL_LOCAL_RELAY
    ).strip()
    if smtp_host not in {'127.0.0.1', '::1', 'localhost'}:
        raise ValueError(
            'GWAS_LOCAL_MAIL_HOST must name a loopback interface'
        )
    smtp_port = int(environ.get(
        'GWAS_LOCAL_MAIL_PORT', FAILURE_EMAIL_LOCAL_RELAY_PORT
    ))
    smtp_timeout = float(environ.get(
        'GWAS_LOCAL_MAIL_TIMEOUT_SECONDS', 15
    ))

    recipients = _failure_email_recipients(
        environ.get('GWAS_FAILURE_EMAIL_TO', FAILURE_EMAIL_RECIPIENT)
    )
    if not recipients:
        raise ValueError('GWAS_FAILURE_EMAIL_TO has no recipient addresses')
    sender = environ.get(
        'GWAS_FAILURE_EMAIL_FROM', FAILURE_EMAIL_SENDER
    ).strip()
    if not sender:
        raise ValueError('GWAS_FAILURE_EMAIL_FROM is empty')

    cooldown_seconds = int(environ.get(
        'GWAS_FAILURE_EMAIL_COOLDOWN_SECONDS',
        FAILURE_EMAIL_COOLDOWN_SECONDS,
    ))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    timestamp = now.timestamp()
    signature = _failure_signature(error)
    state_path = _failure_notification_state_path(repository_path)
    if _failure_notification_is_throttled(
            state_path, signature, timestamp, cooldown_seconds):
        return 'suppressed'

    hostname = socket.gethostname()
    message = EmailMessage()
    message['Subject'] = (
        '[GWAS Diversity Monitor] generate_data.py failed on '
        f'{hostname}'
    )
    message['From'] = sender
    message['To'] = ', '.join(recipients)
    bounded_traceback = (traceback_text or 'No traceback available.')[-30000:]
    message.set_content(
        'The GWAS Diversity Monitor data-generation job did not complete '
        'successfully.\n\n'
        f'Time (UTC): {now.astimezone(datetime.timezone.utc).isoformat()}\n'
        f'Host: {hostname}\n'
        f'Working directory: {os.path.abspath(repository_path)}\n'
        f'Exit status: {exit_status}\n'
        f'Error: {type(error).__name__}: {error}\n\n'
        f'Traceback (last {len(bounded_traceback):,} characters):\n'
        f'{bounded_traceback}\n'
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=smtp_timeout) as connection:
        connection.ehlo()
        connection.send_message(message)

    try:
        _atomic_write_json(state_path, {
            'sent_at': now.astimezone(datetime.timezone.utc).isoformat(),
            'sent_timestamp': timestamp,
            'signature': signature,
        })
    except OSError:
        if logger is not None:
            logger.exception(
                'Failure email was sent, but its cooldown state could not be '
                'recorded.'
            )
    return 'sent'


def _clear_failure_notification_state(repository_path, logger=None):
    state_path = _failure_notification_state_path(repository_path)
    try:
        if os.path.isfile(state_path):
            os.unlink(state_path)
            _fsync_directory(os.path.dirname(state_path))
    except OSError:
        if logger is not None:
            logger.exception(
                'The previous failure-email cooldown state could not be '
                'cleared after a successful run.'
            )


def _notify_generation_failure(error, repository_path, exit_status, logger):
    try:
        outcome = send_failure_notification(
            error,
            traceback.format_exc(),
            repository_path,
            exit_status=exit_status,
            logger=logger,
        )
        if outcome == 'sent':
            message = 'Failure notification email sent to %s.' % \
                os.environ.get(
                    'GWAS_FAILURE_EMAIL_TO', FAILURE_EMAIL_RECIPIENT
                )
            if logger is not None:
                logger.info(message)
            else:
                print(message, file=sys.stderr)
        elif outcome == 'suppressed':
            message = (
                'An identical failure notification was suppressed during '
                'the configured cooldown period.'
            )
            if logger is not None:
                logger.warning(message)
            else:
                print(message, file=sys.stderr)
        elif outcome == 'not-production':
            message = (
                'Failure notification email was not sent because this '
                'process is not marked as the canonical '
                f'{FAILURE_EMAIL_PRODUCTION_DOMAIN} deployment.'
            )
            if logger is not None:
                logger.info(message)
            else:
                print(message, file=sys.stderr)
    except Exception:
        if logger is not None:
            logger.exception(
                'Failure notification email could not be handed to the '
                'loopback-only mail transfer agent on the production host.'
            )
        else:
            print(
                'Failure notification email could not be sent:\n'
                + traceback.format_exc(),
                file=sys.stderr,
            )


def summarize_catalog_pvalues(catalog_path, logger, chunksize=100_000,
                              example_limit=10):
    """Summarize only finite GWAS Catalog p-values in inclusive [0, 1].

    The downloaded catalog remains untouched so upstream problems can still be
    inspected. Invalid rows are excluded from all generated p-value summary
    statistics and, when present, are recorded with bounded identifying
    context in the generate-data log.
    """
    header = pd.read_csv(catalog_path, sep='\t', nrows=0)
    if 'P-VALUE' not in header.columns:
        raise KeyError(f'{catalog_path}: missing required column P-VALUE')

    context_candidates = [
        'PUBMEDID',
        'FIRST AUTHOR',
        'STUDY ACCESSION',
        'DISEASE/TRAIT',
        'MAPPED_TRAIT',
        'SNPS',
        'P-VALUE (TEXT)'
    ]
    context_columns = [
        column for column in context_candidates if column in header.columns
    ]
    usecols = list(dict.fromkeys(context_columns + ['P-VALUE']))

    total_count = 0
    valid_count = 0
    valid_sum = 0.0
    threshold_count = 0
    rejected_counts = {
        'missing/blank': 0,
        'unparseable': 0,
        'non-finite': 0,
        'below 0': 0,
        'above 1': 0
    }
    rejected_examples = []
    examples_collected = 0

    reader = pd.read_csv(
        catalog_path,
        sep='\t',
        usecols=usecols,
        dtype=str,
        keep_default_na=False,
        quotechar='"',
        on_bad_lines='skip',
        chunksize=chunksize,
        low_memory=False
    )

    for chunk in reader:
        raw_pvalues = chunk['P-VALUE'].str.strip()
        numeric_pvalues = pd.to_numeric(raw_pvalues, errors='coerce')
        finite = pd.Series(
            np.isfinite(numeric_pvalues.to_numpy()),
            index=numeric_pvalues.index
        )

        missing = raw_pvalues.eq('')
        unparseable = ~missing & numeric_pvalues.isna()
        non_finite = numeric_pvalues.notna() & ~finite
        below_zero = finite & numeric_pvalues.lt(0)
        above_one = finite & numeric_pvalues.gt(1)
        valid = finite & numeric_pvalues.between(0, 1, inclusive='both')

        reason_masks = {
            'missing/blank': missing,
            'unparseable': unparseable,
            'non-finite': non_finite,
            'below 0': below_zero,
            'above 1': above_one
        }
        for reason, mask in reason_masks.items():
            rejected_counts[reason] += int(mask.sum())

        total_count += len(chunk)
        valid_values = numeric_pvalues.loc[valid]
        valid_count += len(valid_values)
        valid_sum += float(valid_values.sum())
        threshold_count += int((valid_values < 5.0e-8).sum())

        rejected = ~valid
        remaining_examples = example_limit - examples_collected
        if rejected.any() and remaining_examples > 0:
            example_index = chunk.index[rejected][:remaining_examples]
            example_columns = [
                column for column in context_columns
                if column != 'P-VALUE (TEXT)'
            ] + ['P-VALUE']
            if 'P-VALUE (TEXT)' in context_columns:
                example_columns.append('P-VALUE (TEXT)')

            examples = chunk.loc[example_index, example_columns].copy()
            examples.insert(0, 'SOURCE ROW', example_index + 2)
            reasons = pd.Series('', index=chunk.index, dtype=object)
            for reason, mask in reason_masks.items():
                reasons.loc[mask] = reason
            examples['REASON'] = reasons.loc[example_index]

            def clean_log_value(value):
                cleaned = re.sub(r'\s+', ' ', str(value)).strip()
                if not cleaned:
                    return '<missing>'
                if len(cleaned) > 160:
                    return cleaned[:157] + '...'
                return cleaned

            text_columns = examples.columns.drop('SOURCE ROW')
            examples[text_columns] = examples[text_columns].map(
                clean_log_value
            )
            rejected_examples.append(examples)
            examples_collected += len(examples)

    if valid_count == 0:
        raise ValueError(
            f'{catalog_path}: no finite P-VALUE values in inclusive [0, 1]'
        )

    rejected_count = total_count - valid_count
    if rejected_count:
        examples = pd.concat(rejected_examples, ignore_index=True)
        examples_text = examples.to_csv(
            sep='\t', index=False, lineterminator='\n'
        ).rstrip()
        omitted_count = rejected_count - len(examples)
        omitted_text = (
            f'; {omitted_count} additional row(s) omitted from this log'
            if omitted_count else ''
        )
        logger.info(
            'Errant upstream p-values detected: rejected %d of %d GWAS '
            'Catalog association rows; retained %d rows with finite P-VALUE '
            'in inclusive [0, 1].\n'
            'Rejected rows by reason: missing/blank=%d; unparseable=%d; '
            'non-finite=%d; below 0=%d; above 1=%d.\n'
            'Rejected upstream rows (%d shown%s):\n%s',
            rejected_count,
            total_count,
            valid_count,
            rejected_counts['missing/blank'],
            rejected_counts['unparseable'],
            rejected_counts['non-finite'],
            rejected_counts['below 0'],
            rejected_counts['above 1'],
            len(examples),
            omitted_text,
            examples_text
        )

    return {
        'average': valid_sum / valid_count,
        'threshold_count': threshold_count,
        'valid_count': valid_count,
        'rejected_count': rejected_count
    }


def create_summarystats(data_path, timeupdated=None):
    """
        Create the summarystats .json used to propogate index.html
        Robust to header changes like 'PUBMEDID' -> 'PUBMED ID'.
    """
    sumstats = {}  # ensure defined even if we hit an exception early
    try:
        # --- Robust read of Cat_Stud with header aliasing ---
        stud_path = os.path.join(data_path, 'catalog', 'raw', 'Cat_Stud.tsv')

        # Canonical -> acceptable variants
        ALIASES = {
            'PUBMEDID': {'PUBMEDID', 'PUBMED ID', 'PUBMED_ID'},
            'DATE': {'DATE'},
            'FIRST AUTHOR': {'FIRST AUTHOR', 'FIRST_AUTHOR', 'FIRST AUTHOR(S)'},
            'STUDY ACCESSION': {'STUDY ACCESSION', 'STUDY_ACCESSION', 'STUDY ACCESSSION'},
            'DISEASE/TRAIT': {'DISEASE/TRAIT', 'DISEASE / TRAIT', 'DISEASE_TRAIT'},
            'MAPPED_TRAIT': {'MAPPED_TRAIT', 'MAPPED TRAIT'},
            'ASSOCIATION COUNT': {'ASSOCIATION COUNT', 'ASSOCIATION_COUNT'},
            'JOURNAL': {'JOURNAL'}
        }

        def _norm(s: str) -> str:
            return s.replace('\ufeff', '').strip().replace('_', ' ').casefold()

        # sniff headers
        sniff = pd.read_csv(stud_path, sep='\t', nrows=0)
        raw_cols = [c.replace('\ufeff','').strip() for c in sniff.columns]

        # required + optional columns
        required = ['PUBMEDID', 'DATE', 'FIRST AUTHOR', 'STUDY ACCESSION',
                    'DISEASE/TRAIT', 'ASSOCIATION COUNT', 'JOURNAL']
        optional = ['MAPPED_TRAIT']

        # build mapping actual_name -> canonical
        found = {}
        for canon in required + optional:
            variants_norm = {_norm(v) for v in (ALIASES.get(canon, {canon}))}
            for c in raw_cols:
                if _norm(c) in variants_norm:
                    found[canon] = c
                    break

        missing = [c for c in required if c not in found]
        if missing:
            diversity_logger.debug(f"Cat_Stud header sniff: {raw_cols}")
            raise KeyError(f"Cat_Stud.tsv missing required columns (after alias matching): {missing}")

        # read only what we found, dtype as strings first
        usecols_actual = [found[c] for c in (required + [c for c in optional if c in found])]
        Cat_Stud = pd.read_csv(
            stud_path,
            sep='\t',
            low_memory=False,
            usecols=usecols_actual,
            quotechar='"',
            on_bad_lines="skip",
            dtype=str
        )

        # rename back to canonical
        Cat_Stud = Cat_Stud.rename(columns={v: k for k, v in found.items()})

        # ensure optional column exists
        if 'MAPPED_TRAIT' not in Cat_Stud.columns:
            Cat_Stud['MAPPED_TRAIT'] = 'N/A'

        # cast types
        Cat_Stud['ASSOCIATION COUNT'] = pd.to_numeric(Cat_Stud['ASSOCIATION COUNT'], errors='coerce')

        pvalue_summary = summarize_catalog_pvalues(
            os.path.join(data_path, 'catalog', 'raw', 'Cat_Full.tsv'),
            diversity_logger
        )

        # --- Ancesty w/ Broader (unchanged) ---
        Cat_Anc_wBroader = pd.read_csv(
            os.path.join(data_path, 'catalog', 'synthetic', 'Cat_Anc_wBroader.tsv'),
            sep='\t',
            index_col=False,
            low_memory=False
        )

        # --- temp bubble df (unchanged) ---
        temp_bubble_df = pd.read_csv(
            os.path.join(data_path, 'toplot', 'bubble_df.csv'),
            sep=',', index_col=False, low_memory=False
        )

        # ------------------- Summary stats -------------------
        sumstats['number_studies'] = int(Cat_Stud['PUBMEDID'].nunique())
        sumstats['first_study_date'] = str(Cat_Stud['DATE'].min())

        datemin = Cat_Stud['DATE'] == Cat_Stud['DATE'].min()
        dateminauth = Cat_Stud.loc[datemin, 'FIRST AUTHOR']
        sumstats['first_study_firstauthor'] = str(dateminauth.iloc[0])
        dateminpubmed = Cat_Stud.loc[datemin, 'PUBMEDID']
        # numeric pubmed if possible
        try:
            sumstats['first_study_pubmedid'] = int(pd.to_numeric(dateminpubmed.iloc[0], errors='coerce'))
        except Exception:
            sumstats['first_study_pubmedid'] = str(dateminpubmed.iloc[0])

        datemax = Cat_Stud['DATE'].max()
        sumstats['last_study_date'] = str(datemax)
        datemaxauth = Cat_Stud.loc[Cat_Stud['DATE'] == datemax, 'FIRST AUTHOR']
        sumstats['last_study_firstauthor'] = str(datemaxauth.iloc[0])
        datemaxpubmed = Cat_Stud.loc[Cat_Stud['DATE'] == datemax, 'PUBMEDID']
        try:
            sumstats['last_study_pubmedid'] = int(pd.to_numeric(datemaxpubmed.iloc[0], errors='coerce'))
        except Exception:
            sumstats['last_study_pubmedid'] = str(datemaxpubmed.iloc[0])

        cat_stud_acc_uniq = Cat_Stud['STUDY ACCESSION'].astype(str).unique()
        sumstats['number_accessions'] = int(len(cat_stud_acc_uniq))
        sumstats['number_diseasestraits'] = int(Cat_Stud['DISEASE/TRAIT'].nunique())
        sumstats['number_mappedtrait'] = int(Cat_Stud['MAPPED_TRAIT'].nunique())

        cat_stud_ass_sum = pd.to_numeric(Cat_Stud['ASSOCIATION COUNT'], errors='coerce').sum(skipna=True)
        sumstats['found_associations'] = int(cat_stud_ass_sum) if not np.isnan(cat_stud_ass_sum) else 0

        cat_stud_ass_mean = pd.to_numeric(Cat_Stud['ASSOCIATION COUNT'], errors='coerce').mean(skipna=True)
        sumstats['average_associations'] = float(cat_stud_ass_mean) if not np.isnan(cat_stud_ass_mean) else 0.0

        # mode() can be empty; guard it
        jmode = Cat_Stud['JOURNAL'].mode()
        sumstats['mostcommon_journal'] = str(jmode.iloc[0]) if not jmode.empty else 'N/A'
        sumstats['unique_journals'] = int(Cat_Stud['JOURNAL'].nunique())

        noneuro_trait = (
            temp_bubble_df[temp_bubble_df['Broader'] != 'European']
            .groupby(['DiseaseOrTrait'])
            .size()
            .sort_values(ascending=False)
            .reset_index()['DiseaseOrTrait'][0]
        )
        sumstats['noneuro_trait'] = str(noneuro_trait)

        sumstats['average_pval'] = float(round(
            pvalue_summary['average'], 10
        ))
        sumstats['threshold_pvals'] = int(
            pvalue_summary['threshold_count']
        )

        # Big-N study info
        Cat_Anc_byN = Cat_Anc_wBroader[['STUDY ACCESSION', 'N']].copy()
        Cat_Anc_byN = Cat_Anc_byN.groupby(by='STUDY ACCESSION').sum(numeric_only=True).reset_index()

        # for author/pubmed lookup, de-dup by accession
        tmp_lookup = (
            Cat_Anc_wBroader.drop_duplicates('STUDY ACCESSION')[['PUBMEDID', 'FIRST AUTHOR', 'STUDY ACCESSION']]
        )
        Cat_Anc_byN = pd.merge(Cat_Anc_byN, tmp_lookup, how='left', on='STUDY ACCESSION')

        lar_acc = Cat_Anc_byN.sort_values(by='N', ascending=False)['N'].iloc[0]
        sumstats['big_n'] = int(lar_acc)
        biggestauth = Cat_Anc_byN.loc[Cat_Anc_byN['N'] == int(lar_acc), 'FIRST AUTHOR']
        sumstats['large_accesion_firstauthor'] = str(biggestauth.iloc[0])
        biggestpubmed = Cat_Anc_byN.loc[Cat_Anc_byN['N'] == int(lar_acc), 'PUBMEDID']
        try:
            sumstats['large_accesion_pubmed'] = int(pd.to_numeric(biggestpubmed.iloc[0], errors='coerce'))
        except Exception:
            sumstats['large_accesion_pubmed'] = str(biggestpubmed.iloc[0])

        # Composition excluding 'In Part Not Recorded'
        Cat_Anc_NoNR = Cat_Anc_wBroader[Cat_Anc_wBroader['Broader'] != 'In Part Not Recorded'].copy()
        no_NR_sum = Cat_Anc_NoNR['N'].sum()
        def pc(x): return round((x / no_NR_sum) * 100, 2) if no_NR_sum else 0.0

        sumstats['total_european'] = pc(Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'] == 'European']['N'].sum())
        sumstats['total_asian']    = pc(Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'] == 'Asian']['N'].sum())
        sumstats['total_african']  = pc(Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'] == 'African']['N'].sum())
        sumstats['total_othermixed']   = pc(Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Other', na=False)]['N'].sum())
        sumstats['total_afamafcam']    = pc(Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Cari', na=False)]['N'].sum())
        sumstats['total_hisorlatinam'] = pc(Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Hispanic', na=False)]['N'].sum())

        # Discovery stage
        anc_nonr_init = Cat_Anc_NoNR[Cat_Anc_NoNR['STAGE'] == 'initial']
        anc_nonr_init_sum = anc_nonr_init['N'].sum()
        def stage_pc(df): return round(((df['N'].sum() / anc_nonr_init_sum) * 100), 2) if anc_nonr_init_sum else 0.0
        def stage_len_pc(n):
            denom = len(anc_nonr_init)
            return round(((n / denom) * 100), 2) if denom else 0.0

        disc_euro = anc_nonr_init[anc_nonr_init['Broader'] == 'European']
        disc_asia = anc_nonr_init[anc_nonr_init['Broader'] == 'Asian']
        disc_afri = anc_nonr_init[anc_nonr_init['Broader'] == 'African']
        disc_othe = anc_nonr_init[anc_nonr_init['Broader'].str.contains('Other', na=False)]
        disc_cari = anc_nonr_init[anc_nonr_init['Broader'].str.contains('Cari', na=False)]
        disc_hisp = anc_nonr_init[anc_nonr_init['Broader'].str.contains('Hispanic', na=False)]

        sumstats['discovery_participants_european']   = stage_pc(disc_euro)
        sumstats['discovery_participants_asian']      = stage_pc(disc_asia)
        sumstats['discovery_participants_african']    = stage_pc(disc_afri)
        sumstats['discovery_participants_othermixed'] = stage_pc(disc_othe)
        sumstats['discovery_participants_afamafcam']  = stage_pc(disc_cari)
        sumstats['discovery_participants_hisorlatinam']= stage_pc(disc_hisp)

        sumstats['discovery_studies_european']   = stage_len_pc(len(disc_euro))
        sumstats['discovery_studies_asian']      = stage_len_pc(len(disc_asia))
        sumstats['discovery_studies_african']    = stage_len_pc(len(disc_afri))
        sumstats['discovery_studies_othermixed'] = stage_len_pc(len(disc_othe))
        sumstats['discovery_studies_afamafcam']  = stage_len_pc(len(disc_cari))
        sumstats['discovery_studies_hisorlatinam']= stage_len_pc(len(disc_hisp))

        # Replication stage
        anc_nonr_repl = Cat_Anc_NoNR[Cat_Anc_NoNR['STAGE'] == 'replication']
        anc_nonr_repl_sum = anc_nonr_repl['N'].sum()
        def r_pc(df): return round(((df['N'].sum() / anc_nonr_repl_sum) * 100), 2) if anc_nonr_repl_sum else 0.0
        def r_len_pc(n):
            denom = len(anc_nonr_repl)
            return round(((n / denom) * 100), 2) if denom else 0.0

        repl_euro = anc_nonr_repl[anc_nonr_repl['Broader'] == 'European']
        repl_asia = anc_nonr_repl[anc_nonr_repl['Broader'] == 'Asian']
        repl_afri = anc_nonr_repl[anc_nonr_repl['Broader'] == 'African']
        repl_othe = anc_nonr_repl[anc_nonr_repl['Broader'].str.contains('Other', na=False)]
        repl_cari = anc_nonr_repl[anc_nonr_repl['Broader'].str.contains('Cari', na=False)]
        repl_hisp = anc_nonr_repl[anc_nonr_repl['Broader'].str.contains('Hispanic', na=False)]

        sumstats['replication_participants_european']   = r_pc(repl_euro)
        sumstats['replication_participants_asian']      = r_pc(repl_asia)
        sumstats['replication_participants_african']    = r_pc(repl_afri)
        sumstats['replication_participants_othermixed'] = r_pc(repl_othe)
        sumstats['replication_participants_afamafcam']  = r_pc(repl_cari)
        sumstats['replication_participants_hisorlatinam']= r_pc(repl_hisp)

        sumstats['replication_studies_european']   = r_len_pc(len(repl_euro))
        sumstats['replication_studies_asian']      = r_len_pc(len(repl_asia))
        sumstats['replication_studies_african']    = r_len_pc(len(repl_afri))
        sumstats['replication_studies_othermixed'] = r_len_pc(len(repl_othe))
        sumstats['replication_studies_afamafcam']  = r_len_pc(len(repl_cari))
        sumstats['replication_studies_hisorlatinam']= r_len_pc(len(repl_hisp))

        # Timestamp + unmapped count
        sumstats['timeupdated'] = timeupdated or \
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        unmapped_path = os.path.join(data_path, 'unmapped', 'unmapped_diseases.txt')
        if os.path.exists(unmapped_path):
            unmapped_dis = pd.read_csv(unmapped_path)
            sumstats['unmapped_diseases'] = int(len(unmapped_dis))
        else:
            sumstats['unmapped_diseases'] = 0

        # Write JSON
        json_path = os.path.join(data_path, 'summary', 'summary.json')
        with open(json_path, 'w') as outfile:
            json.dump(sumstats, outfile)

        diversity_logger.info('Build of the summary stats: Complete')
    except Exception as e:
        diversity_logger.exception(f'Build of the summary stats: Failed -- {e}')
        raise
    return sumstats


def make_heatmatrix(merged, stage, out_path):
    """
    Build heatmap CSVs (count & sum) without DataFrame.append.
    Output matches the original: rows = Broader (repeated per year),
    columns = all parent terms + 'Year' (as last column), with zeros
    for missing combos. Index is written to CSV (blank header) to keep
    DataLoader expectations intact.
    """
    # Columns (parent terms) and row index (ancestries) in the same order as before
    parent_terms = merged['parentterm'].unique().tolist()
    index_list   = merged.loc[merged['Broader'].notnull(), 'Broader'].unique().tolist()

    frames_count = []
    frames_sum   = []

    for year in range(2008, final_year + 1):
        # same year filter behavior as original (DATE was already cast to str)
        mask = (merged['STAGE'] == stage) & (merged['DATE'].str.contains(str(year)))
        tmp  = merged.loc[mask, ['Broader', 'parentterm', 'N']]

        # counts
        count = (tmp
                 .groupby(['Broader', 'parentterm'])
                 .size()
                 .unstack('parentterm'))
        # sums
        ssum  = (tmp
                 .groupby(['Broader', 'parentterm'])['N']
                 .sum()
                 .unstack('parentterm'))

        # ensure full grid & zeros for missing combos; keep original ordering
        count = count.reindex(index=index_list, columns=parent_terms, fill_value=0)
        ssum  = ssum .reindex(index=index_list, columns=parent_terms, fill_value=0)

        # add Year as last column (overwrites any earlier placeholder like the original)
        count['Year'] = year
        ssum['Year']  = year

        # preserve column order: all parent terms + 'Year'
        count = count[parent_terms + ['Year']]
        ssum  = ssum [parent_terms + ['Year']]

        frames_count.append(count)
        frames_sum.append(ssum)

    # one concat instead of repeated append
    count_df = pd.concat(frames_count)
    sum_df   = pd.concat(frames_sum)

    # write with index to keep the leading blank header column expected by DataLoader.getHeatMapData
    sum_df.to_csv(os.path.join(out_path, f'heatmap_sum_{stage}.csv'),
                  index=True, index_label='')
    count_df.to_csv(os.path.join(out_path, f'heatmap_count_{stage}.csv'),
                    index=True, index_label='')


def make_heatmap_dfs(data_path):
    """
        Make the heatmap dfs
    """
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               usecols = ['STUDY ACCESSION', 'DISEASE/TRAIT'],
                               sep='\t')
        Cat_Map = pd.read_csv(os.path.join(data_path, 'catalog',
                                           'raw', 'Cat_Map.tsv'),
                              sep='\t',
                              usecols = ['Disease trait', 'Parent term'])
        Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                               left_on='DISEASE/TRAIT',
                               right_on='Disease trait')
        Cat_StudMap.to_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                        'Disease_to_Parent_Mappings.tsv'),
                           sep='\t')
        Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION',
                                   'DISEASE/TRAIT']].drop_duplicates()
        Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path,
                                                    'catalog',
                                                    'synthetic',
                                                    'Cat_Anc_wBroader.tsv'),
                                       sep='\t',
                                       index_col=False,
                                       parse_dates=['DATE'])
        Cat_Anc_wBroader = Cat_Anc_wBroader[Cat_Anc_wBroader['Broader'] !=
                                                             'In Part Not Recorded']
        merged = pd.merge(Cat_StudMap, Cat_Anc_wBroader,
                          how='left', on='STUDY ACCESSION')
        merged.to_csv(os.path.join(data_path,
                                   'catalog',
                                   'synthetic',
                                   'Cat_Anc_wBroader_withParents.tsv'),
                      sep='\t')
        # A missing parent term is an upstream crosswalk miss; it does not
        # imply that the study's disease/trait label itself is empty.
        unmapped_mask = merged['parentterm'].isna()
        unmapped_path = os.path.join(
            data_path, 'unmapped', 'unmapped_diseases.txt'
        )
        if unmapped_mask.any():
            unmapped_diseases = pd.Series(
                merged.loc[unmapped_mask, 'DISEASE/TRAIT'].unique()
            )
            unmapped_diseases.to_csv(unmapped_path, index=False)
            diversity_logger.info(
                'GWAS Catalog parent-term mappings are unavailable for %d '
                'unique disease/trait labels across %d studies; affected '
                'labels were written to %s and are excluded from parent-term '
                'visualisations.',
                int(unmapped_diseases.notna().sum()),
                int(merged.loc[
                    unmapped_mask, 'STUDY ACCESSION'
                ].nunique()),
                unmapped_path
            )
        else:
            pd.Series(dtype='object', name='DISEASE/TRAIT').to_csv(
                unmapped_path, index=False
            )
            diversity_logger.info(
                'All GWAS Catalog disease/trait labels have parent-term '
                'mappings.'
            )
        merged = merged[merged["parentterm"].notnull()]
        merged = merged[merged["parentterm"]!='NR']
        merged["parentterm"] = merged["parentterm"].astype(str)
        merged["DATE"] = merged["DATE"].astype(str)
        make_heatmatrix(merged, 'initial', os.path.join(data_path,
                                                        'toplot'))
        make_heatmatrix(merged, 'replication', os.path.join(data_path, 'toplot'))
        diversity_logger.info('Build of the heatmap dataset: Complete')
    except Exception as e:
        diversity_logger.exception(f'Build of the heatmap dataset: Failed -- {e}')
        raise


def make_choro_df(data_path):
    """
    Create the dataframe for the choropleth map.
    Robust year extraction and LEFT join to the country lookup to avoid
    losing rows for newer/renamed countries.
    """
    try:
        Cat_Ancestry = pd.read_csv(
            os.path.join(data_path, 'catalog', 'synthetic', 'Cat_Anc_wBroader.tsv'),
            sep='\t'
        )
        # Clean CoR without exploding (one record per study)
        Clean_CoR = make_clean_CoR(Cat_Ancestry, data_path)
        Clean_CoR['Year'] = pd.to_datetime(Clean_CoR['Date'], errors='coerce').dt.year

        countrylookup = pd.read_csv(
            os.path.join(data_path, 'support', 'Country_Lookup.csv'),
            index_col='Country'
        )

        frames = []
        for year in range(2008, final_year + 1):
            tmp = Clean_CoR[Clean_CoR['Year'] == year]
            if tmp.empty:
                continue

            # Aggregate by country
            agg_sum  = tmp.groupby('Cleaned Country')['N'].sum().to_frame('N')
            agg_cnt  = tmp.groupby('Cleaned Country')['N'].count().to_frame('Count')
            tempdf_merged = agg_sum.join(agg_cnt, how='outer')  # keep all countries observed
            tempdf_merged['Year'] = year

            # LEFT join from data to lookup; don’t drop unknown names
            merged = tempdf_merged.merge(countrylookup, left_index=True, right_index=True, how='left')
            merged = merged.reset_index().rename(columns={'index': 'Country'})

            # Percentages (guard against zero totals)
            totN = merged['N'].sum()
            totC = merged['Count'].sum()
            merged['Count (%)'] = (merged['Count'] / totC * 100).round(2) if totC else 0.0
            merged['N (%)']     = (merged['N']     / totN * 100).round(2) if totN else 0.0

            frames.append(merged)

        if frames:
            annual_df = pd.concat(frames, ignore_index=True)
            out = os.path.join(data_path, 'toplot', 'choro_df.csv')
            annual_df.to_csv(out, index=False)

            actual_years = {
                int(year) for year in annual_df['Year'].dropna().unique()
            }
            expected_years = set(range(2008, final_year + 1))
            missing_years = sorted(expected_years - actual_years)
            if missing_years:
                diversity_logger.warning(
                    'Choropleth data are missing expected years: %s',
                    missing_years
                )
            diversity_logger.info('Build of the choropleth dataset: Complete')
        else:
            raise ValueError('No choropleth data generated (no yearly data found)')

    except Exception:
        diversity_logger.exception('Build of the choropleth dataset: Failed')
        raise


def make_timeseries_df(Cat_Ancestry, data_path, savename):
    """
        Make the timeseries dataframes (both for ts1 and ts2)
    """
    try:
        DateSplit = Cat_Ancestry['DATE'].str.split('-', expand=True).\
            rename({0: 'Year', 1: 'Month', 2: 'Day'}, axis=1)
        Cat_Ancestry = pd.merge(Cat_Ancestry, DateSplit, how='left',
                                left_index=True, right_index=True)
        Cat_Ancestry['Year'] = pd.to_numeric(Cat_Ancestry['Year'])
        Cat_Ancestry['Month'] = pd.to_numeric(Cat_Ancestry['Month'])
        broader_list = Cat_Ancestry['Broader'].unique().tolist()
        ts_init_sum = pd.DataFrame(index=range(2007, final_year+1),
                                   columns=broader_list)
        ts_rep_sum = pd.DataFrame(index=range(2007, final_year+1),
                                  columns=broader_list)
        ts_init_count = pd.DataFrame(index=range(2007, final_year+1),
                                     columns=broader_list)
        ts_rep_count = pd.DataFrame(index=range(2007, final_year+1),
                                    columns=broader_list)
        for ancestry in broader_list:
            for year in range(2007, final_year+1):
                temp_df = Cat_Ancestry[(Cat_Ancestry['Year'] == year) &
                                       (Cat_Ancestry['Broader'] == ancestry) &
                                       (Cat_Ancestry['STAGE'] == 'initial')]
                ts_init_sum.at[year, ancestry] = temp_df['N'].sum()
                ts_init_count.at[year, ancestry] = len(temp_df['N'])
                temp_df = Cat_Ancestry[(Cat_Ancestry['Year'] == year) &
                                       (Cat_Ancestry['Broader'] == ancestry) &
                                       (Cat_Ancestry['STAGE'] == 'replication')]
                ts_rep_sum.at[year, ancestry] = temp_df['N'].sum()
                ts_rep_count.at[year, ancestry] = len(temp_df['N'])
        ts_init_sum_pc = ((ts_init_sum.T / ts_init_sum.T.sum()).T) * 100
        ts_init_sum_pc = ts_init_sum_pc.reset_index()
        ts_init_sum_pc.to_csv(os.path.join(data_path, 'toplot',
                                           savename + '_initial_sum.csv'),
                                 index=False)
        ts_init_count_pc = ((ts_init_count.T / ts_init_count.T.sum()).T)*100
        ts_init_count_pc = ts_init_count_pc.reset_index()
        ts_init_count_pc.to_csv(os.path.join(data_path, 'toplot',
                                             savename + '_initial_count.csv'),
                                   index=False)
        ts_rep_sum_pc = ((ts_rep_sum.T /ts_rep_sum.T.sum()).T)*100
        ts_rep_sum_pc = ts_rep_sum_pc.reset_index()
        ts_rep_sum_pc.to_csv(os.path.join(data_path, 'toplot',
                                          savename + '_replication_sum.csv'),
                                     index=False)
        ts_rep_count_pc = ((ts_rep_count.T / ts_rep_count.T.sum()).T)*100
        ts_rep_count_pc = ts_rep_count_pc.reset_index()
        ts_rep_count_pc.to_csv(os.path.join(data_path, 'toplot',
                                            savename + '_replication_count.csv'),
                                       index=False)
        diversity_logger.info('Build of the ts dataset: Complete')
    except Exception as e:
        diversity_logger.exception(f'Build of the ts dataset: Failed -- {e}')
        raise




def make_doughnut_df_old(data_path):
    """Make the production-compatible doughnut dataframe for the app."""
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               sep='\t',
                               usecols = ['STUDY ACCESSION',
                                          'DISEASE/TRAIT',
                                          'ASSOCIATION COUNT'])
        Cat_Map = pd.read_csv(os.path.join(data_path, 'catalog', 'raw',
                                           'Cat_Map.tsv'), sep='\t',
                              usecols = ['Disease trait', 'Parent term'])
        Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                               left_on='DISEASE/TRAIT',
                               right_on='Disease trait')
        Cat_StudMap.to_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                        'Disease_to_Parent_Mappings.tsv'),
                           sep='\t')
        Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION',
                                   'DISEASE/TRAIT', 'ASSOCIATION COUNT']]
        Cat_StudMap = Cat_StudMap.drop_duplicates()
        Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path, 'catalog',
                                                    'synthetic',
                                                    'Cat_Anc_wBroader.tsv'),
                                       sep='\t',
                                       index_col=False,
                                       parse_dates=['DATE'])
        Cat_Anc_wBroader = Cat_Anc_wBroader[Cat_Anc_wBroader['Broader'] !=
                                            'In Part Not Recorded']
        merged = pd.merge(Cat_StudMap, Cat_Anc_wBroader,
                          how='left', on='STUDY ACCESSION')
        merged["DATE"] = merged["DATE"].astype(str)
        cols = ['Broader', 'parentterm', 'Year', 'InitialN', 'InitialCount',
                'ReplicationN', 'ReplicationCount', 'InitialAssociationSum']
        doughnut_df = pd.DataFrame(index=[], columns=cols)
        merged = merged[merged['Broader'].notnull()]
        merged = merged[merged['parentterm'].notnull()]

        # Preserve the legacy row order and calculations, but stop rebuilding the
        # same full-dataframe masks for every metric in every output row.  Slicing
        # hierarchically also preserves source-row order, so Series.sum() produces
        # the same floating-point values as the original implementation.
        ancestries = merged['Broader'].unique().tolist()
        parents = merged['parentterm'].unique().tolist()
        counter = 0
        for year in range(2008, final_year+1):
            year_df = merged[merged['DATE'].str.contains(str(year))]
            initial_df = year_df[year_df['STAGE'] == 'initial']
            replication_df = year_df[year_df['STAGE'] == 'replication']

            initial_n_total = initial_df['N'].sum()
            replication_n_total = replication_df['N'].sum()
            initial_association_total = initial_df['ASSOCIATION COUNT'].sum()
            initial_count_total = len(initial_df)
            replication_count_total = len(replication_df)

            parent_slices = {}
            for parent in parents:
                parent_initial = initial_df[initial_df['parentterm'] == parent]
                parent_replication = replication_df[
                    replication_df['parentterm'] == parent
                ]
                parent_slices[parent] = (
                    parent_initial,
                    parent_replication,
                    parent_initial['N'].sum(),
                    parent_replication['N'].sum(),
                    parent_initial['ASSOCIATION COUNT'].sum(),
                    len(parent_initial),
                    len(parent_replication),
                )

            for ancestry in ancestries:
                ancestry_initial = initial_df[
                    initial_df['Broader'] == ancestry
                ]
                ancestry_replication = replication_df[
                    replication_df['Broader'] == ancestry
                ]

                doughnut_df.at[counter, 'Broader'] = ancestry
                doughnut_df.at[counter, 'parentterm'] = 'All'
                doughnut_df.at[counter, 'Year'] = year
                doughnut_df.at[counter, 'ReplicationN'] = (
                    ancestry_replication['N'].sum() / replication_n_total
                ) * 100
                doughnut_df.at[counter, 'InitialN'] = (
                    ancestry_initial['N'].sum() / initial_n_total
                ) * 100
                doughnut_df.at[counter, 'InitialAssociationSum'] = (
                    ancestry_initial['ASSOCIATION COUNT'].sum() /
                    initial_association_total
                ) * 100
                doughnut_df.at[counter, 'InitialCount'] = (
                    len(ancestry_initial) / initial_count_total
                ) * 100
                doughnut_df.at[counter, 'ReplicationCount'] = (
                    len(ancestry_replication) / replication_count_total
                ) * 100
                counter = counter + 1

                for parent in parents:
                    try:
                        doughnut_df.at[counter, 'Broader'] = ancestry
                        doughnut_df.at[counter, 'parentterm'] = parent
                        doughnut_df.at[counter, 'Year'] = year

                        (parent_initial,
                         parent_replication,
                         parent_initial_n_total,
                         parent_replication_n_total,
                         parent_initial_association_total,
                         parent_initial_count_total,
                         parent_replication_count_total) = parent_slices[parent]
                        ancestry_parent_initial = parent_initial[
                            parent_initial['Broader'] == ancestry
                        ]
                        ancestry_parent_replication = parent_replication[
                            parent_replication['Broader'] == ancestry
                        ]

                        doughnut_df.at[counter, 'ReplicationN'] = (
                            ancestry_parent_replication['N'].sum() /
                            parent_replication_n_total
                        ) * 100
                        doughnut_df.at[counter, 'InitialN'] = (
                            ancestry_parent_initial['N'].sum() /
                            parent_initial_n_total
                        ) * 100
                        doughnut_df.at[counter, 'InitialAssociationSum'] = (
                            ancestry_parent_initial['ASSOCIATION COUNT'].sum() /
                            parent_initial_association_total
                        ) * 100
                        doughnut_df.at[counter, 'ReplicationCount'] = (
                            len(ancestry_parent_replication) /
                            parent_replication_count_total
                        ) * 100
                        doughnut_df.at[counter, 'InitialCount'] = (
                            len(ancestry_parent_initial) /
                            parent_initial_count_total
                        ) * 100
                    except ZeroDivisionError:
                        # Retain the legacy blank-cell behaviour relied upon by
                        # DataLoader and the doughnut graph's no-data handling.
                        doughnut_df.at[counter, 'InitialN'] = np.nan
                    counter = counter + 1
        doughnut_df['Broader'] = doughnut_df['Broader'].str.\
            replace('Hispanic/Latin American', 'Hispanic/L.A.')
        doughnut_df.to_csv(os.path.join(data_path, 'toplot', 'doughnut_df.csv'))
        diversity_logger.info('Build of the doughnut datasets: Complete')
    except Exception as e:
        diversity_logger.exception(f'Build of the doughnut datasets: Failed -- {e}')
        raise



def make_doughnut_df(data_path):
    """ Make the doughnut chart dataframe for use in main.py """
    try:
        # ---------- Cat_Stud ----------
        stud_path = os.path.join(data_path, 'catalog', 'raw', 'Cat_Stud.tsv')
        sniff = pd.read_csv(stud_path, sep='\t', nrows=0)
        usecols = ['STUDY ACCESSION', 'DISEASE/TRAIT', 'ASSOCIATION COUNT']
        if 'MAPPED_TRAIT' in sniff.columns:
            usecols.append('MAPPED_TRAIT')
        if 'MAPPED_TRAIT_URI' in sniff.columns:
            usecols.append('MAPPED_TRAIT_URI')

        Cat_Stud = pd.read_csv(stud_path, sep='\t', usecols=usecols, dtype=str)
        Cat_Stud['ASSOCIATION COUNT'] = pd.to_numeric(Cat_Stud['ASSOCIATION COUNT'], errors='coerce')
        Cat_Stud['STUDY ACCESSION'] = Cat_Stud['STUDY ACCESSION'].astype(str)

        req = {'STUDY ACCESSION','DISEASE/TRAIT','ASSOCIATION COUNT'}
        missing = req - set(Cat_Stud.columns)
        if missing:
            raise KeyError(f'Cat_Stud missing required columns: {sorted(missing)}')
        diversity_logger.debug(f'Cat_Stud: {Cat_Stud.shape}, assoc non-null={Cat_Stud["ASSOCIATION COUNT"].notna().mean():.3f}')

        # ---------- Cat_Map (robust) ----------
        cmap_path = os.path.join(data_path, 'catalog', 'raw', 'Cat_Map.tsv')
        Cat_Map = pd.read_csv(cmap_path, sep='\t', dtype=str)
        req_map = {'Disease trait','EFO term','EFO URI','Parent term','Parent URI'}
        if not req_map.issubset(set(Cat_Map.columns)):
            raise KeyError(f'Unexpected Cat_Map.tsv columns: {Cat_Map.columns.tolist()}')
        Cat_Map = Cat_Map[['Disease trait','EFO term','EFO URI','Parent term','Parent URI']]

        diversity_logger.debug(f'Cat_Map: {Cat_Map.shape}')

        # ---------- Normalized keys & mapping dicts ----------
        def _norm_text(s: pd.Series) -> pd.Series:
            return (s.astype(str).str.strip().str.replace(r'\s+',' ', regex=True).str.casefold())
        def _norm_uri(s: pd.Series) -> pd.Series:
            return s.astype(str).str.strip().str.casefold()

        Cat_Stud['_DT_norm']  = _norm_text(Cat_Stud['DISEASE/TRAIT'])
        if 'MAPPED_TRAIT' in Cat_Stud.columns:
            Cat_Stud['_MT_norm']  = _norm_text(Cat_Stud['MAPPED_TRAIT'])
        else:
            Cat_Stud['_MT_norm']  = ''
        if 'MAPPED_TRAIT_URI' in Cat_Stud.columns:
            Cat_Stud['_MTU_norm'] = _norm_uri(Cat_Stud['MAPPED_TRAIT_URI'])
        else:
            Cat_Stud['_MTU_norm'] = ''

        Cat_Map['_DT_norm']   = _norm_text(Cat_Map['Disease trait'])
        Cat_Map['_ET_norm']   = _norm_text(Cat_Map['EFO term'])
        Cat_Map['_EURI_norm'] = _norm_uri (Cat_Map['EFO URI'])

        # mapping Series (use first occurrence)
        map_DT   = Cat_Map.dropna(subset=['_DT_norm'])  .drop_duplicates('_DT_norm').set_index('_DT_norm')['Parent term']
        map_ET   = Cat_Map.dropna(subset=['_ET_norm'])  .drop_duplicates('_ET_norm').set_index('_ET_norm')['Parent term']
        map_EURI = Cat_Map.dropna(subset=['_EURI_norm']).drop_duplicates('_EURI_norm').set_index('_EURI_norm')['Parent term']

        # ---------- Build Cat_StudMap (no merge misalignment) ----------
        Cat_StudMap = Cat_Stud[['STUDY ACCESSION','DISEASE/TRAIT','ASSOCIATION COUNT']].copy()
        # successive fallbacks
        parent = Cat_Stud['_DT_norm'].map(map_DT)
        parent = parent.fillna(Cat_Stud['_DT_norm'].map(map_ET))
        if 'MAPPED_TRAIT' in Cat_Stud.columns:
            parent = parent.fillna(Cat_Stud['_MT_norm'].map(map_DT))
            parent = parent.fillna(Cat_Stud['_MT_norm'].map(map_ET))
        if 'MAPPED_TRAIT_URI' in Cat_Stud.columns:
            parent = parent.fillna(Cat_Stud['_MTU_norm'].map(map_EURI))

        Cat_StudMap['parentterm'] = parent
        diversity_logger.debug(f'Cat_StudMap parentterm coverage overall={Cat_StudMap["parentterm"].notna().mean():.3f}')

        # Write-through (as before) for traceability
        Cat_StudMap[['DISEASE/TRAIT','parentterm']].rename(columns={'parentterm':'Parent term'}).to_csv(
            os.path.join(data_path, 'catalog', 'synthetic', 'Disease_to_Parent_Mappings.tsv'),
            sep='\t', index=False
        )

        # ---------- Ancestry & merge ----------
        anc_path = os.path.join(data_path, 'catalog', 'synthetic', 'Cat_Anc_wBroader.tsv')
        Cat_Anc_wBroader = pd.read_csv(anc_path, sep='\t', index_col=False, parse_dates=['DATE'])
        Cat_Anc_wBroader = Cat_Anc_wBroader[Cat_Anc_wBroader['Broader'] != 'In Part Not Recorded']
        Cat_Anc_wBroader['STUDY ACCESSION'] = Cat_Anc_wBroader['STUDY ACCESSION'].astype(str)

        merged = pd.merge(Cat_StudMap, Cat_Anc_wBroader, how='left', on='STUDY ACCESSION')
        merged['DATE'] = pd.to_datetime(merged['DATE'], errors='coerce')
        merged['_Year'] = merged['DATE'].dt.year

        # original filters
        merged = merged[merged['Broader'].notnull() & merged['parentterm'].notnull()]
        if merged.empty:
            raise RuntimeError('merged is empty after join to ancestry')

        year_cov = (merged.groupby('_Year')['parentterm'].apply(lambda s: s.notna().mean()).sort_index())
        diversity_logger.debug(f'parentterm coverage by year (tail): {year_cov.tail(5).to_dict()}')
        diversity_logger.debug(f'Years present in merged (tail): {sorted(merged["_Year"].dropna().unique().tolist())[-10:]}')

        # ---------- Build output (same schema) ----------
        cols = ['Broader','parentterm','Year',
                'InitialN','InitialCount','ReplicationN','ReplicationCount',
                'InitialAssociationSum']
        doughnut_df = pd.DataFrame(columns=cols)

        counter = 0
        for year in range(2008, final_year + 1):
            year_df = merged[merged['_Year'] == year]
            if year_df.empty:
                continue

            for ancestry in year_df['Broader'].unique():
                anc_df = year_df[year_df['Broader'] == ancestry]

                # "All"
                rep_anc = anc_df.loc[anc_df['STAGE'] == 'replication', 'N'].sum()
                rep_tot = year_df.loc[year_df['STAGE'] == 'replication', 'N'].sum()
                init_anc = anc_df.loc[anc_df['STAGE'] == 'initial', 'N'].sum()
                init_tot = year_df.loc[year_df['STAGE'] == 'initial', 'N'].sum()

                init_ass_anc = anc_df.loc[anc_df['STAGE'] == 'initial', 'ASSOCIATION COUNT'].sum()
                init_ass_tot = year_df.loc[year_df['STAGE'] == 'initial', 'ASSOCIATION COUNT'].sum()

                doughnut_df.loc[counter] = [
                    ancestry, 'All', year,
                    (init_anc / init_tot * 100) if init_tot else np.nan,
                    (len(anc_df[anc_df['STAGE'] == 'initial']) /
                     len(year_df[year_df['STAGE'] == 'initial']) * 100) if len(year_df[year_df['STAGE'] == 'initial']) else np.nan,
                    (rep_anc / rep_tot * 100) if rep_tot else np.nan,
                    (len(anc_df[anc_df['STAGE'] == 'replication']) /
                     len(year_df[year_df['STAGE'] == 'replication']) * 100) if len(year_df[year_df['STAGE'] == 'replication']) else np.nan,
                    (init_ass_anc / init_ass_tot * 100) if init_ass_tot else np.nan
                ]
                counter += 1

                # Per-parent rows
                for parent in year_df['parentterm'].dropna().unique():
                    parent_df = year_df[year_df['parentterm'] == parent]
                    anc_parent_df = parent_df[parent_df['Broader'] == ancestry]

                    rep_anc = anc_parent_df.loc[anc_parent_df['STAGE'] == 'replication', 'N'].sum()
                    rep_tot = parent_df.loc[parent_df['STAGE'] == 'replication', 'N'].sum()
                    init_anc = anc_parent_df.loc[anc_parent_df['STAGE'] == 'initial', 'N'].sum()
                    init_tot = parent_df.loc[parent_df['STAGE'] == 'initial', 'N'].sum()

                    init_ass_anc = anc_parent_df.loc[anc_parent_df['STAGE'] == 'initial', 'ASSOCIATION COUNT'].sum()
                    init_ass_tot = parent_df.loc[parent_df['STAGE'] == 'initial', 'ASSOCIATION COUNT'].sum()

                    doughnut_df.loc[counter] = [
                        ancestry, parent, year,
                        (init_anc / init_tot * 100) if init_tot else np.nan,
                        (len(anc_parent_df[anc_parent_df['STAGE'] == 'initial']) /
                         len(parent_df[parent_df['STAGE'] == 'initial']) * 100) if len(parent_df[parent_df['STAGE'] == 'initial']) else np.nan,
                        (rep_anc / rep_tot * 100) if rep_tot else np.nan,
                        (len(anc_parent_df[anc_parent_df['STAGE'] == 'replication']) /
                         len(parent_df[parent_df['STAGE'] == 'replication']) * 100) if len(parent_df[parent_df['STAGE'] == 'replication']) else np.nan,
                        (init_ass_anc / init_ass_tot * 100) if init_ass_tot else np.nan
                    ]
                    counter += 1

        doughnut_df['Broader'] = doughnut_df['Broader'].str.replace(
            'Hispanic/Latin American', 'Hispanic/L.A.', regex=False
        )
        diversity_logger.debug(f'doughnut_df shape={doughnut_df.shape}, last year={doughnut_df["Year"].max() if not doughnut_df.empty else None}')


        doughnut_df['Value'] = doughnut_df['InitialN']  # default donut metric

        cols_legacy = [
            'parentterm', 'Broader', 'Year',
            'InitialN', 'InitialCount',
            'ReplicationN', 'ReplicationCount',
            'InitialAssociationSum',
            'Value'
        ]

        # enforce exact column order and clean NaNs/Infs
        doughnut_df.columns = [str(c) for c in doughnut_df.columns]
        doughnut_df = doughnut_df[cols_legacy].replace([np.inf, -np.inf], np.nan).fillna(0)

        # write + quick sanity logs
        out = os.path.join(data_path, 'toplot', 'doughnut_df.csv')
        doughnut_df.to_csv(out, index=False)
        diversity_logger.debug("doughnut_df header: %s", ",".join(doughnut_df.columns))
        diversity_logger.debug("doughnut_df first row: %s", doughnut_df.head(1).to_dict(orient='records'))

        diversity_logger.info('Build of the doughnut datasets: Complete')

    except Exception:
        diversity_logger.exception('Build of the doughnut datasets: Failed')
        raise




def make_bubbleplot_df(data_path):
    """ Make data for the bubbleplot """
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               sep='\t',
                               usecols=['STUDY ACCESSION', 'DISEASE/TRAIT',
                                        'COHORT', 'JOURNAL'])
        study_metadata = Cat_Stud[
            ['STUDY ACCESSION', 'COHORT', 'JOURNAL']
        ].copy()

        def join_metadata(values):
            unique_values = []
            for value in values:
                if pd.isna(value):
                    continue
                for item in str(value).split('|'):
                    item = item.strip()
                    if item and item not in unique_values:
                        unique_values.append(item)
            return ' | '.join(unique_values)

        study_metadata = study_metadata.groupby(
            'STUDY ACCESSION', as_index=False, sort=False
        ).agg({'COHORT': join_metadata, 'JOURNAL': join_metadata})
        Cat_Stud = Cat_Stud[['STUDY ACCESSION', 'DISEASE/TRAIT']]
        Cat_Map = pd.read_csv(os.path.join(data_path, 'catalog',
                                           'raw', 'Cat_Map.tsv'),
                              sep='\t',
                              usecols = ['Disease trait', 'Parent term'])
        Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                               left_on='DISEASE/TRAIT',
                               right_on='Disease trait')
        Cat_StudMap.to_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                        'Disease_to_Parent_Mappings.tsv'),
                           sep='\t')
        Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION', 'DISEASE/TRAIT']]
        Cat_StudMap = Cat_StudMap.drop_duplicates()
        Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path, 'catalog',
                                                    'synthetic',
                                                    'Cat_Anc_wBroader.tsv'),
                                       sep='\t',
                                       index_col=False, parse_dates=['DATE'])
        merged = pd.merge(Cat_StudMap, Cat_Anc_wBroader, how='left', on='STUDY ACCESSION')
        merged["AUTHOR"] = merged["FIRST AUTHOR"]
        merged = merged[["Broader", "N", "PUBMEDID", "AUTHOR", "DISEASE/TRAIT",
                         "STAGE", 'DATE', "STUDY ACCESSION", "parentterm"]]
        merged = merged[merged["parentterm"].notnull()]
        merged = merged.rename(columns={'DISEASE/TRAIT': 'DiseaseOrTrait'})
        merged = merged[merged['Broader'] != 'In Part Not Recorded']
        merged = merged.rename(columns={"STUDY ACCESSION": "ACCESSION"})
        merged['DiseaseOrTrait'] = merged['DiseaseOrTrait'].astype(str)
        merged["parentterm"] = merged["parentterm"].astype(str)
        make_disease_list(merged, data_path)
        merged = merged.groupby(["Broader", "N", "PUBMEDID", "AUTHOR", "STAGE",
                                 "DATE",  "DiseaseOrTrait","ACCESSION"])['parentterm'].\
            apply(', '.join).reset_index()
        merged = merged.groupby(["Broader", "N", "PUBMEDID", "AUTHOR",
                                 "parentterm", "STAGE", "DATE","ACCESSION"])['DiseaseOrTrait'].\
            apply(', '.join).reset_index()
        merged = pd.merge(
            merged, study_metadata, how='left',
            left_on='ACCESSION', right_on='STUDY ACCESSION'
        ).drop(columns=['STUDY ACCESSION'])
        merged[['COHORT', 'JOURNAL']] = merged[
            ['COHORT', 'JOURNAL']
        ].fillna('')
        merged = merged.sort_values(by='DATE', ascending=True)
        merged['DiseaseOrTrait'] = merged['DiseaseOrTrait'].\
            apply(lambda x: x.encode('ascii', 'ignore').decode('ascii'))
        merged['cssclassname'] = merged['Broader'].str.replace(r'/', '-', regex=False).str. \
                                     replace(r'\s', '-', regex=True).str.lower() + " " + \
                                 merged['parentterm'].str.replace(r',\s+', ',', regex=True).str. \
                                     replace(r'\s', '-', regex=True).str. \
                                     replace(',', ' ', regex=False).str.lower()
        merged['DiseaseOrTrait'] = merged['DiseaseOrTrait'].str. \
            replace('>', 'more than', regex=False).str. \
            replace('<', 'less than', regex=False)
        merged['trait'] = merged['DiseaseOrTrait'].str. \
            replace(r'\s', '-', regex=True).str. \
            replace('(', '-', regex=False).str. \
            replace(')', '-', regex=False).str.lower()

        merged.to_csv(os.path.join(data_path, 'toplot', 'bubble_df.csv'))
        diversity_logger.info('Build of the bubble datasets: Complete')
    except Exception as e:
        diversity_logger.exception(f'Build of the bubble datasets: Failed -- {e}')
        raise


def update_static_bundle(bundle_path, source_path, archive_name, logger):
    """Atomically replace one file in a ZIP, without duplicate members."""
    if not os.path.isfile(bundle_path):
        logger.warning('Static data bundle not found: %s', bundle_path)
        return False

    with open(source_path, 'rb') as source_file:
        replacement = source_file.read()

    with zipfile.ZipFile(bundle_path, 'r') as bundle:
        matching = [item for item in bundle.infolist()
                    if item.filename == archive_name]
        if len(matching) == 1 and bundle.read(matching[0]) == replacement:
            return False

    bundle_directory = os.path.dirname(os.path.abspath(bundle_path))
    descriptor, temporary_path = tempfile.mkstemp(
        prefix='.data_static.', suffix='.zip', dir=bundle_directory
    )
    os.close(descriptor)

    try:
        with zipfile.ZipFile(bundle_path, 'r') as old_bundle, \
                zipfile.ZipFile(temporary_path, 'w') as new_bundle:
            new_bundle.comment = old_bundle.comment
            replaced = False
            for item in old_bundle.infolist():
                if item.filename == archive_name:
                    if not replaced:
                        new_bundle.write(source_path, archive_name,
                                         compress_type=item.compress_type)
                        replaced = True
                    continue

                if item.is_dir():
                    new_bundle.writestr(item, b'')
                else:
                    with old_bundle.open(item, 'r') as old_member, \
                            new_bundle.open(item, 'w') as new_member:
                        shutil.copyfileobj(old_member, new_member)

            if not replaced:
                new_bundle.write(source_path, archive_name,
                                 compress_type=zipfile.ZIP_DEFLATED)

        shutil.copymode(bundle_path, temporary_path)
        os.replace(temporary_path, bundle_path)
    except Exception:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise

    logger.info('Updated %s in %s', archive_name, bundle_path)
    return True


def reconcile_broader_ancestry(Cat_Anc, dictionary_path, unmapped_path,
                               logger, static_bundle_path=None):
    """Apply and extend the existing broad-ancestry dictionary.

    Existing unambiguous dictionary rows remain authoritative. Previously
    unseen combinations are classified from their component terms, and only
    Broader values already present in the dictionary can be emitted. Successful
    inferences are persisted as exact mappings for auditability and reuse.
    """
    cleaner_broad = pd.read_csv(dictionary_path, sep='\t', header=0,
                                index_col=False, dtype=str)
    required = {'BROAD ANCESTRAL', 'Broader'}
    missing = required - set(cleaner_broad.columns)
    if missing:
        raise KeyError(
            f'{dictionary_path}: missing required columns: {sorted(missing)}'
        )

    aliases = {
        'hispanic/latin american': 'Hispanic or Latin American',
        'otheradmixed ancestry': 'Other admixed ancestry',
        'unspecified': 'NR'
    }

    def normalize_term(term):
        cleaned = re.sub(r'\s+', ' ', str(term).replace('\u00a0', ' ')).strip()
        return aliases.get(cleaned.casefold(), cleaned)

    def split_terms(value):
        """Split top-level commas, preserving commas inside parentheses."""
        if pd.isna(value) or not str(value).strip():
            return ()
        text = str(value).strip()
        terms, start, depth = [], 0, 0
        for index, character in enumerate(text):
            if character == '(':
                depth += 1
            elif character == ')':
                depth = max(0, depth - 1)
            elif character == ',' and depth == 0:
                term = normalize_term(text[start:index])
                if term:
                    terms.append(term)
                start = index + 1
        final_term = normalize_term(text[start:])
        if final_term:
            terms.append(final_term)
        return tuple(terms)

    def ordered_key(value):
        return tuple(term.casefold() for term in split_terms(value))

    def collect_dictionary_state(dictionary):
        exact = {}
        signatures = {}
        components = {}
        allowed = set()
        for broad_ancestral, broader in dictionary[
                ['BROAD ANCESTRAL', 'Broader']].itertuples(
                    index=False, name=None):
            if pd.isna(broad_ancestral) or pd.isna(broader):
                continue
            key = ordered_key(broad_ancestral)
            broader = str(broader).strip()
            if not key or not broader:
                continue
            signature = tuple(sorted(set(key)))
            allowed.add(broader)
            exact.setdefault(key, set()).add(broader)
            signatures.setdefault(signature, set()).add(broader)
            if len(signature) == 1:
                components.setdefault(signature[0], set()).add(broader)
        return exact, signatures, components, allowed

    exact_candidates, signature_candidates, component_candidates, \
        allowed_broader = collect_dictionary_state(cleaner_broad)
    component_mappings = {
        key: next(iter(values)) for key, values in component_candidates.items()
        if len(values) == 1
    }

    def classification_result(broader, method, unknown_terms=()):
        if broader not in allowed_broader:
            return None, 'unresolved-output-not-configured', ()
        return broader, method, unknown_terms

    def classify_components(key, source_terms):
        unknown_terms = tuple(dict.fromkeys(
            term for term, component in zip(source_terms, key)
            if component not in component_mappings
        ))
        if unknown_terms:
            return None, 'unresolved-unknown-component', unknown_terms

        not_recorded = 'In Part Not Recorded'
        recorded_groups = {
            component_mappings[component] for component in key
        }
        has_not_recorded = not_recorded in recorded_groups
        recorded_groups.discard(not_recorded)

        african_groups = {
            'African', 'African American or Afro-Caribbean'
        }
        if ('African' in recorded_groups
                and recorded_groups <= african_groups):
            recorded_groups = {'African'}

        if not recorded_groups:
            return classification_result(not_recorded, 'rule-not-recorded')
        if has_not_recorded and len(recorded_groups) == 1:
            return classification_result(not_recorded,
                                         'rule-partly-not-recorded')
        if len(recorded_groups) == 1 and not has_not_recorded:
            return classification_result(next(iter(recorded_groups)),
                                         'rule-homogeneous')
        return classification_result('Other/Mixed', 'rule-mixed')

    dictionary_changed = False
    conflict_repairs = {}
    conflict_labels = {}
    unresolved_conflicts = []
    for key, values in exact_candidates.items():
        if len(values) <= 1:
            continue
        result = classify_components(key, key)
        if result[0] is None:
            unresolved_conflicts.append(', '.join(key))
        else:
            conflict_repairs[key] = result[0]

    if conflict_repairs:
        repaired_rows = []
        repaired_keys = set()
        for _, row in cleaner_broad.iterrows():
            key = ordered_key(row['BROAD ANCESTRAL'])
            if key not in conflict_repairs:
                repaired_rows.append(row)
                continue
            if key in repaired_keys:
                continue
            repaired_row = row.copy()
            repaired_row['Broader'] = conflict_repairs[key]
            repaired_rows.append(repaired_row)
            repaired_keys.add(key)
            conflict_labels[key] = str(row['BROAD ANCESTRAL']).strip()

        cleaner_broad = pd.DataFrame(
            repaired_rows, columns=cleaner_broad.columns
        ).reset_index(drop=True)
        dictionary_changed = True
        logger.info(
            'Permanently repaired %d conflicting ancestry dictionary '
            'entries:\n%s',
            len(conflict_repairs),
            '\n'.join(
                f'{conflict_labels[key]} -> {broader}'
                for key, broader in conflict_repairs.items()
            )
        )

        exact_candidates, signature_candidates, component_candidates, \
            allowed_broader = collect_dictionary_state(cleaner_broad)
        component_mappings = {
            key: next(iter(values))
            for key, values in component_candidates.items()
            if len(values) == 1
        }

    if unresolved_conflicts:
        logger.warning(
            'Could not safely repair conflicting ancestry dictionary '
            'entries: %s', '; '.join(sorted(unresolved_conflicts))
        )

    exact_mappings = {
        key: next(iter(values)) for key, values in exact_candidates.items()
        if len(values) == 1
    }
    signature_mappings = {
        key: next(iter(values)) for key, values in signature_candidates.items()
        if len(values) == 1
    }
    dictionary_keys = set(exact_candidates)

    def classify(value):
        key = ordered_key(value)
        if not key:
            return None, 'unresolved-empty', ()
        if key in exact_mappings:
            return classification_result(exact_mappings[key],
                                         'dictionary-exact')

        signature = tuple(sorted(set(key)))
        if signature in signature_mappings:
            return classification_result(signature_mappings[signature],
                                         'dictionary-reordered')
        return classify_components(key, split_terms(value))

    Cat_Anc = Cat_Anc.copy()
    source_column = 'BROAD ANCESTRAL'
    Cat_Anc[source_column] = Cat_Anc[source_column].astype(str).str.strip()
    unique_terms = Cat_Anc[source_column].drop_duplicates().tolist()
    results = {term: classify(term) for term in unique_terms}
    Cat_Anc['Broader'] = Cat_Anc[source_column].map(
        lambda term: results[term][0]
    )

    inferred = [
        (term, result[0], result[1])
        for term, result in results.items()
        if result[0] is not None and ordered_key(term) not in dictionary_keys
    ]
    if inferred:
        additions = pd.DataFrame(
            [(term, broader) for term, broader, _ in inferred],
            columns=['BROAD ANCESTRAL', 'Broader']
        ).sort_values('BROAD ANCESTRAL')
        cleaner_broad = pd.concat(
            [cleaner_broad, additions], ignore_index=True
        ).drop_duplicates()
        dictionary_changed = True
        logger.info(
            'Automatically added %d ancestry mappings to %s:\n%s',
            len(additions), dictionary_path,
            '\n'.join(
                f'{term} -> {broader} ({method})'
                for term, broader, method in sorted(inferred)
            )
        )

    if dictionary_changed:
        cleaner_broad.to_csv(dictionary_path, sep='\t', index=False)

    if static_bundle_path:
        try:
            update_static_bundle(
                static_bundle_path,
                dictionary_path,
                'support/dict_replacer_broad.tsv',
                logger
            )
        except (OSError, zipfile.BadZipFile) as error:
            logger.warning(
                'Could not persist ancestry mappings to %s: %s',
                static_bundle_path, error
            )

    unresolved = [
        (term, result)
        for term, result in results.items()
        if result[0] is None
    ]
    unmapped_directory = os.path.dirname(unmapped_path)
    if unmapped_directory:
        os.makedirs(unmapped_directory, exist_ok=True)
    pd.Series(
        sorted(term for term, _ in unresolved),
        name=source_column,
        dtype='object'
    ).to_csv(unmapped_path, index=False)

    if unresolved:
        logger.warning(
            'Need to update dictionary terms; %d ancestry combinations could '
            'not be classified safely:\n%s',
            len(unresolved),
            '\n'.join(
                f'{term} ({result[1]}'
                + (
                    f": {', '.join(result[2])}"
                    if result[2] else ''
                )
                + ')'
                for term, result in sorted(unresolved)
            )
        )
    else:
        logger.info('No missing Broader terms! Nice!')

    return Cat_Anc


def clean_gwas_cat(data_path, static_bundle_path=None):
    """ Clean the catalog and do some general preprocessing """
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               header=0,
                               sep='\t',
                               encoding='utf-8',
                               index_col=False)
        Cat_Stud.fillna('N/A', inplace=True)
        Cat_Anc = pd.read_csv(os.path.join(data_path, 'catalog',
                                           'raw', 'Cat_Anc.tsv'),
                              header=0,
                              sep='\t',
                              encoding='utf-8',
                              index_col=False)
        Cat_Anc.rename(columns={'BROAD ANCESTRAL CATEGORY': 'BROAD ANCESTRAL',
                                'NUMBER OF INDIVDUALS': 'N'},
                       inplace=True)
        Cat_Anc = Cat_Anc[~Cat_Anc['BROAD ANCESTRAL'].isnull()]
        Cat_Anc.columns = Cat_Anc.columns.str.replace('ACCCESSION', 'ACCESSION')
        Cat_Anc_byN = Cat_Anc[['STUDY ACCESSION', 'N', 'DATE']].groupby(by='STUDY ACCESSION').sum()
        Cat_Anc_byN = Cat_Anc_byN.reset_index()
        Cat_Anc_byN = pd.merge(Cat_Anc_byN,
                               Cat_Stud[['STUDY ACCESSION', 'DATE']],
                               how='left', on='STUDY ACCESSION')
        dictionary_path = os.path.join(data_path, 'support',
                                       'dict_replacer_broad.tsv')
        unmapped_path = os.path.join(data_path, 'unmapped',
                                     'unmapped_broader.txt')
        if static_bundle_path is None:
            static_bundle_path = os.path.join(
                os.path.dirname(os.path.abspath(data_path)), 'data_static.zip'
            )
        Cat_Anc = reconcile_broader_ancestry(
            Cat_Anc, dictionary_path, unmapped_path, diversity_logger,
            static_bundle_path
        )
        Cat_Anc['Dates'] = [pd.to_datetime(d) for d in Cat_Anc['DATE']]
        Cat_Anc['N'] = pd.to_numeric(Cat_Anc['N'], errors='coerce')
        Cat_Anc = Cat_Anc[Cat_Anc['N'].notnull()]
        Cat_Anc['N'] = Cat_Anc['N'].astype(int)
        Cat_Anc = Cat_Anc.sort_values(by='Dates')
        #Cat_Anc = Cat_Anc[Cat_Anc['Broader'].notnull()]
        #Cat_Anc = Cat_Anc[Cat_Anc['N'].notnull()]
        Cat_Anc.to_csv(os.path.join(data_path, 'catalog', 'synthetic', 'Cat_Anc_wBroader.tsv'),
                       sep='\t',
                       index=False)
        diversity_logger.info('Clean of the raw GWAS Catalog datasets: Complete')
    except Exception as e:
        diversity_logger.exception(
            f'Clean of the raw GWAS Catalog datasets: Failed -- {e}'
        )
        raise


def make_clean_CoR(Cat_Anc, data_path):
    """
    Clean the country of recruitment WITHOUT exploding multi-country entries.
    Deterministically select the first nonempty, non-'NR' token as the
    canonical country. One record in -> one record out.
    """
    try:
        req_base = ['DATE', 'PUBMEDID', 'COUNTRY OF RECRUITMENT']
        missing = [c for c in req_base if c not in Cat_Anc.columns]
        if missing:
            raise KeyError(f"Cat_Anc missing columns: {missing}")

        df = Cat_Anc[req_base].copy()

        # N: accept 'N' (synthetic) or 'NUMBER OF INDIVDUALS' (raw)
        if 'N' in Cat_Anc.columns:
            df['N'] = pd.to_numeric(Cat_Anc['N'], errors='coerce')
        elif 'NUMBER OF INDIVDUALS' in Cat_Anc.columns:
            df['N'] = pd.to_numeric(Cat_Anc['NUMBER OF INDIVDUALS'], errors='coerce')
        else:
            df['N'] = pd.NA
        df['N'] = df['N'].fillna(0)

        # Standardize delimiters then select first usable token
        s = (df['COUNTRY OF RECRUITMENT'].astype(str)
                                      .str.replace('[;|]', ',', regex=True))

        def first_token(x: str) -> str:
            for t in x.split(','):
                t = t.strip()
                if t and t != 'NR':
                    return t
            return ''

        df['Cleaned Country'] = s.apply(first_token)
        df = df[df['Cleaned Country'] != '']

        # Harmonize common variants
        repl = {
            'U.S.': 'United States',
            'U.K.': 'United Kingdom',
            'Gambia': 'Gambia, The',
            'Republic of Korea': 'Korea, South',
            'Czech Republic': 'Czechia',
            'Russian Federation': 'Russia',
            r'Iran \(Islamic Republic of\)': 'Iran',
            'Viet Nam': 'Vietnam',
            'United Republic of Tanzania': 'Tanzania',
            'Republic of Ireland': 'Ireland',
            r'Micronesia \(Federated States of\)': 'Micronesia, Federated States of',
        }
        for k, v in repl.items():
            df['Cleaned Country'] = df['Cleaned Country'].str.replace(k, v, regex=True)

        # Persist (same schema/paths as before)
        out_csv = os.path.join(data_path, 'catalog', 'synthetic', 'ancestry_CoR.csv')
        df[['DATE', 'PUBMEDID', 'N', 'Cleaned Country']].rename(columns={'DATE': 'Date'}).to_csv(out_csv, index=False)

        out_tsv = os.path.join(data_path, 'catalog', 'synthetic', 'GWAScatalogue_CleanedCountry.tsv')
        df[['DATE', 'PUBMEDID', 'N', 'Cleaned Country']].rename(columns={'DATE': 'Date'}).to_csv(out_tsv, sep='\t', index=False)

        # Return with 'Date' column name for downstream
        return df.rename(columns={'DATE': 'Date'})

    except Exception:
        diversity_logger.exception('Clean of the raw Country datasets: Failed')
        raise



def _safe_filename(resp, fallback):
    """
    Extract filename from Content-Disposition if present; otherwise use fallback.
    Handles both filename= and RFC5987 filename*=
    """
    cd = resp.headers.get('Content-Disposition', '') or ''
    try:
        if 'filename*=' in cd:
            # e.g. attachment; filename*=UTF-8''Cat_Stud.tsv
            part = cd.split('filename*=', 1)[1].split(';', 1)[0].strip().strip('"')
            name = part.split("''", 1)[-1]
            return name or fallback
        if 'filename=' in cd:
            # e.g. attachment; filename="Cat_Stud.tsv"
            part = cd.split('filename=', 1)[1].split(';', 1)[0].strip().strip('"')
            return part or fallback
    except Exception:
        pass
    return fallback


def download_cat(data_path, ebi_download):
    """Download and validate the current GWAS Catalog release files."""
    try:
        raw_dir = os.path.join(data_path, 'catalog', 'raw')
        os.makedirs(raw_dir, exist_ok=True)

        http_endpoints = [
            ('studies/v1.0.3.1', 'Cat_Stud.tsv', {'STUDY ACCESSION'}),
            ('ancestry', 'Cat_Anc.tsv', {'BROAD ANCESTRAL CATEGORY'}),
            ('associations/v1.0?split=false', 'Cat_Full.tsv', {'P-VALUE'}),
        ]

        for endpoint, fallback_name, required_headers in http_endpoints:
            url = ebi_download + endpoint
            with requests.get(url, timeout=(15, 300), stream=True) as r:
                r.raise_for_status()
                server_name = _safe_filename(r, fallback_name)
                out_path = os.path.join(raw_dir, fallback_name)
                descriptor, download_path = tempfile.mkstemp(
                    prefix='.gwas_download.', dir=raw_dir
                )
                extracted_path = None
                archive_member = None

                try:
                    with os.fdopen(descriptor, 'wb') as download_file:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                download_file.write(chunk)

                    candidate_path = download_path
                    if zipfile.is_zipfile(download_path):
                        with zipfile.ZipFile(download_path, 'r') as archive:
                            tsv_members = [
                                item for item in archive.infolist()
                                if not item.is_dir()
                                and item.filename.lower().endswith('.tsv')
                            ]
                            if len(tsv_members) != 1:
                                raise ValueError(
                                    f'{url}: expected one TSV in archive, '
                                    f'found {len(tsv_members)}'
                                )
                            archive_member = tsv_members[0].filename
                            extracted_descriptor, extracted_path = \
                                tempfile.mkstemp(
                                    prefix='.gwas_extracted.', dir=raw_dir
                                )
                            with os.fdopen(extracted_descriptor, 'wb') as \
                                    extracted_file, \
                                    archive.open(tsv_members[0], 'r') as \
                                    archived_file:
                                shutil.copyfileobj(archived_file,
                                                   extracted_file)
                            candidate_path = extracted_path

                    with open(candidate_path, 'rb') as candidate_file:
                        header = candidate_file.readline().decode(
                            'utf-8-sig'
                        ).rstrip('\r\n').split('\t')
                    missing_headers = required_headers - set(header)
                    if missing_headers:
                        raise ValueError(
                            f'{url}: downloaded file is missing columns '
                            f'{sorted(missing_headers)}'
                        )

                    os.replace(candidate_path, out_path)
                    if candidate_path == extracted_path:
                        extracted_path = None
                    else:
                        download_path = None
                finally:
                    for temporary_path in (download_path, extracted_path):
                        if temporary_path and os.path.exists(temporary_path):
                            os.unlink(temporary_path)

                archive_detail = (
                    f'; archive member: {archive_member}'
                    if archive_member else ''
                )
                diversity_logger.info(
                    f'Download of {endpoint}: Complete '
                    f'(saved as {fallback_name}; server filename: '
                    f'{server_name}{archive_detail})'
                )

        # FTP: trait mappings
        requests_ftp.monkeypatch_session()
        s = requests.Session()
        ftpsite = 'ftp://ftp.ebi.ac.uk'
        subdom = '/pub/databases/gwas/releases/latest/'
        file = 'gwas-efo-trait-mappings.tsv'
        r = s.get(ftpsite + subdom + file, timeout=60)
        r.raise_for_status()
        out_path = os.path.join(raw_dir, 'Cat_Map.tsv')
        descriptor, temporary_path = tempfile.mkstemp(
            prefix='.gwas_mapping.', dir=raw_dir
        )
        try:
            with os.fdopen(descriptor, 'wb') as mapping_file:
                mapping_file.write(r.content)
            with open(temporary_path, 'rb') as mapping_file:
                header = mapping_file.readline().decode(
                    'utf-8-sig'
                ).rstrip('\r\n').split('\t')
            missing_headers = RAW_REQUIRED_COLUMNS[
                'catalog/raw/Cat_Map.tsv'
            ] - set(header)
            if missing_headers:
                raise ValueError(
                    'Downloaded trait mappings are missing columns '
                    f'{sorted(missing_headers)}'
                )
            os.replace(temporary_path, out_path)
            temporary_path = None
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)
        diversity_logger.info('Download of efo-trait-mappings: Complete')

    except Exception:
        diversity_logger.exception('Problem downloading Catalog data!')
        raise


def make_disease_list(df, data_path):
    """ Makes a unique list of diseases and traits """
    uniq_dis_trait = pd.Series(df['DiseaseOrTrait'].unique())
    uniq_dis_trait.to_csv(os.path.join(data_path, 'summary', 'uniq_dis_trait.txt'),
                          header=False,
                          index=False)


def make_parent_list(data_path):
    """ Makes a unique list of parent terms """
    df = pd.read_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                  'Cat_Anc_wBroader_withParents.tsv'),
                     sep='\t')
    uniq_parent = pd.Series(df[df['parentterm'].
                               notnull()]['parentterm'].unique())
    uniq_parent.to_csv(os.path.join(data_path, 'summary', 'uniq_parent.txt'),
                       header=False,
                       index=False)


def _write_reproducible_zip(destination, source, members):
    """Write a byte-reproducible, uncompressed archive in fixed order."""
    with zipfile.ZipFile(destination, 'w') as archive:
        for file_name in members:
            source_path = os.path.join(source, file_name)
            member = zipfile.ZipInfo(file_name, (1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = 0o100664 << 16
            member.compress_type = zipfile.ZIP_STORED
            with open(source_path, 'rb') as source_file, \
                    archive.open(member, 'w') as member_file:
                shutil.copyfileobj(source_file, member_file, 1024 * 1024)


def _reuse_or_write_zip(destination, source, members, previous_path=None):
    if previous_path and os.path.isfile(previous_path):
        try:
            _validate_zip(previous_path, members, source)
        except (OSError, ValueError, zipfile.BadZipFile):
            pass
        else:
            shutil.copy2(previous_path, destination)
            return
    _write_reproducible_zip(destination, source, members)


def zip_for_download(source, destination, previous_destination=None):
    """Build download archives from the fixed generated-file manifest."""
    all_path = os.path.join(destination, 'gwasdiversitymonitor_download.zip')
    heat_path = os.path.join(destination, 'heatmap.zip')
    ts_path = os.path.join(destination, 'timeseries.zip')
    try:
        os.makedirs(destination, exist_ok=True)
        missing = [
            file_name for file_name in TOPLOT_OUTPUT_FILES
            if not os.path.isfile(os.path.join(source, file_name))
        ]
        if missing:
            raise FileNotFoundError(
                f'Cannot build download archives; missing files: {missing}'
            )

        previous_all_path = previous_heat_path = previous_ts_path = None
        if previous_destination:
            previous_all_path = os.path.join(
                previous_destination, 'gwasdiversitymonitor_download.zip'
            )
            previous_heat_path = os.path.join(
                previous_destination, 'heatmap.zip'
            )
            previous_ts_path = os.path.join(
                previous_destination, 'timeseries.zip'
            )

        heat_members = tuple(
            name for name in TOPLOT_OUTPUT_FILES
            if name.lower().startswith('heat')
        )
        timeseries_members = tuple(
            name for name in TOPLOT_OUTPUT_FILES
            if name.lower().startswith('ts')
        )
        _reuse_or_write_zip(
            all_path, source, TOPLOT_OUTPUT_FILES, previous_all_path
        )
        _reuse_or_write_zip(
            heat_path, source, heat_members, previous_heat_path
        )
        _reuse_or_write_zip(
            ts_path, source, timeseries_members, previous_ts_path
        )
        diversity_logger.info('Build of the zipped Datasets: Complete')
    except Exception as e:
        diversity_logger.exception(f'Build of the zipped datasets: Failed -- {e}')
        raise

def determine_year(day):
    """ Determines year, day is a datetime.date obj"""
    return day.year if math.ceil(day.month/3.) > 2 else day.year-1


def _generation_parameters():
    """Return non-file inputs which can change generated output."""
    current_final_year = globals().get(
        'final_year', determine_year(datetime.date.today())
    )
    return {'final_year': int(current_final_year)}

def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _file_fingerprint(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    size = os.path.getsize(path)
    if size <= 0:
        raise ValueError(f'Required file is empty: {path}')
    return {'sha256': _sha256_file(path), 'size': size}


def _fingerprint_files(root, relative_paths):
    return {
        relative_path: _file_fingerprint(
            os.path.join(root, relative_path)
        )
        for relative_path in relative_paths
    }


def _fsync_directory(path):
    """Flush directory metadata after an atomic rename or unlink."""
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix='.generate_data.', suffix='.json', dir=directory
    )
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output_file:
            json.dump(payload, output_file, indent=2, sort_keys=True)
            output_file.write('\n')
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(directory)
        temporary_path = None
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _read_json(path):
    with open(path, encoding='utf-8') as input_file:
        return json.load(input_file)


def _fingerprints_match(root, fingerprints):
    try:
        for relative_path, expected in fingerprints.items():
            path = os.path.join(root, relative_path)
            if not os.path.isfile(path):
                return False
            if os.path.getsize(path) != expected.get('size'):
                return False
            if _sha256_file(path) != expected.get('sha256'):
                return False
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return False
    return True


def _implementation_fingerprints(repository_path):
    implementation_files = (
        'generate_data.py',
        'app/DataLoader.py',
        'funder_pipeline.py',
        'data/funders/funder_cleaner.json',
    )
    return _fingerprint_files(repository_path, implementation_files)


def _expected_published_files(data_path):
    return PUBLISHED_DATA_FILES + funder_pipeline.funder_artifact_files(
        data_path
    )


def _validate_raw_inputs(data_path):
    for relative_path, required_columns in RAW_REQUIRED_COLUMNS.items():
        path = os.path.join(data_path, relative_path)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise FileNotFoundError(f'Missing or empty raw input: {path}')
        header = pd.read_csv(path, sep='\t', nrows=0).columns
        missing = required_columns - set(header)
        if missing:
            raise ValueError(
                f'{path}: missing required columns {sorted(missing)}'
            )
        for alternatives in RAW_REQUIRED_COLUMN_ALTERNATIVES.get(
                relative_path, ()):
            if not set(header).intersection(alternatives):
                raise ValueError(
                    f'{path}: missing one of the required column aliases '
                    f'{sorted(alternatives)}'
                )
    return _fingerprint_files(data_path, RAW_INPUT_FILES)


def _zip_member_matches_file(archive, member_name, file_path):
    with archive.open(member_name, 'r') as archived_file, \
            open(file_path, 'rb') as source_file:
        while True:
            archived_block = archived_file.read(1024 * 1024)
            source_block = source_file.read(1024 * 1024)
            if archived_block != source_block:
                return False
            if not archived_block:
                return True


def _validate_zip(path, expected_members, source_directory):
    with zipfile.ZipFile(path, 'r') as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError(f'{path}: contains duplicate members')
        if names != list(expected_members):
            raise ValueError(
                f'{path}: members do not match the generated manifest; '
                f'expected {list(expected_members)}, found {names}'
            )
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f'{path}: corrupt member {bad_member}')
        for member_name in expected_members:
            source_path = os.path.join(source_directory, member_name)
            if not _zip_member_matches_file(
                    archive, member_name, source_path):
                raise ValueError(
                    f'{path}: member differs from generated file: '
                    f'{member_name}'
                )


def validate_generated_release(data_path, static_bundle_path):
    """Validate the complete staged release before any live file is replaced."""
    for relative_path in PUBLISHED_DATA_FILES:
        path = os.path.join(data_path, relative_path)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise FileNotFoundError(
                f'Missing or empty generated artifact: {path}'
            )

    raw_fingerprints = _validate_raw_inputs(data_path)

    required_columns = {
        'catalog/synthetic/Cat_Anc_wBroader.tsv': {
            'BROAD ANCESTRAL', 'Broader', 'STUDY ACCESSION'
        },
        'catalog/synthetic/Cat_Anc_wBroader_withParents.tsv': {
            'Broader', 'parentterm', 'STUDY ACCESSION'
        },
        'toplot/bubble_df.csv': {
            'Broader', 'DiseaseOrTrait', 'STAGE', 'COHORT', 'JOURNAL',
            'FUNDER'
        },
        'toplot/choro_df.csv': {'Cleaned Country', 'Year'},
        'toplot/doughnut_df.csv': {
            'Broader', 'parentterm', 'Year', 'InitialN',
            'ReplicationN'
        },
    }
    for relative_path, columns in required_columns.items():
        separator = '\t' if relative_path.endswith('.tsv') else ','
        actual_columns = set(pd.read_csv(
            os.path.join(data_path, relative_path),
            sep=separator,
            nrows=0
        ).columns)
        missing = columns - actual_columns
        if missing:
            raise ValueError(
                f'{relative_path}: missing generated columns '
                f'{sorted(missing)}'
            )

    ancestry_path = os.path.join(
        data_path, 'catalog', 'synthetic', 'Cat_Anc_wBroader.tsv'
    )
    ancestry_broader = pd.read_csv(
        ancestry_path, sep='\t', usecols=['Broader'], low_memory=False
    )['Broader']
    if ancestry_broader.isna().any():
        raise ValueError(
            'Cat_Anc_wBroader.tsv contains unclassified ancestry rows'
        )

    json_paths = ('summary/summary.json',) + tuple(
        f'toplot/{name}' for name in TOPLOT_JSON_FILES
    )
    for relative_path in json_paths:
        value = _read_json(os.path.join(data_path, relative_path))
        if not isinstance(value, (dict, list)) or not value:
            raise ValueError(f'{relative_path}: generated JSON is empty')

    summary_path = os.path.join(data_path, 'summary', 'summary.json')
    plot_summary_path = os.path.join(data_path, 'toplot', 'summary.json')
    if _sha256_file(summary_path) != _sha256_file(plot_summary_path):
        raise ValueError('summary.json copies differ')

    plot_path = os.path.join(data_path, 'toplot')
    all_members = TOPLOT_OUTPUT_FILES
    heat_members = tuple(
        name for name in TOPLOT_OUTPUT_FILES
        if name.lower().startswith('heat')
    )
    timeseries_members = tuple(
        name for name in TOPLOT_OUTPUT_FILES
        if name.lower().startswith('ts')
    )
    _validate_zip(
        os.path.join(
            data_path, 'todownload',
            'gwasdiversitymonitor_download.zip'
        ),
        all_members,
        plot_path
    )
    _validate_zip(
        os.path.join(data_path, 'todownload', 'heatmap.zip'),
        heat_members,
        plot_path
    )
    _validate_zip(
        os.path.join(data_path, 'todownload', 'timeseries.zip'),
        timeseries_members,
        plot_path
    )

    dictionary_path = os.path.join(
        data_path, 'support', 'dict_replacer_broad.tsv'
    )
    with zipfile.ZipFile(static_bundle_path, 'r') as static_bundle:
        dictionary_members = [
            name for name in static_bundle.namelist()
            if name == 'support/dict_replacer_broad.tsv'
        ]
        if len(dictionary_members) != 1:
            raise ValueError(
                f'{static_bundle_path}: expected exactly one bundled '
                'ancestry dictionary'
            )
        bundled_dictionary = static_bundle.read(dictionary_members[0])
    with open(dictionary_path, 'rb') as dictionary_file:
        if bundled_dictionary != dictionary_file.read():
            raise ValueError(
                'The ancestry dictionary and data_static.zip member differ'
            )

    funder_files = funder_pipeline.validate_funder_artifacts(data_path)
    published_files = PUBLISHED_DATA_FILES + funder_files
    return {
        'raw_fingerprints': raw_fingerprints,
        'artifact_fingerprints': _fingerprint_files(
            data_path, published_files
        ),
        'static_bundle_fingerprint': _file_fingerprint(static_bundle_path),
    }


def _completion_state_valid(data_path, repository_path=None,
                            expected_raw_fingerprints=None,
                            check_implementation=False,
                            honor_publication_marker=True):
    state_path = os.path.join(data_path, GENERATION_STATE_FILE)
    publication_path = os.path.join(
        data_path, GENERATION_CONTROL_DIRECTORY,
        GENERATION_PUBLICATION_FILE
    )
    if honor_publication_marker and os.path.exists(publication_path):
        return False
    if not os.path.isfile(state_path):
        return False
    try:
        state = _read_json(state_path)
        if state.get('version') != GENERATION_STATE_VERSION:
            return False
        if state.get('generation_parameters') != _generation_parameters():
            return False
        if not isinstance(
                state.get('input_static_bundle_fingerprint'), dict):
            return False
        artifacts = state.get('artifact_fingerprints', {})
        if set(artifacts) != set(_expected_published_files(data_path)):
            return False
        if not _fingerprints_match(data_path, artifacts):
            return False
        raw_fingerprints = state.get('raw_fingerprints', {})
        if set(raw_fingerprints) != set(RAW_INPUT_FILES):
            return False
        if expected_raw_fingerprints is not None \
                and raw_fingerprints != expected_raw_fingerprints:
            return False
        if repository_path is not None:
            bundle_path = os.path.join(repository_path, 'data_static.zip')
            if state.get('static_bundle_fingerprint') != \
                    _file_fingerprint(bundle_path):
                return False
        if check_implementation:
            if repository_path is None:
                return False
            if state.get('implementation_fingerprints') != \
                    _implementation_fingerprints(repository_path):
                return False
    except (AttributeError, KeyError, OSError, ValueError, TypeError,
            json.JSONDecodeError):
        return False
    return True


def check_data(data_path):
    """Return True only for a fully published, fingerprinted data release."""
    return _completion_state_valid(os.path.abspath(data_path))


def _generation_paths(data_path):
    control_path = os.path.join(data_path, GENERATION_CONTROL_DIRECTORY)
    workspace_path = os.path.join(
        control_path, GENERATION_WORKSPACE_DIRECTORY
    )
    return {
        'control': control_path,
        'workspace': workspace_path,
        'workspace_data': os.path.join(workspace_path, 'data'),
        'workspace_bundle': os.path.join(workspace_path, 'data_static.zip'),
        'raw_state': os.path.join(
            workspace_path, GENERATION_RAW_STATE_FILE
        ),
        'staged_state': os.path.join(
            workspace_path, GENERATION_STAGED_STATE_FILE
        ),
        'publication': os.path.join(
            control_path, GENERATION_PUBLICATION_FILE
        ),
        'fallback_data': os.path.join(
            control_path, GENERATION_FALLBACK_DIRECTORY
        ),
        'lock': os.path.join(control_path, GENERATION_LOCK_FILE),
    }


@contextlib.contextmanager
def _generation_lock(data_path):
    paths = _generation_paths(data_path)
    os.makedirs(paths['control'], exist_ok=True)
    with open(paths['lock'], 'a+', encoding='utf-8') as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                'Another generate_data.py process is already running'
            ) from error
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _safe_extract_static_bundle(bundle_path, destination):
    destination = os.path.abspath(destination)
    with zipfile.ZipFile(bundle_path, 'r') as archive:
        for member in archive.infolist():
            target = os.path.abspath(os.path.join(destination, member.filename))
            if os.path.commonpath((destination, target)) != destination:
                raise ValueError(
                    f'Unsafe path in {bundle_path}: {member.filename}'
                )
        archive.extractall(destination)


def _workspace_raw_fingerprints(paths):
    if not os.path.isfile(paths['raw_state']):
        return None
    try:
        raw_state = _read_json(paths['raw_state'])
        if raw_state.get('version') != GENERATION_STATE_VERSION:
            return None
        if raw_state.get('generation_failures', 0) >= \
                GENERATION_RAW_FAILURE_LIMIT:
            diversity_logger.warning(
                'The retained raw snapshot failed generation %d times; '
                'discarding that cache and obtaining a fresh snapshot.',
                raw_state['generation_failures']
            )
            return None
        fingerprints = raw_state.get('raw_fingerprints', {})
        if set(fingerprints) != set(RAW_INPUT_FILES):
            return None
        if not _fingerprints_match(paths['workspace_data'], fingerprints):
            return None
        _validate_raw_inputs(paths['workspace_data'])
        return fingerprints
    except (AttributeError, KeyError, OSError, ValueError, TypeError,
            json.JSONDecodeError):
        return None


def _record_workspace_generation_failure(paths):
    """Count deterministic failures so a poisoned raw cache is not eternal."""
    if not os.path.isfile(paths['raw_state']):
        return
    try:
        raw_state = _read_json(paths['raw_state'])
        failures = int(raw_state.get('generation_failures', 0)) + 1
        raw_state['generation_failures'] = failures
        raw_state['last_generation_failure_at'] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        _atomic_write_json(paths['raw_state'], raw_state)
        diversity_logger.warning(
            'Retained raw snapshot generation failure %d of %d; it will be '
            'refreshed after the limit is reached.',
            failures, GENERATION_RAW_FAILURE_LIMIT
        )
    except (AttributeError, OSError, TypeError, ValueError,
            json.JSONDecodeError):
        diversity_logger.exception(
            'Could not record the retained raw snapshot failure count'
        )


def _initialize_generation_workspace(repository_path, data_path,
                                     ebi_download):
    paths = _generation_paths(data_path)
    if os.path.isdir(paths['workspace']):
        shutil.rmtree(paths['workspace'])
    os.makedirs(paths['workspace_data'])

    live_bundle = os.path.join(repository_path, 'data_static.zip')
    if not os.path.isfile(live_bundle):
        raise FileNotFoundError(f'Missing static data bundle: {live_bundle}')
    shutil.copy2(live_bundle, paths['workspace_bundle'])
    input_bundle_fingerprint = _file_fingerprint(live_bundle)
    _safe_extract_static_bundle(
        paths['workspace_bundle'], paths['workspace_data']
    )

    download_cat(paths['workspace_data'], ebi_download)
    raw_fingerprints = _validate_raw_inputs(paths['workspace_data'])
    _atomic_write_json(paths['raw_state'], {
        'version': GENERATION_STATE_VERSION,
        'completed_at': datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        'raw_fingerprints': raw_fingerprints,
        'generation_failures': 0,
        'input_static_bundle_fingerprint': input_bundle_fingerprint,
    })
    return paths, raw_fingerprints


def _prepare_generation_workspace(repository_path, data_path, ebi_download):
    paths = _generation_paths(data_path)
    raw_fingerprints = _workspace_raw_fingerprints(paths)
    if raw_fingerprints is not None:
        diversity_logger.info(
            'Reusing the complete raw input snapshot from the previous '
            'interrupted generation.'
        )
        return paths, raw_fingerprints
    return _initialize_generation_workspace(
        repository_path, data_path, ebi_download
    )


def _reset_workspace_for_wrangling(repository_path, paths):
    live_bundle = os.path.join(repository_path, 'data_static.zip')
    shutil.copy2(live_bundle, paths['workspace_bundle'])
    input_bundle_fingerprint = _file_fingerprint(live_bundle)
    _safe_extract_static_bundle(
        paths['workspace_bundle'], paths['workspace_data']
    )

    raw_state = _read_json(paths['raw_state'])
    raw_state['input_static_bundle_fingerprint'] = \
        input_bundle_fingerprint
    _atomic_write_json(paths['raw_state'], raw_state)

    for relative_directory in (
            'catalog/synthetic', 'toplot', 'todownload', 'unmapped'):
        directory = os.path.join(paths['workspace_data'], relative_directory)
        if os.path.isdir(directory):
            shutil.rmtree(directory)
        os.makedirs(directory)

    for relative_path in SUMMARY_OUTPUT_FILES:
        path = os.path.join(paths['workspace_data'], relative_path)
        if os.path.exists(path):
            os.unlink(path)
    if os.path.exists(paths['staged_state']):
        os.unlink(paths['staged_state'])
        _fsync_directory(os.path.dirname(paths['staged_state']))

    return input_bundle_fingerprint


def _release_timeupdated(paths, raw_fingerprints, previous_data_path):
    """Keep the displayed update time stable when raw bytes are unchanged."""
    try:
        previous_state = _read_json(os.path.join(
            previous_data_path, GENERATION_STATE_FILE
        ))
        if previous_state.get('raw_fingerprints') == raw_fingerprints:
            previous_summary = _read_json(os.path.join(
                previous_data_path, 'summary', 'summary.json'
            ))
            previous_time = previous_summary.get('timeupdated')
            if isinstance(previous_time, str) and previous_time:
                return previous_time
    except (AttributeError, OSError, TypeError, ValueError,
            json.JSONDecodeError):
        pass

    try:
        raw_state = _read_json(paths['raw_state'])
        completed_at = datetime.datetime.fromisoformat(
            raw_state['completed_at']
        )
        return completed_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _run_wrangling(data_path, static_bundle_path, timeupdated=None,
                   previous_data_path=None):
    clean_gwas_cat(data_path, static_bundle_path)
    make_bubbleplot_df(data_path)
    make_doughnut_df_old(data_path)
    tsinput = pd.read_csv(
        os.path.join(
            data_path, 'catalog', 'synthetic', 'Cat_Anc_wBroader.tsv'
        ),
        sep='\t'
    )
    make_timeseries_df(tsinput, data_path, 'ts1')
    make_timeseries_df(
        tsinput[tsinput['Broader'] != 'In Part Not Recorded'],
        data_path,
        'ts2'
    )
    make_choro_df(data_path)
    make_heatmap_dfs(data_path)
    make_parent_list(data_path)
    create_summarystats(data_path, timeupdated)
    json_converter(data_path)
    zip_for_download(
        os.path.join(data_path, 'toplot'),
        os.path.join(data_path, 'todownload'),
        os.path.join(previous_data_path, 'todownload')
        if previous_data_path else None
    )


def _run_funder_wrangling(repository_path, data_path, previous_data_path=None):
    """Build funder outputs in the staged release, reusing its PubMed cache."""
    funder_root = os.path.join(data_path, 'funders')
    cleaner_source = funder_pipeline.funder_cleaner_path(
        os.path.join(repository_path, 'data')
    )
    cleaner_path = funder_pipeline.funder_cleaner_path(data_path)
    cache_path = os.path.join(funder_root, 'pubmed_grants.json')
    previous_cache = os.path.join(
        previous_data_path or '', 'funders', 'pubmed_grants.json'
    )
    os.makedirs(funder_root, exist_ok=True)
    if not os.path.isfile(cleaner_source):
        raise FileNotFoundError(
            f'Missing funder normalization configuration: {cleaner_source}'
        )
    if os.path.abspath(cleaner_source) != os.path.abspath(cleaner_path):
        shutil.copy2(cleaner_source, cleaner_path)
    if not os.path.isfile(cache_path) and os.path.isfile(previous_cache):
        shutil.copy2(previous_cache, cache_path)
        diversity_logger.info('Reused the previous PubMed funding cache.')

    cache = funder_pipeline.collect_pubmed_grants(data_path, cache_path)
    index = funder_pipeline.build_funder_artifacts(
        repository_path,
        data_path,
        cache,
        cleaner_path,
    )
    # Funder generation enriches bubble_df with the complete canonical funding
    # list for each publication. Refresh the main compact payload and download
    # archive so the unfiltered dashboard receives the same metadata as the
    # funder- and dataset-filtered dashboards.
    bubble_path = os.path.join(data_path, 'toplot', 'bubble_df.csv')
    if os.path.isfile(bubble_path):
        cleaner = funder_pipeline.load_funder_cleaner(cleaner_path)
        funder_pipeline.write_bubble_funding_metadata(
            data_path, cache, cleaner
        )
        json_converter(data_path)
        zip_for_download(
            os.path.join(data_path, 'toplot'),
            os.path.join(data_path, 'todownload'),
            os.path.join(previous_data_path, 'todownload')
            if previous_data_path else None
        )
    diversity_logger.info(
        'Build of %d funder dashboards: Complete', len(index['funders'])
    )


def _build_completion_state(repository_path, validation,
                            input_static_bundle_fingerprint=None):
    if input_static_bundle_fingerprint is None:
        input_static_bundle_fingerprint = _file_fingerprint(
            os.path.join(repository_path, 'data_static.zip')
        )
    return {
        'version': GENERATION_STATE_VERSION,
        'completed_at': datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        'raw_fingerprints': validation['raw_fingerprints'],
        'generation_parameters': _generation_parameters(),
        'input_static_bundle_fingerprint':
            input_static_bundle_fingerprint,
        'artifact_fingerprints': validation['artifact_fingerprints'],
        'static_bundle_fingerprint': validation[
            'static_bundle_fingerprint'
        ],
        'implementation_fingerprints': _implementation_fingerprints(
            repository_path
        ),
    }


def _atomic_copy(source_path, target_path, expected_fingerprint=None):
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if expected_fingerprint and os.path.isfile(target_path):
        if os.path.getsize(target_path) == expected_fingerprint['size'] \
                and _sha256_file(target_path) == \
                expected_fingerprint['sha256']:
            return False

    descriptor, temporary_path = tempfile.mkstemp(
        prefix='.generate_data.publish.',
        dir=os.path.dirname(target_path)
    )
    try:
        with open(source_path, 'rb') as source_file, \
                os.fdopen(descriptor, 'wb') as target_file:
            shutil.copyfileobj(source_file, target_file, 1024 * 1024)
            target_file.flush()
            os.fsync(target_file.fileno())
        shutil.copymode(source_path, temporary_path)
        os.replace(temporary_path, target_path)
        _fsync_directory(os.path.dirname(target_path))
        temporary_path = None
        return True
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _staged_state_valid(paths, repository_path=None):
    if not os.path.isfile(paths['staged_state']):
        return False
    try:
        state = _read_json(paths['staged_state'])
        if state.get('version') != GENERATION_STATE_VERSION:
            return False
        if state.get('generation_parameters') != _generation_parameters():
            return False
        input_bundle_fingerprint = state.get(
            'input_static_bundle_fingerprint'
        )
        if not isinstance(input_bundle_fingerprint, dict):
            return False
        raw_fingerprints = state.get('raw_fingerprints', {})
        if raw_fingerprints != _workspace_raw_fingerprints(paths):
            return False
        artifacts = state.get('artifact_fingerprints', {})
        if set(artifacts) != set(
                _expected_published_files(paths['workspace_data'])):
            return False
        if not _fingerprints_match(paths['workspace_data'], artifacts):
            return False
        bundle_fingerprint = state.get('static_bundle_fingerprint')
        if _file_fingerprint(paths['workspace_bundle']) != bundle_fingerprint:
            return False
        if repository_path is not None and \
                state.get('implementation_fingerprints') != \
                _implementation_fingerprints(repository_path):
            return False
        if repository_path is not None:
            current_bundle_fingerprint = _file_fingerprint(
                os.path.join(repository_path, 'data_static.zip')
            )
            allowed_bundle_fingerprints = [input_bundle_fingerprint]
            if os.path.isfile(paths['publication']):
                allowed_bundle_fingerprints.append(bundle_fingerprint)
            if current_bundle_fingerprint not in \
                    allowed_bundle_fingerprints:
                return False
        return True
    except (AttributeError, KeyError, OSError, ValueError, TypeError,
            json.JSONDecodeError):
        return False


def _verified_previous_runtime_fingerprints(repository_path, data_path):
    """Return trusted runtime fingerprints for the current live release."""
    try:
        state = _read_json(os.path.join(data_path, GENERATION_STATE_FILE))
        artifacts = state.get('artifact_fingerprints', {})
        runtime_fingerprints = {
            relative_path: artifacts[relative_path]
            for relative_path in RUNTIME_DATA_FILES
        }
        if _fingerprints_match(data_path, runtime_fingerprints):
            return runtime_fingerprints
    except (AttributeError, KeyError, OSError, TypeError, ValueError,
            json.JSONDecodeError):
        pass

    try:
        validation = validate_generated_release(
            data_path, os.path.join(repository_path, 'data_static.zip')
        )
        return {
            relative_path: validation['artifact_fingerprints'][relative_path]
            for relative_path in RUNTIME_DATA_FILES
        }
    except Exception:
        return None


def _verified_previous_funder_fingerprints(data_path):
    """Return trusted funder files when the live release already has them."""
    try:
        relative_paths = funder_pipeline.validate_funder_artifacts(data_path)
        return _fingerprint_files(data_path, relative_paths)
    except Exception:
        return {}


def _create_previous_release_snapshot(repository_path, data_path, paths):
    """Snapshot the coherent runtime release before live publication."""
    if os.path.isdir(paths['fallback_data']):
        shutil.rmtree(paths['fallback_data'])
    runtime_fingerprints = _verified_previous_runtime_fingerprints(
        repository_path, data_path
    )
    if runtime_fingerprints is None:
        diversity_logger.info(
            'No verified previous runtime release exists; this is treated as '
            'an initial publication.'
        )
        return None

    try:
        snapshot_fingerprints = dict(runtime_fingerprints)
        snapshot_fingerprints.update(
            _verified_previous_funder_fingerprints(data_path)
        )
        for relative_path in snapshot_fingerprints:
            source_path = os.path.join(data_path, relative_path)
            target_path = os.path.join(paths['fallback_data'], relative_path)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            try:
                os.link(source_path, target_path)
            except OSError:
                _atomic_copy(source_path, target_path)
            _fsync_directory(os.path.dirname(target_path))

        fallback_state = None
        live_state_path = os.path.join(data_path, GENERATION_STATE_FILE)
        if os.path.isfile(live_state_path):
            try:
                candidate_state = _read_json(live_state_path)
                candidate_artifacts = candidate_state.get(
                    'artifact_fingerprints', {}
                )
                if all(
                        candidate_artifacts.get(relative_path) == fingerprint
                        for relative_path, fingerprint
                        in runtime_fingerprints.items()):
                    fallback_state = candidate_state
            except (AttributeError, OSError, TypeError, ValueError,
                    json.JSONDecodeError):
                pass
        if fallback_state is None:
            fallback_state = {
                'version': GENERATION_STATE_VERSION,
                'artifact_fingerprints': runtime_fingerprints,
            }
        _atomic_write_json(
            os.path.join(paths['fallback_data'], GENERATION_STATE_FILE),
            fallback_state
        )

        if not _fingerprints_match(
                paths['fallback_data'], snapshot_fingerprints):
            raise ValueError(
                'Previous release snapshot differs from its live manifest'
            )

        for relative_path in (
                'summary/summary.json',
                *(f'toplot/{name}' for name in TOPLOT_JSON_FILES)):
            _read_json(os.path.join(paths['fallback_data'], relative_path))
        for relative_path in DOWNLOAD_OUTPUT_FILES:
            with zipfile.ZipFile(
                    os.path.join(paths['fallback_data'], relative_path),
                    'r') as archive:
                if archive.testzip() is not None:
                    raise ValueError(
                        f'Previous release archive is corrupt: {relative_path}'
                    )
    except (OSError, TypeError, ValueError, zipfile.BadZipFile,
            json.JSONDecodeError) as error:
        if os.path.isdir(paths['fallback_data']):
            shutil.rmtree(paths['fallback_data'])
        raise RuntimeError(
            'Could not preserve the verified previous runtime release; '
            'publication was aborted before changing live files'
        ) from error

    return os.path.relpath(paths['fallback_data'], data_path)


def _cleanup_directory_best_effort(path):
    if not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path)
    except OSError:
        diversity_logger.warning(
            'The release is complete, but obsolete generation files could '
            'not be removed: %s', path, exc_info=True
        )


def _cleanup_committed_publication(paths):
    """Remove recovery files without turning a committed release into failure."""
    for path in (paths['workspace'], paths['fallback_data']):
        _cleanup_directory_best_effort(path)


def _publish_staged_release(repository_path, data_path, paths):
    if not _staged_state_valid(paths, repository_path):
        raise RuntimeError(
            'Cannot publish: the staged generation is incomplete or changed'
        )
    state = _read_json(paths['staged_state'])
    if not os.path.isfile(paths['publication']):
        fallback_data = _create_previous_release_snapshot(
            repository_path, data_path, paths
        )
        with published_data_lock(data_path, exclusive=True):
            _atomic_write_json(paths['publication'], {
                'version': GENERATION_STATE_VERSION,
                'started_at': datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
                'fallback_data': fallback_data,
            })

    artifact_paths = list(state['artifact_fingerprints'])
    funder_index = 'funders/index.json'
    if funder_index in artifact_paths:
        artifact_paths.remove(funder_index)
        artifact_paths.append(funder_index)
    for relative_path in artifact_paths:
        _atomic_copy(
            os.path.join(paths['workspace_data'], relative_path),
            os.path.join(data_path, relative_path),
            state['artifact_fingerprints'][relative_path]
        )

    _atomic_copy(
        paths['workspace_bundle'],
        os.path.join(repository_path, 'data_static.zip'),
        state['static_bundle_fingerprint']
    )
    _atomic_copy(
        paths['staged_state'],
        os.path.join(data_path, GENERATION_STATE_FILE)
    )

    if not _completion_state_valid(
            data_path,
            repository_path,
            state['raw_fingerprints'],
            check_implementation=False,
            honor_publication_marker=False):
        raise RuntimeError(
            'Published files did not pass completion-state validation'
        )

    with published_data_lock(data_path, exclusive=True):
        os.unlink(paths['publication'])
        _fsync_directory(os.path.dirname(paths['publication']))

    _cleanup_committed_publication(paths)
    diversity_logger.info(
        'Published the complete generated dataset; completion manifest and '
        'all artifact fingerprints passed validation.'
    )


def _resume_publication_if_needed(repository_path, data_path):
    paths = _generation_paths(data_path)
    if not os.path.isfile(paths['publication']):
        return False
    if _staged_state_valid(paths, repository_path):
        diversity_logger.info(
            'Resuming an interrupted publication from its validated staged '
            'release.'
        )
        _publish_staged_release(repository_path, data_path, paths)
        return True
    if _completion_state_valid(
            data_path,
            repository_path,
            check_implementation=False,
            honor_publication_marker=False):
        with published_data_lock(data_path, exclusive=True):
            os.unlink(paths['publication'])
            _fsync_directory(os.path.dirname(paths['publication']))
        _cleanup_directory_best_effort(paths['fallback_data'])
        diversity_logger.info(
            'Removed a stale publication marker after verifying the complete '
            'live release; normal input and implementation checks will now '
            'continue.'
        )
        return False
    diversity_logger.warning(
        'The interrupted publication stage is incomplete; rebuilding it from '
        'the retained raw snapshot before attempting publication again. The '
        'previous runtime release remains available during recovery.'
    )
    if _workspace_raw_fingerprints(paths) is None \
            and os.path.isdir(paths['workspace']):
        shutil.rmtree(paths['workspace'])
    return False


def generate_and_publish(repository_path, ebi_download,
                         generation_year=None):
    global final_year
    final_year = int(generation_year) if generation_year is not None else \
        determine_year(datetime.date.today())
    repository_path = os.path.abspath(repository_path)
    data_path = os.path.join(repository_path, 'data')
    os.makedirs(data_path, exist_ok=True)

    with _generation_lock(data_path):
        if _resume_publication_if_needed(repository_path, data_path):
            return 'resumed'

        paths = _generation_paths(data_path)
        if _staged_state_valid(paths, repository_path):
            diversity_logger.info(
                'Publishing the complete staged release retained from an '
                'interrupted generation.'
            )
            _publish_staged_release(repository_path, data_path, paths)
            return 'resumed'

        paths, raw_fingerprints = _prepare_generation_workspace(
            repository_path, data_path, ebi_download
        )
        if _completion_state_valid(
                data_path,
                repository_path,
                raw_fingerprints,
                check_implementation=True):
            _cleanup_committed_publication(paths)
            diversity_logger.info(
                'No new raw data found and the complete published artifact '
                'manifest passed validation; wrangling is not required.'
            )
            return 'unchanged'

        input_bundle_fingerprint = _reset_workspace_for_wrangling(
            repository_path, paths
        )
        previous_data_path = data_path
        if os.path.isfile(paths['publication']) and \
                os.path.isdir(paths['fallback_data']):
            previous_data_path = paths['fallback_data']
        timeupdated = _release_timeupdated(
            paths, raw_fingerprints, previous_data_path
        )
        try:
            _run_wrangling(
                paths['workspace_data'], paths['workspace_bundle'],
                timeupdated, previous_data_path
            )
            _run_funder_wrangling(
                repository_path, paths['workspace_data'], previous_data_path
            )
            validation = validate_generated_release(
                paths['workspace_data'], paths['workspace_bundle']
            )
            if validation['raw_fingerprints'] != raw_fingerprints:
                raise RuntimeError(
                    'Raw inputs changed while the staged release was generated'
                )
        except Exception:
            _record_workspace_generation_failure(paths)
            raise
        completion_state = _build_completion_state(
            repository_path, validation, input_bundle_fingerprint
        )
        _atomic_write_json(paths['staged_state'], completion_state)
        _publish_staged_release(repository_path, data_path, paths)
        return 'published'


def main():
    global diversity_logger, final_year
    repository_path = os.getcwd()
    logpath = os.path.join(repository_path, 'app', 'logging')
    ebi_download = 'https://www.ebi.ac.uk/gwas/api/search/downloads/'
    active_logger = None
    try:
        active_logger = setup_logging(logpath)
        diversity_logger = active_logger
        final_year = determine_year(datetime.date.today())
        diversity_logger.info(
            'Data path: %s', os.path.join(repository_path, 'data')
        )
        diversity_logger.info('final year is being set to: %s', final_year)
        result = generate_and_publish(repository_path, ebi_download)
        diversity_logger.info(
            'generate_data.py ran successfully; result=%s', result
        )
        _clear_failure_notification_state(repository_path, diversity_logger)
        return 0
    except KeyboardInterrupt as error:
        if active_logger is not None:
            active_logger.warning(
                'generate_data.py was interrupted. Any in-progress '
                'publication will continue serving its previous runtime '
                'snapshot and will be recovered automatically on the next '
                'run.'
            )
        _notify_generation_failure(error, repository_path, 130, active_logger)
        return 130
    except Exception as error:
        if active_logger is not None:
            active_logger.exception(
                'generate_data.py failed; the validated raw staging snapshot '
                'was retained when possible, and no incomplete release was '
                'marked complete. Any interrupted publication remains '
                'recoverable.'
            )
        else:
            print(
                'generate_data.py failed before file logging was available:\n'
                + traceback.format_exc(),
                file=sys.stderr,
            )
        _notify_generation_failure(error, repository_path, 1, active_logger)
        return 1
    finally:
        logging.shutdown()


if __name__ == "__main__":
    sys.exit(main())
