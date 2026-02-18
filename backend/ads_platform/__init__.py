"""ads_platform package.

Keep __init__ light; URLs should normally import views from ads_platform.views.
"""

# Optional re-exports (kept for backward compatibility with older imports)
try:
    from .views import decision_view, events_view, report_summary_view

    __all__ = [
        "decision_view",
        "events_view",
        "report_summary_view",
    ]
except Exception:
    # If views module isn't available during partial installs/tests, don't crash on import.
    __all__ = []
