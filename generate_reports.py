import os
import PyPDF2
import shutil
from numpy import loadtxt
from tqdm import tqdm
from pylatex import Document, Section, Subsection,\
    Command, Head, Foot, PageStyle, LineBreak,\
    MiniPage, LargeText, MediumText, SmallText
from pylatex.utils import italic, NoEscape, bold

def get_agency_list(data_path):
    agency_path = os.path.join(data_path,
                               'pubmed',
                               'agency_list.txt')
    with open(agency_path, 'r') as file:
        agency_list = file.read().replace('\n', '')
    agency_list = agency_list.split(',')
    return agency_list


def watermark(path, data_path):
    watermark_path = os.path.join(data_path, 'support', 'logo_white_rect.png.pdf')
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
    doc.preamble.append(Command('title', 'Funder Diversity Report: ' + agency))
    doc.preamble.append(Command('author', 'Created by the GWAS Diversity Monitor'))
    doc.preamble.append(Command('date', NoEscape(r'\today')))
    doc.append(NoEscape(r'\maketitle'))

def build_paths(reports_path, agency_list):
    if os.path.exists(reports_path):
        shutil.rmtree(reports_path)
    os.mkdir(reports_path)
    for agency in agency_list:
        os.mkdir(os.path.join(reports_path, agency))


def execute_tex(reports_path, data_path, agency_list):
    for agency in tqdm(agency_list):
        path = (os.path.join(reports_path, agency, 'report'))
        if agency != 'All Funders':
            geometry_options = {"tmargin": "2.5cm", "lmargin": "2.5cm"}
            doc = Document(geometry_options=geometry_options)
            fill_document(doc, agency)
            doc.generate_pdf(path, clean_tex=False, clean=True)
            watermark(path, data_path)
        elif agency == 'Funders':
            geometry_options = {"tmargin": "2.5cm", "lmargin": "2.5cm"}
            doc = Document(geometry_options=geometry_options)
            fill_document(doc, agency)
            doc.generate_pdf(path, clean_tex=False, clean=True)
            watermark(path, data_path)

def generate_reports(data_path, reports_path):
    agency_list = get_agency_list(data_path)
    build_paths(reports_path, agency_list)
    execute_tex(reports_path, data_path, agency_list)
