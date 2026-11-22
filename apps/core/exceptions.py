"""Consistent JSON error envelope for every API error.

{"error": {"code": "...", "message": "...", "fields": {...}}}
"""
import logging

from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class ApiError(exceptions.APIException):
    """Raise for business-rule failures with a stable machine-readable code."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "BAD_REQUEST"

    def __init__(self, code: str, message: str, status_code: int | None = None, fields: dict | None = None):
        super().__init__(detail=message, code=code)
        self.error_code = code
        self.message = message
        self.fields = fields or {}
        if status_code:
            self.status_code = status_code


def _flatten_fields(detail) -> dict:
    fields = {}
    if isinstance(detail, dict):
        for key, value in detail.items():
            if isinstance(value, (list, tuple)) and value and not isinstance(value[0], (dict, list)):
                fields[key] = str(value[0])
            elif isinstance(value, dict):
                for sub_key, sub_val in _flatten_fields(value).items():
                    fields[f"{key}.{sub_key}"] = sub_val
            elif isinstance(value, (list, tuple)):
                for idx, item in enumerate(value):
                    for sub_key, sub_val in _flatten_fields(item).items():
                        fields[f"{key}[{idx}].{sub_key}"] = sub_val
            else:
                fields[key] = str(value)
    return fields


def api_exception_handler(exc, context):
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API error")
        return Response(
            {"error": {"code": "SERVER_ERROR", "message": "Something went wrong. Please try again.", "fields": {}}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, ApiError):
        code, message, fields = exc.error_code, exc.message, exc.fields
    elif isinstance(exc, exceptions.ValidationError):
        code, message = "VALIDATION_ERROR", "Please review the highlighted fields."
        fields = _flatten_fields(exc.detail) if isinstance(exc.detail, dict) else {}
        if not fields and isinstance(exc.detail, (list, tuple)) and exc.detail:
            message = str(exc.detail[0])
    elif isinstance(exc, exceptions.NotAuthenticated):
        code, message, fields = "NOT_AUTHENTICATED", "Please sign in to continue.", {}
        response.status_code = status.HTTP_401_UNAUTHORIZED
    elif isinstance(exc, exceptions.AuthenticationFailed):
        code, message, fields = "AUTHENTICATION_FAILED", "Please sign in to continue.", {}
        response.status_code = status.HTTP_401_UNAUTHORIZED
    elif isinstance(exc, exceptions.PermissionDenied):
        code, message, fields = "FORBIDDEN", "You do not have permission to do that.", {}
    elif isinstance(exc, exceptions.NotFound):
        code, message, fields = "NOT_FOUND", "Not found.", {}
    elif isinstance(exc, exceptions.Throttled):
        code, message, fields = "RATE_LIMITED", "Too many requests. Please wait and try again.", {}
    elif isinstance(exc, exceptions.MethodNotAllowed):
        code, message, fields = "METHOD_NOT_ALLOWED", "Method not allowed.", {}
    elif isinstance(exc, exceptions.ParseError):
        code, message, fields = "PARSE_ERROR", "Malformed request body.", {}
    else:
        code, message, fields = "ERROR", str(getattr(exc, "detail", "Request failed.")), {}

    response.data = {"error": {"code": code, "message": message, "fields": fields}}
    return response
