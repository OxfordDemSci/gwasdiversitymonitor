import os
import PyPDF2
import shutil
import matplotlib.pyplot as plt
import pdflatex
from numpy import loadtxt
from tqdm import tqdm
from matplotlib.gridspec import GridSpec
from pylatex.utils import escape_latex, italic, NoEscape, bold
from pylatex import Hyperref, Package
# @TODO some redundancy here
from pylatex import Document, Section, Subsection,\
    Command, Head, Foot, PageStyle, LineBreak,\
    MiniPage, LargeText, MediumText, SmallText,\
    Figure, Subsubsection

def hyperlink(url, text):
    text = escape_latex(text)
    return NoEscape(r'\href{' + url + '}{' + text + '}')

def get_agency_list(data_path):
    agency_path = os.path.join(data_path,
                               'pubmed',
                               'agency_list.txt')
    with open(agency_path, 'r') as file:
        agency_list = file.read().replace('\n', '')
    agency_list = agency_list.split(',')
    return agency_list


def watermark(path, data_path):
    watermark_path = os.path.join('app',
                                  'static',
                                  'images',
                                  'lcds_watermark.pdf')
    Doc = open(path+'.pdf', 'rb')
    pdfReader = PyPDF2.PdfReader(Doc)
    pdfWatermark = PyPDF2.PdfReader(open(watermark_path, 'rb'))
    pdfWriter = PyPDF2.PdfWriter()
    pageObj = pdfReader.pages[0]
    pageObj.merge_page(pdfWatermark.pages[0])
    pdfWriter.add_page(pageObj)
    resultPdfFile = open(path+'.pdf', 'wb')
    pdfWriter.write(resultPdfFile)
    Doc.close()
    resultPdfFile.close()


def fill_document(doc, agency):
    doc.preamble.append(Command('title', 'Funders Diversity Report: ' + agency))
    doc.preamble.append(Command('author', 'Created by the GWAS Diversity Monitor'))
    doc.preamble.append(Command('date', NoEscape(r'\today')))
    doc.append(NoEscape(r'\maketitle'))
    doc.packages.append(Package('xcolor', options='dvipsnames'))
    doc.packages.append(Package('hyperref',
                                options=['colorlinks=true',
                                         'linkcolor=blue',
                                         'urlcolor=Blue']))
#   with doc.create(Subsection('General Summary Statistics',
#                              numbering=False)):
    doc.append('The ')
    doc.append(hyperlink("https://www.gwasdiversitymonitor.com",
                         "GWAS Diversity Monitor "))
    doc.append(' believes that the ')
    doc.append(hyperlink("https://www.ebi.ac.uk/gwas/",
                         "NHGRI-EBI GWAS Catalog "))
    doc.append(' has cached X authorships across Y studies '
               'in Z papers which are funded by ' + agency)
    doc.append('. This represents A, B and C of entire Catalog '
               'respectively.')
    doc.append('The earliest study found in the Catalog which '
               'occured at DDDD, and the latest was at DDDD. ')
    doc.append('These studies involved a total of Y participants. ')
    doc.append('The most commonly studied Disease/Trait was X,'
               'and the most commonly studied parent term was Y.')

    with doc.create(Figure(position = 'h!')) as fig:
        fig.add_image('figure.pdf', width='500px')
#        fig.add_caption('My caption here')

    with doc.create(Subsubsection('About the GWAS Diversity Monitor',
                               numbering=False)):
        doc.append('The GWAS Diversity Monitor is maintained by the ')
        doc.append(hyperlink("https://www.demographicscience.ox.ac.uk/",
                             "Leverhulme Centre for Demographic Science. "))
        doc.append('Launched in March of 2020, it has been consistently '
                   'tracking Diversity in Genome Wide Assocation '
                   'Studies with data provided from the ')
        doc.append(hyperlink("https://www.ebi.ac.uk/gwas/", "NHGRI-EBI GWAS Catalog"))
        doc.append('. Please cite this as:')
#        doc.append(NoEscape(r'\hspace*{25mm}'))
        doc.append(NoEscape(r'\begin{center}'
                            r'Mills, M., Rahal, C., Leasure, D.,'
                            'Yan, J. and Akimova, E., (2022),'
                   '"GWAS Diversity by Funder", SocArXiv, p.1-3, '
                   'Available at https://osf.io/preprints/socarxiv/123abc'
                   '\end{center}'))
        doc.append('We remain grateful to ')
        doc.append(hyperlink("https: // www.global-initiative.com",
                             "Global Initative "))
        doc.append(' proving development assistance. ')
        doc.append('Please see the README.md file on our ')
        doc.append(hyperlink("https://github.com/OxfordDemSci/gwasdiversitymonitor",
                             "GitHub Page "))
        doc.append(' for of the all latest updates'
                   'and a full list of acknowledgements.')


# @TODO uncomment this before pushing
def build_paths(reports_path, agency_list):
    if os.path.exists(reports_path):
        shutil.rmtree(reports_path)
    os.mkdir(reports_path)
    for agency in agency_list:
        os.mkdir(os.path.join(reports_path, agency))


def execute_tex(reports_path, data_path, agency_list):
    for agency in tqdm(agency_list):
        path = (os.path.join(reports_path, agency, 'report'))
        geometry_options = {"tmargin": "2.5cm", "lmargin": "1.5cm"}
        doc = Document(geometry_options=geometry_options)
        if agency != 'All Funders':
            fill_document(doc, agency)
            doc.generate_pdf(path, clean_tex=False, clean=True, compiler='pdflatex')
            watermark(path, data_path)
            if agency == 'NHLBI NIH HHS': break
        elif agency == 'All Funders':
            fill_document(doc, agency)
            doc.generate_pdf(path, clean_tex=False, clean=True, compiler='pdflatex')
            watermark(path, data_path)


def build_figures(agency_list, data_path, reports_path):
    for agency in agency_list:
        gs = GridSpec(2, 2)
        fig = plt.figure(figsize=(8, 4))
        ax1 = fig.add_subplot(gs[0, 0])
        ax2 = fig.add_subplot(gs[0, 1])
        ax3 = fig.add_subplot(gs[1, :])
        plt.tight_layout()
        figpath = os.path.join(reports_path, agency, 'figure.pdf')
        plt.savefig(figpath)


def generate_reports(data_path, reports_path):
    agency_list = get_agency_list(data_path)
    build_paths(reports_path, agency_list)
    build_figures(agency_list, data_path, reports_path)
    execute_tex(reports_path, data_path, agency_list)
