from unittest.mock import patch

from django.test import override_settings

from apps.orders.models import Order
from apps.orders.notifications import _format_admin_order, _format_customer_order, notify_new_order, notify_order_confirmed

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
    def test_new_order_admin_and_customer_messages_use_shared_email_delivery(self):
        response = self.place_order()
        order = Order.objects.select_related("customer").prefetch_related("items").get(pk=response.data["id"])

        with patch("apps.orders.notifications.send_transactional_email_async") as send_email:
            notify_new_order(order)

        self.assertEqual(send_email.call_count, 2)
        self.assertEqual(send_email.call_args_list[0].args[2], ["staff1@example.test", "staff2@example.test"])
        self.assertEqual(send_email.call_args_list[1].args[0], f"We received your order {order.reference}")
        self.assertEqual(send_email.call_args_list[1].args[2], ["jane@example.com"])

    def test_payment_confirmation_uses_shared_email_delivery(self):
        response = self.place_order()
        order = Order.objects.select_related("customer").get(pk=response.data["id"])

        with patch("apps.orders.notifications.send_transactional_email_async") as send_email:
            notify_order_confirmed(order)

        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.args[0], f"Payment confirmed for order {order.reference}")
        self.assertEqual(send_email.call_args.args[2], ["jane@example.com"])
