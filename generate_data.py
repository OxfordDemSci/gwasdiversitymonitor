import pandas as pd
from decimal import Decimal
import json
import logging
import datetime
import numpy as np
import requests
import traceback
import requests_ftp
import os
import re
import csv
import shutil
from yattag import Doc, indent
import warnings
warnings.filterwarnings("ignore")


def setup_logging(logpath):
    ''' Set up the logging '''
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
    ''' create the summarystats .json then used to propogate index.html '''
    Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                        'raw', 'Cat_Stud.tsv'),
                           sep='\t', low_memory=False)
    Cat_Full = pd.read_csv(os.path.join(data_path, 'catalog',
                                        'raw', 'Cat_Full.tsv'),
                           sep='\t', low_memory=False)
    Cat_Anc_withBroader = pd.read_csv(os.path.join(data_path, 'catalog',
                                                   'synthetic',
                                                   'Cat_Anc_withBroader.tsv'),
                                      '\t', index_col=False, low_memory=False)
    temp_bubble_df = pd.read_csv(os.path.join(data_path,
                                              'toplot', 'bubble_df.csv'),
                                 sep=',', index_col=False, low_memory=False)
    sumstats = {}
    sumstats['number_studies'] = int(len(Cat_Stud['PUBMEDID'].unique()))
    sumstats['first_study_date'] = str(Cat_Stud['DATE'].min())
    dateminauth = Cat_Stud.loc[Cat_Stud['DATE'] == Cat_Stud['DATE'].min(),
                               'FIRST AUTHOR']
    sumstats['first_study_firstauthor'] = str(dateminauth.iloc[0])
    dateminpubmed = Cat_Stud.loc[Cat_Stud['DATE'] == Cat_Stud['DATE'].min(),
                                 'PUBMEDID']
    sumstats['first_study_pubmedid'] = int(dateminpubmed.iloc[0])
    sumstats['last_study_date'] = str(Cat_Stud['DATE'].max())
    datemaxauth = Cat_Stud.loc[Cat_Stud['DATE'] == Cat_Stud['DATE'].max(),
                               'FIRST AUTHOR']
    sumstats['last_study_firstauthor'] = str(datemaxauth.iloc[0])
    datemaxpubmed = Cat_Stud.loc[Cat_Stud['DATE'] == Cat_Stud['DATE'].max(),
                                 'PUBMEDID']
    sumstats['last_study_pubmedid'] = int(datemaxpubmed.iloc[0])
    sumstats['number_accessions'] = int(len(Cat_Stud['STUDY ACCESSION'].
                                            unique()))
    sumstats['number_diseasestraits'] = int(len(Cat_Stud['DISEASE/TRAIT'].
                                                unique()))
    sumstats['number_mappedtrait'] = int(len(Cat_Stud['MAPPED_TRAIT'].
                                             unique()))
    sumstats['found_associations'] = int(Cat_Stud['ASSOCIATION COUNT'].
                                         sum())
    sumstats['average_associations'] = float(Cat_Stud['ASSOCIATION COUNT'].
                                             mean())
    noneuro_trait = pd.DataFrame(temp_bubble_df[
                                 temp_bubble_df['Broader'] != 'European'].
                                 groupby(['DiseaseOrTrait']).size()).\
                                 sort_values(by=0, ascending=False).reset_index()['DiseaseOrTrait'][0]
    sumstats['noneuro_trait'] = str(noneuro_trait)
    sumstats['average_pval'] = float(round(Cat_Full['P-VALUE'].
                                           astype(float).mean(), 10))
    sumstats['threshold_pvals'] = int(len(Cat_Full[Cat_Full['P-VALUE'].
                                          astype(float) < 5.000000e-8]))
    sumstats['mostcommon_journal'] = str(Cat_Stud['JOURNAL'].mode()[0])
    sumstats['unique_journals'] = int(len(Cat_Stud['JOURNAL'].unique()))
    Cat_Anc_byN = Cat_Anc_withBroader[['STUDY ACCESSION', 'N']].\
        groupby(by='STUDY ACCESSION').sum()
    Cat_Anc_byN = Cat_Anc_byN.reset_index()
    Cat_Anc_withBroader = Cat_Anc_withBroader.\
        drop_duplicates('STUDY ACCESSION')[['PUBMEDID', 'FIRST AUTHOR',
                                            'STUDY ACCESSION']]
    Cat_Anc_byN = pd.merge(Cat_Anc_byN, Cat_Anc_withBroader, how='left',
                           left_on='STUDY ACCESSION',
                           right_on='STUDY ACCESSION')
    sumstats['large_accesion_N'] = int(Cat_Anc_byN.
                                       sort_values(by='N',
                                                   ascending=False)['N'].
                                       iloc[0])
    biggestauth = Cat_Anc_byN.loc[Cat_Anc_byN['N'] ==
                                  sumstats['large_accesion_N'],
                                  'FIRST AUTHOR']
    sumstats['large_accesion_firstauthor'] = str(biggestauth.iloc[0])
    biggestpubmed = Cat_Anc_byN.loc[Cat_Anc_byN['N'] ==
                                    sumstats['large_accesion_N'],
                                    'PUBMEDID']
    sumstats['large_accesion_pubmed'] = int(biggestpubmed.iloc[0])
    Cat_Anc_withBroader = pd.read_csv(os.path.join(data_path, 'catalog',
                                                   'synthetic',
                                                   'Cat_Anc_withBroader.tsv'),
                                      '\t', index_col=False, low_memory=False)
    Cat_Anc_NoNR = Cat_Anc_withBroader[Cat_Anc_withBroader['Broader'] != 'In Part Not Recorded']
    total_european = round(((Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'] == 'European']['N'].
                             sum() / Cat_Anc_NoNR['N'].sum())*100), 2)
    sumstats['total_european'] = total_european
    total_asian = round(((Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'] == 'Asian']['N'].
                          sum() / Cat_Anc_NoNR['N'].sum())*100), 2)
    sumstats['total_asian'] = total_asian
    total_african = round(((Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'] == 'African']['N'].
                           sum() / Cat_Anc_NoNR['N'].sum())*100), 2)
    sumstats['total_african'] = total_african
    total_othermixed = round(((Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Other')]['N'].
                               sum() / Cat_Anc_NoNR['N'].sum())*100), 2)
    sumstats['total_othermixed'] = total_othermixed
    total_afamafcam = round(((Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Cari')]['N'].
                           sum() / Cat_Anc_NoNR['N'].sum())*100), 2)
    sumstats['total_afamafcam'] = total_afamafcam
    total_hisorlatinam = round(((Cat_Anc_NoNR[Cat_Anc_NoNR['Broader'].str.contains('Hispanic')]['N'].
                           sum() / Cat_Anc_NoNR['N'].sum())*100), 2)
    sumstats['total_hisorlatinam'] = total_hisorlatinam

    # now rotate through the 4 filters
    Cat_Anc_NoNR = Cat_Anc_withBroader[Cat_Anc_withBroader['Broader'] != 'In Part Not Recorded']
    Cat_Anc_NoNR_initial = Cat_Anc_NoNR[Cat_Anc_NoNR['STAGE'] == 'initial']
    discovery_participants_european = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'European']['N'].
                                      sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['discovery_participants_european'] = discovery_participants_european
    discovery_participants_asian = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'Asian']['N'].
                                   sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['discovery_participants_asian'] = discovery_participants_asian
    discovery_participants_african = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'African']['N'].
                           sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['discovery_participants_african'] = discovery_participants_african
    discovery_participants_othermixed = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Other')]['N'].
                               sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['discovery_participants_othermixed'] = discovery_participants_othermixed
    discovery_participants_afamafcam = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Cari')]['N'].
                           sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['discovery_participants_afamafcam'] = discovery_participants_afamafcam
    discovery_participants_hisorlatinam = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Hispanic')]['N'].
                           sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['discovery_participants_hisorlatinam'] = discovery_participants_hisorlatinam

    discovery_studies_european = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'European']) /
                                        len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['discovery_studies_european'] = discovery_studies_european
    discovery_studies_asian = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'Asian']) /
                                     len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['discovery_studies_asian'] = discovery_studies_asian
    discovery_studies_african = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'African']) /
                                      len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['discovery_studies_african'] = discovery_studies_african
    discovery_studies_othermixed = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Other')]) /
                                         len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['discovery_studies_othermixed'] = discovery_studies_othermixed
    discovery_studies_afamafcam = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Cari')]) /
                                        len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['discovery_studies_afamafcam'] = discovery_studies_afamafcam
    discovery_studies_hisorlatinam = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Hispanic')]) /
                                           len(Cat_Anc_NoNR_initial))*100), 2)

    Cat_Anc_NoNR = Cat_Anc_withBroader[Cat_Anc_withBroader['Broader'] != 'In Part Not Recorded']
    Cat_Anc_NoNR_initial = Cat_Anc_NoNR[Cat_Anc_NoNR['STAGE'] == 'replication']
    replication_participants_european = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'European']['N'].
                                      sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['replication_participants_european'] = replication_participants_european
    replication_participants_asian = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'Asian']['N'].
                                   sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['replication_participants_asian'] = replication_participants_asian
    replication_participants_african = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'African']['N'].
                           sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['replication_participants_african'] = replication_participants_african
    replication_participants_othermixed = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Other')]['N'].
                               sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['replication_participants_othermixed'] = replication_participants_othermixed
    replication_participants_afamafcam = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Cari')]['N'].
                           sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['replication_participants_afamafcam'] = replication_participants_afamafcam
    replication_participants_hisorlatinam = round(((Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Hispanic')]['N'].
                           sum() / Cat_Anc_NoNR_initial['N'].sum())*100), 2)
    sumstats['replication_participants_hisorlatinam'] = replication_participants_hisorlatinam

    replication_studies_european = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'European']) /
                                        len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['replication_studies_european'] = replication_studies_european
    replication_studies_asian = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'Asian']) /
                                     len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['replication_studies_asian'] = replication_studies_asian
    replication_studies_african = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'] == 'African']) /
                                      len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['replication_studies_african'] = replication_studies_african
    replication_studies_othermixed = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Other')]) /
                                         len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['replication_studies_othermixed'] = replication_studies_othermixed
    replication_studies_afamafcam = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Cari')]) /
                                        len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['replication_studies_afamafcam'] = replication_studies_afamafcam
    replication_studies_hisorlatinam = round(((len(Cat_Anc_NoNR_initial[Cat_Anc_NoNR_initial['Broader'].str.contains('Hispanic')]) /
                                           len(Cat_Anc_NoNR_initial))*100), 2)
    sumstats['discovery_studies_hisorlatinam'] = discovery_studies_hisorlatinam
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
    return sumstats


def update_summarystats(sumstats, summaryfile):
    '''
    Write the summary stats json into the index.html
    This is currently extremely hacky and as a minimum needs to be using
    something like yattag, if not a custom js function to embed'''

    starttag = '<li> <p>'
    endtag = '</p></li>\n'
    sum_line_1 = 'There are a total of ' + str(sumstats['number_studies']) +\
                 ' studies in the Catalog.'
    sum_line_2 = 'Earliest study in catalogue was PubMedID ' +\
                 str(sumstats['first_study_pubmedid']) + ' on ' +\
                 str(sumstats['first_study_date']) + ' by ' +\
                 str(sumstats['first_study_firstauthor']) + ' et al.'
    sum_line_3 = 'Most recent study in the catalogue was PubMedID ' +\
                 str(sumstats['last_study_pubmedid']) + ' on ' +\
                 str(sumstats['last_study_date']) + ' by ' +\
                 str(sumstats['last_study_firstauthor']) + ' et al.'
    sum_line_4 = 'Accession with biggest sample is PubMedID ' +\
                 str(sumstats['large_accesion_pubmed']) + ' (N=' +\
                 str(sumstats['large_accesion_N']) + ') by ' +\
                 str(sumstats['large_accesion_firstauthor']) + ' et al.'
    sum_line_5 = 'There are a total of ' +\
                 str(sumstats['number_accessions']) +\
                 ' unique study accessions.'
    sum_line_6 = 'There are a total of ' +\
                 str(sumstats['number_diseasestraits']) +\
                 ' unique diseases and traits studied.'
    sum_line_7 = 'There are a total of ' +\
                 str(sumstats['number_mappedtrait']) +\
                 ' unique EBI "Mapped Traits".'
    sum_line_8 = 'The total number of associations found is ' +\
                 str(sumstats['found_associations']) + '.'
    sum_line_9 = 'The average number of associations found is ' +\
                 str(round(sumstats['average_associations'], 2)) + '.'
    sum_line_10 = 'Mean P-Value for the strongest SNP risk allele is: ' +\
                  "{:.3E}".format(Decimal(sumstats['average_pval'])) + '.'
    sum_line_11 = 'The number of associations reaching the 5e-8 significance threshold: ' +\
                  str(sumstats['threshold_pvals']) + '.'
    sum_line_12 = 'The journal to feature the most GWAS studies is: ' +\
                  str(sumstats['mostcommon_journal']) + '.'
    sum_line_13 = 'Total number of different journals publishing GWAS is: ' +\
                  str(sumstats['unique_journals']) + '.'
    sum_line_14 = 'Most frequently studied (Non-European) disease or trait: ' +\
                  str(sumstats['noneuro_trait']) + '.'

    outpath = os.path.abspath(os.path.join('gwasdiversitymonitor_app', 'templates', 'sumstats.html'))
    doc, tag, text, line = Doc().ttl()
    doc.asis('<!DOCTYPE html>')
    with tag('head'):
        doc.asis('<meta charset="utf-8">')
        doc.asis('<meta name="viewport" content="width=device-width, initial-scale=1">')
        doc.asis('<link rel="stylesheet" href="http://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/css/bootstrap.min.css">')
        with tag('script', src="https://ajax.googleapis.com/ajax/libs/jquery/1.12.0/jquery.min.js"):
            pass
        with tag('script', src="http://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/js/bootstrap.min.js"):
            pass
        with tag('html'):
            with tag('h1'):
                text('Summary Statistics')
            with tag('p'):
                text('Here we present a range of summary statistics related to the data which powers the dashboard (and some which doesnt), updated when the Catalog is updated. Note that these summary statistics and the figures themselves only use raw (wrangled) data from the GWAS Catalog. Currently:')
            with tag('body'):
                with tag('ul', id='summary-list'):
                    line('li', sum_line_1)
                    line('li', sum_line_2)
                    line('li', sum_line_3)
                    line('li', sum_line_4)
                    line('li', sum_line_5)
                    line('li', sum_line_6)
                    line('li', sum_line_7)
                    line('li', sum_line_8)
                    line('li', sum_line_9)
                    line('li', sum_line_10)
                    line('li', sum_line_11)
                    line('li', sum_line_12)
                    line('li', sum_line_13)
                    line('li', sum_line_14)
    summaryhtml = indent(doc.getvalue(), indent_text=True)
    with open(outpath, "w") as file:
        file.write(summaryhtml)

    with open(summaryfile, 'r') as file:
        summary = file.readlines()
    summary[172] = starttag + sum_line_1 + endtag
    summary[173] = starttag + sum_line_2 + endtag
    summary[174] = starttag + sum_line_3 + endtag
    summary[175] = starttag + sum_line_4 + endtag
    summary[176] = starttag + sum_line_5 + endtag
    summary[177] = starttag + sum_line_6 + endtag
    summary[178] = starttag + sum_line_7 + endtag
    summary[179] = starttag + sum_line_8 + endtag
    summary[180] = starttag + sum_line_9 + endtag
    summary[181] = starttag + sum_line_10 + endtag
    summary[182] = starttag + sum_line_11 + endtag
    summary[183] = starttag + sum_line_12 + endtag
    summary[184] = starttag + sum_line_13 + endtag
    summary[185] = starttag + sum_line_14 + endtag
    with open(summaryfile, 'w') as file:
        file.writelines(summary)


def update_downloaddata(sumstats, downloaddata):
    '''
        update the headline summary stats:
        this should again be using something like yattag and then be loaded
        in dynamically to the index.html, not just replacing lines
    '''
    with open(downloaddata, 'r') as file:
        download = file.readlines()
    download[120] = '<span class="badge badge-primary badge-pill">' + str(sumstats['total_european']) + '%</span>\n'
    download[123] = '<span class="badge badge-primary badge-pill">' + str(sumstats['total_african']) + '%</span>\n'
    download[126] = '<span class="badge badge-primary badge-pill">' + str(sumstats['total_afamafcam']) + '%</span>\n'
    download[129] = '<span class="badge badge-primary badge-pill">' + str(sumstats['total_othermixed']) + '%</span>\n'
    download[132] = '<span class="badge badge-primary badge-pill">' + str(sumstats['total_asian']) + '%</span>\n'
    download[135] = '<span class="badge badge-primary badge-pill">' + str(sumstats['total_hisorlatinam']) +'%</span>\n'
    with open(downloaddata, 'w') as file:
        file.writelines(download)


def update_header(headerfile):
    ''' update the 'last updated' part of the header on both tabs '''
    with open(headerfile, 'r') as file:
        header = file.readlines()
    header[109] = '<p style="font-size:14px;">Last updated: ' +\
                 datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") +\
                 '. Privacy Policy <a href="https://github.com/crahal/gwasdiversitymonitor/blob/master/privacy_policy.md">here</a>.<br>\n'

    with open(headerfile, 'w') as file:
        file.writelines(header)


def ancestry_cleaner(row, field):
    """ clean up the ancestry fields in GWASCatalogue_Ancestry.

    Keyword arguments:
    row: the row of the ancestry DataFrame
    field: the field of the ancestry dataframe ('initial' or 'replication')
    """
    free_text = re.sub(r'(\d+),?([\d+]?)', r'\1\2', str(row[field]))
    free_text = re.sub(r'(\d+)', r'; \1', str(free_text))
    free_text = punctuation_cleaner(free_text)
    free_text = remove_lower(free_text)
    free_text = remove_lower(free_text)
    free_text = free_text.replace('  ', ' ')
    free_text = free_text.replace('  ', ' ')
    free_text = list_remover(free_text)
    free_text = dict_replace(free_text)
    try:
        if free_text[-1] == ';':
            free_text = free_text[:-1]
    except ValueError:
        pass
    cleaned = []
    for ancestry in free_text[1:].split(';'):
        if " and" in ancestry.strip()[-4:]:
            cleaned.append(ancestry.replace(' and', '').strip())
        elif " or" in ancestry.strip()[-4:]:
            cleaned.append(ancestry.replace(' or', '').strip())
        else:
            cleaned.append(ancestry.strip())
    cleaned = ';'.join(cleaned)
    cleaned = cleaned.replace(';', ' ; ')
    for word in cleaned.split(' '):
        if (word.isalpha()) and (len(word) < 3) and word != "or":
            cleaned = cleaned.replace(word, '')
    cleaned = re.sub(r';\s+;', ';', cleaned)
    return cleaned


def make_heatmatrix(merged, stage, out_path):
    ''' not currently used by the dashboard '''
    col_list = merged['parentterm'].unique().tolist()
    col_list.append('Year')
    index_list = merged[merged['Broader'].notnull()]['Broader'].unique().tolist()
    count_df = pd.DataFrame(columns=col_list)
    sum_df = pd.DataFrame(columns=col_list)
    for year in range(2008, 2020):
        temp_merged = merged[(merged['STAGE'] == stage) &
                             (merged['DATE'].str.contains(str(year)))]
        temp_count_df = pd.DataFrame(index=index_list,
                                     columns=col_list)
        for index in temp_count_df.index:
            for column in temp_count_df.columns:
                temp_count_df.at[index,
                                 column] = len(temp_merged[(temp_merged['Broader'] == index) &
                                              (temp_merged['parentterm'] == column)])
                temp_count_df.at[index, 'Year'] = year
        count_df = count_df.append(temp_count_df, sort=False)
        temp_sum_df = pd.DataFrame(index=index_list,
                                   columns=col_list)
        for index in temp_sum_df.index:
            for column in temp_sum_df.columns:
                temp_sum_df.at[index,
                               column] = temp_merged[(temp_merged['Broader'] == index) &
                                                     (temp_merged['parentterm'] == column)]['N'].\
                                                     sum()
            temp_sum_df.at[index, 'Year'] = year
        sum_df = sum_df.append(temp_sum_df, sort=False)
    sum_df.to_csv(os.path.join(out_path, 'heatmap_sum_'+stage+'.csv'))
    count_df.to_csv(os.path.join(out_path, 'heatmap_count_'+stage+'.csv'))


def make_heatmap_dfs(data_path):
    ''' not currently used by the dashboard '''
    Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                        'raw', 'Cat_Stud.tsv'),
                           sep='\t')
    Cat_Stud = Cat_Stud[['STUDY ACCESSION', 'DISEASE/TRAIT']]
    Cat_Map = pd.read_csv(os.path.join(data_path, 'catalog',
                                        'raw', 'Cat_Map.tsv'),
                           sep='\t')
    Cat_Map = Cat_Map[['Disease trait', 'Parent term']]
    Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                           left_on = 'DISEASE/TRAIT',
                           right_on = 'Disease trait')
    Cat_StudMap.to_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                    'Disease_to_Parent_Mappings.tsv'), sep='\t')
    Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION',
                               'DISEASE/TRAIT']].drop_duplicates()
    Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
    Cat_Anc_withBroader = pd.read_csv(os.path.join(data_path,
                                                   'catalog',
                                                   'synthetic',
                                                   'Cat_Anc_withBroader.tsv'),
                                      '\t', index_col=False,
                                      parse_dates=['DATE'])
    Cat_Anc_withBroader = Cat_Anc_withBroader[Cat_Anc_withBroader['Broader'] != 'In Part Not Recorded']
    merged = pd.merge(Cat_StudMap, Cat_Anc_withBroader,
                      how='left', on='STUDY ACCESSION')
    #### TODO -- QUANTIFY THIS FOR THE SUMSTATS.HTML INTO THE JSON
    merged.to_csv(os.path.join(data_path,
                               'catalog',
                               'synthetic',
                               'Cat_Anc_withBroader_withParents.tsv'), '\t')
    if len(merged[merged['parentterm'].isnull()]) > 0:
        diversity_logger.debug('Wuhoh! There are some empty disease terms!')
        pd.Series(merged[merged['parentterm'].\
                         isnull()]['DISEASE/TRAIT'].unique()).\
        to_csv(os.path.join(data_path, 'unmapped', 'unmapped_diseases.txt'),
               index=False)
    else:
        diversity_logger.info('No missing disease terms! Nice!')
    merged = merged[merged["parentterm"].notnull()]
    merged["parentterm"] = merged["parentterm"].astype(str)
    merged["DATE"] = merged["DATE"].astype(str)
    make_heatmatrix(merged, 'initial', os.path.join(data_path,
                                                    'toplot'))
    make_heatmatrix(merged, 'replication', os.path.join(data_path,
                                                        'toplot'))


def dict_replace(text):
    """ sanitize the free text strings from the initial/replication fields.

    Keyword arguements:
    text: the free text string prior to splitting

    Taken from the comms bio paper, possibly needs updating periodically.
    This should probably be loaded in from a text file

    """
    replacedict = {'Arabian': 'Arab', 'HIspanic': 'Hispanic',
                   'Korculan': 'Korcula', 'Hispaic': 'Hispanic',
                   'Hispanics': 'Hispanic', 'Chineses': 'Chinese',
                   'Europea ': 'European ', 'Finish': 'Finnish',
                   'Val Bbera': 'Val Borbera', 'Chinese Han': 'Han Chinese',
                   'Erasmus Rchen': 'Erasmus Rucphen', 'Cilen ': 'Cilento',
                   'Erasmus Rupchen': 'Erasmus Rucphen', 'Clien': 'Cilento',
                   'Erasmus Ruchpen': 'Erasmus Rucphen', 'Geman': 'German',
                   'Old Amish': 'Old Order Amish', 'Americans': 'American',
                   'Japnese': 'Japanese', 'Finland': 'Finnish',
                   'Eat Aian': 'East Asian', 'Hipanic': 'Hispanic',
                   'Sub African': 'Sub-saharan African', 'Israeli': 'Isreali',
                   'Erasmus Rucphen Family': 'Erasmus Rucphen',
                   'Nfolk Island': 'Norfolk Island', 'Sh Asian': 'Asian',
                   'Hispanic Latino': 'Hispanic/Latino', 'Uighur': 'Uyghur',
                   'Hispanic Latin ': 'Hispanic/Latino',
                   'European ad': 'European', 'Val Bbera': 'Val Borbera',
                   'European End': 'European', 'Oceanian': 'Oceania',
                   'LatinoAmerican': 'Latino American', 'Giuli': 'Giulia',
                   'Cilentoto': 'Cilento', 'Friuli': 'Fruili',
                   'Giuliaa': 'Giulia', 'Rupchen': 'Rucphen', '≥': '',
                   'Korcula': 'Korkulan', 'Ruchpen': 'Rucphen',
                   'Brazillian': 'Brazilian', 'Sub-saharan': 'Sub Saharan',
                   'Tyrolian': 'Tyrolean', 'Seychelles': 'Seychellois',
                   'South Tyrolean': 'South Tyrol', 'Europen': 'European'}
    for key in replacedict:
        if key in text:
            text = text.replace(key, replacedict[key])
    return text


def list_remover(text):
    """ titlecase/capitalised words to remove from the strings which
    are not associated with countries, races or ancestries.

    Keyword arguements:
    text: the free text string prior to splitting

    Taken from the comms bio paper, possibly needs updating periodically.
    This should probably be loaded in from a text file

    """
    removelist = ['AIS', 'APOE', 'HIV', 'Â', 'HER2-', '1000 Genomes',
                  'MYCN-amplification', 'Alzheimer', 'ASD', 'OCB', 'BD',
                  'Genetically', 'Homogenous', 'BRCA', 'ALL', 'Coronary',
                  'Amyotrophic', 'Large', 'anti-dsDNA', 'Up ', 'Biracial',
                  'Follicular', 'Hodgkin', 'Lymphoma', 'GI', 'Abstinent',
                  'Schizophrenia', 'Îµ', 'JAK', 'ADHD', 'Diabetes',
                  'Allogenic', 'BGPI', 'Ischemic', 'Chronic', 'Major',
                  'Diabetic', 'Microalbuminuria', 'Asthma', 'Individuals',
                  'At ', "Barrett's", 'Crohn', 'Bipolar', 'MMR', 'HBV', 'RA',
                  'Elated', 'Escitalpram', 'Irritable', 'Lymphoblastoid',
                  'ACPA', 'HCC', 'pPhe508del', 'Anti', 'B2GPI', 'Kashin Beck',
                  '(LDL-cholesterol)', 'TPO', 'OCD', 'CCT', 'FTD', 'CAPOX B',
                  'LAC', 'LOAD', ' So ', 'MYCN-amplification', 'Yang', 'Tae',
                  'Eum', 'Non-abstinent', 'EBWL', 'Semantic', 'General',
                  'Cluster', 'Frontremporal', 'Frontotremporal',
                  'Frontotemporal', 'Graves', 'Attention', 'Autism', 'Liu',
                  'High', 'Low', 'HCV', 'Citalopram', 'Haemophilia', ' III ',
                  ' II ', ' I ', 'NFT', 'Progressive', 'Ancestry', 'Parkinson',
                  'Lin', 'BMD', 'GBA', 'Traylor', 'Consortium', ' Torgerson',
                  'EVE', 'Germain', 'Boraska', 'Cases', 'HapMap', 'vWF', 'HDL',
                  'LDL', ' Mild', 'Cognitive', 'Impairment', 'Sarcoidosis',
                  'Yu Zhi', 'Lymphoma', 'Impairment', 'Type', 'Kuru',
                  'Frontemporal', 'Erasmus', 'Barrett', 'Lofgren', 'Hashimoto',
                  'Family', 'Multiple', 'Richardson', 'Metropolitan']
    for word in removelist:
        text = text.replace(word, '')
    return text


def remove_lower(free_text):
    """ remove lowercase letters (assumed to not be associated with
    countries, races or ancestries.)
    Keyword arguements:
    text: the free text string prior to splitting

    Taken from the comms bio paper, possibly needs updating periodically.

    """
    free_text = free_text.replace('up to', '')
    for word in free_text.split(' '):
        if (word.title() != word.strip()):
            try:
                float(word)
            except ValueError:
                if ';' in word:
                    free_text = free_text.replace(word, ';').strip()
                elif (';' not in word) and (word != "and") and (word != "or"):
                    if free_text.find(word) == 0:
                        free_text = free_text.replace(word + ' ', ' ')
                    else:
                        free_text = free_text.replace(' ' + word, ' ')
    return free_text.strip()


def punctuation_cleaner(temp):
    """ remove various punctuation (assumed to not be associated with
    countries, races or ancestries.)

    Keyword arguements:
    text: the free text string prior to splitting
    """
    temp = temp.replace(',', ';')
    for pmark in ['-', '\'', '’', '?', '+']:
        temp = temp.replace(pmark, ' ')
    for pmark in ['(', ')', '.', '*', '~', '<', '>']:
        temp = temp.replace(pmark, '')
    return temp


def ancestry_parser(output_path, input_series, Cat_Studies):
    ''' Parse single individual ancestries from the free text
        based on capitalisations '''
    with open(output_path, 'w', encoding='utf-8') as csv_file:
        fileout = csv.writer(csv_file, delimiter=',', lineterminator='\n')
        fileout.writerow(['STUDY ACCESSION', 'Cleaned_Ancestry',
                          'Cleaned_Ancestry_Size'])
        for index, row in Cat_Studies.iterrows():
            checksum = 0
            for ancestry in row[input_series].split(';'):
                number = re.findall(r'\d+', ancestry.strip())
                if (len(number) == 1):
                    checksum += 1
            if checksum == len(row[input_series].split(';')):
                for ancestry in row[input_series].split(';'):
                    number = re.findall(r'\d+', ancestry.strip())
                    words = ''.join(i for i in ancestry.strip() if not i.isdigit())
                    if (len(number) == 1) and (len(words.strip()) > 3) and \
                       (sum(1 for c in words if c.isupper()) > 0):
                        fileout.writerow([row['STUDY ACCESSION'],
                                          words.strip(), str(number[0])])


def make_choro_df(data_path):
    ''' Create the dataframe for the choropleth map '''
    Cat_Ancestry = pd.read_csv(os.path.join(data_path,
                                            'catalog',
                                            'synthetic',
                                            'Cat_Anc_withBroader.tsv'),
                               sep='\t')
    annual_df = pd.DataFrame(columns=['Year', 'N', 'Count'])
    Clean_CoR = make_clean_CoR(Cat_Ancestry, data_path)
    countrylookup = pd.read_csv(os.path.join(data_path,
                                             'shapefiles',
                                             'Country_Lookup.csv'),
                                index_col='Country')
    for year in range(2008, 2020):
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
        country_merged['Count (%)'] = round((pd.to_numeric(
                                             country_merged['Count']) /
                                             pd.to_numeric(
                                             country_merged['Count']).sum())
                                            * 100, 2)
        country_merged['N (%)'] = round((pd.to_numeric(
                                         country_merged['N']) /
                                         pd.to_numeric(
                                         country_merged['N'].sum()))
                                        * 100, 2)
        annual_df = annual_df.append(country_merged, sort=True)
    annual_df = annual_df.reset_index().drop(['level_0'], axis=1)
    del annual_df.index.name
    annual_df.to_csv(os.path.join(data_path, 'toplot', 'choro_df.csv'))


def make_timeseries_df(Cat_Ancestry, data_path, savename):
    '''   Make the timeseries dataframes (both for ts1 and ts2) '''
    DateSplit = Cat_Ancestry['DATE'].str.split('-', expand=True).\
        rename({0: 'Year', 1: 'Month', 2: 'Day'}, axis=1)
    Cat_Ancestry = pd.merge(Cat_Ancestry, DateSplit, how='left',
                            left_index=True, right_index=True)
    Cat_Ancestry['Year'] = pd.to_numeric(Cat_Ancestry['Year'])
    Cat_Ancestry['Month'] = pd.to_numeric(Cat_Ancestry['Month'])
    broader_list = Cat_Ancestry['Broader'].unique().tolist()
    ts_initial_sum = pd.DataFrame(index=range(2007, 2020),
                                  columns=broader_list)
    ts_replication_sum = pd.DataFrame(index=range(2007, 2020),
                                      columns=broader_list)
    ts_initial_count = pd.DataFrame(index=range(2007, 2020),
                                    columns=broader_list)
    ts_replication_count = pd.DataFrame(index=range(2007, 2020),
                                        columns=broader_list)
    for ancestry in broader_list:
        for year in range(2007, 2020):
            temp_df = Cat_Ancestry[(Cat_Ancestry['Year'] == year) &
                                   (Cat_Ancestry['Broader'] == ancestry) &
                                   (Cat_Ancestry['STAGE'] == 'initial')]
            ts_initial_sum.at[year, ancestry] = temp_df['N'].sum()
            ts_initial_count.at[year, ancestry] = len(temp_df['N'])
            temp_df = Cat_Ancestry[(Cat_Ancestry['Year'] == year) &
                                   (Cat_Ancestry['Broader'] == ancestry) &
                                   (Cat_Ancestry['STAGE'] == 'replication')]
            ts_replication_sum.at[year, ancestry] = temp_df['N'].sum()
            ts_replication_count.at[year, ancestry] = len(temp_df['N'])
    ts_initial_sum_pc = ((ts_initial_sum.T / ts_initial_sum.T.sum()).T)*100
    ts_initial_sum_pc = ts_initial_sum_pc.reset_index()
    ts_initial_sum_pc.to_csv(os.path.join(data_path, 'toplot',
                                          savename + '_initial_sum.csv'),
                             index=False)
    ts_initial_count_pc = ((ts_initial_count.T /
                            ts_initial_count.T.sum()).T)*100
    ts_initial_count_pc = ts_initial_count_pc.reset_index()
    ts_initial_count_pc.to_csv(os.path.join(data_path, 'toplot',
                                            savename + '_initial_count.csv'),
                               index=False)
    ts_replication_sum_pc = ((ts_replication_sum.T /
                              ts_replication_sum.T.sum()).T)*100
    ts_replication_sum_pc = ts_replication_sum_pc.reset_index()
    ts_replication_sum_pc.to_csv(os.path.join(data_path, 'toplot',
                                              savename +
                                              '_replication_sum.csv'),
                                 index=False)
    ts_replication_count_pc = ((ts_replication_count.T /
                                ts_replication_count.T.sum()).T)*100
    ts_replication_count_pc = ts_replication_count_pc.reset_index()
    ts_replication_count_pc.to_csv(os.path.join(data_path, 'toplot',
                                                savename +
                                                '_replication_count.csv'),
                                   index=False)


def make_freetext_dfs(data_path):
    ''' Make the dataframe for the free text analysis. Requires optimisation '''
    big_dataframe = pd.DataFrame()
    for year in range(2008, 2020):
        Cat_Studies = pd.read_csv(os.path.join(data_path,
                                               'catalog',
                                               'raw',
                                               'Cat_Stud.tsv'),
                                  sep='\t')
        Cat_Studies = Cat_Studies[Cat_Studies['DATE'].str.contains(str(year))]
        Cat_Studies['InitialClean'] = Cat_Studies.apply(
            lambda row: ancestry_cleaner(row, 'INITIAL SAMPLE SIZE'), axis=1)
        output_path = os.path.abspath(
                      os.path.join(data_path,
                                   'catalog',
                                   'synthetic',
                                   'new_initial_sample.csv'))
        ancestry_parser(output_path, 'InitialClean', Cat_Studies)
        Cat_Studies['ReplicationClean'] = Cat_Studies.apply(
            lambda row: ancestry_cleaner(row, 'REPLICATION SAMPLE SIZE'),
            axis=1)
        output_path = os.path.abspath(
                      os.path.join(data_path,
                                   'catalog',
                                   'synthetic',
                                   'new_replication_sample.csv'))
        ancestry_parser(output_path, 'ReplicationClean', Cat_Studies)
        clean_intial = pd.read_csv(os.path.abspath(
                                   os.path.join(data_path,
                                                'catalog', 'synthetic',
                                                'new_initial_sample.csv')),
                                   encoding='utf-8')
        clean_initial_sum = pd.DataFrame(
            clean_intial.groupby(['Cleaned_Ancestry']).sum())
        clean_initial_sum.rename(
            columns={'Cleaned_Ancestry_Size': 'Initial_Ancestry_Sum'},
            inplace=True)
        clean_initial_count = clean_intial.groupby(['Cleaned_Ancestry']).\
                              count()
        clean_initial_count.rename(
            columns={'Cleaned_Ancestry_Size': 'Initial_Ancestry_Count'},
            inplace=True)
        clean_initial_merged = clean_initial_sum.merge(pd.DataFrame(
            clean_initial_count['Initial_Ancestry_Count']),
            how='outer', left_index=True, right_index=True)
        clean_initial_merged = clean_initial_merged.sort_values(
            by='Initial_Ancestry_Sum', ascending=False)
        clean_initial_merged['Initial_Ancestry_Sum_%'] =\
            (clean_initial_merged['Initial_Ancestry_Sum'] /
             clean_initial_merged['Initial_Ancestry_Sum'].sum())*100
        clean_initial_merged['Initial_Ancestry_Count_%'] =\
            (clean_initial_merged['Initial_Ancestry_Count'] /
             clean_initial_merged['Initial_Ancestry_Count'].sum())*100
        clean_replication = pd.read_csv(os.path.abspath(
                                        os.path.join(
                                            data_path, 'catalog', 'synthetic',
                                            'new_replication_sample.csv')),
                                        encoding='utf-8')
        clean_replication_sum = pd.DataFrame(
            clean_replication.groupby(['Cleaned_Ancestry']).sum())
        clean_replication_sum.rename(
            columns={'Cleaned_Ancestry_Size': 'Replication_Ancestry_Sum'},
            inplace=True)
        clean_replication_count = clean_replication.groupby(
            ['Cleaned_Ancestry']).count()
        clean_replication_count.rename(
            columns={'Cleaned_Ancestry_Size': 'Replication_Ancestry_Count'},
            inplace=True)
        clean_replication_merged = clean_replication_sum.merge(
            pd.DataFrame(clean_replication_count['Replication_Ancestry_Count']),
            how='outer', left_index=True, right_index=True)
        clean_replication_merged = clean_replication_merged.sort_values(
            by='Replication_Ancestry_Sum', ascending=False)
        clean_initial_merged = clean_initial_merged.sort_values(
            by='Initial_Ancestry_Sum', ascending=False)
        clean_replication_merged['Replication_Ancestry_Sum_%'] =\
            (clean_replication_merged['Replication_Ancestry_Sum'] /
             clean_replication_merged['Replication_Ancestry_Sum'].sum())*100
        clean_replication_merged['Replication_Ancestry_Count_%'] =\
            (clean_replication_merged['Replication_Ancestry_Count'] /
             clean_replication_merged['Replication_Ancestry_Count'].sum())*100
        merged = pd.merge(clean_initial_merged,
                          clean_replication_merged,
                          left_on='Cleaned_Ancestry',
                          right_on='Cleaned_Ancestry',
                          how='outer')
        merged["Year"] = year
        big_dataframe = pd.concat([big_dataframe, merged], ignore_index=False)
    big_dataframe = big_dataframe.round(2).reset_index()
#    big_dataframe['Cleaned_Ancestry'] = big_dataframe['Cleaned_Ancestry'].str.\
#                                        replace('African American',
#                                                'African Am.')
    big_dataframe.to_csv(os.path.join(data_path,
                                      'toplot',
                                      'freetext_merged.csv'))


def make_doughnut_df(data_path):
    ''' Make the doughnut chart dataframe for use in main.py'''
    Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                        'raw', 'Cat_Stud.tsv'),
                           sep='\t')
    Cat_Stud = Cat_Stud[['STUDY ACCESSION', 'DISEASE/TRAIT',
                         'ASSOCIATION COUNT']]
    Cat_Map = pd.read_csv(os.path.join(data_path, 'catalog',
                                        'raw', 'Cat_Map.tsv'),
                           sep='\t')
    Cat_Map = Cat_Map[['Disease trait', 'Parent term']]
    Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                           left_on = 'DISEASE/TRAIT',
                           right_on = 'Disease trait')
    Cat_StudMap.to_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                    'Disease_to_Parent_Mappings.tsv'), sep='\t')
    Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION',
                               'DISEASE/TRAIT', 'ASSOCIATION COUNT']].\
                               drop_duplicates()
    Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
    Cat_Anc_withBroader = pd.read_csv(os.path.join(data_path,
                                                   'catalog',
                                                   'synthetic',
                                                   'Cat_Anc_withBroader.tsv'),
                                      '\t', index_col=False,
                                      parse_dates=['DATE'])
    Cat_Anc_withBroader = Cat_Anc_withBroader[Cat_Anc_withBroader['Broader'] != 'In Part Not Recorded']
    merged = pd.merge(Cat_StudMap, Cat_Anc_withBroader,
                      how='left', on='STUDY ACCESSION')
    merged["DATE"] = merged["DATE"].astype(str)
    doughnut_df = pd.DataFrame(index=[], columns=['Broader',
                                                  'parentterm',
                                                  'Year',
                                                  'InitialN',
                                                  'InitialCount',
                                                  'ReplicationN',
                                                  'ReplicationCount',
                                                  'InitialAssociationSum',
                                                  ])
    merged = merged[merged['Broader'].notnull()]
    merged = merged[merged['parentterm'].notnull()]
    counter = 0
    for year in range(2008, 2020):
        for ancestry in merged['Broader'].unique().tolist():
            doughnut_df.at[counter, 'Broader'] = ancestry
            doughnut_df.at[counter, 'parentterm'] = 'All'
            doughnut_df.at[counter, 'Year'] = year
            doughnut_df.at[counter, 'ReplicationN'] = (merged[(merged['STAGE'] ==
                                                         'replication') &
                                                        (merged['Broader'] ==
                                                         ancestry) &
                                                        (merged['DATE'].str.contains(str(year)))]['N'].sum() /
                                                 merged[(merged['STAGE'] ==
                                                        'replication') &
                                                        (merged['DATE'].str.contains(str(year)))]['N'].sum())*100
            doughnut_df.at[counter, 'InitialN'] = (merged[(merged['STAGE'] ==
                                                         'initial') &
                                                        (merged['Broader'] ==
                                                         ancestry) &
                                                        (merged['DATE'].str.contains(str(year)))]['N'].sum() /
                                                 merged[(merged['STAGE'] ==
                                                        'initial') &
                                                        (merged['DATE'].str.contains(str(year)))]['N'].sum())*100
            doughnut_df.at[counter, 'InitialAssociationSum'] = (merged[(merged['STAGE'] ==
                                                                'initial') &
                                                               (merged['Broader'] ==
                                                                ancestry) &
                                                                (merged['DATE'].str.contains(str(year)))]['ASSOCIATION COUNT'].sum() /
                                                               merged[(merged['STAGE'] ==
                                                                      'initial') &
                                                                  (merged['DATE'].str.contains(str(year)))]['ASSOCIATION COUNT'].sum())*100
            doughnut_df.at[counter, 'ReplicationCount'] = (len(merged[
                                                              (merged['STAGE'] =='replication') &
                                                              (merged['DATE'].str.contains(str(year))) &
                                                              (merged['Broader'] == ancestry)]) /
                                                           len(merged[(merged['STAGE'] ==
                                                                       'replication') &
                                                                      (merged['DATE'].str.contains(str(year)))]))*100
            doughnut_df.at[counter, 'InitialCount'] = (len(merged[
                                                         (merged['STAGE'] ==
                                                          'initial') &
                                                          (merged['DATE'].str.contains(str(year))) &
                                                         (merged['Broader'] ==
                                                          ancestry)]) /
                                                     len(merged[(merged['STAGE'] ==
                                                                'initial') &
                                                                (merged['DATE'].str.contains(str(year)))]))*100
            counter = counter + 1
            for parent in merged['parentterm'].unique().tolist():
                try:
                    doughnut_df.at[counter, 'Broader'] = ancestry
                    doughnut_df.at[counter, 'parentterm'] = parent
                    doughnut_df.at[counter, 'Year'] = year
                    doughnut_df.at[counter,
                                 'ReplicationN'] = (merged[
                                                    (merged['STAGE'] == 'replication') &
                                                    (merged['parentterm'] == parent) &
                                                    (merged['DATE'].str.contains(str(year))) &
                                                    (merged['Broader'] == ancestry)]['N'].sum() /
                                                    merged[(merged['STAGE'] == 'replication') &
                                                    (merged['DATE'].str.contains(str(year))) &
                                                    (merged['parentterm'] == parent)]['N'].sum())*100
                    doughnut_df.at[counter,
                                 'InitialN'] = (merged[(merged['STAGE'] == 'initial') &
                                                       (merged['Broader'] == ancestry) &
                                                       (merged['DATE'].str.contains(str(year))) &
                                                       (merged['parentterm'] == parent)]['N'].sum() /
                                                merged[(merged['STAGE'] == 'initial') &
                                                       (merged['DATE'].str.contains(str(year))) &
                                                       (merged['parentterm'] == parent)]['N'].sum())*100
                    doughnut_df.at[counter,
                                   'InitialAssociationSum'] = (merged[(merged['STAGE'] == 'initial') &
                                                       (merged['Broader'] == ancestry) &
                                                       (merged['DATE'].str.contains(str(year))) &
                                                       (merged['parentterm'] == parent)]['ASSOCIATION COUNT'].sum() /
                                                merged[(merged['STAGE'] == 'initial') &
                                                       (merged['DATE'].str.contains(str(year))) &
                                                       (merged['parentterm'] == parent)]['ASSOCIATION COUNT'].sum())*100
                    doughnut_df.at[counter,
                                 'ReplicationCount'] = (len(merged[
                                                           (merged['STAGE'] == 'replication') &
                                                           (merged['parentterm'] == parent) &
                                                           (merged['DATE'].str.contains(str(year))) &
                                                           (merged['Broader'] == ancestry)]) /
                                                        len(merged[
                                                           (merged['STAGE'] == 'replication') &
                                                           (merged['DATE'].str.contains(str(year))) &
                                                           (merged['parentterm'] == parent)])) * 100
                    doughnut_df.at[counter,
                                 'InitialCount'] = (len(merged[
                                                       (merged['STAGE'] == 'initial') &
                                                       (merged['parentterm'] == parent) &
                                                       (merged['DATE'].str.contains(str(year))) &
                                                       (merged['Broader'] == ancestry)]) /
                                                    len(merged[
                                                       (merged['STAGE'] == 'initial') &
                                                       (merged['DATE'].str.contains(str(year))) &
                                                       (merged['parentterm'] == parent)])) * 100
                except ZeroDivisionError:
                    doughnut_df.at[counter, 'InitialN'] = np.nan
                counter = counter + 1
    doughnut_df['Broader'] = doughnut_df['Broader'].str.\
        replace('Hispanic/Latin American', 'Hispanic/L.A.')
    doughnut_df.to_csv(os.path.join(data_path, 'toplot', 'doughnut_df.csv'))


def make_bubbleplot_df(data_path):
    Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                        'raw', 'Cat_Stud.tsv'),
                           sep='\t')
    Cat_Stud = Cat_Stud[['STUDY ACCESSION', 'DISEASE/TRAIT']]
    Cat_Map = pd.read_csv(os.path.join(data_path, 'catalog',
                                        'raw', 'Cat_Map.tsv'),
                           sep='\t')
    Cat_Map = Cat_Map[['Disease trait', 'Parent term']]
    Cat_StudMap = pd.merge(Cat_Stud, Cat_Map, how='left',
                           left_on = 'DISEASE/TRAIT',
                           right_on = 'Disease trait')
    Cat_StudMap.to_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                    'Disease_to_Parent_Mappings.tsv'), sep='\t')
    Cat_StudMap = Cat_StudMap[['Parent term', 'STUDY ACCESSION',
                               'DISEASE/TRAIT']].drop_duplicates()
    Cat_StudMap = Cat_StudMap.rename(columns={"Parent term": "parentterm"})
    Cat_Anc_withBroader = pd.read_csv(os.path.join(data_path,
                                                   'catalog',
                                                   'synthetic',
                                                   'Cat_Anc_withBroader.tsv'),
                                      '\t', index_col=False,
                                      parse_dates=['DATE'])
    merged = pd.merge(Cat_StudMap, Cat_Anc_withBroader,
                      how='left', on='STUDY ACCESSION')
    merged["AUTHOR"] = merged["FIRST AUTHOR"]
    merged = merged[["Broader", "N", "PUBMEDID", "AUTHOR", "DISEASE/TRAIT",
                     "STAGE", 'DATE', "STUDY ACCESSION", "parentterm"]]
    merged = merged.rename(columns={'DISEASE/TRAIT':
                                    'DiseaseOrTrait'})
    merged = merged[merged['Broader'] != 'In Part Not Recorded']
    merged["color"] = 'black'
    merged["color"] = np.where(merged["Broader"] == 'European',
                               "#d53e4f", merged["color"])
    merged["color"] = np.where(merged["Broader"] == 'Asian',
                               "#3288bd", merged["color"])
    merged["color"] = np.where(merged["Broader"] == 'African American or Afro-Caribbean',
                               "#fee08b", merged["color"])
    merged["color"] = np.where(merged["Broader"] == 'Hispanic/Latin American',
                               "#807dba", merged["color"])
    merged["color"] = np.where(merged["Broader"] == 'Other/Mixed',
                               "#99d594", merged["color"])
    merged["color"] = np.where(merged["Broader"] == 'African',
                               "#fc8d59", merged["color"])
    merged = merged.rename(columns={"STUDY ACCESSION": "ACCESSION"})
    merged['DiseaseOrTrait'] = merged['DiseaseOrTrait'].astype(str)
    merged["parentterm"] = merged["parentterm"].astype(str)
    make_disease_list(merged)
    merged = merged.groupby(["Broader", "N", "PUBMEDID", "AUTHOR", "STAGE",
                             "DATE",  "DiseaseOrTrait", "color",
                             "ACCESSION"])['parentterm'].\
                             apply(', '.join).reset_index()
    merged = merged.groupby(["Broader", "N", "PUBMEDID", "AUTHOR",
                             "parentterm", "STAGE", "DATE", "color",
                             "ACCESSION"])['DiseaseOrTrait'].\
                             apply(', '.join).reset_index()
    merged = merged.sort_values(by='DATE', ascending=True)
    merged.to_csv(os.path.join(data_path, 'toplot', 'bubble_df.csv'))


def clean_gwas_cat(data_path):
    ''' Clean the catalog and do some general preprocessing '''
    Cat_Stud = pd.read_csv(os.path.join(data_path, 'catalog',
                                        'raw', 'Cat_Stud.tsv'),
                           header=0, sep='\t', encoding='utf-8',
                           index_col=False)
    Cat_Stud.fillna('N/A', inplace=True)
    Cat_Anc = pd.read_csv(os.path.join(data_path, 'catalog', 'raw',
                                       'Cat_Anc.tsv'),
                          header=0, sep='\t', encoding='utf-8',
                          index_col=False)
    Cat_Anc.rename(columns={'BROAD ANCESTRAL CATEGORY': 'BROAD ANCESTRAL',
                            'NUMBER OF INDIVDUALS': 'N'}, inplace=True)
    Cat_Anc = Cat_Anc[~Cat_Anc['BROAD ANCESTRAL'].isnull()]
    Cat_Anc.columns = Cat_Anc.columns.str.replace('ACCCESSION', 'ACCESSION')
    Cat_Anc_byN = Cat_Anc[['STUDY ACCESSION', 'N',
                           'DATE']].groupby(by='STUDY ACCESSION').sum()
    Cat_Anc_byN = Cat_Anc_byN.reset_index()
    Cat_Anc_byN = pd.merge(Cat_Anc_byN, Cat_Stud[[
        'STUDY ACCESSION', 'DATE']], how='left', on='STUDY ACCESSION')
    cleaner_broad = pd.read_csv(os.path.join(data_path, 'support',
                                             'dict_replacer_broad.tsv'),
                                sep='\t', header=0, index_col=False)
    Cat_Anc = pd.merge(Cat_Anc, cleaner_broad, how='left',
                       on='BROAD ANCESTRAL')
    Cat_Anc['Dates'] = [pd.to_datetime(d) for d in Cat_Anc['DATE']]
    Cat_Anc['N'] = pd.to_numeric(Cat_Anc['N'], errors='coerce')
    Cat_Anc = Cat_Anc[Cat_Anc['N'].notnull()]
    Cat_Anc['N'] = Cat_Anc['N'].astype(int)
    Cat_Anc = Cat_Anc.sort_values(by='Dates')
#    Cat_Anc['Broader'] = Cat_Anc['Broader'].str.replace(
#        'African American/Afro-Caribbean', 'African Am./Caribbean')
#    Cat_Anc['Broader'] = Cat_Anc['Broader'].str.replace(
#        'Hispanic or Latin American', 'Hispanic/Latin American')
    if len(Cat_Anc[Cat_Anc['Broader'].isnull()]) > 0:
        diversity_logger.debug('Wuhoh! Need to update dictionary terms:\n' +
              '\n'.join(Cat_Anc[Cat_Anc['Broader'].
                        isnull()]['BROAD ANCESTRAL'].unique()))
        Cat_Anc[Cat_Anc['Broader'].\
                isnull()]['BROAD ANCESTRAL'].\
        to_csv(os.path.join(data_path, 'unmapped', 'unmapped_broader.txt'))
    else:
        diversity_logger.info('No missing Broader terms! Nice!')
    Cat_Anc = Cat_Anc[Cat_Anc['Broader'].notnull()]
    Cat_Anc = Cat_Anc[Cat_Anc['N'].notnull()]
    Cat_Anc.to_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                'Cat_Anc_withBroader.tsv'),
                   sep='\t', index=False)


def make_clean_CoR(Cat_Anc, data_path):
    """
        Clean the country of recruitment field for the geospatial analysis.
    """
    with open(os.path.abspath(
              os.path.join(data_path, 'catalog', 'synthetic',
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
                            os.path.join(data_path, 'catalog', 'synthetic',
                                         'ancestry_CoR.csv')))
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'U.S.', 'United States')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'Gambia', 'Gambia, The')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'U.K.', 'United Kingdom')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'Republic of Korea', 'Korea, South')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'Czech Republic', 'Czechia')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'Russian Federation', 'Russia')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'Iran \(Islamic Republic of\)', 'Iran')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'Viet Nam', 'Vietnam')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'United Republic of Tanzania', 'Tanzania')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'Republic of Ireland', 'Ireland')
    Clean_CoR['Cleaned Country'] = Clean_CoR['Cleaned Country'].str.replace(
        'Micronesia \(Federated States of\)',
        'Micronesia, Federated States of')
    Clean_CoR = Clean_CoR[Clean_CoR['Cleaned Country'] != 'NR']
    Clean_CoR.to_csv(os.path.abspath(
                     os.path.join(data_path, 'catalog', 'synthetic',
                                  'GWAScatalogue_CleanedCountry.tsv')),
                     sep='\t', index=False)
    return Clean_CoR


def download_cat(data_path, ebi_download):
    """ download the data from the ebi main site and ftp"""
    try:
        r = requests.get(ebi_download + 'studies_alternative')
        if r.status_code == 200:
            catstud_name = r.headers['Content-Disposition'].split('=')[1]
            with open(os.path.join(data_path, 'catalog', 'raw',
                                   'Cat_Stud.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
            diversity_logger.info('Successfully downloaded ' + catstud_name)
        else:
            diversity_logger.debug('Problem downloading the Cat_Stud file...')
        r = requests.get(ebi_download + 'ancestry')
        if r.status_code == 200:
            catanc_name = r.headers['Content-Disposition'].split('=')[1]
            with open(os.path.join(data_path, 'catalog', 'raw',
                                   'Cat_Anc.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
            diversity_logger.info('Successfully downloaded ' + catanc_name)
        else:
            diversity_logger.debug('Problem downloading the Cat_Anc file...')
        r = requests.get(ebi_download + 'full')
        if r.status_code == 200:
            catfull_name = r.headers['Content-Disposition'].split('=')[1]
            with open(os.path.join(data_path, 'catalog', 'raw',
                                   'Cat_Full.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
        else:
            diversity_logger.info('Successfully downloaded ' + catfull_name)
            diversity_logger.debug('Problem downloading the Cat_full file...')
        requests_ftp.monkeypatch_session()
        s = requests.Session()
        ftpsite = 'ftp://ftp.ebi.ac.uk/'
        subdom = '/pub/databases/gwas/releases/latest/'
        file = 'gwas-efo-trait-mappings.tsv'
        r = s.get(ftpsite+subdom+file)
        todaysdate = datetime.datetime.now().strftime("%Y_%m_%d")
        if r.status_code == 200:
            with open(os.path.join(data_path, 'catalog', 'raw',
                                   'Cat_Map.tsv'), 'wb') as tsvfile:
                tsvfile.write(r.content)
            diversity_logger.info('Successfully downloaded efo-trait-mappings')
        else:
            diversity_logger.debug('Problem downloading efo-trait-mappings file...')
    except Exception as e:
        diversity_logger.debug('Problem downloading the Catalog data!' + str(e))


def make_archive(source, destination):
    ''' Make the archive for the zipfile download on the dashboard'''
    base = os.path.basename(destination)
    name = base.split('.')[0]
    format = base.split('.')[1]
    archive_from = os.path.dirname(source)
    archive_to = os.path.basename(source.strip(os.sep))
    print(source, destination, archive_from, archive_to)
    shutil.make_archive(name, format, archive_from, archive_to)
    shutil.move('%s.%s' % (name, format), destination)


def zip_toplot(source, destination):
    try:
        base = os.path.basename(destination)
        name = base.split('.')[0]
        format = base.split('.')[1]
        shutil.make_archive(name, format, source)
        shutil.move('%s.%s' % (name, format), destination)
        diversity_logger.info('Successfully zipped the files to be downloaded')
    except Exception as e:
        diversity_logger.debug('Problem zipping the files to tbe downloaded' +
                               str(e))

def make_disease_list(df):
    uniq_dis_trait = pd.Series(df['DiseaseOrTrait'].unique())
    uniq_dis_trait.to_csv(os.path.join(data_path, 'summary',
                          'uniq_dis_trait.txt'),
                          header=False, index=False)

def make_parent_list(data_path):
    df = pd.read_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                  'Cat_Anc_withBroader_withParents.tsv'),
                    sep='\t')
    uniq_parent = pd.Series(df[df['parentterm'].\
                               notnull()]['parentterm'].unique())
    uniq_parent.to_csv(os.path.join(data_path, 'summary',
                                    'uniq_parent.txt'),
                       header=False, index=False)


def make_broader_list(data_path):
    df = pd.read_csv(os.path.join(data_path, 'catalog', 'synthetic',
                                  'Cat_Anc_withBroader_withParents.tsv'),
                     sep='\t')
    uniq_broader = pd.Series(df[df['Broader'].notnull()]['Broader'].unique())
    uniq_broader.to_csv(os.path.join(data_path, 'summary',
                                    'uniq_broader.txt'),
                       header=False, index=False)


if __name__ == "__main__":
    logpath = os.path.abspath(os.path.join(__file__, '..', 'logging'))
    diversity_logger = setup_logging(logpath)
    data_path = os.path.abspath(os.path.join(__file__, '..', 'data'))
    #index_filepath = os.path.abspath(os.path.join(__file__, '..','templates', 'index.html'))
    ebi_download = 'https://www.ebi.ac.uk/gwas/api/search/downloads/'
    try:
        download_cat(data_path, ebi_download)
        clean_gwas_cat(data_path)
        make_bubbleplot_df(data_path)
        make_doughnut_df(data_path)
        tsinput = pd.read_csv(os.path.join(data_path, 'catalog','synthetic','Cat_Anc_withBroader.tsv'), sep='\t')
        make_timeseries_df(tsinput, data_path, 'ts1')
        tsinput = tsinput[tsinput['Broader'] != 'In Part Not Recorded']
        make_timeseries_df(tsinput, data_path, 'ts2')
        make_choro_df(data_path)
        make_freetext_dfs(data_path)
        make_heatmap_dfs(data_path)
        # update_header(index_filepath)
        make_parent_list(data_path)
        make_broader_list(data_path)
        sumstats = create_summarystats(data_path)
        # update_summarystats(sumstats, index_filepath)
        # update_downloaddata(sumstats, index_filepath)
        diversity_logger.info('generate_data.py ran successfully!')
        zip_toplot(os.path.join(data_path, 'toplot'),  os.path.join(data_path, 'todownload', 'gwasdiversitymonitor_download.zip'))
    except Exception as e:
        diversity_logger.debug('generate_data.py failed, uncaught error: ' +
                               str(traceback.format_exc()))
    logging.shutdown()
