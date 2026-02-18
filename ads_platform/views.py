import json
from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from .services import (
    ValidationError,
    build_summary,
    decide_ad,
    get_producer,
    parse_event,
)


def _auth_tenant(request: HttpRequest, tenant_id: int) -> bool:
    mapping = settings.TENANT_API_KEYS
    if not mapping:
        return True
    provided = request.headers.get("Authorization", "").replace("Bearer ", "")
    expected = mapping.get(tenant_id)
    return bool(expected and provided == expected)


@require_POST
def events_view(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8"))
        event = parse_event(payload)
        if not _auth_tenant(request, event.tenant_id):
            return JsonResponse({"detail": "Unauthorized for tenant"}, status=403)
        get_producer().produce_event(event)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    except ValidationError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    return JsonResponse({"status": "queued"}, status=202)


@require_GET
def report_summary_view(request: HttpRequest) -> JsonResponse:
    try:
        tenant_id = int(request.GET["tenant_id"])
        dt_from = parse_datetime(request.GET["from"])
        dt_to = parse_datetime(request.GET["to"])
        if dt_from is None or dt_to is None:
            raise ValidationError("from and to must be valid ISO-8601 datetimes")
        campaign_id = int(request.GET["campaign_id"]) if "campaign_id" in request.GET else None
        placement_id = int(request.GET["placement_id"]) if "placement_id" in request.GET else None
        if not _auth_tenant(request, tenant_id):
            return JsonResponse({"detail": "Unauthorized for tenant"}, status=403)
        data = build_summary(tenant_id=tenant_id, dt_from=dt_from, dt_to=dt_to, campaign_id=campaign_id, placement_id=placement_id)
        return JsonResponse(data)
    except KeyError:
        return JsonResponse({"detail": "tenant_id, from, and to are required"}, status=400)
    except (TypeError, ValueError, ValidationError) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


@require_POST
def decision_view(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8"))
        tenant_id = int(payload["tenant_id"])
        placement_key = str(payload["placement_key"])
        user_id = str(payload["user_id"])
        context = payload.get("context") or {}
        if not isinstance(context, dict):
            raise ValidationError("context must be object")
        if not _auth_tenant(request, tenant_id):
            return JsonResponse({"detail": "Unauthorized for tenant"}, status=403)
        decision = decide_ad(tenant_id=tenant_id, placement_key=placement_key, user_id=user_id, context=context)
        return JsonResponse(decision)
    except KeyError as exc:
        return JsonResponse({"detail": f"Missing field: {exc.args[0]}"}, status=400)
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    except ValidationError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
