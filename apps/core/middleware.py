import uuid

from .logging import set_request_id


class RequestIDMiddleware:
    """Attach a correlation ID to every request, log line and response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming[:64] if incoming.replace("-", "").isalnum() else uuid.uuid4().hex
        request.request_id = request_id
        set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            set_request_id("-")
        response["X-Request-ID"] = request_id
        return response
