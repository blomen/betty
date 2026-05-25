# ---- Backend runtime ----
FROM python:3.12-slim

# System deps for Playwright/Camoufox headless browsers
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 fonts-liberation \
    curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps — cached layer, only rebuilds when pyproject.toml changes
# IMPORTANT: Do NOT copy source code before this — it busts the cache
COPY pyproject.toml ./
RUN mkdir -p backend/src && touch backend/src/__init__.py && \
    pip install --no-cache-dir -e ".[scrape]" && \
    pip install --no-cache-dir uvloop scikit-learn joblib lightgbm && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir "camoufox[geoip]" && python -m camoufox fetch

# Playwright browser
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright
RUN playwright install chromium && playwright install-deps && patchright install chromium

# Backend source — this is the LAST layer, so code changes only rebuild this
COPY backend/ backend/

# Re-install in editable mode now that source exists (fast — deps already cached)
RUN pip install --no-cache-dir --no-deps -e ".[scrape]"

# Non-root user
RUN useradd -m -u 1000 -s /bin/bash arnold && \
    mkdir -p /app/data /app/logs /app/models /app/data/rl /app/backend/data && \
    ln -s /app/data/rl /app/backend/data/rl && \
    chown -R arnold:arnold /app/data /app/logs /app/models /app/backend/data /app/.playwright && \
    cp -r /root/.cache /home/arnold/.cache 2>/dev/null; chown -R arnold:arnold /home/arnold/.cache 2>/dev/null; true

ENV ARNOLD_DATA_DIR=/app/data
ENV ARNOLD_LOGS_DIR=/app/logs

EXPOSE 8000

USER arnold
WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "120", "--ws-ping-interval", "0", "--ws-ping-timeout", "0", "--loop", "uvloop"]
