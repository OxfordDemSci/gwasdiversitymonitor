"""Build identity, deliberately separate from scientific artifact identity."""
import os
import re

_value = os.environ.get('GWAS_BUILD_SHA', '')
BUILD_SHA = _value if re.fullmatch(r'[0-9a-f]{40}', _value) else None


def application_version():
    return {'commit': BUILD_SHA, 'source': 'image-build' if BUILD_SHA else 'unversioned'}
