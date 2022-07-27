# syntax=docker/dockerfile:1
# docker container for GWAS data collection with cron scheduler

FROM python:3.8-slim-buster

RUN apt-get update && apt-get install -y cron

WORKDIR /app

COPY deploy/cronjob /etc/cron.d/cronjob
RUN chmod 0644 /etc/cron.d/cronjob && crontab /etc/cron.d/cronjob

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY data_static.zip data_static.zip
COPY generate_data.py generate_data.py
COPY app/DataLoader.py app/DataLoader.py

# CMD ["python3", "generate_data.py", "&&", "cron", "-f"]
