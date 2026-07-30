"""Project-wide API error handling.

Guarantees that every response leaving the API is JSON with a predictable
shape, so the dashboard never has to parse an HTML error page:

    {
      "detail": "Human-readable summary.",
      "code": "validation_error",
      "fieldErrors": {"email": ["A user with this email already exists."]},
      "requestId": "b3f1c2e4"          # only on 5xx, for log correlation
    }

`fieldErrors` is present only when the error is attributable to specific
input fields. `detail` is ALWAYS a plain string safe to show to a user.
"""
import logging
import uuid

from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404, JsonResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("sigma17.api")

# Fallback wording per status, used when the exception carries nothing better.
STATUS_MESSAGES = {
    400: "The request could not be processed. Please check your input.",
    401: "Your session has expired. Please sign in again.",
    403: "You do not have permission to perform this action.",
    404: "The requested resource was not found.",
    405: "That action is not allowed on this resource.",
    409: "This conflicts with existing data.",
    413: "The uploaded file is too large.",
    429: "Too many requests. Please wait a moment and try again.",
    500: "Something went wrong on our end. Please try again.",
    503: "The service is temporarily unavailable. Please try again shortly.",
}

CODES = {
    400: "bad_request",
    401: "authentication_failed",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    429: "throttled",
    500: "server_error",
    503: "service_unavailable",
}

# Non-field errors are collected under DRF's conventional key.
NON_FIELD = "non_field_errors"


def _flatten(value):
    """Reduce a nested DRF error structure to a flat list of strings."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out = []
        for inner in value.values():
            out.extend(_flatten(inner))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for inner in value:
            out.extend(_flatten(inner))
        return out
    return [str(value)]


def _split_field_errors(data):
    """Split a DRF error body into (field_errors, summary_message).

    DRF hands us either a dict keyed by field name, a list of messages, or a
    bare string. Only the dict form yields usable field errors.
    """
    if isinstance(data, dict):
        # `detail` alone means a non-field API error (permission, throttle...).
        if set(data.keys()) == {"detail"}:
            return {}, " ".join(_flatten(data["detail"]))

        field_errors = {}
        for key, value in data.items():
            messages = _flatten(value)
            if messages:
                field_errors[key] = messages

        # Prefer non-field errors for the summary; else name the first field so
        # the toast is still meaningful when the form is not visible.
        if NON_FIELD in field_errors:
            summary = field_errors[NON_FIELD][0]
        elif field_errors:
            first_key = next(iter(field_errors))
            first_msg = field_errors[first_key][0]
            if first_key == "detail":
                summary = first_msg
            else:
                summary = f"{_humanize(first_key)}: {first_msg}"
        else:
            summary = ""
        return field_errors, summary

    messages = _flatten(data)
    if not messages:
        return {}, ""
    if len(messages) == 1:
        return {}, messages[0]
    return {NON_FIELD: messages}, messages[0]


def _humanize(field_name):
    """`roleIds` / `first_name` -> `Role ids` / `First name`."""
    spaced = "".join(
        f" {ch.lower()}" if ch.isupper() else ch for ch in str(field_name)
    ).replace("_", " ")
    spaced = " ".join(spaced.split())
    return spaced[:1].upper() + spaced[1:] if spaced else str(field_name)


def api_exception_handler(exc, context):
    """DRF EXCEPTION_HANDLER: normalize everything, including 500s."""
    request = context.get("request") if context else None
    view = context.get("view") if context else None

    # Translate framework/database exceptions DRF does not handle itself so
    # they become clean 4xx responses instead of an unhandled 500.
    if isinstance(exc, Http404):
        exc_response = drf_exception_handler(exc, context)
    elif isinstance(exc, PermissionDenied):
        exc_response = drf_exception_handler(exc, context)
    elif isinstance(exc, DjangoValidationError):
        payload = getattr(exc, "message_dict", None) or getattr(exc, "messages", [])
        exc_response = Response(payload, status=status.HTTP_400_BAD_REQUEST)
    elif isinstance(exc, IntegrityError):
        logger.warning(
            "Integrity error on %s %s: %s",
            getattr(request, "method", "?"),
            getattr(request, "path", "?"),
            exc,
        )
        exc_response = Response(
            {"detail": _integrity_message(exc)}, status=status.HTTP_409_CONFLICT
        )
    else:
        exc_response = drf_exception_handler(exc, context)

    if exc_response is None:
        # Genuinely unexpected. Log the full traceback with a correlation id and
        # return an opaque JSON 500 — never Django's HTML debug/error page.
        request_id = uuid.uuid4().hex[:8]
        logger.exception(
            "Unhandled exception [%s] in %s on %s %s",
            request_id,
            type(view).__name__ if view else "unknown view",
            getattr(request, "method", "?"),
            getattr(request, "path", "?"),
        )
        return Response(
            {
                "detail": STATUS_MESSAGES[500],
                "code": CODES[500],
                "requestId": request_id,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = exc_response.status_code
    field_errors, summary = _split_field_errors(exc_response.data)

    body = {
        "detail": summary or STATUS_MESSAGES.get(code, STATUS_MESSAGES[500]),
        "code": getattr(exc, "default_code", None) or CODES.get(code, "error"),
    }
    if field_errors:
        body["fieldErrors"] = field_errors
        if code == status.HTTP_400_BAD_REQUEST:
            body["code"] = "validation_error"

    if code >= 500:
        request_id = uuid.uuid4().hex[:8]
        body["requestId"] = request_id
        logger.error(
            "API %s [%s] on %s %s: %s",
            code,
            request_id,
            getattr(request, "method", "?"),
            getattr(request, "path", "?"),
            body["detail"],
        )

    exc_response.data = body
    return exc_response


def _integrity_message(exc):
    """Turn a DB constraint violation into something a user can act on."""
    text = str(exc).lower()
    if "unique" in text or "duplicate" in text:
        if "email" in text or "username" in text:
            return "A user with this email already exists."
        return "A record with these details already exists."
    if "foreign key" in text:
        return "This references a record that no longer exists."
    if "not null" in text or "null value" in text:
        return "A required value is missing."
    return STATUS_MESSAGES[409]


# --- Non-DRF fallbacks -------------------------------------------------------
# Requests that never reach a DRF view (unmatched URL, middleware failure) are
# rendered by Django itself. Force JSON for those too.

def json_404(request, exception=None):
    return JsonResponse(
        {"detail": STATUS_MESSAGES[404], "code": CODES[404]}, status=404
    )


def json_500(request):
    request_id = uuid.uuid4().hex[:8]
    logger.exception(
        "Unhandled server error [%s] on %s %s",
        request_id,
        getattr(request, "method", "?"),
        getattr(request, "path", "?"),
    )
    return JsonResponse(
        {
            "detail": STATUS_MESSAGES[500],
            "code": CODES[500],
            "requestId": request_id,
        },
        status=500,
    )
