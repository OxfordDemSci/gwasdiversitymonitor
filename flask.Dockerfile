# syntax=docker/dockerfile:1
# docker container for GWAS flask app with gunicorn wsgi server

FROM python:3.8-slim-buster

WORKDIR /app

COPY app app
COPY config.py config.py
COPY gunicorn_config.py gunicorn_config.py
COPY gwasdiversitymonitor.iml gwasdiversitymonitor.iml
COPY gwasdiversitymonitor.py gwasdiversitymonitor.py
COPY wsgi.py wsgi.py
COPY requirements.txt requirements.txt

RUN pip3 install gunicorn
RUN pip3 install -r requirements.txt

# CMD [ "gunicorn", "--config", "gunicorn_config.py", "wsgi:app"]