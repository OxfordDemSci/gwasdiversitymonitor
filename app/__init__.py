import os
import re

from flask import Flask, g, request, send_file, url_for
from werkzeug.utils import safe_join


PRODUCTION_DEPLOYMENT_DOMAIN = "gwasdiversitymonitor.com"
FINGERPRINTED_ASSET_PATTERN = re.compile(
    r"\.[0-9a-f]{12,64}\.[A-Za-z0-9]+$"
)
COMPRESSIBLE_STATIC_EXTENSIONS = frozenset({
    ".css", ".js", ".json", ".svg", ".html", ".txt", ".xml",
})


def robots_noindex_enabled(environment=None):
    """Return whether this deployment should instruct robots not to index."""
    environment = os.environ if environment is None else environment
    deployment_domain = environment.get(
        "GWAS_DEPLOYMENT_DOMAIN", ""
    ).strip().casefold()
    if deployment_domain == PRODUCTION_DEPLOYMENT_DOMAIN:
        return False

    return environment.get(
        "GWAS_NOINDEX", ""
    ).strip().casefold() in {"1", "true", "yes", "on"}


app = Flask(__name__)
app.config.from_object('config')

# Leave analytics disabled unless a deployment explicitly provides the base URL
# of its self-hosted GoatCounter instance.
app.config["GOATCOUNTER_URL"] = os.environ.get(
    "GOATCOUNTER_URL", app.config.get("GOATCOUNTER_URL", "")
).rstrip("/")
app.config["GWAS_NOINDEX"] = robots_noindex_enabled()


@app.before_request
def serve_compressed_static():
    """Cache text asset compression once when Flask serves static files.

    Production nginx can serve these assets itself. This also keeps direct
    Flask/Gunicorn deployments fast without changing the source assets.
    """
    if request.endpoint != "static" or request.method not in {"GET", "HEAD"}:
        return None
    filename = (request.view_args or {}).get("filename", "")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in COMPRESSIBLE_STATIC_EXTENSIONS:
        return None
    source = safe_join(app.static_folder, filename)
    if source is None:
        return None
    # The compression cache must never read files outside the static tree,
    # including symlinks that resolve outside it.
    static_root = os.path.realpath(app.static_folder)
    source = os.path.realpath(source)
    if os.path.commonpath((static_root, source)) != static_root:
        return None
    try:
        source_stat = os.stat(source)
        if source_stat.st_size < 1024 or not os.path.isfile(source):
            return None
    except OSError:
        return None

    g.static_encoding_varies = True
    if request.headers.get("Range") or request.accept_encodings["gzip"] <= 0:
        return None

    from app.BrowserPlotCache import prepare_cached_gzip

    try:
        compressed = prepare_cached_gzip(
            source,
            app.config.get(
                "STATIC_GZIP_CACHE_DIRECTORY",
                os.path.join(app.instance_path, "static-gzip"),
            ),
        )
        response = send_file(
            compressed,
            download_name=filename,
            conditional=True,
            last_modified=source_stat.st_mtime,
            max_age=app.get_send_file_max_age(filename),
        )
    except OSError:
        # Static files remain available when the disposable cache is read-only.
        return None
    response.headers["Content-Encoding"] = "gzip"
    return response


@app.after_request
def apply_robots_policy(response):
    """Apply deployment policy and cache fingerprinted static assets."""
    if app.config.get("GWAS_NOINDEX", False):
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"

    if request.endpoint == "static":
        if getattr(g, "static_encoding_varies", False):
            response.vary.add("Accept-Encoding")
        filename = (request.view_args or {}).get("filename", "")
        requested_version = request.args.get("v")
        if (
            response.status_code in {200, 304}
            and (
                FINGERPRINTED_ASSET_PATTERN.search(filename)
                or (
                    requested_version
                    and requested_version == _static_asset_version(filename)
                )
            )
        ):
            response.cache_control.no_cache = None
            response.cache_control.public = True
            response.cache_control.max_age = 31536000
            response.cache_control.immutable = True

    return response


def _static_asset_version(filename):
    asset_path = safe_join(app.static_folder, filename)
    if not asset_path:
        return None

    try:
        return str(os.stat(asset_path).st_mtime_ns)
    except OSError:
        return None


@app.template_global()
def versioned_static(filename):
    version = _static_asset_version(filename)
    if version is None:
        return url_for('static', filename=filename)

    return url_for('static', filename=filename, v=version)

from app import routes
