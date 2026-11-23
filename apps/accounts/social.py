"""Server-side verification of Google / Apple ID tokens.

The browser obtains an ID token (JWT) from Google Identity Services or
"Sign in with Apple" and sends it to us. We never trust any claim until the
signature (against the provider's published keys), issuer, audience and expiry
have been checked here.
"""
import hashlib
import hmac
import logging
from dataclasses import dataclass

import jwt
from django.conf import settings
from jwt import PyJWKClient
from rest_framework import status

from apps.core.exceptions import ApiError

logger = logging.getLogger(__name__)

PROVIDERS = {
    "google": {
        "jwks_url": "https://www.googleapis.com/oauth2/v3/certs",
        "issuers": ["https://accounts.google.com", "accounts.google.com"],
        "audiences_setting": "GOOGLE_CLIENT_IDS",
        "label": "Google",
    },
    "apple": {
        "jwks_url": "https://appleid.apple.com/auth/keys",
        "issuers": ["https://appleid.apple.com"],
        "audiences_setting": "APPLE_CLIENT_IDS",
        "label": "Apple",
    },
}

_clients: dict[str, PyJWKClient] = {}


def _jwks_client(provider: str) -> PyJWKClient:
    # Keys are cached in-process (and refreshed hourly) so we are not calling the
    # provider on every login.
    if provider not in _clients:
        _clients[provider] = PyJWKClient(
            PROVIDERS[provider]["jwks_url"], cache_keys=True, lifespan=3600, timeout=10
        )
    return _clients[provider]


@dataclass(frozen=True)
class SocialIdentity:
    provider: str
    subject: str
    email: str | None  # only set when the provider says it is verified
    claimed_email: str | None
    email_verified: bool
    name: str


def _truthy(value) -> bool:
    return value is True or str(value).lower() == "true"


def verify_id_token(provider: str, token: str, nonce: str | None = None) -> SocialIdentity:
    cfg = PROVIDERS[provider]
    audiences = list(getattr(settings, cfg["audiences_setting"], []) or [])
    if not audiences:
        raise ApiError(
            "PROVIDER_NOT_CONFIGURED",
            f"Sign in with {cfg['label']} is not available right now.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        signing_key = _jwks_client(provider).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],  # never accept "none"/HS256 tokens
            audience=audiences,
            issuer=cfg["issuers"],
            leeway=30,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWKClientConnectionError:
        logger.error("Could not fetch %s signing keys", provider)
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            f"Could not reach {cfg['label']}. Please try again.",
            status.HTTP_502_BAD_GATEWAY,
        )
    except jwt.PyJWTError as exc:
        logger.warning("Rejected %s ID token: %s", provider, type(exc).__name__)
        raise ApiError(
            "INVALID_SOCIAL_TOKEN",
            f"Your {cfg['label']} sign-in could not be verified. Please try again.",
            status.HTTP_401_UNAUTHORIZED,
        )

    if nonce:
        token_nonce = str(claims.get("nonce") or "")
        # Apple/Google SDKs either echo the nonce or its SHA-256 hex digest.
        accepted = {nonce, hashlib.sha256(nonce.encode()).hexdigest()}
        if not any(hmac.compare_digest(token_nonce, candidate) for candidate in accepted):
            raise ApiError(
                "INVALID_SOCIAL_TOKEN",
                f"Your {cfg['label']} sign-in could not be verified. Please try again.",
                status.HTTP_401_UNAUTHORIZED,
            )

    claimed_email = (claims.get("email") or "").strip().lower() or None
    verified = bool(claimed_email) and _truthy(claims.get("email_verified"))
    return SocialIdentity(
        provider=provider,
        subject=str(claims["sub"]),
        email=claimed_email if verified else None,
        claimed_email=claimed_email,
        email_verified=verified,
        name=str(claims.get("name") or "").strip(),
    )
