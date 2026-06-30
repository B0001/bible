FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# NLTK stopwords corpus is needed by the Snowball stemmer (ignore_stopwords=True).
RUN python -m nltk.downloader -d /usr/share/nltk_data stopwords
ENV NLTK_DATA=/usr/share/nltk_data

COPY parser.py dash_app.py ./
COPY sample ./sample

# Pre-grade the bundled sample data so the app has something to serve out of the
# box. Mount your own data and re-run parser.py to override out/graded.csv.
RUN python parser.py --bible sample/nasb_sample.txt \
        --vocab sample/my_vocab.txt --out out/graded.csv

ENV DASH_HOST=0.0.0.0 DASH_PORT=8050
EXPOSE 8050
CMD ["python", "dash_app.py"]
