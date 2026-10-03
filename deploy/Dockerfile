# Wallet-Pass-Plattform (API) - Build aus dem Repository-Wurzelverzeichnis:
#   docker build -f deploy/Dockerfile -t wallet-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus

WORKDIR /srv
COPY pyproject.toml README.md alembic.ini ./
COPY creatwallet ./creatwallet
COPY app ./app
COPY migrations ./migrations
RUN pip install ".[server]" \
    && useradd --system --uid 10001 --home-dir /srv wallet \
    && mkdir -p /data/certs && chown -R wallet /data

USER wallet
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

# Migrationen laufen beim Start; danach die API mit mehreren Prozessen.
CMD ["sh", "-c", "rm -rf $PROMETHEUS_MULTIPROC_DIR && mkdir -p $PROMETHEUS_MULTIPROC_DIR && python -m app migrate && exec uvicorn app.api:create_app --factory --host 0.0.0.0 --port 8000 --workers ${WEB_WORKERS:-2} --proxy-headers --forwarded-allow-ips='*'"]
