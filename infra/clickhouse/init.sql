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