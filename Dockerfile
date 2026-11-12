# Pin the interpreter patch and OS series; record the built image digest for rollback.
# One image, two EasyPanel services:
#   yojana-api     default CMD (uvicorn via deploy/entrypoint.py), port 8000
#   yojana-worker  command override: python -m worker   (no port, never public)
FROM python:3.13.17-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    SCHEME_DB=/runtime/schemes.db LLM_LOG=0 HOME=/runtime
# ffmpeg decodes WhatsApp voice notes (OGG/Opus) to 16 kHz mono PCM for Sarvam.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock.txt requirements-deploy.txt ./
RUN pip install --no-cache-dir -r requirements-deploy.txt \
    && groupadd --gid 10001 yojana && useradd --uid 10001 --gid yojana --no-create-home yojana \
    && mkdir /runtime && chown yojana:yojana /runtime
COPY --chown=yojana:yojana agents agents
COPY --chown=yojana:yojana core core
COPY --chown=yojana:yojana discovery discovery
COPY --chown=yojana:yojana ingest ingest
COPY --chown=yojana:yojana llm llm
COPY --chown=yojana:yojana saathi saathi
COPY --chown=yojana:yojana schemes schemes
COPY --chown=yojana:yojana store store
COPY --chown=yojana:yojana web web
COPY --chown=yojana:yojana worker worker
COPY --chown=yojana:yojana data/schemes.db /app/catalog/schemes.db
COPY --chown=yojana:yojana deploy/entrypoint.py /app/entrypoint.py
COPY --chown=yojana:yojana deploy/healthcheck.py /app/healthcheck.py
COPY --chown=yojana:yojana deploy/smoke_imports.py /app/smoke_imports.py
USER 10001:10001
# Fail the build if any api/worker module cannot import or ffmpeg lacks OGG/Opus support.
RUN python /app/smoke_imports.py
EXPOSE 8000
# api: /readyz; worker: fresh heartbeat file (see deploy/healthcheck.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python /app/healthcheck.py
CMD ["python", "/app/entrypoint.py"]
