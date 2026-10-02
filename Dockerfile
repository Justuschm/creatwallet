# creatwallet - Web-Editor für Apple-Wallet-Pässe
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CREATWALLET_HOST=0.0.0.0 \
    CREATWALLET_PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY creatwallet ./creatwallet
RUN pip install . \
    && useradd --system --uid 10001 --home-dir /app creatwallet \
    && mkdir -p /certs /data && chown creatwallet /data

USER creatwallet
WORKDIR /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('CREATWALLET_PORT', '8080'), timeout=2)"

ENTRYPOINT ["creatwallet"]
CMD ["serve"]
