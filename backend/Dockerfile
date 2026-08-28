# Waypoint backend — recorded-rail deployment image (S10, ADR 0007).
#
# Zero credentials by construction: NO atlas-flight CLI, NO uv, NO keyring
# packages. The recorded client (app/atlas/recorded.py) never spawns a
# subprocess — it serves the envelopes shipped in data/recorded/. The
# live rail (client.py's subprocess transport) is present in the image
# as code but has nothing to spawn and no credentials to use; compose
# selects recorded explicitly via WAYPOINT_ATLAS_MODE=recorded.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency layer first (cache-friendly). requirements.txt is the real
# manifest (fastapi / uvicorn[standard] / sqlalchemy / pytest / httpx).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App + operational scripts + the recording. Nothing else: no tests,
# no .env (sandbox keys stay out of the image), no venv.
COPY app ./app
COPY scripts ./scripts
COPY data/recorded ./data/recorded
# Bot passport path (S3): the curated ISO-3->ISO-2 nationality CSV must be
# present for mrz.validate() to run when the bot is enabled.
COPY data/iso3_to_iso2.csv ./data/iso3_to_iso2.csv

# Empty DB fallback: compose parks the DB on the /app/db volume via
# WAYPOINT_DATABASE_URL; a plain `docker run` without that env falls
# back to the default ./waypoint.db, so ship an empty file.
RUN touch waypoint.db

EXPOSE 8000

# ONE worker — one SQLite file; asyncio.to_thread DB access is the
# concurrency model (ADR 0007). Never add --workers > 1.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
