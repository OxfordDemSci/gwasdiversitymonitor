# syntax=docker/dockerfile:1

FROM python:3.8-slim-buster

WORKDIR /app

COPY requirements.txt requirements.txt

RUN python3 -m venv venv

RUN . venv/bin/activate

RUN pip3 install gunicorn

RUN pip3 install -r requirements.txt

COPY . .

# CMD [ "python3", "-m" , "flask", "run", "--host=0.0.0.0", "--port=5001"]
# CMD [ "gunicorn", "--config", "gunicorn_config.py", "wsgi:app"]