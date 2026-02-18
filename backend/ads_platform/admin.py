from django.contrib import admin

from .models import (
    AttributionRule,
    Campaign,
    Creative,
    LineItem,
    MinuteAdAggregate,
    Placement,
    RawAdEvent,
    Tenant,
)

admin.site.register(Tenant)
admin.site.register(Campaign)
admin.site.register(Placement)
admin.site.register(LineItem)
admin.site.register(Creative)
admin.site.register(AttributionRule)
admin.site.register(RawAdEvent)
admin.site.register(MinuteAdAggregate)
