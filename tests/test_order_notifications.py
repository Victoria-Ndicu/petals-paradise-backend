from unittest.mock import patch

from django.test import override_settings

from apps.orders.models import Order
from apps.orders.notifications import _format_admin_order, _format_customer_order, _send_alerts

from .base import BaseTest


class OrderNotificationTests(BaseTest):
    def test_alert_contains_order_delivery_total_and_items(self):
        response = self.place_order()
        order = Order.objects.select_related("customer").prefetch_related("items").get(pk=response.data["id"])

        message = _format_admin_order(order)

        self.assertIn(f"New order {order.reference}", message)
        self.assertIn("Recipient: Mary (254700111222)", message)
        self.assertIn(f"Delivery: {order.delivery_date:%d %b %Y}, 9am - 12pm", message)
        self.assertIn("Area: Westlands", message)
        self.assertIn("Total: KES 5,400", message)
        self.assertIn("- 2 x Roses (Small, Bouquet)", message)

    def test_customer_confirmation_says_payment_is_not_yet_confirmed(self):
        response = self.place_order()
        order = Order.objects.select_related("customer").prefetch_related("items").get(pk=response.data["id"])

        message = _format_customer_order(order)

        self.assertIn(f"We've received your order {order.reference}.", message)
        self.assertIn("awaiting payment confirmation", message)
        self.assertIn("Total: KES 5,400", message)
        self.assertIn("- 2 x Roses (Small, Bouquet)", message)

    @override_settings(ORDER_ALERT_EMAILS=["staff1@example.test", "staff2@example.test"])
    def test_sends_separate_admin_and_customer_emails(self):
        with patch("apps.orders.notifications.send_mail") as send_mail:
            _send_alerts(
                ["staff1@example.test", "staff2@example.test"],
                "jane@example.com",
                "PP-TEST",
                "Admin order details",
                "Customer order confirmation",
            )

        self.assertEqual(send_mail.call_count, 2)
        self.assertEqual(send_mail.call_args_list[0].args[1], "Admin order details")
        self.assertEqual(send_mail.call_args_list[0].args[3], ["staff1@example.test", "staff2@example.test"])
        self.assertEqual(send_mail.call_args_list[1].args[0], "We received your order PP-TEST")
        self.assertEqual(send_mail.call_args_list[1].args[3], ["jane@example.com"])
        self.assertEqual(send_mail.call_args_list[1].args[1], "Customer order confirmation")

    def test_email_failure_is_logged_and_does_not_raise(self):
        with patch("apps.orders.notifications.send_mail", side_effect=RuntimeError("SMTP unavailable")):
            with self.assertLogs("apps.orders.notifications", level="ERROR"):
                _send_alerts(["staff@example.test"], "jane@example.com", "PP-TEST", "Admin", "Customer")
