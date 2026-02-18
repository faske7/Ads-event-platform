from django.db import models


class Tenant(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self) -> str:
        return self.name


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"
        ENDED = "ENDED", "Ended"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    daily_budget_cents = models.IntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.tenant_id}:{self.name}"


class Placement(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    key = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "key"], name="uniq_tenant_placement")]


class LineItem(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE)
    placement = models.ForeignKey(Placement, on_delete=models.CASCADE)
    bid_cents = models.IntegerField(default=1)
    targeting_json = models.JSONField(default=dict, blank=True)
    pacing_mode = models.CharField(max_length=32, default="STANDARD")


class Creative(models.Model):
    line_item = models.ForeignKey(LineItem, on_delete=models.CASCADE, related_name="creatives")
    product_sku = models.CharField(max_length=128)
    image_url = models.URLField()
    landing_url = models.URLField()


class AttributionRule(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    click_window_hours = models.IntegerField(default=24)
    view_window_hours = models.IntegerField(default=24)


class RawAdEvent(models.Model):
    event_date = models.DateField()
    event_time = models.DateTimeField()
    event_id = models.CharField(max_length=64, unique=True)
    tenant_id = models.BigIntegerField()
    user_id = models.CharField(max_length=255)
    session_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=32)
    campaign_id = models.BigIntegerField(default=0)
    line_item_id = models.BigIntegerField(default=0)
    placement_id = models.BigIntegerField(default=0)
    creative_id = models.BigIntegerField(default=0)
    value_cents = models.BigIntegerField(default=0)
    metadata_json = models.JSONField(default=dict, blank=True)


class MinuteAdAggregate(models.Model):
    minute = models.DateTimeField()
    tenant_id = models.BigIntegerField()
    campaign_id = models.BigIntegerField(default=0)
    placement_id = models.BigIntegerField(default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    conversions = models.BigIntegerField(default=0)
    revenue_cents = models.BigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["minute", "tenant_id", "campaign_id", "placement_id"],
                name="uniq_minute_tenant_campaign_placement",
            )
        ]
