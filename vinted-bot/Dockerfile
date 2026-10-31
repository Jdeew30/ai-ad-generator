FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY vintedbot ./vintedbot
COPY config.example.toml .env.example ./

RUN useradd --create-home --uid 10001 bot && mkdir -p /app/data && chown bot /app/data
USER bot

CMD ["python", "-m", "vintedbot", "run"]
