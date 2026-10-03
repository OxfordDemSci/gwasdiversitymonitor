"""Run all existing Python tests in a fresh fixture, with no checkout data/config."""
import os
from pathlib import Path
import tempfile
import unittest
import shutil

from fixture_data import REPOSITORY, build_fixture, configure


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='gwas-unit-fixture-') as directory:
        configure(directory)
        expected = build_fixture(directory)
        with tempfile.TemporaryDirectory(prefix='gwas-determinism-check-') as other:
            assert build_fixture(other) == expected, 'Fixture identity changed with its workspace path'
        # One legacy test opens this asset relative to cwd. Copy only that
        # read-only input, not an app/data symlink back into the checkout.
        css = Path(directory) / 'app/static/css/app.scss.css'
        css.parent.mkdir(parents=True)
        shutil.copyfile(REPOSITORY / 'app/static/css/app.scss.css', css)
        os.chdir(directory)
        suite = unittest.defaultTestLoader.discover(str(REPOSITORY / 'tests'), pattern='test_*.py')
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(not result.wasSuccessful())
