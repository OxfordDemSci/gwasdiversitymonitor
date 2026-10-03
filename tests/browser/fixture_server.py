"""Run the real Gunicorn launcher from an isolated generated-data workspace."""
import os
from pathlib import Path
import runpy

from fixture_data import REPOSITORY, build_fixture, configure


if __name__ == '__main__':
    workspace = Path.cwd()
    configure(workspace)
    build_fixture(workspace)
    os.environ['GWAS_HOST'] = '127.0.0.1'
    os.environ['GWAS_WORKERS'] = '1'
    os.environ['GWAS_THREADS'] = '4'
    os.environ['GWAS_NOINDEX'] = '1'
    os.environ['GOATCOUNTER_URL'] = ''
    runpy.run_path(str(REPOSITORY / 'gwasdiversitymonitor.py'), run_name='__main__')
