FROM python:3.12-slim

WORKDIR /app

# System deps (common ones for building wheels like psycopg/cryptography, etc.)
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
     curl \
     build-essential \
     libpq-dev \
  && rm -rf /var/lib/apt/lists/*

# Install Poetry
ENV POETRY_VERSION=1.8.3
ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_CREATE=false
ENV PYTHONUNBUFFERED=1

RUN curl -sSL https://install.python-poetry.org | python - \
  && ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Copy only dependency files first (better layer caching)
COPY ../pyproject.toml ../poetry.lock* ./

# Install deps (skip installing the project itself to avoid packaging issues)
RUN poetry lock --no-update --no-ansi
RUN poetry install --no-ansi --no-root

# Copy the rest of the app
COPY . .

EXPOSE 8000
