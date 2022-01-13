import pandas as pd
import traceback
import json
import logging
import datetime
import numpy as np
import requests
import requests_ftp
import os
import csv
import shutil
import warnings
import zipfile
import math
warnings.filterwarnings("ignore")


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
    """
    try:
        dtypes = {'FIRST AUTHOR': str, 'DISEASE/TRAIT': str,
                  'ASSOCIATION COUNT': np.int64, 'JOURNAL': str,
                  'MAPPED_TRAIT': str}
        usecols = ['PUBMEDID', 'DATE', 'FIRST AUTHOR', 'STUDY ACCESSION',
                   'DISEASE/TRAIT', 'MAPPED_TRAIT', 'ASSOCIATION COUNT',
                   'JOURNAL']
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               sep='\t',
                               low_memory=False,
                               usecols=usecols,
                               quotechar='"',
                               warn_bad_lines=False,
                               error_bad_lines=False,
                               dtype=dtypes
                               )
        Cat_Full = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                            'raw', 'Cat_Full.tsv'),
                               sep='\t',
                               engine='python',
                               usecols=['P-VALUE'],
                               quotechar='"',
                               warn_bad_lines=False,
                               error_bad_lines=False,
                               dtype={'P-VALUE': object})
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                                    'synthetic',
                                                    'Cat_Anc_wBroader.tsv'),
                                       '\t', index_col=False,
                                       low_memory=False)
        temp_bubble_df = pd.read_csv(os.path.join(data_path,
                                                  'data/toplot', 'bubble_df.csv'),
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
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                                    'synthetic', 'Cat_Anc_wBroader.tsv'),
                                       sep='\t',
                                       index_col=False,
                                       low_memory=False)
        Cat_Anc_NoNR = Cat_Anc_wBroader[Cat_Anc_wBroader['Broader'] != 'In Part Not Recorded']
        no_NR_sum = Cat_Anc_NoNR['N'].sum()
        euro_sum = Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'] == 'European']['N'].sum()
        sumstats['total_european'] = round(((euro_sum / no_NR_sum)*100), 2)
        asia_sum = Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'] == 'Asian']['N'].sum()
        sumstats['total_asian'] = round(((asia_sum / no_NR_sum)*100), 2)
        afri_sum = Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'] == 'African']['N'].sum()
        sumstats['total_african'] = round(((afri_sum / no_NR_sum)*100), 2)
        oth_sum = Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Other')]['N'].sum()
        sumstats['total_othermixed'] = round(((oth_sum / no_NR_sum)*100), 2)
        cari_sum = Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Cari')]['N'].sum()
        sumstats['total_afamafcam'] = round(((cari_sum / no_NR_sum)*100), 2)
        hisp_sum = Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Hispanic')]['N'].sum()
        sumstats['total_hisorlatinam'] = round(((hisp_sum / no_NR_sum)*100), 2)

        # now rotate through the 4 filters with six broader ancestries:
        #        1. initial phase, number of participants
        #        2. initial phase, number of studies
        #        3. replication phase, number of participants
        #        4. replication phase, number of studies

        # 1.: first set up the sum of cat_anc_nonr_init
        anc_nonr_init = Cat_Anc_NoNR[Cat_Anc_NoNR['STAGE'] == 'initial']
        anc_nonr_init_sum = anc_nonr_init['N'].sum()

        # 1.1.: then for euro
        disc_euro = anc_nonr_init[anc_nonr_init['Broader'] == 'European']
        disc_part_euro_sum = disc_euro['N'].sum()
        disc_part_euro_pc = round(((disc_part_euro_sum / anc_nonr_init_sum) * 100), 2)
        sumstats['discovery_participants_european'] = disc_part_euro_pc

        # 1.2.: then for asian
        disc_asia = anc_nonr_init[anc_nonr_init['Broader'] == 'Asian']
        disc_part_asia_sum = disc_asia['N'].sum()
        disc_part_asia_pc = round(((disc_part_asia_sum / anc_nonr_init_sum) * 100), 2)
        sumstats['discovery_participants_asian'] = disc_part_asia_pc

        # 1.3.: then for african
        disc_afri = anc_nonr_init[anc_nonr_init['Broader'] == 'African']
        disc_part_afri_sum = disc_afri['N'].sum()
        disc_part_afri_pc = round(((disc_part_afri_sum / anc_nonr_init_sum) * 100), 2)
        sumstats['discovery_participants_african'] = disc_part_afri_pc

        # 1.4.: then for other/mixed
        disc_othe = anc_nonr_init[anc_nonr_init['Broader'].str.contains('Other')]
        disc_part_othe_sum = disc_othe['N'].sum()
        disc_part_othe_pc = round(((disc_part_othe_sum / anc_nonr_init_sum) * 100), 2)
        sumstats['discovery_participants_othermixed'] = disc_part_othe_pc

        # 1.5.: then for afamafcam
        disc_cari = anc_nonr_init[anc_nonr_init['Broader'].str.contains('Cari')]
        disc_part_cari_sum = disc_cari['N'].sum()
        disc_part_cari_pc = round(((disc_part_cari_sum / anc_nonr_init_sum) * 100), 2)
        sumstats['discovery_participants_afamafcam'] = disc_part_cari_pc

        # 1.6.: then for hisorlatinam
        disc_hisp = anc_nonr_init[anc_nonr_init['Broader'].str.contains('Hispanic')]
        disc_part_hisp_sum = disc_hisp['N'].sum()
        disc_part_hisp_pc = round(((disc_part_hisp_sum / anc_nonr_init_sum) * 100), 2)
        sumstats['discovery_participants_hisorlatinam'] = disc_part_hisp_pc

        # 2.: second set up the len of cat_anc_nonr_init
        anc_nonr_init_len = len(anc_nonr_init)

        # 2.1.: then for euro
        disc_euro_len = len(disc_euro)
        disc_euro_len_pc = round(((disc_euro_len / anc_nonr_init_len) * 100), 2)
        sumstats['discovery_studies_european'] = disc_euro_len_pc

        # 2.2.: then for asian
        disc_asia_len = len(disc_asia)
        disc_asia_len_pc = round(((disc_asia_len / anc_nonr_init_len) * 100), 2)
        sumstats['discovery_studies_asian'] = disc_asia_len_pc

        # 2.3.: then for african
        disc_afri_len = len(disc_afri)
        disc_afri_len_pc = round(((disc_afri_len / anc_nonr_init_len) * 100), 2)
        sumstats['discovery_studies_african'] = disc_afri_len_pc

        # 2.4.: then for othermixed
        disc_othe_len = len(disc_othe)
        disc_othe_len_pc = round(((disc_othe_len / anc_nonr_init_len) * 100), 2)
        sumstats['discovery_studies_othermixed'] = disc_othe_len_pc

        # 2.5.: then for afamafcam
        disc_cari_len = len(disc_cari)
        disc_cari_len_pc = round(((disc_cari_len / anc_nonr_init_len) * 100), 2)
        sumstats['discovery_studies_afamafcam'] = disc_cari_len_pc

        # 2.6.: then for hisorlatinam
        disc_hisp_len = len(disc_hisp)
        disc_cari_len_pc = round(((disc_cari_len / anc_nonr_init_len) * 100), 2)
        sumstats['discovery_studies_hisorlatinam'] = disc_cari_len_pc

        # 3.: first set up the sum of cat_anc_nonr_repl
        anc_nonr_repl = Cat_Anc_NoNR[Cat_Anc_NoNR['STAGE'] == 'replication']
        anc_nonr_repl_sum = anc_nonr_repl['N'].sum()

        # 3.1.: then for euro
        repl_euro = anc_nonr_repl[anc_nonr_repl['Broader'] == 'European']
        repl_part_euro_sum = repl_euro['N'].sum()
        repl_part_euro_pc = round(((repl_part_euro_sum / anc_nonr_repl_sum) * 100), 2)
        sumstats['replication_participants_european'] = repl_part_euro_pc

        # 3.2.: then for asian
        repl_asia = anc_nonr_repl[anc_nonr_repl['Broader'] == 'Asian']
        repl_part_asia_sum = repl_asia['N'].sum()
        repl_part_asia_pc = round(((repl_part_asia_sum / anc_nonr_repl_sum) * 100), 2)
        sumstats['replication_participants_asian'] = repl_part_asia_pc

        # 3.3.: then for african
        repl_afri = anc_nonr_repl[anc_nonr_repl['Broader'] == 'African']
        repl_part_afri_sum = repl_afri['N'].sum()
        repl_part_afri_pc = round(((repl_part_afri_sum / anc_nonr_repl_sum) * 100), 2)
        sumstats['replication_participants_african'] = repl_part_afri_pc

        # 3.4.: then for other/mixed
        repl_othe = anc_nonr_repl[anc_nonr_repl['Broader'].str.contains('Other')]
        repl_part_othe_sum = repl_othe['N'].sum()
        repl_part_othe_pc = round(((repl_part_othe_sum / anc_nonr_repl_sum) * 100), 2)
        sumstats['replication_participants_othermixed'] = repl_part_othe_pc

        # 3.5.: then for afamafcam
        repl_cari = anc_nonr_repl[anc_nonr_repl['Broader'].str.contains('Cari')]
        repl_part_cari_sum = repl_cari['N'].sum()
        repl_part_cari_pc = round(((repl_part_cari_sum / anc_nonr_repl_sum) * 100), 2)
        sumstats['replication_participants_afamafcam'] = repl_part_cari_pc

        # 3.6.: then for hisorlatinam
        repl_hisp = anc_nonr_repl[anc_nonr_repl['Broader'].str.contains('Hispanic')]
        repl_part_hisp_sum = repl_hisp['N'].sum()
        repl_part_hisp_pc = round(((repl_part_hisp_sum / anc_nonr_repl_sum) * 100), 2)
        sumstats['replication_participants_hisorlatinam'] = repl_part_hisp_pc

        # 4.: second set up the len of cat_anc_nonr_repl
        anc_nonr_repl_len = len(anc_nonr_repl)

        # 4.1.: then for euro
        repl_euro_len = len(repl_euro)
        repl_euro_len_pc = round(((repl_euro_len / anc_nonr_repl_len) * 100), 2)
        sumstats['replication_studies_european'] = repl_euro_len_pc

        # 4.2.: then for asian
        repl_asia_len = len(repl_asia)
        repl_asia_len_pc = round(((repl_asia_len / anc_nonr_repl_len) * 100), 2)
        sumstats['replication_studies_asian'] = repl_asia_len_pc

        # 4.3.: then for african
        repl_afri_len = len(repl_afri)
        repl_afri_len_pc = round(((repl_afri_len / anc_nonr_repl_len) * 100), 2)
        sumstats['replication_studies_african'] = repl_afri_len_pc

        # 4.4.: then for othermixed
        repl_othe_len = len(repl_othe)
        repl_othe_len_pc = round(((repl_othe_len / anc_nonr_repl_len) * 100), 2)
        sumstats['replication_studies_othermixed'] = repl_othe_len_pc

        # 4.5.: then for afamafcam
        repl_cari_len = len(repl_cari)
        repl_cari_len_pc = round(((repl_cari_len / anc_nonr_repl_len) * 100), 2)
        sumstats['replication_studies_afamafcam'] = repl_cari_len_pc

        # 4.6.: then for hisorlatinam
        repl_hisp_len = len(repl_hisp)
        repl_cari_len_pc = round(((repl_cari_len / anc_nonr_repl_len) * 100), 2)
        sumstats['replication_studies_hisorlatinam'] = repl_cari_len_pc

        sumstats['timeupdated'] = datetime.datetime.now().\
                                  strftime("%Y-%m-%d %H:%M:%S")
        if os.path.exists(os.path.join(data_path, 'data/unmapped',
                                       'unmapped_diseases.txt')):
            unmapped_dis = pd.read_csv(os.path.join(data_path, 'data/unmapped',
                                       'unmapped_diseases.txt'))
            sumstats['unmapped_diseases'] = len(unmapped_dis)
        else:
            sumstats['unmapped_diseases'] = 0
        json_path = os.path.join(data_path, 'data/summary', 'summary.json')
        with open(json_path, 'w') as outfile:
            json.dump(sumstats, outfile)
        diversity_logger.info('Build of the summary stats: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the summary stats: Failed -- {e}')
    return sumstats


def make_heatmatrix(merged, stage, out_path):
    """
    Make the heatmatrix!
    """
    col_list = merged['parentterm'].unique().tolist()
    col_list.append('Year')
    index_list = merged[merged['Broader'].notnull()]['Broader'].\
        unique().tolist()
    count_df = pd.DataFrame(columns=col_list)
    sum_df = pd.DataFrame(columns=col_list)
    for year in range(2008, final_year+1):
        temp_merged = merged[(merged['STAGE'] == stage) &
                             (merged['DATE'].str.contains(str(year)))]
        temp_count_df = pd.DataFrame(index=index_list,
                                     columns=col_list)
        for index in temp_count_df.index:
            for column in temp_count_df.columns:
                length = len(temp_merged[(temp_merged['Broader'] == index) &
                                         (temp_merged['parentterm'] == column)])
                temp_count_df.at[index, column] = length
                temp_count_df.at[index, 'Year'] = year
        count_df = count_df.append(temp_count_df, sort=False)
        temp_sum_df = pd.DataFrame(index=index_list,
                                   columns=col_list)
        for index in temp_sum_df.index:
            for column in temp_sum_df.columns:
                sum = temp_merged[(temp_merged['Broader'] == index) &
                                  (temp_merged['parentterm'] == column)]['N'].sum()
                temp_sum_df.at[index, column] = sum
            temp_sum_df.at[index, 'Year'] = year
        sum_df = sum_df.append(temp_sum_df, sort=False)
    sum_df.to_csv(os.path.join(out_path, 'heatmap_sum_'+stage+'.csv'))
    count_df.to_csv(os.path.join(out_path, 'heatmap_count_'+stage+'.csv'))


def make_heatmap_dfs(data_path):
    """
        Make the heatmap dfs
    """
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               usecols = ['STUDY ACCESSION', 'DISEASE/TRAIT'],
                               sep='\t')
        Cat_Map = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                           'raw', 'Cat_Map.tsv'),
                              sep='\t',
                              usecols = ['Disease trait', 'Parent term'])
        Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                               left_on='DISEASE/TRAIT',
                               right_on='Disease trait')
        Cat_StudMap.to_csv(os.path.join(data_path, 'data/catalog', 'synthetic',
                                        'Disease_to_Parent_Mappings.tsv'),
                           sep='\t')
        Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION',
                                   'DISEASE/TRAIT']].drop_duplicates()
        Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path,
                                                    'data/catalog',
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
                                   'data/catalog',
                                   'synthetic',
                                   'Cat_Anc_wBroader_withParents.tsv'),
                      sep='\t')
        if len(merged[merged['parentterm'].isnull()]) > 0:
            diversity_logger.debug('Wuhoh! There are some empty disease terms!')
            pd.Series(merged[merged['parentterm'].
                             isnull()]['DISEASE/TRAIT'].unique()).\
                to_csv(os.path.join(data_path, 'data/unmapped',
                                    'unmapped_diseases.txt'),
                       index=False)
        else:
            diversity_logger.info('No missing disease terms! Nice!')
        merged = merged[merged["parentterm"].notnull()]
        merged["parentterm"] = merged["parentterm"].astype(str)
        merged["DATE"] = merged["DATE"].astype(str)
        make_heatmatrix(merged, 'initial', os.path.join(data_path,
                                                        'data/toplot'))
        make_heatmatrix(merged, 'replication', os.path.join(data_path, 'data/toplot'))
        diversity_logger.info('Build of the heatmap dataset: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the heatmap dataset: Failed -- {e}')

def make_choro_df(data_path):
    """
        Create the dataframe for the choropleth map
    """
    try:
        Cat_Ancestry = pd.read_csv(os.path.join(data_path,
                                                'data/catalog',
                                                'synthetic',
                                                'Cat_Anc_wBroader.tsv'),
                                   sep='\t')
        annual_df = pd.DataFrame(columns=['Year', 'N', 'Count'])
        Clean_CoR = make_clean_CoR(Cat_Ancestry, data_path)
        countrylookup = pd.read_csv(os.path.join(data_path,
                                                 'data/support',
                                                 'Country_Lookup.csv'),
                                    index_col='Country')
        for year in range(2008, final_year+1):
            tempdf = Clean_CoR[Clean_CoR['Date'].str.contains(str(year))]
            tempdf_sum = pd.DataFrame(
                tempdf.groupby(['Cleaned Country'])['N'].sum())
            tempdf_count = pd.DataFrame(
                tempdf.groupby(['Cleaned Country'])['N'].count()).\
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
            annual_df = annual_df.append(country_merged, sort=True)
        annual_df = annual_df.reset_index().drop(['level_0'], axis=1)
        annual_df.to_csv(os.path.join(data_path, 'data/toplot', 'choro_df.csv'))
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
        ts_init_sum_pc.to_csv(os.path.join(data_path, 'data/toplot',
                                           savename + '_initial_sum.csv'),
                                 index=False)
        ts_init_count_pc = ((ts_init_count.T / ts_init_count.T.sum()).T)*100
        ts_init_count_pc = ts_init_count_pc.reset_index()
        ts_init_count_pc.to_csv(os.path.join(data_path, 'data/toplot',
                                             savename + '_initial_count.csv'),
                                   index=False)
        ts_rep_sum_pc = ((ts_rep_sum.T /ts_rep_sum.T.sum()).T)*100
        ts_rep_sum_pc = ts_rep_sum_pc.reset_index()
        ts_rep_sum_pc.to_csv(os.path.join(data_path, 'data/toplot',
                                          savename + '_replication_sum.csv'),
                                     index=False)
        ts_rep_count_pc = ((ts_rep_count.T / ts_rep_count.T.sum()).T)*100
        ts_rep_count_pc = ts_rep_count_pc.reset_index()
        ts_rep_count_pc.to_csv(os.path.join(data_path, 'data/toplot',
                                            savename + '_replication_count.csv'),
                                       index=False)
        diversity_logger.info('Build of the ts dataset: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the ts dataset: Failed -- {e}')


def make_doughnut_df(data_path):
    """ Make the doughnut chart dataframe for use in main.py"""
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               sep='\t',
                               usecols = ['STUDY ACCESSION',
                                          'DISEASE/TRAIT',
                                          'ASSOCIATION COUNT'])
        Cat_Map = pd.read_csv(os.path.join(data_path, 'data/catalog', 'raw',
                                           'Cat_Map.tsv'), sep='\t',
                              usecols = ['Disease trait', 'Parent term'])
        Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                               left_on='DISEASE/TRAIT',
                               right_on='Disease trait')
        Cat_StudMap.to_csv(os.path.join(data_path, 'data/catalog', 'synthetic',
                                        'Disease_to_Parent_Mappings.tsv'),
                           sep='\t')
        Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION',
                                   'DISEASE/TRAIT', 'ASSOCIATION COUNT']]
        Cat_StudMap = Cat_StudMap.drop_duplicates()
        Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path, 'data/catalog',
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
        doughnut_df.to_csv(os.path.join(data_path, 'data/toplot', 'doughnut_df.csv'))
        diversity_logger.info('Build of the doughnut datasets: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the doughnut datasets: Failed -- {e}')


def make_bubbleplot_df(data_path):
    """ Make data for the bubbleplot """
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               sep='\t',
                               usecols = ['STUDY ACCESSION', 'DISEASE/TRAIT'])
        Cat_Map = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                           'raw', 'Cat_Map.tsv'),
                              sep='\t',
                              usecols = ['Disease trait', 'Parent term'])
        Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                               left_on='DISEASE/TRAIT',
                               right_on='Disease trait')
        Cat_StudMap.to_csv(os.path.join(data_path, 'data/catalog', 'synthetic',
                                        'Disease_to_Parent_Mappings.tsv'),
                           sep='\t')
        Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION', 'DISEASE/TRAIT']]
        Cat_StudMap = Cat_StudMap.drop_duplicates()
        Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
        Cat_Anc_wBroader = pd.read_csv(os.path.join(data_path, 'data/catalog',
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
        merged.to_csv(os.path.join(data_path, 'data/toplot', 'bubble_df.csv'))
        diversity_logger.info('Build of the bubble datasets: Complete')
    except Exception as e:
        diversity_logger.debug(f'Build of the bubble datasets: Failed -- {e}')


def clean_gwas_cat(data_path):
    """ Clean the catalog and do some general preprocessing """
    try:
        Cat_Stud = pd.read_csv(os.path.join(data_path, 'data/catalog',
                                            'raw', 'Cat_Stud.tsv'),
                               header=0,
                               sep='\t',
                               encoding='utf-8',
                               index_col=False)
        Cat_Stud.fillna('N/A', inplace=True)
        Cat_Anc = pd.read_csv(os.path.join(data_path, 'data/catalog',
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
        cleaner_broad = pd.read_csv(os.path.join(data_path, 'data/support',
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
                to_csv(os.path.join(data_path, 'data/unmapped', 'unmapped_broader.txt'))
        else:
            diversity_logger.info('No missing Broader terms! Nice!')
        Cat_Anc = Cat_Anc[Cat_Anc['Broader'].notnull()]
        Cat_Anc = Cat_Anc[Cat_Anc['N'].notnull()]
        Cat_Anc.to_csv(os.path.join(data_path, 'data/catalog', 'synthetic', 'Cat_Anc_wBroader.tsv'),
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
                  os.path.join(data_path, 'data/catalog', 'synthetic',
                               'ancestry_CoR.csv')), 'w') as fileout:
            rec_out = csv.writer(fileout, delimiter=',', lineterminator='\n')
            rec_out .writerow(['Date', 'PUBMEDID', 'N', 'Cleaned Country'])
            for index, row in Cat_Anc.iterrows():
                if len(row['COUNTRY OF RECRUITMENT'].split(',')) == 1:
                    rec_out .writerow([row['DATE'],
                                       str(row['PUBMEDID']),
                                       str(row['N']),
                                       row['COUNTRY OF RECRUITMENT']])
        Clean_CoR = pd.read_csv(os.path.abspath(
                                os.path.join(data_path, 'data/catalog', 'synthetic',
                                             'ancestry_CoR.csv')))
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
                         os.path.join(data_path, 'data/catalog', 'synthetic',
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
            with open(os.path.join(data_path, 'data/catalog', 'raw',
                                   'Cat_Stud.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
            diversity_logger.info(f'Download of {catstud_name}: Complete')
        else:
            diversity_logger.debug(f'Download of {catstud_name}: Failed')
        r = requests.get(ebi_download + 'ancestry')
        if r.status_code == 200:
            catanc_name = r.headers['Content-Disposition'].split('=')[1]
            with open(os.path.join(data_path, 'data/catalog', 'raw',
                                   'Cat_Anc.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
            diversity_logger.info(f'Download of {catanc_name}: Complete')
        else:
            diversity_logger.debug(f'Download of {catanc_name}: Failed')
        r = requests.get(ebi_download + 'full')
        if r.status_code == 200:
            catfull_name = r.headers['Content-Disposition'].split('=')[1]
            with open(os.path.join(data_path, 'data/catalog', 'raw',
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
            with open(os.path.join(data_path, 'data/catalog', 'raw',
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
    uniq_dis_trait.to_csv(os.path.join(data_path, 'data/summary', 'uniq_dis_trait.txt'),
                          header=False,
                          index=False)


def make_parent_list(data_path):
    """ Makes a unique list of parent terms """
    df = pd.read_csv(os.path.join(data_path, 'data/catalog', 'synthetic',
                                  'Cat_Anc_wBroader_withParents.tsv'),
                     sep='\t')
    uniq_parent = pd.Series(df[df['parentterm'].
                               notnull()]['parentterm'].unique())
    uniq_parent.to_csv(os.path.join(data_path, 'data/summary', 'uniq_parent.txt'),
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


if __name__ == "__main__":
    logpath = os.path.abspath(os.path.join(__file__, '', 'logging'))
    diversity_logger = setup_logging(logpath)
    data_path = os.path.abspath(os.path.join(__file__, '', 'data'))
    ebi_download = 'https://www.ebi.ac.uk/gwas/api/search/downloads/'
    final_year = determine_year(datetime.date.today())
    diversity_logger.info('final year is being set to: ' + str(final_year))
    try:
        download_cat(data_path, ebi_download)
        clean_gwas_cat(data_path)
        make_bubbleplot_df(data_path)
        make_doughnut_df(data_path)
        tsinput = pd.read_csv(os.path.join(data_path, 'data/catalog', 'synthetic',
                                           'Cat_Anc_wBroader.tsv'),  sep='\t')
        make_timeseries_df(tsinput, data_path, 'ts1')
        tsinput = tsinput[tsinput['Broader'] != 'In Part Not Recorded']
        make_timeseries_df(tsinput, data_path, 'ts2')
        make_choro_df(data_path)
        make_heatmap_dfs(data_path)
        make_parent_list(data_path)
        sumstats = create_summarystats(data_path)
        zip_for_download(os.path.join(data_path, 'data/toplot'),
                         os.path.join(data_path, 'data/todownload'))
        diversity_logger.info('generate_data.py ran successfully!')
    except Exception as e:
        diversity_logger.debug(f'generate_data.py failed, uncaught error: {e}')
    logging.shutdown()
