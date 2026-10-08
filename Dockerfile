FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations/ migrations/
COPY src/ src/
COPY entrypoint.sh /entrypoint.sh

RUN mkdir -p data/cache data/geonames logs

ENV PUID=1000
ENV PGID=1000
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 9500

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "geonames_server.api.app:app", "--host", "0.0.0.0", "--port", "9500"]
