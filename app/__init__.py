import sys
from flask import Flask
from sassutils.wsgi import SassMiddleware

# Ensure that the config is always imported when starting the application
sys.path.append('/home/gi-liam/Documents/Code/gwasdiversitymonitor/deploy')

app = Flask(__name__)
app.config.from_object('config')


#specify the Google Analytics key here
app.config["GA_KEY"]=''

from app import routes

app.wsgi_app = SassMiddleware(app.wsgi_app, {
    'app': ('static/sass', 'static/css', '/static/css', False)
})
