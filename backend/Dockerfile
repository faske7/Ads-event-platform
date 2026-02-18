FROM python:3.12-slim

WORKDIR /app

# зависимости (тут как тебе удобнее: requirements.txt / poetry)
COPY ../requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Порт только для информации, реальный — в docker-compose
EXPOSE 8000
