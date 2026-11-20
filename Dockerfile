FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY pricing_api ./pricing_api
COPY ui ./ui
COPY data/sample_emails.json data/fixture_extractions.json ./data/

RUN useradd --create-home walter && mkdir -p /srv/var && chown walter /srv/var
USER walter

# One image, three roles (see docker-compose.yml). Default: workflow API.
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
