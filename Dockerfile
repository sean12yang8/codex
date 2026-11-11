# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN --mount=type=secret,id=proxy_ca if [ -f /run/secrets/proxy_ca ]; then NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca npm ci --strict-ssl=true; else npm ci --strict-ssl=true; fi
COPY frontend/ ./
RUN npm run build
FROM python:3.12-slim-bookworm
WORKDIR /app
COPY backend/requirements.lock backend/requirements.lock
RUN --mount=type=secret,id=proxy_ca if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir -r backend/requirements.lock; else pip install --no-cache-dir -r backend/requirements.lock; fi
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
COPY --chown=app:app backend/ backend/
COPY --from=frontend --chown=app:app /build/dist frontend/dist
USER app
ENV DATA_DIR=/data
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
