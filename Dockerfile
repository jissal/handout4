FROM node:24-bookworm-slim AS frontend
WORKDIR /assets
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY scripts/vendor.mjs ./scripts/vendor.mjs
RUN npm run vendor

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SAL_USE_VCLPLUGIN=svp OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 HOME=/tmp/handout4-home PORT=10000
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress fonts-nanum fontconfig tini \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home appuser \
    && mkdir -p /tmp/handout4-home \
    && chown appuser:appuser /tmp/handout4-home
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py conversion.py ./
COPY static ./static
COPY --from=frontend /assets/static/vendor ./static/vendor
USER appuser
EXPOSE 10000
# Reap short-lived LibreOffice/GPG grandchildren, including those missed between samples.
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
# One worker is essential: it owns the single conversion lock.
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --limit-concurrency 8 --timeout-keep-alive 5 --no-access-log"]
