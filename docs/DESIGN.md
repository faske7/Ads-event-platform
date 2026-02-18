# Ads Event Platform – High-Level Design (MVP)

## 1. Problem & Scope

This document describes the design of a **minimal ads event platform** for a multi-tenant visual commerce product (e.g., room visualizer for interior décor).

The platform should support:

- Ingesting high-volume ad and behavioral events (`impression`, `click`, `conversion`, etc.)
- Streaming these events via Kafka for reliable, scalable processing
- Storing events in an analytical store (ClickHouse) for reporting
- Maintaining real-time aggregations (CTR, conversions, revenue) per tenant/campaign/placement
- Providing:
  - **Ingest API** for events
  - **Reporting API** for analytics
  - **Decisioning API** that chooses what ad/creative to show in a visual experience

This is an **MVP**: not feature-complete, but structured as a real ads backend you can evolve.

---

## 2. Goals

- Support **multi-tenant** ads use cases (each vendor = tenant)
- Handle **high-throughput event ingestion** with low latency
- Provide **at-least-once** processing with **idempotency** and deduplication
- Enable **real-time and historical reporting** with OLAP-friendly storage
- Provide a **decisioning service** to choose campaigns/creatives for a specific user/placement
- Make it easy to scale horizontally (Kafka partitions, multiple consumers, ClickHouse shards)

---

## 3. Non-Goals

- No full-featured ad server with complex targeting language or pacing algorithms
- No UI/admin application (beyond what Django admin gives out-of-the-box)
- No sophisticated fraud detection, brand safety, or machine-learning ranking
- No multi-region active-active setup (single-region, single Kafka cluster)

These areas can be added later as extensions to this foundation.

---

## 4. High-Level Architecture

### 4.1 Components

- **Ingest API (Django)**  
  Receives HTTP ad/behavioral events and publishes them to Kafka.

- **Kafka (Event Stream)**  
  Acts as a durable log for all events. Consumers can replay from any offset.

- **Event Processor / Aggregator (Worker)**  
  A Django management command acting as a Kafka consumer:
  - Validates and deduplicates events
  - Writes raw events into ClickHouse
  - Updates aggregated metrics (minute-level and above)

- **ClickHouse (Analytics Store)**  
  Columnar database for:
  - Raw event storage
  - Aggregated tables
  - Fast analytical queries (for reports/dashboards)

- **Postgres (Domain Store)**  
  Stores configuration and domain models:
  - Tenants
  - Campaigns, placements, line items, creatives
  - Attribution rules

- **Redis (Caches & Dedup)**  
  - Idempotency/deduplication using `event_id`
  - Frequency capping counters (per user/campaign)
  - Potential place for real-time counters

- **Reporting API (Django)**  
  HTTP endpoints querying ClickHouse for analytics (CTR, conversion rate, revenue).

- **Decisioning API (Django)**  
  Chooses the best eligible ad/creative for a `(tenant, placement, user, context)` request in real time.

### 4.2 Architecture Diagram (ASCII)

                 +-------------------------+
                 |    Visual Experience    |
                 | (Room / Product Viewer) |
                 +------------+------------+
                              |
             user sees ad / interacts
                              |
                   +----------v----------+
                   |    Decisioning API  |
                   |  (Django / Redis)   |
                   +----------+----------+
                              |
                              | selected campaign/creative
                              |
                       (events emitted)
                              |
                      +-------v--------+
                      |   Ingest API   |
                      |    (Django)    |
                      +-------+--------+
                              |
                              | JSON events (HTTP)
                              |
                     +--------v--------+
                     |     Kafka       |
                     |  ad_events topic|
                     +--------+--------+
                              |
                   +----------v-----------+
                   |   Event Processor    |
                   | (Django mgmt cmd)    |
                   +----------+-----------+
                              |
                              | validated, deduped events
                              |
             +----------------+-----------------------+
             |                                        |
       +-----v-----+                          +-------v--------+
       | ClickHouse |                          |   Redis       |
       |  Raw + Agg |                          | Dedup & Caps  |
       +-----+------+                          +-------+-------+
             |                                        |
             |                                +-------v-------+
      +------v--------+                       |   Postgres    |
      | Reporting API |                       | (Domain model)|
      +---------------+                       +---------------+

## 5. Data Model

### 5.1 Domain (Postgres)

#### Tenant

Represents a vendor/brand using the platform.

- `Tenant`
  - `id` (PK)
  - `name` (string)

`tenant_id` is used in all domain entities and events for **data isolation**.

#### Campaign

High-level advertising unit.

- `Campaign`
  - `id` (PK)
  - `tenant` (FK → Tenant)
  - `name` (string)
  - `status` (enum: `DRAFT`, `ACTIVE`, `PAUSED`, `ENDED`)
  - `start_at` (datetime, UTC)
  - `end_at` (datetime, UTC)
  - `daily_budget_cents` (int)

Eligibility for serving is determined by status and time window.

#### Placement

Represents an ad slot in the visual experience.

- `Placement`
  - `id` (PK)
  - `tenant` (FK → Tenant)
  - `key` (string, e.g. `visualizer_native_1`)

Key is what the client uses to request an ad for a given visual slot.

#### LineItem

Segment of a campaign bound to a placement with a bid and targeting.

- `LineItem`
  - `id` (PK)
  - `campaign` (FK → Campaign)
  - `placement` (FK → Placement)
  - `bid_cents` (int)
  - `targeting_json` (JSON)
  - `pacing_mode` (enum/string, e.g. `STANDARD`, `ACCELERATED`)

For MVP, `targeting_json` can be a simple key–value map (country, device, room type, etc.).

#### Creative

Concrete ad asset that can be rendered in the visualizer.

- `Creative`
  - `id` (PK)
  - `line_item` (FK → LineItem)
  - `product_sku` (string)
  - `image_url` (string)
  - `landing_url` (string)

#### AttributionRule

Defines how conversions are attributed to impressions/clicks for a tenant.

- `AttributionRule`
  - `id` (PK)
  - `tenant` (FK → Tenant)
  - `click_window_hours` (int)
  - `view_window_hours` (int)

For MVP, one rule per tenant is enough. In a richer system this can be per campaign or line item.

---

### 5.2 Events (Conceptual Model)

All events share one unified schema, regardless of type.

**Common fields:**

- `event_id` — globally unique UUID, used for idempotency and deduplication
- `tenant_id` — references `Tenant`
- `user_id` — opaque user identifier
- `session_id` — opaque session identifier
- `ts` — event timestamp in UTC
- `event_type` — e.g. `IMPRESSION`, `CLICK`, `CONVERSION`, `VIEW`, `INTERACTION`
- `campaign_id` — optional, FK-style reference
- `line_item_id` — optional
- `placement_id` — optional
- `creative_id` — optional
- `value_cents` — numeric value for conversions/revenue events
- `metadata` — generic JSON with extra context (device, country, room type, etc.)

The same logical schema is used:

- in the ingest API body,
- in Kafka messages,
- in the ClickHouse raw events table.

---

### 5.3 ClickHouse Schema

#### Raw Events Table

```sql
CREATE TABLE IF NOT EXISTS ad_events_raw (
  event_date Date,
  event_time DateTime,
  event_id String,
  tenant_id UInt64,
  user_id String,
  session_id String,
  event_type String,
  campaign_id UInt64,
  line_item_id UInt64,
  placement_id UInt64,
  creative_id UInt64,
  value_cents Int64,
  metadata_json String
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_date)
ORDER BY (tenant_id, event_time, campaign_id, placement_id);
```

# Minute Aggregates Table
```sql
CREATE TABLE IF NOT EXISTS ad_events_minute_agg (
  minute DateTime,
  tenant_id UInt64,
  campaign_id UInt64,
  placement_id UInt64,
  impressions UInt64,
  clicks UInt64,
  conversions UInt64,
  revenue_cents Int64
)
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(minute)
ORDER BY (tenant_id, minute, campaign_id, placement_id);
```

Aggregation Strategy
	•	For each processed event, the worker:
	•	Inserts the raw event into ad_events_raw.
	•	Calculates minute = toStartOfMinute(event_time) and writes a row into ad_events_minute_agg with 0/1 flags for impressions/clicks/conversions and a revenue_cents value.
	•	ClickHouse merges rows over time (SummingMergeTree) → pre-aggregated metrics per minute, campaign, placement, tenant.

Alternatively, a materialized view can be defined to automatically populate ad_events_minute_agg from ad_events_raw. For MVP, either approach is acceptable.

⸻

6. Ingest Flow

6.1 Ingest API
	•	Endpoint: POST /api/events/
	•	Body: single event or a batch of events (MVP can start with single).
	•	Steps:
	1.	Validate request against event schema (e.g., Pydantic or DRF serializer).
	2.	Normalize timestamp to UTC.
	3.	Ensure all identifiers (tenant_id, campaign_id, etc.) are consistent (optionally: soft-validate existence).
	4.	Produce event to Kafka ad_events topic:
	•	Key: tenant_id (or tenant_id:campaign_id) for partitioning
	•	Value: JSON serialized event
	•	Response:
	•	202 Accepted with { "status": "queued" }.

6.2 Kafka Topic
	•	Topic: ad_events
	•	Partitions: configurable (e.g., N_PARTITIONS = 8 or more)
	•	Replication factor: environment dependent (MVP: 1, production: >= 3)

Partitioning by tenant ensures:
	•	Related events for a tenant are processed in order by the same consumer instance.
	•	Easy to scale by adding partitions & consumers.

⸻

7. Event Processing & Aggregation

7.1 Consumer / Worker

Implemented as a Django management command (e.g., consume_events) that:
	1.	Subscribes to the ad_events topic as part of a consumer group (ad_events_consumers).
	2.	Polls messages in batches.
	3.	For each message:
	•	Parse JSON → internal AdEvent object.
	•	Deduplicate using Redis:
	•	Key: ad_event:{event_id}
	•	Operation: SETNX with TTL (e.g., 48 hours).
	•	If key already exists → skip (event already processed).
	•	Insert event into ad_events_raw in ClickHouse.
	•	Update aggregates:
	•	Compute minute = toStartOfMinute(event_time).
	•	Insert row into ad_events_minute_agg with appropriate counters.

7.2 Processing Guarantees
	•	Kafka + consumer provide at-least-once delivery.
	•	Idempotency is achieved by:
	•	Deduplication based on event_id in Redis.
	•	Optionally, a secondary idempotency mechanism directly in ClickHouse (e.g., using ReplacingMergeTree + versioning).
	•	Late events:
	•	If an event arrives late, it still writes into the correct minute bucket.
	•	Aggregations remain correct because we are summing counts.

⸻

8. Reporting

8.1 Reporting API

Example endpoint: GET /api/report/summary/
	•	Query params:
	•	tenant_id
	•	from / to (interval)
	•	Optional: campaign_id, placement_id
	•	Query performed on ClickHouse:
	1.	Filter ad_events_minute_agg on:
	•	tenant_id
	•	minute between from and to
	•	(Optional) campaign_id, placement_id
	2.	Aggregate:
	•	SUM(impressions)
	•	SUM(clicks)
	•	SUM(conversions)
	•	SUM(revenue_cents)
	3.	Compute metrics in application layer:
	•	CTR = clicks / impressions
	•	conversion_rate = conversions / clicks (definition must be explicit)
	•	eCPM = (revenue_cents / 100) / impressions * 1000
	•	eCPC = (revenue_cents / 100) / clicks

8.2 Use Cases
	•	Tenant wants to see:
	•	Performance per campaign and placement for a date range.
	•	Funnel: impressions → clicks → conversions.
	•	Revenue and ROI per campaign.

These reports can be integrated into admin dashboards or exported via CSV.

⸻

9. Decisioning

9.1 Decisioning API

Endpoint: POST /api/decision/

Example request:

{
  "tenant_id": 1,
  "placement_key": "visualizer_native_1",
  "user_id": "u-123",
  "session_id": "s-456",
  "context": {
    "room_type": "bedroom",
    "country": "CA"
  }
}

Steps:
	1.	Fetch eligible line items:
	•	Join Placement by tenant_id + placement_key.
	•	Select LineItem records where:
	•	campaign.status = ACTIVE
	•	campaign.start_at <= now <= campaign.end_at
	•	(MVP) ignore complex targeting, or implement very simple filters from targeting_json.
	2.	Apply frequency capping:
	•	Use Redis:
	•	Key: fc:{tenant_id}:{campaign_id}:{user_id}
	•	Increment with TTL (e.g., 24 hours).
	•	If the counter exceeds a cap (e.g., 5 impressions per user per day), exclude that campaign.
	3.	Apply simple bidding logic:
	•	Weighted random selection by bid_cents.
	•	Example: higher bid_cents → higher probability of being chosen.
	4.	Select a creative:
	•	For the chosen line item, pick a creative (e.g., round-robin or random).
	5.	Return response:

{
  "campaign_id": 10,
  "line_item_id": 42,
  "creative": {
    "id": 1001,
    "image_url": "https://...",
    "landing_url": "https://...",
    "product_sku": "SKU-123"
  }
}

	6.	The client (visualizer) is responsible for:
	•	Rendering the creative
	•	Emitting subsequent IMPRESSION / CLICK / CONVERSION events back to the ingest API.

⸻

10. Multi-Tenancy & Data Isolation
	•	Every domain entity and event includes tenant_id.
	•	In Postgres:
	•	All queries must be filtered by tenant_id.
	•	Optionally, use row-level security if tenants are exposed via UI.
	•	In Kafka:
	•	Partition key uses tenant_id, ensuring tenant-level locality of events.
	•	In ClickHouse:
	•	tenant_id present in raw and aggregate tables, included in ORDER BY keys.
	•	In APIs:
	•	tenant_id is always required as input, and responses are scoped to a single tenant.

This design prevents accidental cross-tenant data leakage and makes it easier to shard per tenant in the future.

⸻

11. Scaling & Performance

11.1 Kafka
	•	Scale ingest by:
	•	Increasing number of partitions for ad_events.
	•	Running multiple consumer instances in the same consumer group.
	•	Higher partitions allow parallel processing of different tenants.

11.2 Event Processor
	•	Stateless workers:
	•	Each worker can handle a subset of partitions.
	•	Workers can be scaled horizontally (e.g., in Kubernetes via HPA).

11.3 ClickHouse
	•	Columnar, compressed storage optimised for analytics.
	•	Scales via:
	•	Sharding (distributed tables).
	•	Replication for high availability.
	•	SummingMergeTree aggregates reduce storage for precomputed stats.

11.4 Redis
	•	In-memory store for:
	•	Dedup keys (event_id).
	•	Frequency capping counters.
	•	Scale via clustering if needed (beyond MVP).

⸻

12. Failure Modes & Recovery

12.1 Kafka Outage
	•	If Kafka is temporarily unavailable:
	•	Ingest API can:
	•	Either fail the request (5xx) and rely on client retries.
	•	Or buffer events in memory/Redis for a short period (MVP: fail + retry).

12.2 ClickHouse Outage
	•	Consumer should:
	•	Log failures inserting into ClickHouse.
	•	Optionally write failed events into a DLQ topic or local file.
	•	When ClickHouse is back, replay events from Kafka offsets.

12.3 Redis Outage
	•	Deduplication and frequency capping become temporarily weaker:
	•	System continues to process events (risk = duplicated events and weaker caps).
	•	Once Redis is back, behavior normalizes.

12.4 Poison Messages
	•	Events that consistently fail validation / parsing:
	•	Send them to a dedicated ad_events_dlq Kafka topic.
	•	Out-of-band process to inspect and fix/replay.

⸻

13. Security & Privacy
	•	All APIs served over HTTPS (terminated at a reverse proxy/load balancer).
	•	Authentication/authorization:
	•	MVP: simple token-based auth per tenant.
	•	Validate that tenant_id in token matches tenant_id in requests.
	•	Data protection:
	•	user_id and session_id are opaque identifiers (no PII by default).
	•	Potential hashing of user identifiers before storage if needed.

⸻

14. Future Work / Extensions
	•	Targeting Language: richer rules (geo, device, behavior segments).
	•	Budget Pacing: distribute impressions over time to respect budgets and avoid early overspend.
	•	Optimization & ML:
	•	Multi-armed bandits for creative selection.
	•	Predictive CTR models using context and user history.
	•	Attribution Logic:
	•	Full view-through attribution pipelines (joining impressions and conversions within windows).
	•	Different attribution models: first-touch, last-touch, multi-touch.
	•	Experiments & A/B Testing:
	•	Support experiments at campaign, creative, or algorithm level.
	•	Fraud Detection:
	•	Detect abnormal patterns (suspicious click rates, bots).
	•	Multi-Region Deployment:
	•	Active-active Kafka + ClickHouse clusters with geo-partitioning.

⸻

15. Summary

This design provides a minimal yet realistic ads event platform:
	•	Kafka for reliable, scalable event streaming
	•	ClickHouse for fast analytical queries and aggregations
	•	Django-based APIs for ingest, reporting, and decisioning
	•	Redis and Postgres for caching, dedup, and domain modeling

It’s intentionally small but aligns with patterns used in production ad systems, and can be evolved into a full Ads Platform over time.