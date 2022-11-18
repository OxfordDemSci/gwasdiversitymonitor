# syntax=docker/dockerfile:1
# docker container for GWAS data collection with cron scheduler

FROM python:3.8-slim-buster

RUN apt-get update

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY data_static.zip data_static.zip
COPY generate_data.py generate_data.py
COPY app/DataLoader.py app/DataLoader.py

# CMD ["python3", "generate_data.py", "&&", "cron", "-f"]
