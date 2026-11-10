# Pin the interpreter patch and OS series; record the built image digest for rollback.
FROM python:3.13.17-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    SCHEME_DB=/runtime/schemes.db LLM_LOG=0 HOME=/runtime
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock.txt requirements-deploy.txt ./
RUN pip install --no-cache-dir -r requirements-deploy.txt \
    && groupadd --gid 10001 yojana && useradd --uid 10001 --gid yojana --no-create-home yojana \
    && mkdir /runtime && chown yojana:yojana /runtime
COPY --chown=yojana:yojana core core
COPY --chown=yojana:yojana discovery discovery
COPY --chown=yojana:yojana ingest ingest
COPY --chown=yojana:yojana llm llm
COPY --chown=yojana:yojana saathi saathi
COPY --chown=yojana:yojana schemes schemes
COPY --chown=yojana:yojana store store
COPY --chown=yojana:yojana web web
COPY --chown=yojana:yojana data/schemes.db /app/catalog/schemes.db
COPY --chown=yojana:yojana deploy/entrypoint.py /app/entrypoint.py
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=4)"
CMD ["python", "/app/entrypoint.py"]
