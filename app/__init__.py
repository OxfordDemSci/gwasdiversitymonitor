import os

from flask import Flask, url_for

app = Flask(__name__)
app.config.from_object('config')

# Leave analytics disabled unless a deployment explicitly provides the base URL
# of its self-hosted GoatCounter instance.
app.config["GOATCOUNTER_URL"] = os.environ.get(
    "GOATCOUNTER_URL", app.config.get("GOATCOUNTER_URL", "")
).rstrip("/")

@app.template_global()
def versioned_static(filename):
    try:
        version = os.stat(os.path.join(app.static_folder, filename)).st_mtime_ns
    except OSError:
        return url_for('static', filename=filename)

    return url_for('static', filename=filename, v=version)

from app import routes
