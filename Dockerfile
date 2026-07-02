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

ENV DASH_HOST=0.0.0.0 DASH_PORT=8050
EXPOSE 8050
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${DASH_PORT:-8050} --workers 2 dash_app:server"]
