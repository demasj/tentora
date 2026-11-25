# Tentora web — sit the practice exams in the browser.
# Stdlib-only Python: no pip install needed.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EXAM_HOST=0.0.0.0 \
    EXAM_PORT=8080

WORKDIR /app
COPY scripts/ scripts/
COPY webapp/ webapp/
# Baked-in exam profiles + banks; docker-compose mounts ./exams over this so new
# banks from `make generate` show up without a rebuild.
COPY exams/ exams/

RUN useradd --create-home --uid 1000 exam && mkdir -p results && chown exam results
USER exam

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/api/catalog', timeout=2)" || exit 1
CMD ["python", "webapp/server.py"]
