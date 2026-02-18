from django.contrib import admin
from django.urls import path

from backend.ads_platform import decision_view, events_view, report_summary_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/events/", events_view),
    path("api/report/summary/", report_summary_view),
    path("api/decision/", decision_view),
]
