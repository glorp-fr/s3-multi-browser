FROM python:3.11-slim

# Lie l'image au dépôt sur GHCR (page du package, provenance, pull public).
LABEL org.opencontainers.image.source="https://github.com/glorp-fr/s3-multi-browser"
LABEL org.opencontainers.image.description="Multi S3 Browser — navigateur S3 multi-comptes (Flask)"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py .
COPY VERSION .

RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV MULTI_S3_BROWSER_DATA_DIR=/app/data
EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "wsgi:app"]
