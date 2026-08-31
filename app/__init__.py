import os

from flask import Flask, url_for


PRODUCTION_DEPLOYMENT_DOMAIN = "gwasdiversitymonitor.com"


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


@app.after_request
def apply_robots_policy(response):
    """Prevent indexing on deployments explicitly marked as non-public."""
    if app.config.get("GWAS_NOINDEX", False):
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response

@app.template_global()
def versioned_static(filename):
    try:
        version = os.stat(os.path.join(app.static_folder, filename)).st_mtime_ns
    except OSError:
        return url_for('static', filename=filename)

    return url_for('static', filename=filename, v=version)

from app import routes
