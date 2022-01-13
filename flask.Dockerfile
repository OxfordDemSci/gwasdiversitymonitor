# syntax=docker/dockerfile:1

FROM python:3.8-slim-buster

WORKDIR /app

COPY requirements.txt requirements.txt

RUN python3 -m venv venv

RUN . venv/bin/activate

RUN pip3 install gunicorn

RUN pip3 install -r requirements.txt

# COPY . .
COPY app app
COPY config.py config.py
COPY gunicorn_config.py gunicorn_config.py
COPY gwasdiversitymonitor.iml gwasdiversitymonitor.iml
COPY gwasdiversitymonitor.py gwasdiversitymonitor.py
COPY wsgi.py wsgi.py

# CMD [ "gunicorn", "--config", "gunicorn_config.py", "wsgi:app"]