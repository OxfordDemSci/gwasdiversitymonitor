from flask import Flask
from sassutils.wsgi import SassMiddleware

app = Flask(__name__)
app.config.from_object('config')


from app import routes

app.wsgi_app = SassMiddleware(app.wsgi_app, {
    'app': ('static/sass', 'static/css', '/static/css')
})
