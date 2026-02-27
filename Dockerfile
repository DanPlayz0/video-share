FROM python:3.11-slim

# Install ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos "" appuser

# Create app directory
WORKDIR /app

# Copy files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create storage root folder
RUN mkdir -p /data && chown -R appuser:appuser /app /data

# Environment
ENV PYTHONUNBUFFERED=1
ENV PORT=5000
ENV GUNICORN_WORKERS=2
ENV GUNICORN_THREADS=4
ENV GUNICORN_TIMEOUT=120

EXPOSE 5000

USER appuser

# Run with gunicorn (production server)
CMD ["sh", "-c", "gunicorn -w ${GUNICORN_WORKERS} --threads ${GUNICORN_THREADS} --timeout ${GUNICORN_TIMEOUT} -b 0.0.0.0:${PORT} app:app"]
