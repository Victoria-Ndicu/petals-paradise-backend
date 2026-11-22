import contextvars
import logging

_request_id = contextvars.ContextVar("request_id", default="-")


def set_request_id(value: str) -> None:
    _request_id.set(value)


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True
