FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY pulse ./pulse

RUN pip install --upgrade pip && pip install .

RUN mkdir -p data/raw data/inbox

VOLUME ["/app/data"]

CMD ["pulse", "serve", "-c", "/app/config/config.yaml"]
