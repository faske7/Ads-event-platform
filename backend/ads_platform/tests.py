import json
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from backend.ads_platform.models import Campaign, Creative, LineItem, Placement, Tenant


class AdsPlatformApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Tenant A")
        self.campaign = Campaign.objects.create(
            tenant=self.tenant,
            name="Campaign A",
            status=Campaign.Status.ACTIVE,
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1),
            daily_budget_cents=10000,
        )
        self.placement = Placement.objects.create(tenant=self.tenant, key="visualizer_native_1")
        self.line_item = LineItem.objects.create(
            campaign=self.campaign,
            placement=self.placement,
            bid_cents=50,
            targeting_json={"country": "CA"},
        )
        self.creative = Creative.objects.create(
            line_item=self.line_item,
            product_sku="SKU-1",
            image_url="https://example.com/img.png",
            landing_url="https://example.com/landing",
        )

    def test_ingest_and_report_flow(self):
        ts = timezone.now().replace(second=0, microsecond=0)
        payload = {
            "event_id": str(uuid.uuid4()),
            "tenant_id": self.tenant.id,
            "user_id": "u-1",
            "session_id": "s-1",
            "ts": ts.isoformat(),
            "event_type": "IMPRESSION",
            "campaign_id": self.campaign.id,
            "line_item_id": self.line_item.id,
            "placement_id": self.placement.id,
            "creative_id": self.creative.id,
        }
        resp = self.client.post("/api/events/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 202)

        call_command("consume_events", "--once")

        report = self.client.get(
            "/api/report/summary/",
            {
                "tenant_id": self.tenant.id,
                "from": (ts - timedelta(minutes=1)).isoformat(),
                "to": (ts + timedelta(minutes=1)).isoformat(),
            },
        )
        self.assertEqual(report.status_code, 200)
        body = report.json()
        self.assertEqual(body["impressions"], 1)
        self.assertEqual(body["clicks"], 0)

    def test_decision_api(self):
        payload = {
            "tenant_id": self.tenant.id,
            "placement_key": self.placement.key,
            "user_id": "u-77",
            "session_id": "s-88",
            "context": {"country": "CA"},
        }
        resp = self.client.post("/api/decision/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["campaign_id"], self.campaign.id)
        self.assertEqual(body["line_item_id"], self.line_item.id)
        self.assertEqual(body["creative"]["id"], self.creative.id)
