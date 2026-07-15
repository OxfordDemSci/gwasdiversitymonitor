# generate data: python script that does the daily GWAS data collection

import pandas as pd
import traceback
import json
import numpy as np
import logging
import datetime
import requests
import requests_ftp
import os
import csv
import shutil
import sys
import warnings
import zipfile
import math
from app.DataLoader import DataLoader

warnings.filterwarnings("ignore")


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
    dl = DataLoader()
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
            # continue to next one

def setup_logging(logpath):
    """ Set up the logging """
    if os.path.exists(logpath) is False:
        os.makedirs(logpath)
    logger = logging.getLogger('diversity_logger')
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler((os.path.abspath(
        os.path.join(logpath, 'diversity_logger.log'))))
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.ERROR)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def create_summarystats(data_path):
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

        # --- Cat_Full (unchanged) ---
        Cat_Full = pd.read_csv(
            os.path.join(data_path, 'catalog', 'raw', 'Cat_Full.tsv'),
            sep='\t',
            engine='python',
            usecols=['P-VALUE'],
            quotechar='"',
            on_bad_lines="skip",
            dtype={'P-VALUE': object}
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

        sumstats['average_pval'] = float(round(pd.to_numeric(Cat_Full['P-VALUE'], errors='coerce').mean(skipna=True), 10))
        sumstats['threshold_pvals'] = int((pd.to_numeric(Cat_Full['P-VALUE'], errors='coerce') < 5.0e-8).sum())

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
        sumstats['total_othermixed']   = pc(Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Other')]['N'].sum())
        sumstats['total_afamafcam']    = pc(Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Cari')]['N'].sum())
        sumstats['total_hisorlatinam'] = pc(Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Hispanic')]['N'].sum())

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
        disc_othe = anc_nonr_init[anc_nonr_init['Broader'].str.contains('Other')]
        disc_cari = anc_nonr_init[anc_nonr_init['Broader'].str.contains('Cari')]
        disc_hisp = anc_nonr_init[anc_nonr_init['Broader'].str.contains('Hispanic')]

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
        repl_othe = anc_nonr_repl[anc_nonr_repl['Broader'].str.contains('Other')]
        repl_cari = anc_nonr_repl[anc_nonr_repl['Broader'].str.contains('Cari')]
        repl_hisp = anc_nonr_repl[anc_nonr_repl['Broader'].str.contains('Hispanic')]

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
        sumstats['timeupdated'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        diversity_logger.debug(f'Build of the summary stats: Failed -- {e}')
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
        if len(merged[merged['parentterm'].isnull()]) > 0:
            diversity_logger.debug('Wuhoh! There are some empty disease terms!')
            pd.Series(merged[merged['parentterm'].
                             isnull()]['DISEASE/TRAIT'].unique()).\
                to_csv(os.path.join(data_path, 'unmapped',
                                    'unmapped_diseases.txt'),
                       index=False)
        else:
            diversity_logger.info('No missing disease terms! Nice!')
        merged = merged[merged["parentterm"].notnull()]
        merged = merged[merged["parentterm"]!='NR']
        merged["parentterm"] = merged["parentterm"].astype(str)
        merged["DATE"] = merged["DATE"].astype(str)
        make_heatmatrix(merged, 'initial', os.path.join(data_path,
                                                        'toplot'))
        make_heatmatrix(merged, 'replication', os.path.join(data_path, 'toplot'))
        diversity_logger.info('Build of the heatmap dataset: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the heatmap dataset: Failed -- {e}')


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

            # Sanity logs so you can see it in your logfile immediately
            yrs = sorted(annual_df['Year'].unique())
            diversity_logger.debug(f"choro_df years present: {yrs[:5]} … {yrs[-5:]}")
            diversity_logger.info('Build of the choropleth dataset: Complete')
        else:
            diversity_logger.warning('No choropleth data generated (no yearly data found)')

    except Exception:
        diversity_logger.exception('Build of the choropleth dataset: Failed')


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
        diversity_logger.debug(f'Build of the ts dataset: Failed -- {e}')




def make_doughnut_df_old(data_path):
    """ Make the doughnut chart dataframe for use in main.py"""
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
        counter = 0
        for year in range(2008, final_year+1):
            for ancestry in merged['Broader'].unique().tolist():
                doughnut_df.at[counter, 'Broader'] = ancestry
                doughnut_df.at[counter, 'parentterm'] = 'All'
                doughnut_df.at[counter, 'Year'] = year
                rep_anc = merged[(merged['STAGE'] == 'replication') &
                                 (merged['Broader'] == ancestry) &
                                 (merged['DATE'].str.contains(str(year)))]['N'].sum()
                rep_tot = merged[(merged['STAGE'] == 'replication') &
                                 (merged['DATE'].str.contains(str(year)))]['N'].sum()
                init_anc =merged[(merged['STAGE'] == 'initial') &
                                 (merged['Broader'] == ancestry) &
                                 (merged['DATE'].str.contains(str(year)))]['N'].sum()
                init_tot = merged[(merged['STAGE'] == 'initial') &
                                  (merged['DATE'].str.contains(str(year)))]['N'].sum()
                doughnut_df.at[counter, 'ReplicationN'] = (rep_anc/rep_tot)*100
                doughnut_df.at[counter, 'InitialN'] =  (init_anc/init_tot)*100
                init_ass_anc = merged[(merged['STAGE'] == 'initial') &
                                      (merged['Broader'] == ancestry) &
                                      (merged['DATE'].str.contains(str(year)))]
                init_ass_anc = init_ass_anc['ASSOCIATION COUNT'].sum()
                init_ass_tot = merged[(merged['STAGE'] =='initial') &
                                      (merged['DATE'].str.contains(str(year)))]
                init_ass_tot = init_ass_tot['ASSOCIATION COUNT'].sum()
                doughnut_df.at[counter, 'InitialAssociationSum'] = (init_ass_anc/init_ass_tot)*100
                init_anc = len(merged[(merged['STAGE'] == 'initial') &
                                      (merged['DATE'].str.contains(str(year))) &
                                      (merged['Broader'] == ancestry)])
                init_tot = len(merged[(merged['STAGE'] == 'initial') &
                                      (merged['DATE'].str.contains(str(year)))])
                rep_anc = len(merged[(merged['STAGE'] =='replication') &
                                     (merged['DATE'].str.contains(str(year))) &
                                     (merged['Broader'] == ancestry)])
                rep_tot = len(merged[(merged['STAGE'] == 'replication') &
                                     (merged['DATE'].str.contains(str(year)))])
                doughnut_df.at[counter, 'InitialCount'] = (init_anc/init_tot)*100
                doughnut_df.at[counter, 'ReplicationCount'] = (rep_anc/rep_tot)*100
                counter = counter + 1
                for parent in merged['parentterm'].unique().tolist():
                    try:
                        doughnut_df.at[counter, 'Broader'] = ancestry
                        doughnut_df.at[counter, 'parentterm'] = parent
                        doughnut_df.at[counter, 'Year'] = year
                        rep_anc = merged[(merged['STAGE'] == 'replication') &
                                         (merged['parentterm'] == parent) &
                                         (merged['DATE'].str.contains(str(year))) &
                                         (merged['Broader'] == ancestry)]['N'].sum()
                        rep_tot = merged[(merged['STAGE'] == 'replication') &
                                         (merged['DATE'].str.contains(str(year))) &
                                         (merged['parentterm'] == parent)]['N'].sum()
                        init_anc = merged[(merged['STAGE'] == 'initial') &
                                          (merged['Broader'] == ancestry) &
                                          (merged['DATE'].str.contains(str(year))) &
                                          (merged['parentterm'] == parent)]['N'].sum()
                        init_tot = merged[(merged['STAGE'] == 'initial') &
                                          (merged['DATE'].str.contains(str(year))) &
                                          (merged['parentterm'] == parent)]['N'].sum()
                        doughnut_df.at[counter, 'ReplicationN'] = (rep_anc/rep_tot)*100
                        doughnut_df.at[counter, 'InitialN'] = (init_anc/init_tot)*100
                        init_ass_anc = merged[(merged['STAGE'] == 'initial') &
                                              (merged['Broader'] == ancestry) &
                                              (merged['DATE'].str.contains(str(year))) &
                                              (merged['parentterm'] == parent)]
                        init_ass_anc = init_ass_anc['ASSOCIATION COUNT'].sum()
                        init_ass_tot = merged[(merged['STAGE'] == 'initial') &
                                              (merged['DATE'].str.contains(str(year))) &
                                              (merged['parentterm'] == parent)]
                        init_ass_tot = init_ass_tot['ASSOCIATION COUNT'].sum()
                        doughnut_df.at[counter, 'InitialAssociationSum'] = (init_ass_anc/init_ass_tot)*100
                        rep_anc = len(merged[(merged['STAGE'] == 'replication') &
                                             (merged['parentterm'] == parent) &
                                             (merged['DATE'].str.contains(str(year))) &
                                             (merged['Broader'] == ancestry)])
                        rep_tot = len(merged[(merged['STAGE'] == 'replication') &
                                             (merged['DATE'].str.contains(str(year))) &
                                             (merged['parentterm'] == parent)])
                        init_anc = len(merged[(merged['STAGE'] == 'initial') &
                                              (merged['parentterm'] == parent) &
                                              (merged['DATE'].str.contains(str(year))) &
                                              (merged['Broader'] == ancestry)])
                        init_tot = len(merged[(merged['STAGE'] == 'initial') &
                                              (merged['DATE'].str.contains(str(year))) &
                                              (merged['parentterm'] == parent)])
                        doughnut_df.at[counter, 'ReplicationCount'] = (rep_anc/rep_tot)*100
                        doughnut_df.at[counter,'InitialCount'] = (init_anc/init_tot)*100
                    except ZeroDivisionError:
                        doughnut_df.at[counter, 'InitialN'] = np.nan
                    counter = counter + 1
        doughnut_df['Broader'] = doughnut_df['Broader'].str.\
            replace('Hispanic/Latin American', 'Hispanic/L.A.')
        doughnut_df.to_csv(os.path.join(data_path, 'toplot', 'doughnut_df.csv'))
        diversity_logger.info('Build of the doughnut datasets: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the doughnut datasets: Failed -- {e}')



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




def make_bubbleplot_df(data_path):
    """ Make data for the bubbleplot """
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               sep='\t',
                               usecols = ['STUDY ACCESSION', 'DISEASE/TRAIT'])
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
        make_disease_list(merged)
        merged = merged.groupby(["Broader", "N", "PUBMEDID", "AUTHOR", "STAGE",
                                 "DATE",  "DiseaseOrTrait","ACCESSION"])['parentterm'].\
            apply(', '.join).reset_index()
        merged = merged.groupby(["Broader", "N", "PUBMEDID", "AUTHOR",
                                 "parentterm", "STAGE", "DATE","ACCESSION"])['DiseaseOrTrait'].\
            apply(', '.join).reset_index()
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
        diversity_logger.debug(f'Build of the bubble datasets: Failed -- {e}')


def clean_gwas_cat(data_path):
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
        cleaner_broad = pd.read_csv(os.path.join(data_path, 'support',
                                                 'dict_replacer_broad.tsv'),
                                    sep='\t',
                                    header=0,
                                    index_col=False)
        Cat_Anc = pd.merge(Cat_Anc, cleaner_broad, how='left',
                           on='BROAD ANCESTRAL')
        Cat_Anc['Dates'] = [pd.to_datetime(d) for d in Cat_Anc['DATE']]
        Cat_Anc['N'] = pd.to_numeric(Cat_Anc['N'], errors='coerce')
        Cat_Anc = Cat_Anc[Cat_Anc['N'].notnull()]
        Cat_Anc['N'] = Cat_Anc['N'].astype(int)
        Cat_Anc = Cat_Anc.sort_values(by='Dates')
        if len(Cat_Anc[Cat_Anc['Broader'].isnull()]) > 0:
            diversity_logger.debug('Need to update dictionary terms:\n' +
                                   '\n'.join(Cat_Anc[Cat_Anc['Broader'].
                                                     isnull()]['BROAD ANCESTRAL'].
                                             unique()))
            Cat_Anc[Cat_Anc['Broader'].
                    isnull()]['BROAD ANCESTRAL'].\
                to_csv(os.path.join(data_path, 'unmapped', 'unmapped_broader.txt'))
        else:
            diversity_logger.info('No missing Broader terms! Nice!')
        #Cat_Anc = Cat_Anc[Cat_Anc['Broader'].notnull()]
        #Cat_Anc = Cat_Anc[Cat_Anc['N'].notnull()]
        Cat_Anc.to_csv(os.path.join(data_path, 'catalog', 'synthetic', 'Cat_Anc_wBroader.tsv'),
                       sep='\t',
                       index=False)
        diversity_logger.info('Clean of the raw GWAS Catalog datasets: Complete')
    except Exception as e:
        diversity_logger.debug(f'Clean of the raw GWAS Catalog datasets: Failed -- {e}')


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
    """Downloads GWAS Catalog files (robust to missing/odd headers)."""
    try:
        raw_dir = os.path.join(data_path, 'catalog', 'raw')
        os.makedirs(raw_dir, exist_ok=True)

        http_endpoints = [
            ('studies/v1.0.3.1', 'Cat_Stud.tsv'),
            ('ancestry',         'Cat_Anc.tsv'),
            ('full',             'Cat_Full.tsv'),
        ]

        for endpoint, fallback_name in http_endpoints:
            url = ebi_download + endpoint
            r = requests.get(url, timeout=60)
            if r.ok:
                server_name = _safe_filename(r, fallback_name)
                out_path = os.path.join(raw_dir, fallback_name)
                with open(out_path, 'wb') as fh:
                    fh.write(r.content)
                diversity_logger.info(
                    f"Download of {endpoint}: Complete "
                    f"(saved as {fallback_name}; server filename: {server_name})"
                )
            else:
                diversity_logger.debug(f"Download of {endpoint}: Failed (HTTP {r.status_code})")

        # FTP: trait mappings
        requests_ftp.monkeypatch_session()
        s = requests.Session()
        ftpsite = 'ftp://ftp.ebi.ac.uk'
        subdom = '/pub/databases/gwas/releases/latest/'
        file = 'gwas-efo-trait-mappings.tsv'
        r = s.get(ftpsite + subdom + file, timeout=60)
        if r.ok:
            out_path = os.path.join(raw_dir, 'Cat_Map.tsv')
            with open(out_path, 'wb') as fh:
                fh.write(r.content)
            diversity_logger.info('Download of efo-trait-mappings: Complete')
        else:
            diversity_logger.debug(f'Download of efo-trait-mappings: Failed (HTTP {r.status_code})')

    except Exception as e:
        diversity_logger.debug('Problem downloading Catalog data! ' + str(e))


def make_disease_list(df):
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


def zip_for_download(source, destination):
    """ Generates a zipfile for downloading """
    mode = 'w'
    all_path = os.path.join(destination, 'gwasdiversitymonitor_download.zip')
    heat_path = os.path.join(destination, 'heatmap.zip')
    ts_path = os.path.join(destination, 'timeseries.zip')
    try:
        with zipfile.ZipFile(all_path, mode) as all_zip:
            for file_name in os.listdir(source):
                all_zip.write(os.path.join(source, file_name), file_name)

        with zipfile.ZipFile(heat_path, mode) as heat_zip:
            for file_name in filter(lambda f: f.lower().startswith('heat'),  os.listdir(source)):
                    heat_zip.write(os.path.join(source, file_name), file_name)

        with zipfile.ZipFile(ts_path, mode) as ts_zip:
            for file_name in filter(lambda f: f.lower().startswith('ts'),  os.listdir(source)):
                    ts_zip.write(os.path.join(source, file_name), file_name)
        diversity_logger.info('Build of the zipped Datasets: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the zipped datasets: Failed -- {e}')

def determine_year(day):
    """ Determines year, day is a datetime.date obj"""
    return day.year if math.ceil(day.month/3.) > 2 else day.year-1

def check_data(data_path):

    static_files = ['catalog/raw/Cat_Anc.tsv',
                    'catalog/raw/Cat_Full.tsv',
                    'catalog/raw/Cat_Map.tsv',
                    'catalog/raw/Cat_Stud.tsv',
                    'catalog/synthetic/Cat_Anc_wBroader.tsv',
                    'catalog/synthetic/Cat_Anc_wB_withParents.tsv',
                    'catalog/synthetic/Disease_to_Parent_Mappings.tsv',
                    'summary/uniq_broader.txt',
                    'support/Country_Lookup.csv',
                    'support/dict_replacer_broad.tsv']

    data_okay = os.path.exists(data_path)

    for i in range(len(static_files)):
        if not data_okay:
            break
        data_okay = os.path.exists(os.path.join(data_path, static_files[i]))

    return data_okay

if __name__ == "__main__":
    logpath = os.path.join(os.getcwd(), 'app', 'logging')
    diversity_logger = setup_logging(logpath)
    logfile = diversity_logger.handlers[0].baseFilename
    sys.stderr.write(f'Generating data. See logfile for details: {logfile}\n')

    data_path = os.path.join(os.getcwd(), 'data')
    sys.stderr.write(f'Data path: {data_path}\n')
    diversity_logger.info('Data path: ' + str(data_path))
    if not check_data(data_path):
        zipfile.ZipFile('data_static.zip').extractall(data_path)

    ebi_download = 'https://www.ebi.ac.uk/gwas/api/search/downloads/'
    final_year = determine_year(datetime.date.today())
    diversity_logger.info('final year is being set to: ' + str(final_year))
    try:
        download_cat(data_path, ebi_download)
        clean_gwas_cat(data_path)
        make_bubbleplot_df(data_path)
        make_doughnut_df(data_path)
        make_doughnut_df_old(data_path)
        tsinput = pd.read_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                           'Cat_Anc_wBroader.tsv'),  sep='\t')
        make_timeseries_df(tsinput, data_path, 'ts1')
        tsinput = tsinput[tsinput['Broader'] != 'In Part Not Recorded']
        make_timeseries_df(tsinput, data_path, 'ts2')
        make_choro_df(data_path)
        make_heatmap_dfs(data_path)
        make_parent_list(data_path)
        sumstats = create_summarystats(data_path)
        zip_for_download(os.path.join(data_path, 'toplot'),
                         os.path.join(data_path, 'todownload'))
        json_converter(data_path)
        diversity_logger.info('generate_data.py ran successfully!')
    except Exception as e:
        diversity_logger.debug(f'generate_data.py failed, uncaught error: {e}')
        sys.stderr.write(f'generate_data.py failed, see the log for details: {logfile}\n')
    logging.shutdown()
