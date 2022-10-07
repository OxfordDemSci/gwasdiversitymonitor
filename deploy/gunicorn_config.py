# config for gunicorn wsgi server (load balancing link between web server and flask application)

bind = "0.0.0.0:8000"
workers = 2
threads = 4
timeout = 120