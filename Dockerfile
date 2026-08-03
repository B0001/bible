FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# NLTK stopwords corpus is needed by the Snowball stemmer (ignore_stopwords=True).
RUN python -m nltk.downloader -d /usr/share/nltk_data stopwords
ENV NLTK_DATA=/usr/share/nltk_data

COPY parser.py dash_app.py bibles.toml ./
COPY sample ./sample
COPY scripts ./scripts

# Pre-grade the bundled sample data so the app has something to serve out of the
# box (the nasb entry in bibles.toml; other entries are skipped until their CSVs
# exist). To add Hebrew/Greek texts, run the scripts/ converters and parser.py
# with --lang inside the container or mount pre-graded CSVs into out/.
RUN python parser.py --bible sample/nasb_sample.txt \
        --vocab sample/my_vocab.txt --out out/nasb_graded.csv

# Non-root runtime. Kubernetes pins runAsUser/runAsGroup to this same 10001 so
# the PVC holding reads.db is writable; keep the two in sync if either changes.
# /app itself stays root-owned and read-only at runtime -- everything the app
# writes lives on the mounted volume (READS_DB) or in the /tmp emptyDir.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

# Bytecode is baked at build time; writing it at runtime would need a writable
# /app, which readOnlyRootFilesystem forbids.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The account's home is /home/app, which does not exist and sits on the
# read-only root filesystem anyway. gunicorn's control server tries to open a
# socket under $HOME at startup and logs "Read-only file system: '/home/app'"
# without it. /tmp is the emptyDir the Deployment mounts.
ENV HOME=/tmp

# Read tracking uses Postgres when DATABASE_URL is set and SQLite otherwise.
# Deliberately neither is set here: Kubernetes injects DATABASE_URL from a
# Secret, and baking a READS_DB default would mean a missing Secret silently
# falls back to per-pod SQLite -- every replica showing a different set of read
# verses -- instead of failing where someone will notice. For a standalone
# `docker run`, pass one explicitly:
#   -e DATABASE_URL=postgresql://...        (shared, multi-container)
#   -e READS_DB=/data/reads.db -v ...:/data (single container, file-backed)

USER 10001:10001

ENV DASH_HOST=0.0.0.0 DASH_PORT=8050
EXPOSE 8050

# `exec` matters: without it the shell stays PID 1 and swallows SIGTERM, so
# Kubernetes pod deletion would hang until terminationGracePeriodSeconds runs
# out instead of shutting gunicorn down gracefully.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${DASH_PORT:-8050} --workers ${GUNICORN_WORKERS:-2} --timeout ${GUNICORN_TIMEOUT:-60} --graceful-timeout 30 --access-logfile - --error-logfile - dash_app:server"]
