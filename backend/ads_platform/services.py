import json
import random
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Campaign, Creative, LineItem, MinuteAdAggregate, Placement, RawAdEvent

try:
    from confluent_kafka import Consumer, Producer
except Exception:  # pragma: no cover - optional in constrained environments
    Consumer = None
    Producer = None

try:
    import redis
except Exception:  # pragma: no cover - optional in constrained environments
    redis = None


class ValidationError(Exception):
    pass


@dataclass
class AdEvent:
    event_id: str
    tenant_id: int
    user_id: str
    session_id: str
    ts: datetime
    event_type: str
    campaign_id: int = 0
    line_item_id: int = 0
    placement_id: int = 0
    creative_id: int = 0
    value_cents: int = 0
    metadata: dict[str, Any] | None = None


ALLOWED_EVENT_TYPES = {"IMPRESSION", "CLICK", "CONVERSION", "VIEW", "INTERACTION"}


def parse_event(payload: dict[str, Any]) -> AdEvent:
    required = {"event_id", "tenant_id", "user_id", "session_id", "ts", "event_type"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ValidationError(f"Missing required fields: {', '.join(missing)}")
    try:
        UUID(str(payload["event_id"]))
    except ValueError as exc:
        raise ValidationError("event_id must be a valid UUID") from exc
    ts = parse_datetime(str(payload["ts"]))
    if ts is None:
        raise ValidationError("ts must be an ISO-8601 datetime")
    if timezone.is_naive(ts):
        ts = timezone.make_aware(ts, timezone=dt_timezone.utc)
    else:
        ts = ts.astimezone(dt_timezone.utc)
    event_type = str(payload["event_type"]).upper()
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValidationError(f"Unsupported event_type: {event_type}")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValidationError("metadata must be a JSON object")
    return AdEvent(
        event_id=str(payload["event_id"]),
        tenant_id=int(payload["tenant_id"]),
        user_id=str(payload["user_id"]),
        session_id=str(payload["session_id"]),
        ts=ts,
        event_type=event_type,
        campaign_id=int(payload.get("campaign_id") or 0),
        line_item_id=int(payload.get("line_item_id") or 0),
        placement_id=int(payload.get("placement_id") or 0),
        creative_id=int(payload.get("creative_id") or 0),
        value_cents=int(payload.get("value_cents") or 0),
        metadata=metadata,
    )


_EVENT_QUEUE: deque[str] = deque()
_REDIS_CLIENT = None


def _redis_client():
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if not settings.REDIS_URL or redis is None:
        return None
    try:
        _REDIS_CLIENT = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        _REDIS_CLIENT.ping()
    except Exception:
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


class LocalKafkaProducer:
    def produce_event(self, event: AdEvent) -> None:
        _EVENT_QUEUE.append(json.dumps(event.__dict__, default=str))


class KafkaProducerAdapter:
    def __init__(self):
        if Producer is None:
            raise ValidationError("confluent-kafka is not available")
        self._topic = settings.KAFKA_TOPIC_EVENTS
        self._producer = Producer({"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS})

    def produce_event(self, event: AdEvent) -> None:
        payload = json.dumps(event.__dict__, default=str)
        self._producer.produce(
            self._topic,
            payload.encode("utf-8"),
            key=str(event.tenant_id).encode("utf-8"),
        )
        self._producer.flush(5)


class LocalKafkaConsumer:
    def poll(self, batch_size: int = 100) -> list[str]:
        rows: list[str] = []
        while _EVENT_QUEUE and len(rows) < batch_size:
            rows.append(_EVENT_QUEUE.popleft())
        return rows


class KafkaConsumerAdapter:
    def __init__(self):
        if Consumer is None:
            raise ValidationError("confluent-kafka is not available")
        self._consumer = Consumer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "group.id": settings.KAFKA_CONSUMER_GROUP,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        self._consumer.subscribe([settings.KAFKA_TOPIC_EVENTS])

    def poll(self, batch_size: int = 100) -> list[str]:
        rows: list[str] = []
        for _ in range(batch_size):
            msg = self._consumer.poll(timeout=0.2)
            if msg is None:
                break
            if msg.error():
                continue
            rows.append(msg.value().decode("utf-8"))
            self._consumer.commit(msg)
        return rows


def get_producer() -> LocalKafkaProducer | KafkaProducerAdapter:
    if settings.KAFKA_BOOTSTRAP_SERVERS:
        try:
            return KafkaProducerAdapter()
        except Exception:
            return LocalKafkaProducer()
    return LocalKafkaProducer()


def get_consumer() -> LocalKafkaConsumer | KafkaConsumerAdapter:
    if settings.KAFKA_BOOTSTRAP_SERVERS:
        try:
            return KafkaConsumerAdapter()
        except Exception:
            return LocalKafkaConsumer()
    return LocalKafkaConsumer()


def _dedup_add(key: str, timeout: int) -> bool:
    client = _redis_client()
    if client is not None:
        try:
            return bool(client.set(key, 1, nx=True, ex=timeout))
        except Exception:
            pass
    return bool(cache.add(key, 1, timeout=timeout))


def _counter_get(key: str) -> int:
    client = _redis_client()
    if client is not None:
        try:
            value = client.get(key)
            return int(value or 0)
        except Exception:
            pass
    return int(cache.get(key, 0))


def _counter_incr_with_ttl(key: str, ttl_seconds: int) -> int:
    client = _redis_client()
    if client is not None:
        try:
            pipeline = client.pipeline()
            pipeline.incr(key)
            pipeline.expire(key, ttl_seconds)
            value, _ = pipeline.execute()
            return int(value)
        except Exception:
            pass
    value = int(cache.get(key, 0)) + 1
    cache.set(key, value, timeout=ttl_seconds)
    return value


def process_event(event: AdEvent) -> bool:
    dedup_key = f"ad_event:{event.event_id}"
    if not _dedup_add(dedup_key, timeout=settings.EVENT_DEDUP_TTL_SECONDS):
        return False

    RawAdEvent.objects.create(
        event_date=event.ts.date(),
        event_time=event.ts,
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        user_id=event.user_id,
        session_id=event.session_id,
        event_type=event.event_type,
        campaign_id=event.campaign_id,
        line_item_id=event.line_item_id,
        placement_id=event.placement_id,
        creative_id=event.creative_id,
        value_cents=event.value_cents,
        metadata_json=event.metadata or {},
    )

    minute = event.ts.replace(second=0, microsecond=0)
    impression = 1 if event.event_type == "IMPRESSION" else 0
    click = 1 if event.event_type == "CLICK" else 0
    conversion = 1 if event.event_type == "CONVERSION" else 0

    with transaction.atomic():
        agg, _created = MinuteAdAggregate.objects.select_for_update().get_or_create(
            minute=minute,
            tenant_id=event.tenant_id,
            campaign_id=event.campaign_id,
            placement_id=event.placement_id,
            defaults={
                "impressions": 0,
                "clicks": 0,
                "conversions": 0,
                "revenue_cents": 0,
            },
        )
        agg.impressions = F("impressions") + impression
        agg.clicks = F("clicks") + click
        agg.conversions = F("conversions") + conversion
        agg.revenue_cents = F("revenue_cents") + (event.value_cents if conversion else 0)
        agg.save(update_fields=["impressions", "clicks", "conversions", "revenue_cents"])
    return True


def build_summary(
    tenant_id: int,
    dt_from: datetime,
    dt_to: datetime,
    campaign_id: int | None,
    placement_id: int | None,
) -> dict[str, Any]:
    query = MinuteAdAggregate.objects.filter(tenant_id=tenant_id, minute__gte=dt_from, minute__lte=dt_to)
    if campaign_id is not None:
        query = query.filter(campaign_id=campaign_id)
    if placement_id is not None:
        query = query.filter(placement_id=placement_id)
    totals = query.aggregate(
        impressions=Sum("impressions"),
        clicks=Sum("clicks"),
        conversions=Sum("conversions"),
        revenue_cents=Sum("revenue_cents"),
    )
    impressions = int(totals["impressions"] or 0)
    clicks = int(totals["clicks"] or 0)
    conversions = int(totals["conversions"] or 0)
    revenue_cents = int(totals["revenue_cents"] or 0)

    ctr = clicks / impressions if impressions else 0.0
    conversion_rate = conversions / clicks if clicks else 0.0
    revenue = revenue_cents / 100
    ecpm = revenue / impressions * 1000 if impressions else 0.0
    ecpc = revenue / clicks if clicks else 0.0

    return {
        "tenant_id": tenant_id,
        "from": dt_from.isoformat(),
        "to": dt_to.isoformat(),
        "campaign_id": campaign_id,
        "placement_id": placement_id,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "revenue_cents": revenue_cents,
        "ctr": ctr,
        "conversion_rate": conversion_rate,
        "ecpm": ecpm,
        "ecpc": ecpc,
    }


def _matches_targeting(targeting: dict[str, Any], context: dict[str, Any]) -> bool:
    return all(context.get(k) == v for k, v in targeting.items())


def decide_ad(tenant_id: int, placement_key: str, user_id: str, context: dict[str, Any]) -> dict[str, Any]:
    now = timezone.now()
    placement = Placement.objects.filter(tenant_id=tenant_id, key=placement_key).first()
    if placement is None:
        raise ValidationError("Unknown placement for tenant")

    line_items = (
        LineItem.objects.select_related("campaign")
        .prefetch_related("creatives")
        .filter(
            placement=placement,
            campaign__tenant_id=tenant_id,
            campaign__status=Campaign.Status.ACTIVE,
            campaign__start_at__lte=now,
            campaign__end_at__gte=now,
        )
    )

    eligible: list[tuple[LineItem, list[Creative]]] = []
    for line_item in line_items:
        if line_item.targeting_json and not _matches_targeting(line_item.targeting_json, context):
            continue
        cap_key = f"fc:{tenant_id}:{line_item.campaign_id}:{user_id}"
        current = _counter_get(cap_key)
        if current >= settings.FREQUENCY_CAP_PER_CAMPAIGN_PER_DAY:
            continue
        creatives = list(line_item.creatives.all())
        if not creatives:
            continue
        eligible.append((line_item, creatives))

    if not eligible:
        raise ValidationError("No eligible ads")

    weights = [max(1, x[0].bid_cents) for x in eligible]
    selected, creatives = random.choices(eligible, weights=weights, k=1)[0]
    creative = random.choice(creatives)

    cap_key = f"fc:{tenant_id}:{selected.campaign_id}:{user_id}"
    _counter_incr_with_ttl(cap_key, ttl_seconds=60 * 60 * 24)

    return {
        "campaign_id": selected.campaign_id,
        "line_item_id": selected.id,
        "creative": {
            "id": creative.id,
            "image_url": creative.image_url,
            "landing_url": creative.landing_url,
            "product_sku": creative.product_sku,
        },
    }
