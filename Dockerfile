FROM python:3.11-slim

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
