# syntax=docker/dockerfile:1
# docker container for GWAS data collection with cron scheduler

FROM python:3.8-slim-buster

RUN apt-get update && apt-get install -y cron

WORKDIR /app

COPY requirements.txt requirements.txt
COPY generate_data.py generate_data.py
COPY cronjob /etc/cron.d/cronjob

RUN chmod 0644 /etc/cron.d/cronjob && crontab /etc/cron.d/cronjob

RUN pip3 install -r requirements.txt

# ENTRYPOINT ["cron", "-f"]
