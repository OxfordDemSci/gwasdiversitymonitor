# syntax=docker/dockerfile:1
# docker container for GWAS data collection with cron scheduler

FROM python:3.13-slim

RUN apt-get update

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY data_static.zip data_static.zip
COPY generate_data.py generate_data.py
COPY funder_pipeline.py funder_pipeline.py
COPY data/funders/funder_cleaner.json data/funders/funder_cleaner.json
COPY data/support/cohort_cleaner.json data/support/cohort_cleaner.json
COPY app/DataLoader.py app/DataLoader.py
COPY app/DashboardFilters.py app/DashboardFilters.py

# CMD ["python3", "generate_data.py", "&&", "cron", "-f"]
