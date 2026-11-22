"""Thin client for the Safaricom Daraja API (M-Pesa Express / STK Push).

Only this module talks to Safaricom. Credentials come from settings (env vars)
and are never logged or returned to the browser.
"""
import base64
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

BASE_URLS = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}
_TOKEN_CACHE_KEY = "daraja:access_token"


class DarajaError(Exception):
    """Raised when Safaricom cannot be reached or rejects a request."""


class DarajaPending(DarajaError):
    """The transaction is still being processed (not a failure)."""


@dataclass
class StkPushResult:
    merchant_request_id: str
    checkout_request_id: str
    response_description: str


@dataclass
class StkQueryResult:
    result_code: int
    result_desc: str


def _base_url() -> str:
    return BASE_URLS.get(settings.MPESA_ENV, BASE_URLS["sandbox"])


def _timestamp() -> str:
    return datetime.now(ZoneInfo(settings.BUSINESS_TIME_ZONE)).strftime("%Y%m%d%H%M%S")


def _password(timestamp: str) -> str:
    raw = f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}"
    return base64.b64encode(raw.encode()).decode()


def _require_config() -> None:
    missing = [
        n
        for n in ("MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_SHORTCODE", "MPESA_PASSKEY")
        if not getattr(settings, n, "")
    ]
    if missing:
        raise DarajaError(f"M-Pesa is not configured (missing {', '.join(missing)}).")


def get_access_token() -> str:
    token = cache.get(_TOKEN_CACHE_KEY)
    if token:
        return token
    _require_config()
    try:
        resp = requests.get(
            f"{_base_url()}/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
            timeout=settings.MPESA_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        ttl = max(int(data.get("expires_in", 3599)) - 120, 60)
    except (requests.RequestException, KeyError, ValueError) as exc:
        logger.error("Daraja OAuth failed: %s", exc.__class__.__name__)
        raise DarajaError("Could not authenticate with M-Pesa.") from exc
    cache.set(_TOKEN_CACHE_KEY, token, ttl)
    return token


def callback_url() -> str:
    base = settings.MPESA_CALLBACK_BASE_URL.rstrip("/")
    if not base:
        raise DarajaError("MPESA_CALLBACK_BASE_URL is not configured.")
    path = "/api/v1/webhooks/mpesa"
    if settings.MPESA_CALLBACK_SECRET:
        path += f"/{settings.MPESA_CALLBACK_SECRET}"
    return base + path


def _post(path: str, payload: dict) -> dict:
    token = get_access_token()
    try:
        resp = requests.post(
            f"{_base_url()}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.MPESA_HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("Daraja request failed: %s", exc.__class__.__name__)
        raise DarajaError("Could not reach M-Pesa.") from exc
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code == 401:
        cache.delete(_TOKEN_CACHE_KEY)
    return {"_http": resp.status_code, **data}


def stk_push(*, amount_kes: int, phone: str, account_reference: str, description: str) -> StkPushResult:
    timestamp = _timestamp()
    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": _password(timestamp),
        "Timestamp": timestamp,
        "TransactionType": settings.MPESA_TRANSACTION_TYPE,
        "Amount": int(amount_kes),
        "PartyA": phone,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": callback_url(),
        "AccountReference": account_reference[:12],
        "TransactionDesc": description[:13],
    }
    data = _post("/mpesa/stkpush/v1/processrequest", payload)
    if data.get("_http") == 200 and str(data.get("ResponseCode")) == "0" and data.get("CheckoutRequestID"):
        return StkPushResult(
            merchant_request_id=data.get("MerchantRequestID", ""),
            checkout_request_id=data["CheckoutRequestID"],
            response_description=data.get("ResponseDescription", ""),
        )
    logger.error("STK push rejected: http=%s code=%s", data.get("_http"), data.get("errorCode") or data.get("ResponseCode"))
    raise DarajaError("M-Pesa did not accept the payment request.")


def stk_query(checkout_request_id: str) -> StkQueryResult:
    """Ask Safaricom for the outcome of an STK push. Raises DarajaPending while
    the customer is still on the PIN prompt."""
    timestamp = _timestamp()
    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": _password(timestamp),
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id,
    }
    data = _post("/mpesa/stkpushquery/v1/query", payload)
    if "ResultCode" in data:
        try:
            return StkQueryResult(result_code=int(data["ResultCode"]), result_desc=str(data.get("ResultDesc", ""))[:255])
        except (TypeError, ValueError):
            pass
    # Daraja answers 500.001.1001 "being processed" while the prompt is open.
    if "being processed" in str(data.get("errorMessage", "")).lower() or data.get("errorCode") == "500.001.1001":
        raise DarajaPending("Transaction is still being processed.")
    raise DarajaError("Could not read the M-Pesa transaction status.")
