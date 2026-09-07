FROM docker:27-cli

LABEL org.opencontainers.image.source="https://github.com/glorp-fr/s3-multi-browser"
LABEL org.opencontainers.image.description="Sidecar de mise à jour pour Multi S3 Browser (pilote docker compose via le socket)"

RUN apk add --no-cache python3

COPY updater.py /updater.py

EXPOSE 9000
ENTRYPOINT ["python3", "/updater.py"]
