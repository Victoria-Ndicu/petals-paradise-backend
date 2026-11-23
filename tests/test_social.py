import time
from types import SimpleNamespace
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings

from apps.accounts.models import SocialAccount, User

from .base import BaseTest

GOOGLE_AUD = "google-client-id.apps.googleusercontent.com"
APPLE_AUD = "ke.co.petals.web"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(provider="google", key=_KEY, alg="RS256", **claims):
    base = {
        "google": {"iss": "https://accounts.google.com", "aud": GOOGLE_AUD},
        "apple": {"iss": "https://appleid.apple.com", "aud": APPLE_AUD},
    }[provider]
    now = int(time.time())
    payload = {**base, "sub": "sub-123", "iat": now, "exp": now + 600,
               "email": "flora@example.com", "email_verified": True, "name": "Flora Wanjiru"}
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm=alg)


class FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=_KEY.public_key())


@override_settings(GOOGLE_CLIENT_IDS=[GOOGLE_AUD], APPLE_CLIENT_IDS=[APPLE_AUD])
class SocialLoginTests(BaseTest):
    def setUp(self):
        super().setUp()
        p = mock.patch("apps.accounts.social._jwks_client", return_value=FakeJWKS())
        p.start()
        self.addCleanup(p.stop)

    def post(self, provider="google", token=None, **extra):
        body = {"idToken": token or make_token(provider), **extra}
        return self.client.post(f"/api/v1/auth/social/{provider}", body, format="json")

    # --- sign up / sign in ---------------------------------------------
    def test_new_user_can_sign_up_without_phone(self):
        r = self.post()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(r.data["phone"])
        user = User.objects.get(social_accounts__provider="google")
        self.assertIsNone(user.phone)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 200)

    def test_signup_with_phone_creates_customer_and_session(self):
        r = self.post(phone="0711 222 333")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["phone"], "254711222333")
        self.assertEqual(r.data["email"], "flora@example.com")
        self.assertTrue(r.data["email_verified"])
        self.assertEqual(r.data["name"], "Flora Wanjiru")
        self.assertEqual(r.data["socialProviders"], ["google"])
        user = User.objects.get(phone="254711222333")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 200)

    def test_returning_user_signs_in_without_phone(self):
        self.post(phone="0711222333")
        self.client.post("/api/v1/auth/logout")
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(User.objects.count(), 4)  # 3 fixtures + 1 social, no duplicate
        self.assertEqual(self.client.get("/api/v1/me").status_code, 200)

    def test_social_user_cannot_password_login(self):
        self.post(phone="0711222333")
        self.client.post("/api/v1/auth/logout")
        for ident in ("0711222333", "flora@example.com"):
            r = self.client.post("/api/v1/auth/login", {"identifier": ident, "password": ""}, format="json")
            self.assertIn(r.status_code, (400, 401))

    def test_invalid_or_duplicate_phone_rejected(self):
        r = self.post(phone="12345")
        self.assertIn("phone", r.data["error"]["fields"])
        r = self.post(phone="0712345678")  # belongs to fixture customer
        self.assertIn("phone", r.data["error"]["fields"])
        self.assertFalse(SocialAccount.objects.exists())

    def test_unverified_email_is_not_stored(self):
        r = self.post(token=make_token(email_verified=False), phone="0711222333")
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.data["email"])

    def test_apple_string_email_verified_and_name_from_payload(self):
        token = make_token("apple", email_verified="true", name=None, email="x@privaterelay.appleid.com")
        r = self.post("apple", token=token, phone="0711222333", name="Apple Person")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["name"], "Apple Person")
        self.assertEqual(r.data["email"], "x@privaterelay.appleid.com")
        self.assertEqual(r.data["socialProviders"], ["apple"])

    # --- verified-email account linking --------------------------------
    def test_verified_google_email_links_existing_password_account(self):
        token = make_token(email="jane@example.com")  # fixture customer's email
        r = self.post(token=token)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["id"], self.customer.pk)
        self.assertEqual(r.data["socialProviders"], ["google"])
        self.assertEqual(SocialAccount.objects.get().user_id, self.customer.pk)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.email_verified)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 200)

    def test_unverified_matching_google_email_cannot_link_or_create_duplicate(self):
        token = make_token(email="jane@example.com", email_verified=False)
        r = self.post(token=token)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["error"]["code"], "EMAIL_NOT_VERIFIED")
        self.assertFalse(SocialAccount.objects.exists())
        self.assertEqual(User.objects.count(), 3)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)

    # --- token verification --------------------------------------------
    def assert_rejected(self, token, provider="google", status=401, **extra):
        r = self.post(provider, token=token, phone="0711222333", **extra)
        self.assertEqual(r.status_code, status, r.content)
        self.assertFalse(SocialAccount.objects.exists())
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)
        return r

    def test_rejects_wrong_audience_issuer_expired_and_missing_claims(self):
        self.assert_rejected(make_token(aud="someone-elses-app"))
        self.assert_rejected(make_token(iss="https://evil.example.com"))
        self.assert_rejected(make_token(exp=int(time.time()) - 3600))
        self.assert_rejected(make_token(sub=None))
        self.assert_rejected("not-a-jwt")

    def test_rejects_token_signed_with_wrong_key_or_algorithm(self):
        self.assert_rejected(make_token(key=_OTHER_KEY))
        self.assert_rejected(make_token(key="shared-secret-of-at-least-32-bytes-long!", alg="HS256"))

    def test_google_token_not_accepted_for_apple_endpoint(self):
        self.assert_rejected(make_token("google"), provider="apple")

    def test_nonce_checked_when_supplied(self):
        self.assert_rejected(make_token(nonce="abc"), nonce="different")
        r = self.post(token=make_token(nonce="abc"), nonce="abc", phone="0711222333")
        self.assertEqual(r.status_code, 201)

    def test_unknown_provider_404_and_unconfigured_provider_503(self):
        r = self.client.post("/api/v1/auth/social/facebook", {"idToken": "x"}, format="json")
        self.assertEqual(r.status_code, 404)
        with override_settings(APPLE_CLIENT_IDS=[]):
            r = self.post("apple")
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.data["error"]["code"], "PROVIDER_NOT_CONFIGURED")

    def test_disabled_account_cannot_sign_in(self):
        self.post(phone="0711222333")
        self.client.post("/api/v1/auth/logout")
        User.objects.filter(phone="254711222333").update(is_active=False)
        r = self.post()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)

    def test_provider_outage_is_reported_not_swallowed(self):
        broken = mock.Mock()
        broken.get_signing_key_from_jwt.side_effect = jwt.PyJWKClientConnectionError("down")
        with mock.patch("apps.accounts.social._jwks_client", return_value=broken):
            r = self.post(phone="0711222333")
        self.assertEqual(r.status_code, 502)

    # --- linking --------------------------------------------------------
    def link(self, provider="google", **claims):
        return self.client.post(
            f"/api/v1/auth/social/{provider}/link", {"idToken": make_token(provider, **claims)}, format="json"
        )

    def test_link_requires_sign_in(self):
        self.assertEqual(self.link().status_code, 401)

    def test_signed_in_customer_can_link_then_sign_in_with_provider(self):
        self.login(self.customer)
        r = self.link()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["socialProviders"], ["google"])
        self.client.post("/api/v1/auth/logout")
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["phone"], self.customer.phone)

    def test_cannot_link_identity_already_used_by_someone_else(self):
        self.login(self.customer)
        self.link()
        self.login(self.other)
        r = self.link()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["error"]["code"], "SOCIAL_ACCOUNT_IN_USE")

    def test_cannot_link_second_identity_of_same_provider(self):
        self.login(self.customer)
        self.link()
        r = self.link(sub="another-google-user")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["error"]["code"], "SOCIAL_ALREADY_LINKED")
