# generate data: python script that does the daily GWAS data collection
import unicodedata2 as unicodedata
import pandas as pd
import os
import numpy as np
import json
import logging
import datetime
import requests
import requests_ftp
import os
import re
import csv
import sys
import traceback
import warnings
import zipfile
import math
from app.DataLoader import DataLoader
from joblib import Parallel, delayed, cpu_count
from Bio import Entrez
from generate_reports import generate_reports
warnings.filterwarnings("ignore")


def json_converter(data_path):
    """Convert the .csvs to .jsons to bypass dataLoader
    Keeps the same names as the outputs returned in routes.py
    """
    try:
        dl = DataLoader()
        plot_path = os.path.join(data_path, 'toplot')
        def json_maker(name, output):
            with open(name, 'w') as fp:
                json.dump(output, fp)

        for key, value in {'ancestries.json': dl.getAncestriesList(),
                           'ancestriesOrdered.json': dl.getAncestriesListOrder(),
                           'parentTerms.json': dl.getTermsList(),
                           'traits.json': dl.getTraitsList(),
                           'bubbleGraph.json': dl.getBubbleGraph(),
                           'tsPlot.json': dl.getTSPlot(),
                           'chloroMap.json': dl.getChloroMap(),
                           'heatMap.json': dl.getHeatMap(),
                           'doughnutGraph.json': dl.getDoughnutGraph(dl.getAncestriesListOrder()),
                           'summary.json': dl.getSummaryStatistics()
                          }.items():
            json_maker(os.path.join(plot_path, key), value)
        diversity_logger.info('Build of the json_converter: Complete')
    except Exception as e:
        print(traceback.format_exc())
        diversity_logger.debug(f'Build of the json_converter: Failed -- {e}')


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


def pcs(input_df, filter, in_or_equals, sum_or_count):
    if in_or_equals == 'equals':
        temp_df = input_df[input_df['Broader'] == filter]
    elif in_or_equals == 'in':
        temp_df = input_df[input_df['Broader'].str.contains(filter)]
    if sum_or_count == 'sum':
        temp_sum = temp_df['N'].sum()
        input_sum = input_df['N'].sum()
        if input_sum != 0 and temp_sum != 0:
            return round(((temp_sum / input_sum) * 100), 2)
        elif input_sum != 0 and temp_sum == 0:
            return 0
        elif input_sum == 0:
            return 0
    elif sum_or_count == 'count':
        temp_count = len(temp_df)
        input_count = len(input_df)
        if input_count != 0 and temp_count != 0:
            return round(((temp_count / input_count) * 100), 2)
        elif input_count != 0 and temp_count == 0:
            return 0
        elif input_count == 0:
            return 0


def make_sumstats_headline(sumstats, funder, input_df):
    sumstats['by_funder'][funder] = {}
    if funder != 'All Funders':
        input_df = input_df[input_df['Agency'].notnull()]
        input_df = input_df[input_df['Agency'].str.contains(funder, regex=False)]
    euro_sum = input_df[input_df['Broader'] == 'European']['N'].sum()
    if round(((euro_sum / input_df['N'].sum())*100), 2) == np.nan:
        sumstats['by_funder'][funder]['total_european'] = 0
    else:
        sumstats['by_funder'][funder]['total_european'] = round(((euro_sum / input_df['N'].sum())*100), 2)
    asia_sum = input_df[input_df['Broader'] == 'Asian']['N'].sum()
    if round(((asia_sum / input_df['N'].sum())*100), 2) == np.nan:
        sumstats['by_funder'][funder]['total_asian'] = 0
    else:
        sumstats['by_funder'][funder]['total_asian'] = round(((asia_sum / input_df['N'].sum())*100), 2)
    afri_sum = input_df[input_df['Broader'] == 'African']['N'].sum()
    if round(((afri_sum / input_df['N'].sum())*100), 2) == np.nan:
        sumstats['by_funder'][funder]['total_african'] = 0
    else:
        sumstats['by_funder'][funder]['total_african'] = round(((afri_sum / input_df['N'].sum())*100), 2)
    oth_sum = input_df[input_df['Broader'].str.contains('Other')]['N'].sum()
    if round(((oth_sum / input_df['N'].sum())*100), 2) == np.nan:
        sumstats['by_funder'][funder]['total_othermixed'] = 0
    else:
        sumstats['by_funder'][funder]['total_othermixed'] = round(((oth_sum / input_df['N'].sum())*100), 2)
    cari_sum = input_df[input_df['Broader'].str.contains('Cari')]['N'].sum()
    if round(((cari_sum / input_df['N'].sum())*100), 2) == np.nan:
        sumstats['by_funder'][funder]['total_afamafcam'] = 0
    else:
        sumstats['by_funder'][funder]['total_afamafcam'] = round(((cari_sum / input_df['N'].sum())*100), 2)
    hisp_sum = input_df[input_df['Broader'].str.contains('Hispanic')]['N'].sum()
    if round(((hisp_sum / input_df['N'].sum())*100), 2) == np.nan:
        sumstats['by_funder'][funder]['total_hisorlatinam'] = 0
    else:
        sumstats['by_funder'][funder]['total_hisorlatinam'] = round(((hisp_sum / input_df['N'].sum())*100), 2)

    anc_nonr_init = input_df[input_df['STAGE'] == 'initial']
    sumstats['by_funder'][funder]['discovery_participants_european'] = pcs(anc_nonr_init, 'European', 'equals', 'sum')
    sumstats['by_funder'][funder]['discovery_participants_asian'] = pcs(anc_nonr_init, 'Asian', 'equals', 'sum')
    sumstats['by_funder'][funder]['discovery_participants_african'] = pcs(anc_nonr_init, 'African', 'equals', 'sum')
    sumstats['by_funder'][funder]['discovery_participants_othermixed'] = pcs(anc_nonr_init, 'Other', 'in', 'sum')
    sumstats['by_funder'][funder]['discovery_participants_afamafcam'] = pcs(anc_nonr_init, 'Cari', 'in', 'sum')
    sumstats['by_funder'][funder]['discovery_participants_hisorlatinam'] = pcs(anc_nonr_init, 'Hispanic', 'in', 'sum')

    sumstats['by_funder'][funder]['discovery_studies_european'] = pcs(anc_nonr_init, 'European', 'equals', 'count')
    sumstats['by_funder'][funder]['discovery_studies_asian'] = pcs(anc_nonr_init, 'Asian', 'equals', 'count')
    sumstats['by_funder'][funder]['discovery_studies_african'] = pcs(anc_nonr_init, 'African', 'equals', 'count')
    sumstats['by_funder'][funder]['discovery_studies_othermixed'] = pcs(anc_nonr_init, 'Other', 'in', 'count')
    sumstats['by_funder'][funder]['discovery_studies_afamafcam'] = pcs(anc_nonr_init, 'Cari', 'in', 'count')
    sumstats['by_funder'][funder]['discovery_studies_hisorlatinam'] = pcs(anc_nonr_init, 'Hispanic', 'in', 'count')

    anc_nonr_repl = input_df[input_df['STAGE'] == 'replication']
    sumstats['by_funder'][funder]['replication_participants_european'] = pcs(anc_nonr_repl, 'European', 'equals', 'sum')
    sumstats['by_funder'][funder]['replication_participants_asian'] = pcs(anc_nonr_repl, 'Asian', 'equals', 'sum')
    sumstats['by_funder'][funder]['replication_participants_african'] = pcs(anc_nonr_repl, 'African', 'equals', 'sum')
    sumstats['by_funder'][funder]['replication_participants_othermixed'] = pcs(anc_nonr_repl, 'Other', 'in', 'sum')
    sumstats['by_funder'][funder]['replication_participants_afamafcam'] = pcs(anc_nonr_repl, 'Cari', 'in', 'sum')
    sumstats['by_funder'][funder]['replication_participants_hisorlatinam'] = pcs(anc_nonr_repl, 'Hispanic', 'in', 'sum')

    sumstats['by_funder'][funder]['replication_studies_european'] = pcs(anc_nonr_repl, 'European', 'equals', 'count')
    sumstats['by_funder'][funder]['replication_studies_asian'] = pcs(anc_nonr_repl, 'Asian', 'equals', 'count')
    sumstats['by_funder'][funder]['replication_studies_african'] = pcs(anc_nonr_repl, 'African', 'equals', 'count')
    sumstats['by_funder'][funder]['replication_studies_othermixed'] = pcs(anc_nonr_repl, 'Other', 'in', 'count')
    sumstats['by_funder'][funder]['replication_studies_afamafcam'] = pcs(anc_nonr_repl, 'Cari', 'in', 'count')
    sumstats['by_funder'][funder]['replication_studies_hisorlatinam'] = pcs(anc_nonr_repl, 'Hispanic', 'in', 'count')
    return sumstats

def create_summarystats(data_path):
    """
        Create the summarystats .json used to propogate index.html
    """
    try:
        dtypes = {'FIRST AUTHOR': str, 'DISEASE/TRAIT': str,
                  'ASSOCIATION COUNT': np.int64, 'JOURNAL': str,
                  'MAPPED_TRAIT': str}
        usecols = ['PUBMEDID', 'DATE', 'FIRST AUTHOR', 'STUDY ACCESSION',
                   'DISEASE/TRAIT', 'MAPPED_TRAIT', 'ASSOCIATION COUNT',
                   'JOURNAL']
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               sep='\t',
                               low_memory=False,
                               usecols=usecols,
                               quotechar='"',
                               warn_bad_lines=False,
                               error_bad_lines=False,
                               dtype=dtypes
                               )
        Cat_Full = pd.read_csv(os.path.join(data_path, 'catalog',
                                            'raw', 'Cat_Full.tsv'),
                               sep='\t',
                               engine='python',
                               usecols=['P-VALUE'],
                               quotechar='"',
                               warn_bad_lines=False,
                               error_bad_lines=False,
                               dtype={'P-VALUE': object})
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path, 'catalog',
                                                    'synthetic',
                                                    'Cat_Anc_wBroader.tsv'),
                                       '\t', index_col=False,
                                       low_memory=False)
        temp_bubble_df = pd.read_csv(os.path.join(data_path,
                                                  'toplot', 'bubble_df.csv'),
                                     sep=',', index_col=False, low_memory=False)
        sumstats = {}

        # Calculate summary statistics from the Cat_Stud file here
        sumstats['number_studies'] = int(len(Cat_Stud['PUBMEDID'].unique()))
        sumstats['first_study_date'] = str(Cat_Stud['DATE'].min())
        datemin = Cat_Stud['DATE'] == Cat_Stud['DATE'].min()
        dateminauth = Cat_Stud.loc[datemin, 'FIRST AUTHOR']
        sumstats['first_study_firstauthor'] = str(dateminauth.iloc[0])
        dateminpubmed = Cat_Stud.loc[datemin, 'PUBMEDID']
        sumstats['first_study_pubmedid'] = int(dateminpubmed.iloc[0])
        datemax = Cat_Stud['DATE'].max()
        sumstats['last_study_date'] = str(datemax)
        datemaxauth = Cat_Stud.loc[Cat_Stud['DATE'] == datemax, 'FIRST AUTHOR']
        sumstats['last_study_firstauthor'] = str(datemaxauth.iloc[0])
        datemaxpubmed = Cat_Stud.loc[Cat_Stud['DATE'] == Cat_Stud['DATE'].max(), 'PUBMEDID']
        sumstats['last_study_pubmedid'] = int(datemaxpubmed.iloc[0])
        cat_stud_acc_uniq = Cat_Stud['STUDY ACCESSION'].unique()
        sumstats['number_accessions'] = int(len(cat_stud_acc_uniq))
        cat_stud_dis_uniq = Cat_Stud['DISEASE/TRAIT'].unique()
        sumstats['number_diseasestraits'] = int(len(cat_stud_dis_uniq))
        cat_stud_map_uniq = Cat_Stud['MAPPED_TRAIT'].unique()
        sumstats['number_mappedtrait'] = int(len(cat_stud_map_uniq))
        cat_stud_ass_sum = Cat_Stud['ASSOCIATION COUNT'].sum()
        sumstats['found_associations'] = int(cat_stud_ass_sum)
        cat_stud_ass_mean = Cat_Stud['ASSOCIATION COUNT'].mean()
        sumstats['average_associations'] = float(cat_stud_ass_mean)
        sumstats['mostcommon_journal'] = str(Cat_Stud['JOURNAL'].mode()[0])
        sumstats['unique_journals'] = int(len(Cat_Stud['JOURNAL'].unique()))

        noneuro_trait = pd.DataFrame(temp_bubble_df[
                                     temp_bubble_df['Broader'] != 'European'].
                                     groupby(['DiseaseOrTrait']).size()).\
            sort_values(by=0, ascending=False).reset_index()['DiseaseOrTrait'][0]
        sumstats['noneuro_trait'] = str(noneuro_trait)
        sumstats['average_pval'] = float(round(Cat_Full['P-VALUE'].
                                               astype(float).mean(), 10))
        sumstats['threshold_pvals'] = int(len(Cat_Full[Cat_Full['P-VALUE'].
                                              astype(float) < 5.000000e-8]))
        Cat_Anc_byN = Cat_Anc_wBroader[['STUDY ACCESSION', 'N']]
        Cat_Anc_byN = Cat_Anc_byN.groupby(by='STUDY ACCESSION').sum()
        Cat_Anc_byN = Cat_Anc_byN.reset_index()
        Cat_Anc_wBroader = Cat_Anc_wBroader.drop_duplicates('STUDY ACCESSION')
        Cat_Anc_wBroader = Cat_Anc_wBroader[['PUBMEDID', 'FIRST AUTHOR', 'STUDY ACCESSION']]
        Cat_Anc_byN = pd.merge(Cat_Anc_byN, Cat_Anc_wBroader,
                               how='left',
                               left_on='STUDY ACCESSION',
                               right_on='STUDY ACCESSION')
        lar_acc = Cat_Anc_byN.sort_values(by='N', ascending=False)['N'].iloc[0]
        sumstats['big_n'] = int(lar_acc)
        biggestauth = Cat_Anc_byN.loc[Cat_Anc_byN['N'] == int(lar_acc), 'FIRST AUTHOR']
        sumstats['large_accesion_firstauthor'] = str(biggestauth.iloc[0])
        biggestpubmed = Cat_Anc_byN.loc[Cat_Anc_byN['N'] == int(lar_acc), 'PUBMEDID']
        sumstats['large_accesion_pubmed'] = int(biggestpubmed.iloc[0])
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path, 'catalog',
                                                    'synthetic', 'Cat_Anc_wBroader.tsv'),
                                       sep='\t',
                                       index_col=False,
                                       low_memory=False)
        Cat_Anc_NoNR = Cat_Anc_wBroader[Cat_Anc_wBroader['Broader'] != 'In Part Not Recorded']
        agencies = pd.read_csv(os.path.join(data_path, 'pubmed', 'agencies.tsv'),
                               sep='\t', usecols=['Agencies'])
        agencies = agencies['Agencies'].to_list()
        paper_grants = read_paper_grants(['PUBMEDID', 'Agency'])
        Cat_Anc_NoNR = pd.merge(Cat_Anc_NoNR, paper_grants, how='left',
                                left_on='PUBMEDID', right_on='PUBMEDID')
        sumstats['by_funder'] = {}
        make_sumstats_headline(sumstats, 'All Funders', Cat_Anc_NoNR)
        for agency in agencies:
            sumstats = make_sumstats_headline(sumstats, agency, Cat_Anc_NoNR)

        sumstats['timeupdated'] = datetime.datetime.now().\
                                  strftime("%Y-%m-%d %H:%M:%S")
        if os.path.exists(os.path.join(data_path, 'unmapped',
                                       'unmapped_diseases.txt')):
            unmapped_dis = pd.read_csv(os.path.join(data_path, 'unmapped',
                                       'unmapped_diseases.txt'))
            sumstats['unmapped_diseases'] = len(unmapped_dis)
        else:
            sumstats['unmapped_diseases'] = 0
        json_path = os.path.join(data_path, 'summary', 'summary.json')
        with open(json_path, 'w') as outfile:
            json.dump(sumstats, outfile)
        diversity_logger.info('Build of the summary stats: Complete')
        return sumstats
    except Exception as e:
        print(traceback.format_exc())
        diversity_logger.debug(f'Build of the summary stats: Failed -- {e}')


def make_heatmatrix(merged, stage, col_list, index_list, funder=None):
    """
    Make the heatmatrix!
    """
    merged = merged[merged['Agency'].notnull()]
    if funder!='All Funders':
    # @TODO: Why are there nulls here? Due to no disease terms?
        merged = merged[merged['Agency'].str.contains(funder, regex=False)]
#    else:
#        diversity_logger.debug(f'Potential null funders in make_heatmatrix()')
    count_df = pd.DataFrame(columns=col_list)
    sum_df = pd.DataFrame(columns=col_list)
    merged = merged[merged['STAGE'] == stage]
    for year in range(2008, final_year+1):
        temp_merged = merged[merged['DATE'].str.contains(str(year))]
        temp_count_df = pd.DataFrame(index=index_list,
                                     columns=col_list)
        temp_sum_df = pd.DataFrame(index=index_list,
                                   columns=col_list)
        for index in temp_count_df.index:
            for column in temp_count_df.columns:
                length = len(temp_merged[(temp_merged['Broader'] == index) &
                                         (temp_merged['parentterm'] == column)])
                temp_count_df.at[index, column] = length
                sum = temp_merged[(temp_merged['Broader'] == index) &
                                  (temp_merged['parentterm'] == column)]['N'].sum()
                temp_sum_df.at[index, column] = sum
        if temp_sum_df.sum().sum() > 0:
#                            temp_sum_df = ((temp_sum_df /
#                            temp_sum_df.sum().sum()) * 100).round(2)
            temp_sum_df['Year'] = year
            if funder != 'All Funders':
                temp_sum_df['Funder'] = funder
            sum_df = pd.concat([sum_df, temp_sum_df], sort=False)
        if temp_count_df.sum().sum() > 0:
#            temp_count_df = ((temp_count_df /
#                              temp_count_df.sum().sum()) * 100).round(2)
            temp_count_df['Year'] = year
            if funder != 'All Funders':
                temp_count_df['Funder'] = funder
            count_df = pd.concat([count_df, temp_count_df], sort=False)
    return sum_df, count_df


def make_heatmap_dfs(data_path):
    """
        Make the heatmap dfs
    """
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               usecols=['STUDY ACCESSION', 'DISEASE/TRAIT'],
                               sep='\t')
        Cat_Map = pd.read_csv(os.path.join(data_path, 'catalog',
                                           'raw', 'Cat_Map.tsv'),
                              sep='\t',
                              usecols=['Disease trait', 'Parent term'])
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
        agencies = pd.read_csv(os.path.join(data_path, 'pubmed', 'agencies.tsv'),
                               sep='\t', usecols=['Agencies'])
        agencies = agencies['Agencies'].to_list()
        paper_grants = read_paper_grants(['PUBMEDID', 'Agency'])
        merged = pd.merge(merged, paper_grants, how='left',
                          left_on='PUBMEDID', right_on='PUBMEDID')
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
        col_list = merged['parentterm'].unique().tolist()
        index_list = merged[merged['Broader'].notnull()]['Broader'].unique().tolist()

        init_count = pd.DataFrame()
        init_sum = pd.DataFrame()
        init_merge = merged[merged['STAGE'] == 'initial']
        for year in range(2008, final_year + 1):
            temp_merged = init_merge[init_merge['DATE'].str.contains(str(year))]
            temp_count_df = pd.DataFrame(index=index_list,
                                         columns=col_list)
            temp_sum_df = pd.DataFrame(index=index_list,
                                       columns=col_list)
            for index in temp_count_df.index:
                for column in temp_count_df.columns:
                    length = len(temp_merged[(temp_merged['Broader'] == index) &
                                             (temp_merged['parentterm'] == column)])
                    temp_count_df.at[index, column] = length
                    sum = temp_merged[(temp_merged['Broader'] == index) &
                                      (temp_merged['parentterm'] == column)]['N'].sum()
                    temp_sum_df.at[index, column] = sum
            if temp_sum_df.sum().sum() > 0:
                temp_sum_df['Year'] = year
                temp_sum_df['Funder'] = 'All Funders'
                init_sum = pd.concat([init_sum, temp_sum_df], sort=False)
            if temp_count_df.sum().sum() > 0:
                temp_count_df['Year'] = year
                temp_count_df['Funder'] = 'All Funders'
                init_count = pd.concat([init_count, temp_count_df], sort=False)

        rep_count = pd.DataFrame()
        rep_sum = pd.DataFrame()
        rep_merge = merged[merged['STAGE'] == 'replication']
        for year in range(2008, final_year + 1):
            temp_merged = rep_merge[rep_merge['DATE'].str.contains(str(year))]
            temp_count_df = pd.DataFrame(index=index_list,
                                         columns=col_list)
            temp_sum_df = pd.DataFrame(index=index_list,
                                       columns=col_list)
            for index in temp_count_df.index:
                for column in temp_count_df.columns:
                    length = len(temp_merged[(temp_merged['Broader'] == index) &
                                             (temp_merged['parentterm'] == column)])
                    temp_count_df.at[index, column] = length
                    sum = temp_merged[(temp_merged['Broader'] == index) &
                                      (temp_merged['parentterm'] == column)]['N'].sum()
                    temp_sum_df.at[index, column] = sum
            if temp_sum_df.sum().sum() > 0:
                temp_sum_df['Year'] = year
                temp_sum_df['Funder'] = 'All Funders'
                rep_sum = pd.concat([rep_sum, temp_sum_df], sort=False)
            if temp_count_df.sum().sum() > 0:
                temp_count_df['Year'] = year
                temp_count_df['Funder'] = 'All Funders'
                rep_count = pd.concat([init_count, temp_count_df], sort=False)
#        agencies.append('All Funders')
        res = Parallel(n_jobs=-1)(delayed(make_heatmatrix)(merged, 'initial', col_list, index_list, agency) for agency in agencies)
        init_sum_list = [item[0] for item in res]
        init_count_list = [item[1] for item in res]
        init_sum_list = pd.concat(init_sum_list)
        init_sum = pd.concat([init_sum, init_sum_list])
        init_count_list = pd.concat(init_count_list)
        init_count = pd.concat([init_count, init_count_list])
        init_sum.to_csv(os.path.join(data_path,
                                     'toplot',
                                     'heatmap_sum_initial.csv'))
        init_count.to_csv(os.path.join(data_path,
                                       'toplot',
                                       'heatmap_count_initial.csv'))
        res = Parallel(n_jobs=-1)(delayed(make_heatmatrix)(merged, 'replication', col_list, index_list, agency) for agency in agencies)
        rep_sum_list = [item[0] for item in res]
        rep_count_list = [item[1] for item in res]
        rep_sum_list = pd.concat(rep_sum_list)
        rep_sum = pd.concat([rep_sum, rep_sum_list])
        rep_count_list = pd.concat(rep_count_list)
        rep_count = pd.concat([rep_count, rep_count_list])
        rep_sum.to_csv(os.path.join(data_path,
                                    'toplot',
                                    'heatmap_sum_replication.csv'))
        rep_count.to_csv(os.path.join(data_path,
                                      'toplot',
                                      'heatmap_count_replication.csv'))
        diversity_logger.info('Build of the heatmap dataset: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the heatmap dataset: Failed -- {e}')



def make_annual_CoR(countrylookup, Clean_CoR, year, funder=None):
    if funder != None:
        Clean_CoR = Clean_CoR[Clean_CoR['Agency'].str.contains(funder, regex=False)]
    tempdf = Clean_CoR[Clean_CoR['Date'].str.contains(str(year))]
    tempdf_sum = pd.DataFrame(
    tempdf.groupby(['Cleaned Country'])['N'].sum())
    tempdf_count = pd.DataFrame(tempdf.groupby(['Cleaned Country'])['N'].count()).\
                rename(columns={'N': 'Count'})
    tempdf_merged = pd.merge(tempdf_sum, tempdf_count,
                             left_index=True, right_index=True)
    tempdf_merged['Year'] = str(year)
    country_merged = pd.merge(countrylookup, tempdf_merged,
                              left_index=True,
                              right_index=True)
    country_merged = country_merged.reset_index()
    count_pc = round((pd.to_numeric(country_merged['Count']) /
                      pd.to_numeric(country_merged['Count']).sum()) * 100, 2)
    country_merged['Count (%)'] = count_pc
    n_pc = round((pd.to_numeric(country_merged['N']) /
                  pd.to_numeric(country_merged['N'].sum())) * 100, 2)
    country_merged['N (%)'] = n_pc
    if funder == None:
       country_merged['Funder'] = 'All Funders'
    else:
        country_merged['Funder'] = funder
    return country_merged


def make_choro_df(data_path):
    """
        Create the dataframe for the choropleth map
    """
    try:
        Cat_Ancestry = pd.read_csv(os.path.join(data_path,
                                                'catalog',
                                                'synthetic',
                                                'Cat_Anc_wBroader.tsv'),
                                   sep='\t')
        annual_df = pd.DataFrame(columns=['Year', 'N', 'Count', 'Funder'])
        paper_grants = read_paper_grants(['PUBMEDID', 'Agency'])
        Cat_Ancestry = pd.merge(Cat_Ancestry, paper_grants, how='left',
                                left_on='PUBMEDID', right_on='PUBMEDID')
        Clean_CoR = make_clean_CoR(Cat_Ancestry, data_path)
        countrylookup = pd.read_csv(os.path.join(data_path,
                                                 'support',
                                                 'Country_Lookup.csv'),
                                    index_col='Country')
        agencies = pd.read_csv(os.path.join(data_path, 'pubmed', 'agencies.tsv'),
                               sep='\t', usecols=['Agencies'])
        agencies = agencies['Agencies'].to_list()
        for year in range(2008, final_year + 1):
            country_merged =  make_annual_CoR(countrylookup, Clean_CoR, year)
            annual_df = annual_df.append(country_merged, sort=True)
        Clean_CoR = Clean_CoR[Clean_CoR['Agency'].notnull()]
        for year in range(2008, final_year + 1):
            for agency in agencies:
                country_merged = make_annual_CoR(countrylookup, Clean_CoR, year, agency)
                annual_df = annual_df.append(country_merged, sort=True)
        annual_df = annual_df.reset_index().drop(['level_0'], axis=1).rename({'index': 'Country'}, axis=1)
        annual_df = annual_df.sort_values(by = 'Year', ascending=True)
        annual_df.to_csv(os.path.join(data_path, 'toplot', 'choro_df.csv'), index=False)

        diversity_logger.info('Build of the choropleth dataset: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the choropleth dataset: Failed -- {e}')


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
        agencies = pd.read_csv(os.path.join(data_path, 'pubmed', 'agencies.tsv'),
                               sep='\t', usecols=['Agencies'])
        agencies = agencies['Agencies'].to_list()
        merge_ts_init_sum = []
        merge_ts_rep_sum = []
        merge_ts_init_count = []
        merge_ts_rep_count = []
        paper_grants = read_paper_grants(['PUBMEDID', 'Agency'])
        Cat_Ancestry = pd.merge(Cat_Ancestry, paper_grants, how='left',
                                left_on='PUBMEDID', right_on='PUBMEDID')
        ts_init_sum = pd.DataFrame(index=range(2007, final_year + 1), columns=[])
        ts_rep_sum = pd.DataFrame(index=range(2007, final_year + 1), columns=[])
        ts_init_count = pd.DataFrame(index=range(2007, final_year + 1), columns=[])
        ts_rep_count = pd.DataFrame(index=range(2007, final_year + 1), columns=[])
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
        ts_init_sum_pc = (((ts_init_sum.T / ts_init_sum.T.sum()).T) * 100).round(2)
        ts_init_sum_pc['Funder'] = 'All Funders'
        ts_init_sum_pc = ts_init_sum_pc.reset_index().rename({'index': 'Year'}, axis=1)
        merge_ts_init_sum.append(ts_init_sum_pc)
        ts_init_count_pc = (((ts_init_count.T / ts_init_count.T.sum()).T)*100).round(2)
        ts_init_count_pc['Funder'] = 'All Funders'
        ts_init_count_pc = ts_init_count_pc.reset_index().rename({'index': 'Year'}, axis=1)
        merge_ts_init_count.append(ts_init_count_pc)
        ts_rep_sum_pc = ((ts_rep_sum.T /ts_rep_sum.T.sum()).T * 100).round(2)
        ts_rep_sum_pc['Funder'] = 'All Funders'
        ts_rep_sum_pc = ts_rep_sum_pc.reset_index().rename({'index': 'Year'}, axis=1)
        merge_ts_rep_sum.append(ts_rep_sum_pc)
        ts_rep_count_pc = (((ts_rep_count.T / ts_rep_count.T.sum()).T)*100).round(2)
        ts_rep_count_pc['Funder'] = 'All Funders'
        ts_rep_count_pc = ts_rep_count_pc.reset_index().rename({'index': 'Year'}, axis=1)
        merge_ts_rep_sum.append(ts_rep_count_pc)

        for agency in agencies:
            ts_init_sum = pd.DataFrame(index=range(2007, final_year + 1), columns=[])
            ts_rep_sum = pd.DataFrame(index=range(2007, final_year + 1), columns=[])
            ts_init_count = pd.DataFrame(index=range(2007, final_year + 1), columns=[])
            ts_rep_count = pd.DataFrame(index=range(2007, final_year + 1), columns=[])
            Init_Cat_Ancestry_Age = Cat_Ancestry[(Cat_Ancestry['STAGE'] == 'initial') &
                                                 (Cat_Ancestry['Agency'].str.contains(agency, regex=False))]
            Rep_Cat_Ancestry_Age = Cat_Ancestry[(Cat_Ancestry['STAGE'] == 'replication') &
                                                (Cat_Ancestry['Agency'].str.contains(agency, regex=False))]
            for ancestry in broader_list:
                for year in range(2007, final_year+1):
                    temp_df = Init_Cat_Ancestry_Age[(Init_Cat_Ancestry_Age['Year'] == year) &
                                                    (Init_Cat_Ancestry_Age['Broader'] == ancestry)]
                    ts_init_sum.at[year, ancestry] = temp_df['N'].sum()
                    ts_init_count.at[year, ancestry] = len(temp_df['N'])
                    temp_df = Rep_Cat_Ancestry_Age[(Rep_Cat_Ancestry_Age['Year'] == year) &
                                                   (Rep_Cat_Ancestry_Age['Broader'] == ancestry)]
                    ts_rep_sum.at[year, ancestry] = temp_df['N'].sum()
                    ts_rep_count.at[year, ancestry] = len(temp_df['N'])

            ts_init_sum_pc =(((ts_init_sum.T / ts_init_sum.T.sum()).T) * 100).round(2)
            if len(ts_init_sum_pc.dropna())>0:
                ts_init_sum_pc['Funder'] = agency
                ts_init_sum_pc = ts_init_sum_pc.reset_index().rename({'index': 'Year'}, axis=1)
                merge_ts_init_sum.append(ts_init_sum_pc)
            ts_init_count_pc = (((ts_init_count.T / ts_init_count.T.sum()).T) * 100).round(2)
            if len(ts_init_count_pc.dropna())>0:
                ts_init_count_pc['Funder'] = agency
                ts_init_count_pc = ts_init_count_pc.reset_index().rename({'index': 'Year'}, axis=1)
                merge_ts_init_count.append(ts_init_count_pc)
            ts_rep_sum_pc = ((ts_rep_sum.T / ts_rep_sum.T.sum()).T * 100).round(2)
            if len(ts_rep_sum_pc.dropna())>0:
                ts_rep_sum_pc['Funder'] = agency
                ts_rep_sum_pc = ts_rep_sum_pc.reset_index().rename({'index': 'Year'}, axis=1)
                merge_ts_rep_sum.append(ts_rep_sum_pc)
            ts_rep_count_pc = (((ts_rep_count.T / ts_rep_count.T.sum()).T) * 100).round(2)
            if len(ts_rep_count_pc.dropna())>0:
                ts_rep_count_pc['Funder'] = agency
                ts_rep_count_pc = ts_rep_count_pc.reset_index().rename({'index': 'Year'}, axis=1)
                merge_ts_rep_count.append(ts_rep_count_pc)
        pd.concat(merge_ts_init_sum).to_csv(os.path.join(data_path, 'toplot', savename + '_initial_sum.csv'),
                                            index=False)
        pd.concat(merge_ts_init_count).to_csv(os.path.join(data_path, 'toplot', savename + '_initial_count.csv'),
                                              index=False)
        pd.concat(merge_ts_rep_sum).to_csv(os.path.join(data_path, 'toplot', savename + '_replication_sum.csv'),
                                           index=False)
        pd.concat(merge_ts_rep_count).to_csv(os.path.join(data_path, 'toplot', savename + '_replication_count.csv'),
                                             index=False)
        diversity_logger.info('Build of the ts dataset: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the ts dataset: Failed -- {e}')

def read_stud(columns):
    return pd.read_csv(os.path.join(data_path, 'catalog',
                                    'raw', 'Cat_Stud.tsv'),
                       sep='\t', usecols=columns)

def read_map(columns):
    return pd.read_csv(os.path.join(data_path, 'catalog',
                                    'raw','Cat_Map.tsv'),
                       sep='\t', usecols=columns)

def save_synth(df_to_save, filename):
    df_to_save.to_csv(os.path.join(data_path, 'catalog',
                                   'synthetic', filename),
                   sep='\t')

def read_cat_anc_wb():
     return pd.read_csv(os.path.join(data_path, 'catalog','synthetic',
                                     'Cat_Anc_wBroader.tsv'),
                        sep='\t', index_col=False, parse_dates=['DATE'],
                        low_memory=False) # @TODO specify dtypes

def read_paper_grants(columns=['PUBMEDID', 'Acronym', 'Agency', 'GrantID']):
    return pd.read_csv(os.path.join(data_path, 'pubmed',
                                    'paper_grants.tsv'),
                       sep="\t", usecols=columns)

def make_doughnut_year(year):
    Cat_Stud = read_stud(['STUDY ACCESSION', 'DISEASE/TRAIT',
                          'ASSOCIATION COUNT'])
    Cat_Map = read_map(['Disease trait', 'Parent term'])
    Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                           left_on='DISEASE/TRAIT',
                           right_on='Disease trait')
    save_synth(Cat_StudMap, 'Disease_to_Parent_Mappings.tsv')
    Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION',
                               'DISEASE/TRAIT', 'ASSOCIATION COUNT']]
    Cat_StudMap = Cat_StudMap.drop_duplicates()
    Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
    Cat_Anc_wBroader = read_cat_anc_wb()
    Cat_Anc_wBroader = Cat_Anc_wBroader[Cat_Anc_wBroader['Broader'] !=
                                        'In Part Not Recorded']
    paper_grants = read_paper_grants(['PUBMEDID', 'Agency'])
    merged = pd.merge(Cat_StudMap, Cat_Anc_wBroader,
                      how='left', on='STUDY ACCESSION')
    merged = pd.merge(merged, paper_grants, how='left',
                      left_on='PUBMEDID', right_on='PUBMEDID')
    merged["DATE"] = merged["DATE"].astype(str)

    # @TODO error handle these: if these are zero, something is perhaps funky
    for field in ['parentterm', 'Agency', 'Broader', 'N', 'STAGE', 'DATE']:
        merged = merged[merged[field].notnull()]

    merged = merged[merged['Broader'].notnull() &
                    merged['parentterm'].notnull()]
    merged = merged[['N', 'STAGE', 'DATE', 'Broader',
                     'parentterm', 'Agency', 'ASSOCIATION COUNT']]
    counter = 0
    agencies = pd.read_csv(os.path.join(data_path, 'pubmed', 'agencies.tsv'),
                           sep='\t', usecols=['Agencies'])
    agencies = agencies['Agencies'].to_list()
    cols = ['Broader', 'parentterm', 'Year', 'Funder', 'InitialN',
            'InitialCount', 'ReplicationN', 'ReplicationCount',
            'InitialAssociationSum']
    doughnut_df = pd.DataFrame(index=[], columns=cols)
    init_year = merged[(merged['STAGE'] == 'initial') &
                       (merged['DATE'].str.contains(str(year)))]
    rep_year = merged[(merged['STAGE'] == 'replication') &
                      (merged['DATE'].str.contains(str(year)))]
    init_year = init_year[['N', 'Broader', 'parentterm', 'Agency', 'ASSOCIATION COUNT']]
    rep_year = rep_year[['N', 'Broader', 'parentterm', 'Agency']]
    rep_yearN = rep_year['N'].sum()
    init_yearN = init_year['N'].sum()
    init_yearAss = init_year['ASSOCIATION COUNT'].sum()
    rep_yearC = len(rep_year)
    init_yearC = len(init_year)
    for ancestry in merged['Broader'].unique().tolist():
        doughnut_df.at[counter, 'Broader'] = ancestry
        doughnut_df.at[counter, 'parentterm'] = 'All Parent Terms'
        doughnut_df.at[counter, 'Year'] = year
        doughnut_df.at[counter, 'Funder'] = 'All Funders'
        rep_year_anc = rep_year[rep_year['Broader'] == ancestry]
        rep_year_anc = rep_year_anc[['N', 'parentterm', 'Agency']]
        rep_year_ancN = rep_year_anc['N'].sum()
        init_year_anc = init_year[init_year['Broader'] == ancestry]
        init_year_anc = init_year_anc[['N', 'parentterm', 'Agency', 'ASSOCIATION COUNT']]
        init_year_ancN = init_year_anc['N'].sum()
        init_year_anc_Ass = init_year_anc['ASSOCIATION COUNT'].sum()
        if (rep_yearN !=0) & (rep_year_ancN != 0):
            doughnut_df.at[counter, 'ReplicationN'] = round((rep_year_ancN /
                                                             rep_yearN) * 100, 2)
        elif (rep_yearN !=0) & (rep_year_ancN == 0):
            doughnut_df.at[counter, 'ReplicationN'] = 0
        else:
            doughnut_df.at[counter, 'ReplicationN'] = np.nan
        if init_yearN !=0:
            doughnut_df.at[counter, 'InitialN'] = round((init_year_ancN /
                                                         init_yearN) * 100, 2)
        elif (init_yearN !=0) & (init_year_ancN == 0):
            doughnut_df.at[counter, 'InitialN'] = 0
        else:
            doughnut_df.at[counter, 'InitialN'] = np.nan
        if init_yearAss !=0:
            doughnut_df.at[counter,
            'InitialAssociationSum'] = round((init_year_anc_Ass /
                                              init_yearAss) * 100, 2)
        elif (init_yearAss != 0) & (init_year_ancAss == 0):
            doughnut_df.at[counter, 'InitialAssociationSum'] = 0
        else:
            doughnut_df.at[counter, 'InitialAssociationSum'] = np.nan
        init_year_ancC = len(init_year_anc)
        rep_year_ancC = len(rep_year_anc)
        if (rep_yearC !=0) & (rep_year_ancC!=0):
            doughnut_df.at[counter, 'ReplicationCount'] = round((rep_year_ancC /
                                                                 rep_yearC) * 100, 2)
        elif (rep_yearC !=0) & (rep_year_ancC==0):
            doughnut_df.at[counter, 'ReplicationCount'] = 0
        else:
            doughnut_df.at[counter, 'ReplicationCount'] = np.nan
        if init_yearC !=0:
            doughnut_df.at[counter, 'InitialCount'] = round((init_year_ancC /
                                                             init_yearC) * 100, 2)
        elif (init_yearC !=0) & (init_year_ancC==0):
            doughnut_df.at[counter, 'InitialCount'] = 0
        else:
            doughnut_df.at[counter, 'InitialCount'] = np.nan
        counter += 1
        for parent in merged['parentterm'].unique().tolist():
            doughnut_df.at[counter, 'Broader'] = ancestry
            doughnut_df.at[counter, 'parentterm'] = parent
            doughnut_df.at[counter, 'Year'] = year
            doughnut_df.at[counter, 'Funder'] = 'All Funders'
            rep_year_anc_par = rep_year_anc[rep_year_anc['parentterm'] == parent].copy()
            rep_year_anc_par = rep_year_anc_par[['N', 'Agency']]
            rep_year_anc_parN = rep_year_anc_par['N'].sum()
            rep_year_par = rep_year[rep_year['parentterm'] == parent].copy()
            rep_year_parN = rep_year_par['N'].sum()
            init_year_anc_par = init_year_anc[init_year_anc['parentterm'] == parent].copy()
            init_year_anc_par = init_year_anc_par[['N', 'Agency', 'ASSOCIATION COUNT']]
            init_year_anc_parN = init_year_anc_par['N'].sum()
            init_year_anc_parAss = init_year_anc_par['ASSOCIATION COUNT'].sum()
            init_year_par = init_year[init_year['parentterm'] == parent].copy()
            init_year_parN = init_year_par['N'].sum()
            init_year_parAss = init_year_par['ASSOCIATION COUNT'].sum()
            if (rep_year_parN != 0) & (rep_year_anc_parN != 0):
                doughnut_df.at[counter, 'ReplicationN'] = round((rep_year_anc_parN /
                                                                 rep_year_parN) * 100, 2)
            elif (rep_year_parN != 0) & (rep_year_anc_parN == 0):
                doughnut_df.at[counter, 'ReplicationN'] = 0
            else:
                doughnut_df.at[counter, 'ReplicationN'] = np.nan

            if (init_year_parN != 0) & (init_year_anc_parN != 0):
                doughnut_df.at[counter, 'InitialN'] = round((init_year_anc_parN /
                                                             init_year_parN) * 100, 2)
            elif (init_year_parN != 0) & (init_year_anc_parN == 0):
                doughnut_df.at[counter, 'InitialN'] = 0
            else:
                doughnut_df.at[counter, 'InitialN'] = np.nan

            if (init_year_parAss != 0) & (init_year_anc_parAss != 0):
                doughnut_df.at[counter, 'InitialAssociationSum'] = round((init_year_anc_parAss /
                                                                          init_year_parAss) * 100, 2)
            elif (init_year_parAss != 0) & (init_year_anc_parAss == 0):
                doughnut_df.at[counter, 'InitialAssociationSum'] = 0
            else:
                doughnut_df.at[counter, 'InitialAssociationSum'] = np.nan

            rep_year_anc_parC = len(rep_year_anc_par)
            rep_year_parC = len(rep_year_par)
            init_year_anc_parC = len(init_year_anc_par)
            init_year_parC = len(init_year_par)

            if (rep_year_parC != 0) & (rep_year_anc_parC != 0):
                doughnut_df.at[counter, 'ReplicationCount'] = round((rep_year_anc_parC /
                                                                     rep_year_parC) * 100, 2)
            elif (rep_year_parC != 0) & (rep_year_anc_parC == 0):
                doughnut_df.at[counter, 'ReplicationCount'] = 0
            else:
                doughnut_df.at[counter, 'ReplicationCount'] = np.nan
            if (init_year_parC != 0) & (init_year_anc_parC != 0):
                doughnut_df.at[counter, 'InitialCount'] = round((init_year_anc_parC /
                                                                 init_year_parC) * 100, 2)
            elif (init_year_parC != 0) & (init_year_anc_parC == 0):
                doughnut_df.at[counter, 'InitialCount'] = 0
            else:
                doughnut_df.at[counter, 'InitialCount'] = np.nan

            counter += 1
            for agency in agencies:
                doughnut_df.at[counter, 'Broader'] = ancestry
                doughnut_df.at[counter, 'parentterm'] = parent
                doughnut_df.at[counter, 'Year'] = year
                doughnut_df.at[counter, 'Funder'] = agency

                rep_year_anc_par_age = rep_year_anc_par[rep_year_anc_par['Agency'].str.contains(agency, regex=False)]
                rep_year_anc_par_ageN = rep_year_anc_par_age['N'].sum()
                rep_year_par_age = rep_year_par[rep_year_par['Agency'].str.contains(agency, regex=False)]
                rep_year_par_ageN = rep_year_par_age['N'].sum()
                rep_year_anc_par_ageC = len(rep_year_anc_par_age)
                rep_year_par_ageC = len(rep_year_par_age)

                if (rep_year_par_ageN != 0) & (rep_year_anc_par_ageN !=0):
                   doughnut_df.at[counter, 'ReplicationN'] = round((rep_year_anc_par_ageN /
                                                                    rep_year_par_ageN) * 100, 2)
                elif (rep_year_par_ageN != 0) & (rep_year_anc_par_ageN == 0):
                    doughnut_df.at[counter, 'ReplicationN'] = 0
                else:
                    doughnut_df.at[counter, 'ReplicationN'] = np.nan
                if (rep_year_par_ageC != 0) & (rep_year_anc_par_ageC !=0):
                    doughnut_df.at[counter, 'ReplicationCount'] = round((rep_year_anc_par_ageC /
                                                                         rep_year_par_ageC) * 100, 2)
                elif (rep_year_par_ageC != 0) & (rep_year_anc_par_ageC == 0):
                    doughnut_df.at[counter, 'ReplicationCount'] = 0
                else:
                    doughnut_df.at[counter, 'ReplicationCount'] = np.nan

                init_year_anc_par_age = init_year_anc_par[init_year_anc_par['Agency'].str.contains(agency, regex=False)]
                init_year_anc_par_ageN = init_year_anc_par_age['N'].sum()
                init_year_anc_par_ageAss = init_year_anc_par_age['ASSOCIATION COUNT'].sum()
                init_year_par_age = init_year_par[init_year_par['Agency'].str.contains(agency, regex=False)]
                init_year_par_ageN = init_year_par_age['N'].sum()
                init_year_par_ageAss = init_year_par_age['ASSOCIATION COUNT'].sum()
                init_year_anc_par_ageC = len(init_year_anc_par_age)
                init_year_par_ageC = len(init_year_par_age)

                if (init_year_par_ageN != 0) & (init_year_anc_par_ageN !=0):
                   doughnut_df.at[counter, 'InitialN'] = round((init_year_anc_par_ageN /
                                                                init_year_par_ageN) * 100, 2)
                elif (init_year_par_ageN != 0) & (init_year_anc_par_ageN == 0):
                    doughnut_df.at[counter, 'InitialN'] = 0
                else:
                    doughnut_df.at[counter, 'InitialN'] = np.nan

                if (init_year_par_ageAss != 0) & (init_year_anc_par_ageAss !=0):
                    doughnut_df.at[counter, 'InitialAssociationSum'] = round((init_year_anc_par_ageAss /
                                                                             init_year_par_ageAss) * 100, 2)
                elif (init_year_par_ageN != 0) & (init_year_anc_par_ageN == 0):
                    doughnut_df.at[counter, 'InitialAssociationSum'] = 0
                else:
                    doughnut_df.at[counter, 'InitialAssociationSum'] = np.nan

                if (init_year_par_ageC != 0) & (init_year_anc_par_ageC !=0):
                    doughnut_df.at[counter, 'InitialCount'] = round((init_year_anc_par_ageC /
                                                                     init_year_par_ageC) * 100, 2)
                elif (init_year_par_ageC != 0) & (init_year_anc_par_ageC == 0):
                    doughnut_df.at[counter, 'InitialCount'] = 0
                else:
                    doughnut_df.at[counter, 'InitialCount'] = np.nan
                counter = counter + 1
    return doughnut_df


def make_doughnut_df(data_path):
    """ Make the doughnut chart dataframe for use in main.py"""
    try:
        results = Parallel(n_jobs=4)(delayed(make_doughnut_year)(i) for i in range(2008, final_year+1))
        doughnut_df = pd.concat(results)
        doughnut_df['Broader'] = doughnut_df['Broader'].str.\
            replace('Hispanic/Latin American', 'Hispanic/L.A.')
        doughnut_df = doughnut_df[(doughnut_df['InitialN'].notnull()) &
                                  (doughnut_df['InitialCount'].notnull()) &
                                  (doughnut_df['ReplicationN'].notnull()) &
                                  (doughnut_df['ReplicationCount'].notnull())]
        doughnut_df.to_csv(os.path.join(data_path, 'toplot', 'doughnut_df.csv'), index=False)
        diversity_logger.info('Build of the doughnut datasets: Complete')
    except Exception as e:
        print(traceback.format_exc())
        diversity_logger.debug(f'Build of the doughnut datasets: Failed -- {e}')


def make_bubbleplot_df(data_path):
    """ Make data for the bubbleplot """
    try:
        Cat_Stud = read_stud(['STUDY ACCESSION', 'DISEASE/TRAIT'])
        Cat_Map = read_map(['Disease trait', 'Parent term'])
        Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                               left_on='DISEASE/TRAIT',
                               right_on='Disease trait')
        save_synth(Cat_StudMap, 'Disease_to_Parent_Mappings.tsv')
        Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION', 'DISEASE/TRAIT']]
        Cat_StudMap = Cat_StudMap.drop_duplicates()
        Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
        Cat_Anc_wBroader = read_cat_anc_wb()
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
        merged['cssclassname'] = merged['Broader'].str.replace('/', '-', regex=False).str. \
                                     replace('\s', '-', regex=True).str.lower() + " " + \
                                 merged['parentterm'].str.replace(',\s+', ',', regex=True).str. \
                                     replace('\s', '-', regex=True).str. \
                                     replace(',', ' ', regex=False).str.lower()
        merged['DiseaseOrTrait'] = merged['DiseaseOrTrait'].str. \
            replace('>', 'more than', regex=False).str. \
            replace('<', 'less than', regex=False)
        merged['trait'] = merged['DiseaseOrTrait'].str. \
            replace('\s', '-', regex=True).str. \
            replace('(', '-', regex=False).str. \
            replace(')', '-', regex=False).str.lower()
        paper_grants = read_paper_grants(['PUBMEDID', 'Agency'])
        merged = pd.merge(merged, paper_grants, how='left', left_on='PUBMEDID', right_on="PUBMEDID")
        merged = merged.rename({'Agency': 'funder'}, axis=1)
        merged.to_csv(os.path.join(data_path, 'toplot', 'bubble_df.csv'), index=False)
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
        Cat_Anc = Cat_Anc[Cat_Anc['Broader'].notnull()]
        Cat_Anc = Cat_Anc[Cat_Anc['N'].notnull()]
        Cat_Anc.to_csv(os.path.join(data_path, 'catalog', 'synthetic', 'Cat_Anc_wBroader.tsv'),
                       sep='\t',
                       index=False)
        diversity_logger.info('Clean of the raw GWAS Catalog datasets: Complete')
    except Exception as e:
        diversity_logger.debug(f'Clean of the raw GWAS Catalog datasets: Failed -- {e}')


def make_clean_CoR(Cat_Anc, data_path):
    """
        Clean the country of recruitment field for the geospatial analysis.
    """
    try:
        with open(os.path.abspath(
                  os.path.join(data_path, 'catalog', 'synthetic',
                               'ancestry_CoR.csv')), 'w') as fileout:
            rec_out = csv.writer(fileout, delimiter=',', lineterminator='\n')
            rec_out .writerow(['Date', 'PUBMEDID', 'N', 'Cleaned Country', 'Agency'])
            for index, row in Cat_Anc.iterrows():
                if len(row['COUNTRY OF RECRUITMENT'].split(',')) == 1:
                    rec_out .writerow([row['DATE'],
                                       str(row['PUBMEDID']),
                                       str(row['N']),
                                       str(row['COUNTRY OF RECRUITMENT']),
                                       str(row['Agency'])])
        Clean_CoR = pd.read_csv(os.path.abspath(
                                os.path.join(data_path, 'catalog', 'synthetic',
                                             'ancestry_CoR.csv')),encoding = 'ISO-8859-1')
        cleaner = {'U.S.': 'United States',
                   'Gambia': 'Gambia, The',
                   'U.K.': 'United Kingdom',
                   'Republic of Korea': 'Korea, South',
                   'Czech Republic': 'Czechia',
                   'Russian Federation': 'Russia',
                   'Iran \(Islamic Republic of\)': 'Iran',
                   'Viet Nam': 'Vietnam',
                   'United Republic of Tanzania': 'Tanzania',
                   'Republic of Ireland': 'Ireland',
                   'Micronesia \(Federated States of\)': 'Micronesia, Federated States of'}
        for key, value in cleaner.items():
            Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(key, value)
        Clean_CoR = Clean_CoR[Clean_CoR['Cleaned Country'] != 'NR']
        Clean_CoR.to_csv(os.path.abspath(
                         os.path.join(data_path, 'catalog', 'synthetic',
                                      'GWAScatalogue_CleanedCountry.tsv')),
                         sep='\t',
                         index=False)
        return Clean_CoR
        diversity_logger.info('Clean of the raw Country datasets: Complete')
    except Exception as e:
        diversity_logger.debug(f'Clean of the raw Country datasets: Failed -- {e}')

def download_cat(data_path, ebi_download):
    """ Downloads the data from the ebi main site and ftp"""
    try:
        r = requests.get(ebi_download + 'studies_alternative')
        if r.status_code == 200:
            catstud_name = r.headers['Content-Disposition'].split('=')[1]
            with open(os.path.join(data_path, 'catalog', 'raw',
                                   'Cat_Stud.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
            diversity_logger.info(f'Download of {catstud_name}: Complete')
        else:
            diversity_logger.debug(f'Download of {catstud_name}: Failed')
        r = requests.get(ebi_download + 'ancestry')
        if r.status_code == 200:
            catanc_name = r.headers['Content-Disposition'].split('=')[1]
            with open(os.path.join(data_path, 'catalog', 'raw',
                                   'Cat_Anc.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
            diversity_logger.info(f'Download of {catanc_name}: Complete')
        else:
            diversity_logger.debug(f'Download of {catanc_name}: Failed')
        r = requests.get(ebi_download + 'full')
        if r.status_code == 200:
            catfull_name = r.headers['Content-Disposition'].split('=')[1]
            with open(os.path.join(data_path, 'catalog', 'raw',
                                   'Cat_Full.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
            diversity_logger.info(f'Download of {catfull_name}: Complete')
        else:
            diversity_logger.debug(f'Download of {catfull_name}: Failed')
        requests_ftp.monkeypatch_session()
        s = requests.Session()
        ftpsite = 'ftp://ftp.ebi.ac.uk/'
        subdom = '/pub/databases/gwas/releases/latest/'
        file = 'gwas-efo-trait-mappings.tsv'
        r = s.get(ftpsite+subdom+file)
        if r.status_code == 200:
            with open(os.path.join(data_path, 'catalog', 'raw',
                                   'Cat_Map.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
            diversity_logger.info(f'Download of efo-trait-mappings: Complete')
        else:
            diversity_logger.debug(f'Download of efo-trait-mappings: Failed')
    except Exception as e:
        diversity_logger.debug('Problem downloading Catalog data!' + str(e))


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
    try:
        static_files = ['support/Country_Lookup.csv',
                        'support/dict_replacer_broad.tsv']
        data_okay = os.path.exists(data_path)

        for i in range(len(static_files)):
            if not data_okay:
                break
            data_okay = os.path.exists(os.path.join(data_path, static_files[i]))

        return data_okay
        diversity_logger.info('Unpacking static_data.zip: Complete')
    except Exception as e:
        diversity_logger.debug(f'Unpacking static_data.zip: Failed -- {e}')


def generate_funder_data(data_path):
    try:
        raw_path = os.path.join(data_path, 'catalog', 'raw')
        cat_stud = pd.read_csv(os.path.join(raw_path, 'Cat_Stud.tsv'), sep='\t', usecols=['PUBMEDID'])
        cat_anc = pd.read_csv(os.path.join(raw_path, 'Cat_Anc.tsv'), sep='\t', usecols=['PUBMEDID'])
        cat_full = pd.read_csv(os.path.join(raw_path, 'Cat_Full.tsv'), sep='\t', usecols=['PUBMEDID'])
        cat_stud_n = len(cat_stud.drop_duplicates())
        cat_anc_n = len(cat_anc.drop_duplicates())
        cat_full_n = len(cat_full.drop_duplicates())
        merged = pd.concat([cat_stud, cat_anc, cat_full]).drop_duplicates()
        if (len(merged) > cat_stud_n) and (len(merged) > cat_anc_n) and (len(merged) > cat_full_n):
            diversity_logger.debug(f'Something in cat_anc/cat_full that isnt in cat_stud')
        id_list = merged['PUBMEDID'].astype(str).tolist()
        paper_grants = pd.DataFrame(index=id_list, columns=['Acronym', 'Agency', 'Country', 'GrantID'])
        paper_grants['Acronym'] = ''
        paper_grants['Agency'] = ''
        paper_grants['Country'] = ''
        paper_grants['GrantID'] = ''
        acronyms = pd.DataFrame()
        agencies = pd.DataFrame(columns=['Counter'])
        countries = pd.DataFrame(columns=['Counter'])
        grantids = pd.DataFrame(columns=['Counter'])
        Entrez.email = 'contact@gwasdiversitymonitor.com'
        papers = Entrez.read(Entrez.efetch(db='pubmed', retmode='xml', id=','.join(id_list)))
        counter = 0
        for i, paper in enumerate(papers['PubmedArticle']):
            PMID = str(papers['PubmedArticle'][counter]['MedlineCitation']['PMID'])
            if 'GrantList' in paper['MedlineCitation']['Article']:
                for grant in paper['MedlineCitation']['Article']['GrantList']:
                    if 'GrantID' in grant:
                        if str(grant['GrantID']) not in str(paper_grants.loc[PMID, 'GrantID']):
                            paper_grants.at[PMID, 'GrantID'] = paper_grants.at[PMID, 'GrantID'] + str(grant['GrantID']) + ';'
                        if str(grant['GrantID']) in grantids.index:
                            grantids.at[str(grant['GrantID']), 'Counter'] += 1
                        else:
                            grantids.at[str(grant['GrantID']), 'Counter'] = 1
                    else:
                        if 'Missing ID' not in str(paper_grants.loc[PMID, 'GrantID']):
                            paper_grants.at[PMID, 'GrantID'] = paper_grants.at[PMID, 'GrantID'] + 'Missing ID;'
                    if 'Acronym' in grant:
                        if str(grant['Acronym']) not in str(paper_grants.loc[PMID, 'Acronym']):
                            paper_grants.at[PMID, 'Acronym'] = paper_grants.at[PMID, 'Acronym'] + str(grant['Acronym']) + ';'
                        if str(grant['Acronym']) in acronyms.index:
                            acronyms.at[str(grant['Acronym']), 'Counter'] += 1
                        else:
                            acronyms.at[str(grant['Acronym']), 'Counter'] = 1
                    else:
                        if 'Missing Acronym' not in str(paper_grants.loc[PMID, 'Acronym']):
                            paper_grants.at[PMID, 'Acronym'] = paper_grants.at[PMID, 'Acronym'] + 'Missing Acronym;'
                    if 'Agency' in grant:
                        if str(grant['Agency']) not in str(paper_grants.loc[PMID, 'Agency']):
                            paper_grants.at[PMID, 'Agency'] = paper_grants.at[PMID, 'Agency'] + str(grant['Agency']) + ';'
                        if str(grant['Agency']) in agencies.index:
                            agencies.at[str(grant['Agency']), 'Counter'] += 1
                        else:
                            agencies.at[str(grant['Agency']), 'Counter'] = 1
                    else:
                        if 'Missing Agency' not in str(paper_grants.loc[PMID, 'Agency']):
                            paper_grants.at[PMID, 'Agency'] = paper_grants.at[PMID, 'Agency'] + 'Missing Agency;'
                    if 'Country' in grant:
                        if str(grant['Country']) not in str(paper_grants.loc[PMID, 'Country']):
                            paper_grants.at[PMID, 'Country'] = paper_grants.at[PMID, 'Country'] + str(grant['Country']) + ';'
                        if str(grant['Country']) in countries.index:
                            countries.at[str(grant['Country']), 'Counter'] += 1
                        else:
                            countries.at[str(grant['Country']), 'Counter'] = 1
                    else:
                        if 'Missing Country' not in str(paper_grants.loc[PMID, 'Country']):
                            paper_grants.at[PMID, 'Country'] = paper_grants.at[PMID, 'Country'] + 'Missing Country;'
            else:
                paper_grants.at[PMID, 'Country'] = 'Missing Country;'
                paper_grants.at[PMID, 'Agency'] = 'Missing Agency;'
                paper_grants.at[PMID, 'Acronym'] = 'Missing Acronym;'
                paper_grants.at[PMID, 'GrantID'] = 'Missing ID;'
            counter += 1

        paper_grants['Country'] = paper_grants['Country'].str[:-1]
        paper_grants['Acronym'] = paper_grants['Acronym'].str[:-1]
        paper_grants['Agency'] = paper_grants['Agency'].str[:-1]
        paper_grants['GrantID'] = paper_grants['GrantID'].str[:-1]
        paper_grants = paper_grants.reset_index().rename({'index': 'PUBMEDID'}, axis=1)

        countries = countries.reset_index().rename({'index': 'Countries'}, axis=1)
        agencies = agencies.reset_index().rename({'index': 'Agencies'}, axis=1)
        acronyms = acronyms.reset_index().rename({'index': 'Acronyms'}, axis=1)
        grantids = grantids.reset_index().rename({'index': 'GrantIDs'}, axis=1)

        if os.path.exists(os.path.join(data_path, 'pubmed')) is False:
            os.mkdir(os.path.join(data_path, 'pubmed'))
        pubmed_path = os.path.join(data_path, 'pubmed')
        paper_grants.to_csv(os.path.join(pubmed_path, 'paper_grants.tsv'), sep='\t', index=False)
        acronyms.to_csv(os.path.join(pubmed_path, 'acronyms.tsv'), sep='\t', index=False)
        agencies.to_csv(os.path.join(pubmed_path, 'agencies.tsv'), sep='\t', index=False)
        countries.to_csv(os.path.join(pubmed_path, 'countries.tsv'), sep='\t', index=False)
        grantids.to_csv(os.path.join(pubmed_path, 'grantids.tsv'), sep='\t', index=False)
        diversity_logger.info('Generating the funding lookup tables: Complete')
    except Exception as e:
        diversity_logger.debug(f'Generating the funding lookup tables: Failed -- {e}')

def check_paths(data_path):
    for subdir in ['catalog', 'pubmed', 'summary', 'support',
                   'todownload', 'toplot', 'unmapped']:
        if os.path.exists(os.path.join(data_path, subdir)) is False:
            os.mkdir(os.path.join(data_path, subdir))
    if os.path.exists(os.path.join(data_path, 'catalog', 'raw')) is False:
        os.mkdir(os.path.join(data_path, 'catalog', 'raw'))
        os.mkdir(os.path.join(data_path, 'catalog', 'raw'))
    if os.path.exists(os.path.join(data_path, 'catalog', 'synthetic')) is False:
        os.mkdir(os.path.join(data_path, 'catalog', 'synthetic'))

def strip_accents(text):
    try:
        text = unicode(text, 'utf-8')
    except NameError:
        pass
    text = unicodedata.normalize('NFD', text)\
           .encode('ascii', 'ignore')\
           .decode("utf-8")
    return str(text)

def clean_agency(agency, funder_cleaner):
    agency = re.sub("\(.*?\)", "()", agency)
    agency = agency.replace('[', '')
    agency = agency.replace(']', '')
    agency = agency.replace('(', '')
    agency = agency.replace(')', '')
    agency = re.sub(' +', ' ', agency)
    agency = strip_accents(agency)
    agency = agency.strip()
    agency = agency.replace('&amp;', 'and')
    if 'veteran' in agency.lower():
        agency = 'Veterans Affairs'
    if agency in funder_cleaner.keys():
        agency = funder_cleaner[agency]
    return agency

def clean_funder_data(data_path):
    try:
        file = open(os.path.join(data_path, 'support', 'funder_cleaner.json'), 'r')
        funder_cleaner = json.load(file)
        agencies = pd.read_csv(os.path.join(data_path, 'pubmed', 'agencies.tsv'),
                               sep='\t', usecols=['Agencies', 'Counter'])
        for row in range(0, len(agencies)):
            agency = clean_agency(agencies.loc[row, 'Agencies'], funder_cleaner)
            agencies.loc[row, 'Agencies'] = agency
        agencies = agencies.groupby(['Agencies']).sum(['Counter'])
        under_50 = agencies[agencies['Counter']<50]['Counter'].sum()
        agencies = agencies[agencies['Counter']>=50]
        agencies.loc['Under 50 Studies', 'Counter'] = under_50
        agencies = agencies.sort_values(by='Counter', ascending=False)
        clean_agencies_list = agencies.index.to_list()
        agencies.to_csv(os.path.join(data_path, 'pubmed', 'agencies.tsv'), sep='\t')
        paper_grants = read_paper_grants()
        for row, column in paper_grants.iterrows():
            agency_holder = ''
            agency_string = paper_grants.loc[row, 'Agency']
            #TODO: why does a row have an empty/unaccounted for funder fields?
            try:
                for agency in agency_string.split(';'):
                    agency = clean_agency(agency, funder_cleaner)
                    if (agency not in clean_agencies_list) and (agency!= 'Missing Agency'):
                        agency = 'Under 50 Studies'
                    agency_holder = agency_holder + agency + ';'
            except AttributeError:
                pass
            agency_holder = agency_holder[:-1]
            paper_grants.loc[row, 'Agency'] = agency_holder
        clean_agencies_list.insert(0, 'All Funders')
        clean_agencies_list.remove('Unclear')
        clean_agencies_list.remove('Under 50 Studies')
        with open(os.path.join(data_path, 'pubmed', 'agency_list.txt'), 'w') as f:
            write = csv.writer(f)
            write.writerow(clean_agencies_list)
        agencies.to_csv(os.path.join(data_path, 'pubmed', 'agencies.tsv'), sep='\t')
        paper_grants.to_csv(os.path.join(data_path, 'pubmed', 'paper_grants.tsv'), sep='\t')
        diversity_logger.info('Cleaning and merging the funders: Complete')
    except Exception as e:
        diversity_logger.debug(f'Cleaning and merging the funders: Failed -- {e}')


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
    check_paths(data_path)
    ebi_download = 'https://www.ebi.ac.uk/gwas/api/search/downloads/'
    final_year = determine_year(datetime.date.today())
    diversity_logger.info('final year is being set to: ' + str(final_year))
    reports_path = os.path.join(os.getcwd(), 'reports')
    try:
#        download_cat(data_path, ebi_download)
#        clean_gwas_cat(data_path)
#        generate_funder_data(data_path)
#        clean_funder_data(data_path)
#        generate_reports(data_path, reports_path, diversity_logger)
#        make_bubbleplot_df(data_path)
#        make_doughnut_df(data_path)
#        tsinput = pd.read_csv(os.path.join(data_path, 'catalog', 'synthetic',
#                                           'Cat_Anc_wBroader.tsv'),  sep='\t')
#        make_timeseries_df(tsinput, data_path, 'ts1')
#        tsinput = tsinput[tsinput['Broader'] != 'In Part Not Recorded']
#        make_timeseries_df(tsinput, data_path, 'ts2')
        make_choro_df(data_path)
#        make_heatmap_dfs(data_path)
#        make_parent_list(data_path)
        sumstats = create_summarystats(data_path)
        zip_for_download(os.path.join(data_path, 'toplot'),
                         os.path.join(data_path, 'todownload'))
        json_converter(data_path)
        diversity_logger.info('generate_data.py ran successfully!')
    except Exception as e:
        print(traceback.format_exc())
        diversity_logger.debug(f'generate_data.py failed, uncaught error: {e}')
        sys.stderr.write(f'generate_data.py failed, see the log for details: {logfile}\n')
    logging.shutdown()
