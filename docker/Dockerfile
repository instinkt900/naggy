# Single-stage image. Code is baked in via COPY + `pip install .`, so a code,
# template, or static change needs an image rebuild (a restart alone won't pick it
# up). tzdata is installed so zoneinfo can resolve the configured display timezone.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY naggy ./naggy
RUN pip install --no-cache-dir .

VOLUME ["/data"]
EXPOSE 8080

ENTRYPOINT ["naggy"]
CMD ["serve", "-c", "/app/config.toml"]
