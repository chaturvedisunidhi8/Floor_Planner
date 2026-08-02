# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf-cache

WORKDIR /app

# libgomp is required by faiss-cpu; the rest are Pillow's image codecs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 libjpeg62-turbo libopenjp2-7 zlib1g curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY scripts ./scripts

# Bake the embedding model into the image so the first request is not the one
# that pays for a 120 MB download.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('BAAI/bge-small-en-v1.5')"

RUN mkdir -p storage/images storage/index \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /opt/hf-cache
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
