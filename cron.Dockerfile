# syntax=docker/dockerfile:1

FROM python:3.8-slim-buster

RUN apt-get update && apt-get install -y cron

WORKDIR /app

COPY requirements.txt requirements.txt

RUN python3 -m venv venv

RUN . venv/bin/activate

RUN pip3 install -r requirements.txt

# COPY . .
COPY generate_data.sh generate_data.sh
COPY generate_data.py generate_data.py
COPY data data

RUN chmod 777 generate_data.sh

RUN crontab -l | { cat; echo "0 20 * * * python3 /app/generate_data.sh"; } | crontab -

# CMD cron
