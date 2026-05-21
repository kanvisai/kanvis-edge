# syntax=docker/dockerfile:1
# Multi-stage: amd64 (Intel N100) y arm64 (Jetson Orin Nano)

FROM python:3.11-slim-bookworm AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libavformat-dev \
    libavcodec-dev \
    libavutil-dev \
    libswscale-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libavformat59 \
    libavcodec59 \
    libavutil57 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ ./src/
COPY config/ ./config/
# UI web (Fase 4) incluida en src/web/static

ENV PYTHONPATH=/app
ENV CONFIG_DIR=/app/config
ENV EDGE_API_HOST=0.0.0.0
ENV EDGE_API_PORT=8000

EXPOSE 8000 8554 8188

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

CMD ["python", "-m", "src.main"]
