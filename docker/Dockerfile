# ── Stage 1: Build dependencies ──
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libc6-dev libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --user -r /tmp/requirements.txt

# ── Stage 2: Runtime ──
FROM python:3.13-slim

WORKDIR /app

# System libraries for Playwright, curl-cffi, ddddocr
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libcurl4 \
    libgl1-mesa-glx libgthread-2.0-0 \
    fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Pre-download Playwright Chromium
RUN playwright install chromium 2>&1 || true

# Pre-download CloakBrowser binary
RUN python3 -c "import cloakbrowser.download; cloakbrowser.download.ensure_binary()" 2>&1 || echo "CloakBrowser binary download deferred to runtime"

COPY apexcrawler/ ./apexcrawler/
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

VOLUME ["/root/.cloakbrowser", "/root/.apexcrawler"]
EXPOSE 8000
ENV CLOAKBROWSER_CACHE_DIR=/root/.cloakbrowser
CMD ["apex", "dashboard"]
