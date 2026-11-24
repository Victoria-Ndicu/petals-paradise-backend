from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from apps.core.email import (
    BREVO_TRANSACTIONAL_EMAIL_URL,
    EmailDeliveryError,
    send_transactional_email,
)


@override_settings(
    BREVO_API_KEY="test-api-key",
    DEFAULT_FROM_EMAIL="Petals Paradise <orders@example.test>",
)
class BrevoEmailDeliveryTests(SimpleTestCase):
    @patch("apps.core.email.requests.post")
    def test_sends_transactional_email_over_https(self, post):
        response = Mock(status_code=201)
        response.raise_for_status.return_value = None
        post.return_value = response

        sent = send_transactional_email(
            "Order received",
            "Hello <customer>",
            None,
            ["customer@example.test"],
        )

        self.assertEqual(sent, 1)
        self.assertEqual(post.call_args.args[0], BREVO_TRANSACTIONAL_EMAIL_URL)
        self.assertEqual(post.call_args.kwargs["headers"]["api-key"], "test-api-key")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "sender": {"email": "orders@example.test", "name": "Petals Paradise"},
                "to": [{"email": "customer@example.test"}],
                "subject": "Order received",
                "textContent": "Hello <customer>",
                "htmlContent": "<pre>Hello &lt;customer&gt;</pre>",
            },
        )
        self.assertEqual(post.call_args.kwargs["timeout"], 10)

    @patch("apps.core.email.requests.post")
    def test_api_failure_raises_delivery_error_with_response_detail(self, post):
        response = Mock(status_code=401, text="invalid API key")
        response.raise_for_status.side_effect = requests.HTTPError("Unauthorized")
        post.return_value = response

        with self.assertRaisesRegex(EmailDeliveryError, "HTTP 401: invalid API key"):
            send_transactional_email("Subject", "Body", None, ["customer@example.test"])

    @override_settings(DEFAULT_FROM_EMAIL="Petals Paradise sender@example.test")
    @patch("apps.core.email.requests.post")
    def test_rejects_invalid_sender_before_calling_brevo(self, post):
        with self.assertRaisesRegex(ValueError, "valid sender email"):
            send_transactional_email("Subject", "Body", None, ["customer@example.test"])
        post.assert_not_called()

    @override_settings(BREVO_API_KEY="", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    @patch("apps.core.email.send_mail", return_value=1)
    def test_falls_back_to_django_mail_without_brevo_key(self, send_mail):
        self.assertEqual(send_transactional_email("Subject", "Body", None, ["to@example.test"]), 1)
        send_mail.assert_called_once_with(
            "Subject",
            "Body",
            None,
            ["to@example.test"],
            fail_silently=False,
        )
