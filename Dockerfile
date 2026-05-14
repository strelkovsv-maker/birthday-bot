FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src ./src
COPY tools ./tools

# Default DB path inside the container — override via env to point at a Railway volume
ENV DB_PATH=/data/state.db
RUN mkdir -p /data

# Run the bot
CMD ["python", "-m", "src.main"]
