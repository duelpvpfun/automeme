FROM python:3.12-slim

# Optional OCR support (safety screening of in-image text). Comment out to slim.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist the SQLite DB + downloaded images outside the image layer.
ENV AUTOMEME_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8080

CMD ["python", "-m", "automeme"]
