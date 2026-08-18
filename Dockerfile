FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY sources.yaml ./
COPY platforms.yaml ./
RUN pip install --no-cache-dir . "psycopg[binary]"
