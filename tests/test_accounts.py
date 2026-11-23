from rest_framework.test import APIClient

from apps.accounts.models import User

from .base import BaseTest


class AccountTests(BaseTest):
    def signup(self, **over):
        data = {"name": "New Person", "phone": "0711 222 333", "email": "New@Example.com", "password": "Another-str0ng-pw"}
        data.update(over)
        return self.client.post("/api/v1/auth/signup", data, format="json")

    def test_signup_normalizes_phone_email_and_signs_in(self):
        r = self.signup()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["phone"], "254711222333")
        self.assertEqual(r.data["email"], "new@example.com")
        self.assertNotIn("password", r.data)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 200)

    def test_signup_without_phone_uses_email_for_login(self):
        r = self.signup(phone=None)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(r.data["phone"])
        self.assertEqual(r.data["email"], "new@example.com")
        user = User.objects.get(email="new@example.com")
        self.assertIsNone(user.phone)

        self.client.post("/api/v1/auth/logout")
        r = self.client.post(
            "/api/v1/auth/login",
            {"identifier": "new@example.com", "password": "Another-str0ng-pw"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)

    def test_profile_can_add_and_remove_phone(self):
        self.login(self.customer)
        r = self.client.patch("/api/v1/me", {"phone": ""}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.data["phone"])
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.phone)

        r = self.client.patch("/api/v1/me", {"phone": "0712345678"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["phone"], "254712345678")

    def test_password_is_hashed_with_argon2(self):
        self.signup()
        self.assertTrue(User.objects.get(phone="254711222333").password.startswith("argon2"))

    def test_signup_cannot_grant_staff(self):
        r = self.signup(is_staff=True, is_superuser=True)
        self.assertEqual(r.status_code, 201)
        u = User.objects.get(phone="254711222333")
        self.assertFalse(u.is_staff or u.is_superuser)

    def test_duplicate_phone_and_email_rejected_with_field_errors(self):
        r = self.signup(phone="0712345678")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("phone", r.data["error"]["fields"])
        r = self.signup(email="jane@example.com")
        self.assertIn("email", r.data["error"]["fields"])

    def test_invalid_phone_and_weak_password(self):
        self.assertIn("phone", self.signup(phone="12345").data["error"]["fields"])
        self.assertIn("password", self.signup(password="12345678").data["error"]["fields"])

    def test_login_with_phone_or_email(self):
        for ident in ("0712345678", "+254712345678", "jane@example.com", "JANE@example.com"):
            c = APIClient()
            r = c.post("/api/v1/auth/login", {"identifier": ident, "password": self.PASSWORD}, format="json")
            self.assertEqual(r.status_code, 200, ident)

    def test_wrong_credentials_give_same_generic_error(self):
        c = APIClient()
        bad_pw = c.post("/api/v1/auth/login", {"identifier": "0712345678", "password": "nope"}, format="json")
        no_user = c.post("/api/v1/auth/login", {"identifier": "0799999999", "password": "nope"}, format="json")
        self.assertEqual(bad_pw.status_code, 401)
        self.assertEqual(bad_pw.data, no_user.data)

    def test_login_is_throttled(self):
        c = APIClient()
        codes = [
            c.post("/api/v1/auth/login", {"identifier": "0712345678", "password": "x"}, format="json").status_code
            for _ in range(12)
        ]
        self.assertIn(429, codes)

    def test_login_requires_csrf_token(self):
        c = APIClient(enforce_csrf_checks=True)
        r = c.post("/api/v1/auth/login", {"identifier": "0712345678", "password": self.PASSWORD}, format="json")
        self.assertEqual(r.status_code, 403)
        token = c.get("/api/v1/auth/csrf").data["csrfToken"]
        r = c.post(
            "/api/v1/auth/login",
            {"identifier": "0712345678", "password": self.PASSWORD},
            format="json",
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(r.status_code, 200, r.content)

    def test_logout_revokes_server_session(self):
        self.login(self.customer)
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)

    def test_me_requires_auth_and_patch_validates(self):
        self.assertEqual(APIClient().get("/api/v1/me").status_code, 401)
        self.login(self.customer)
        r = self.client.patch("/api/v1/me", {"name": "Jane D", "default_delivery_area": "westlands"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["name"], "Jane D")
        r = self.client.patch("/api/v1/me", {"phone": "0722000111"}, format="json")  # taken by other
        self.assertEqual(r.status_code, 400)
        r = self.client.patch("/api/v1/me", {"default_delivery_area": "mars"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_customer_cannot_use_staff_endpoints(self):
        self.login(self.customer)
        self.assertEqual(self.client.get("/api/v1/admin/orders").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/admin/inquiries").status_code, 403)
        self.assertEqual(APIClient().get("/api/v1/admin/orders").status_code, 401)
