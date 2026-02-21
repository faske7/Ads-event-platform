# Ads Event Platform

MVP implementation based on `docs/DESIGN.md`.

## What's included

- Django APIs:
  - `POST /api/events/` — ingest events into a Kafka-like queue
  - `POST /api/decision/` — choose campaign/creative for placement + user
  - `GET /api/report/summary/` — aggregated analytics summary
- Domain model (tenant, campaign, placement, line item, creative, attribution rule)
- Worker command: `python manage.py consume_events --once`
  - consumes queued events
  - deduplicates by `event_id`
  - stores raw events
  - updates minute-level aggregates
- Infrastructure integration:
  - Kafka producer/consumer via `confluent-kafka` when `KAFKA_BOOTSTRAP_SERVERS` is set
  - Redis dedup/frequency cap via `REDIS_URL`
  - Automatic in-memory fallback for local tests/dev when Kafka/Redis are unavailable

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python manage.py migrate
python manage.py runserver
```

## Tests

```bash
python manage.py test
```
