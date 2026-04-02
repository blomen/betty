FROM python:3.10-slim AS base

# System deps for Playwright headless Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 fonts-liberation \
    curl && rm -rf /var/lib/apt/lists/*

# Node.js for frontend build
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (cached layer — only rebuilds when pyproject.toml changes)
COPY pyproject.toml ./
COPY backend/src/ backend/src/
RUN pip install --no-cache-dir -e ".[scrape]" && \
    pip install --no-cache-dir uvloop && \
    pip install --no-cache-dir scikit-learn joblib && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Playwright browser
RUN playwright install chromium && playwright install-deps

# Frontend build
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci --ignore-scripts

COPY frontend/ frontend/
RUN cd frontend && npm run build

# Backend source (refresh — earlier COPY was just for dep install caching)
COPY backend/ backend/

# Data directories
RUN mkdir -p /app/data /app/logs /app/models

ENV FIREV_DATA_DIR=/app/data
ENV FIREV_LOGS_DIR=/app/logs
ENV FIREV_FRONTEND_DIR=/app/frontend/dist

EXPOSE 8000

WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "120", "--loop", "uvloop"]
