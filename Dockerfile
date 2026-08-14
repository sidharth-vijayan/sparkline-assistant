FROM python:3.11-slim

WORKDIR /app

# System dependencies for document parsing and OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml poetry.lock ./
RUN pip install --no-cache-dir poetry==1.8.3 && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev --no-interaction --no-root

# Readers for the pre-2007 binary Office formats. Deliberately a separate layer
# after the dependency install so adding them does not invalidate the cached
# poetry layer (which pulls torch and takes many minutes to rebuild).
#   antiword — extracts text from .doc
#   xlrd     — reads .xls (2.x dropped .xlsx, which openpyxl already handles)
RUN apt-get update && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "xlrd==2.0.1"

# Copy application code
COPY . .

# Create data directory for BM25 index persistence
RUN mkdir -p data

EXPOSE 8000

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
