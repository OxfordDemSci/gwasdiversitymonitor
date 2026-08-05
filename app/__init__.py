import os

from flask import Flask, url_for
from sassutils.wsgi import SassMiddleware

app = Flask(__name__)
app.config.from_object('config')

# specify the Google Analytics key here
app.config["GA_KEY"] = ''

@app.template_global()
def versioned_static(filename):
    try:
        version = os.stat(os.path.join(app.static_folder, filename)).st_mtime_ns
    except OSError:
        return url_for('static', filename=filename)

    return url_for('static', filename=filename, v=version)

from app import routes

app.wsgi_app = SassMiddleware(app.wsgi_app, {
    'app': ('static/sass', 'static/css', '/static/css', False)  # last arg = strip_extension
})
