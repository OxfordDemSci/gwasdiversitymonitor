import os
import sys

from app import app
from app import DataLoader


def _int_from_env(name, default):
    value = os.environ.get(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        sys.stderr.write(f'{name} must be an integer; got {value!r}.\n')
        sys.exit(1)


def _check_required_data():
    try:
        with DataLoader.published_data_lock() as published_path:
            if DataLoader.runtime_release_ready(published_path):
                return
    except (OSError, DataLoader.PublishedDataUnavailable):
        pass

    sys.stderr.write(
        'The complete published data manifest is missing or invalid. '
        'Please run python3 ./generate_data.py!\n'
    )
    sys.exit(1)


def run_production_server():
    try:
        from gunicorn.app.base import BaseApplication
    except ImportError:
        sys.stderr.write(
            'Gunicorn is required to serve this app in production. '
            'Install dependencies with: pip install -r requirements.txt\n'
        )
        sys.exit(1)

    class GwasDiversityMonitorServer(BaseApplication):
        def __init__(self, flask_app, options):
            self.options = options
            self.flask_app = flask_app
            super().__init__()

        def load_config(self):
            for key, value in self.options.items():
                if key in self.cfg.settings and value is not None:
                    self.cfg.set(key.lower(), value)

        def load(self):
            return self.flask_app

    host = os.environ.get('GWAS_HOST', '0.0.0.0')
    port = _int_from_env('GWAS_PORT', 8000)
    options = {
        'bind': f'{host}:{port}',
        'workers': _int_from_env('GWAS_WORKERS', 2),
        'threads': _int_from_env('GWAS_THREADS', 4),
        'timeout': _int_from_env('GWAS_TIMEOUT', 120),
        'accesslog': '-',
        'errorlog': '-',
    }

    GwasDiversityMonitorServer(app, options).run()


if __name__ == '__main__':
    _check_required_data()
    run_production_server()
