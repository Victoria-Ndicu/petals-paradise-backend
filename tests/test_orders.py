from datetime import date, timedelta

from unittest.mock import patch

from apps.accounts.models import User
from apps.catalogue.models import BlockedDate, ProductVariant
from django.utils import timezone

from apps.core.models import Notification
from apps.orders.models import Order
from apps.orders.services import confirm_order_payment, expire_stale_orders

from .base import BaseTest, idem


class OrderCreationTests(BaseTest):
    @patch("apps.orders.services.notify_new_order")
    def test_new_order_alert_is_scheduled_after_commit_once(self, notify_new_order):
        key = idem()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            first = self.place_order(key=key)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(len(callbacks), 1)
        notify_new_order.assert_called_once()

        with self.captureOnCommitCallbacks(execute=True) as replay_callbacks:
            replay = self.place_order(key=key)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay_callbacks, [])
        notify_new_order.assert_called_once()

    def test_server_calculates_totals_and_ignores_client_money(self):
        r = self.place_order(
            items=[
                {
                    "productId": "roses",
                    "variantId": self.small.pk,
                    "presentationId": "bouquet",
                    "quantity": 2,
                    "price": 1,
                    "unitPriceKes": 1,
                    "lineTotalKes": 2,
                }
            ],
            totalKes=1,
            deliveryFeeKes=0,
            subtotalKes=1,
            reference="HACKED",
            customer=self.other.pk,
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["subtotalKes"], 5000)
        self.assertEqual(r.data["deliveryFeeKes"], 400)
        self.assertEqual(r.data["totalKes"], 5400)
        self.assertEqual(r.data["status"], "pending_payment")
        self.assertTrue(r.data["reference"].startswith("PP-"))
        order = Order.objects.get(pk=r.data["id"])
        self.assertEqual(order.customer, self.customer)
        item = order.items.get()
        self.assertEqual((item.unit_price_kes, item.line_total_kes, item.quantity), (2500, 5000, 2))

    def test_presentation_price_adjustment_is_server_configured(self):
        r = self.place_order(
            items=[{"productId": "roses", "variantId": self.small.pk, "presentationId": "box", "quantity": 1}]
        )
        self.assertEqual(r.data["items"][0]["unitPriceKes"], 3000)

    def test_item_snapshot_survives_price_change(self):
        r = self.place_order()
        self.small.price_kes = 9999
        self.small.save()
        self.login(self.customer)
        again = self.client.get(f"/api/v1/orders/{r.data['id']}")
        self.assertEqual(again.data["items"][0]["unitPriceKes"], 2500)
        self.assertEqual(again.data["totalKes"], 5400)

    def test_requires_authentication(self):
        r = self.client.post("/api/v1/orders", self.order_payload(), format="json", headers={"Idempotency-Key": idem()})
        self.assertEqual(r.status_code, 401)

    def test_requires_idempotency_key(self):
        self.login(self.customer)
        r = self.client.post("/api/v1/orders", self.order_payload(), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["error"]["code"], "IDEMPOTENCY_KEY_REQUIRED")

    def test_idempotent_replay_returns_same_order(self):
        key = idem()
        a = self.place_order(key=key)
        b = self.place_order(key=key)
        self.assertEqual((a.status_code, b.status_code), (201, 200))
        self.assertEqual(a.data["id"], b.data["id"])
        self.assertEqual(Order.objects.count(), 1)

    def test_same_customer_can_create_identical_orders_with_new_idempotency_keys(self):
        first = self.place_order(key="same-order-details-001")
        second = self.place_order(key="same-order-details-002")

        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertNotEqual(first.data["id"], second.data["id"])
        self.assertEqual(Order.objects.filter(customer=self.customer).count(), 2)

    def test_same_key_different_cart_conflicts(self):
        key = idem()
        self.place_order(key=key)
        r = self.place_order(
            key=key, items=[{"productId": "roses", "variantId": self.large.pk, "presentationId": "bouquet", "quantity": 1}]
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_validation_errors_use_envelope_with_camel_fields(self):
        r = self.place_order(recipientPhone="123", recipientName="")
        self.assertEqual(r.status_code, 400)
        fields = r.data["error"]["fields"]
        self.assertIn("recipientPhone", fields)
        self.assertIn("recipientName", fields)

    def test_empty_cart_and_bad_quantity(self):
        self.assertEqual(self.place_order(items=[]).status_code, 400)
        bad = [{"productId": "roses", "variantId": self.small.pk, "presentationId": "bouquet", "quantity": 0}]
        self.assertEqual(self.place_order(items=bad).status_code, 400)

    def test_unavailable_product_and_variant(self):
        r = self.place_order(items=[{"productId": "draft-item", "variantId": 1, "quantity": 1}])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["error"]["code"], "PRODUCT_UNAVAILABLE")
        # variant belonging to a different product
        r = self.place_order(
            items=[{"productId": "roses", "variantId": self.tracked_v.pk, "presentationId": "bouquet", "quantity": 1}]
        )
        self.assertEqual(r.data["error"]["code"], "VARIANT_UNAVAILABLE")

    def test_presentation_must_be_allowed(self):
        r = self.place_order(
            items=[{"productId": "roses", "variantId": self.small.pk, "presentationId": "nope", "quantity": 1}]
        )
        self.assertEqual(r.data["error"]["code"], "INVALID_PRESENTATION")
        r = self.place_order(items=[{"productId": "roses", "variantId": self.small.pk, "quantity": 1}])
        self.assertEqual(r.data["error"]["code"], "INVALID_PRESENTATION")

    def test_delivery_rules(self):
        self.assertEqual(self.place_order(deliveryArea="mars").data["error"]["code"], "DELIVERY_AREA_UNAVAILABLE")
        self.assertEqual(self.place_order(deliverySlot=9999).data["error"]["code"], "SLOT_UNAVAILABLE")
        past = (date.today() - timedelta(days=2)).isoformat()
        self.assertEqual(self.place_order(deliveryDate=past).data["error"]["code"], "DATE_IN_PAST")
        far = (date.today() + timedelta(days=500)).isoformat()
        self.assertEqual(self.place_order(deliveryDate=far).data["error"]["code"], "DATE_TOO_FAR")
        blocked = self.delivery_date(4)
        BlockedDate.objects.create(date=blocked)
        self.assertEqual(self.place_order(deliveryDate=blocked).data["error"]["code"], "DATE_UNAVAILABLE")

    def test_card_message_length_limited(self):
        self.assertEqual(self.place_order(cardMessage="x" * 301).status_code, 400)


class StockTests(BaseTest):
    def tracked_items(self, qty):
        return [{"productId": "tracked", "variantId": self.tracked_v.pk, "quantity": qty}]

    def test_reserves_stock_and_blocks_overselling(self):
        self.assertEqual(self.place_order(items=self.tracked_items(2)).status_code, 201)
        self.tracked_v.refresh_from_db()
        self.assertEqual(self.tracked_v.stock_quantity, 1)
        r = self.place_order(user=self.other, items=self.tracked_items(2))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["error"]["code"], "OUT_OF_STOCK")
        self.tracked_v.refresh_from_db()
        self.assertEqual(self.tracked_v.stock_quantity, 1)

    def test_split_lines_cannot_bypass_stock_check(self):
        lines = self.tracked_items(2) + self.tracked_items(2)
        self.assertEqual(self.place_order(items=lines).data["error"]["code"], "OUT_OF_STOCK")

    def test_cancel_releases_stock_once(self):
        r = self.place_order(items=self.tracked_items(3))
        self.tracked_v.refresh_from_db()
        self.assertEqual(self.tracked_v.stock_quantity, 0)
        c = self.client.post(f"/api/v1/orders/{r.data['id']}/cancel")
        self.assertEqual(c.status_code, 200)
        self.assertEqual(c.data["status"], "cancelled")
        self.tracked_v.refresh_from_db()
        self.assertEqual(self.tracked_v.stock_quantity, 3)
        self.assertEqual(self.client.post(f"/api/v1/orders/{r.data['id']}/cancel").status_code, 409)
        self.tracked_v.refresh_from_db()
        self.assertEqual(self.tracked_v.stock_quantity, 3)


class OrderAccessAndFulfilmentTests(BaseTest):
    def test_customers_only_see_their_own_orders(self):
        mine = self.place_order().data["id"]
        theirs = self.place_order(user=self.other).data["id"]
        self.login(self.customer)
        self.assertEqual(self.client.get(f"/api/v1/orders/{mine}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/orders/{theirs}").status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/orders/{theirs}/cancel").status_code, 404)
        ids = [o["id"] for o in self.client.get("/api/v1/me/orders").data["results"]]
        self.assertEqual(ids, [mine])

    def test_staff_can_read_any_order_and_progress_a_paid_one(self):
        order_id = self.place_order().data["id"]
        Order.objects.filter(pk=order_id).update(status=Order.Status.CONFIRMED)
        self.login(self.staff)
        self.assertEqual(self.client.get(f"/api/v1/orders/{order_id}").status_code, 200)
        for nxt in ("preparing", "ready", "out_for_delivery", "delivered"):
            r = self.client.post(f"/api/v1/admin/orders/{order_id}/status", {"status": nxt}, format="json")
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual(r.data["status"], nxt)
        r = self.client.get(f"/api/v1/admin/orders/{order_id}")
        self.assertEqual(len(r.data["events"]), 5)  # created + 4 staff transitions (confirm was set directly)
        # terminal state cannot move
        bad = self.client.post(f"/api/v1/admin/orders/{order_id}/status", {"status": "preparing"}, format="json")
        self.assertEqual(bad.status_code, 409)

    def test_staff_cannot_fulfil_an_unpaid_order(self):
        order_id = self.place_order().data["id"]
        self.login(self.staff)
        for target in ("preparing", "ready", "out_for_delivery", "delivered"):
            r = self.client.post(f"/api/v1/admin/orders/{order_id}/status", {"status": target}, format="json")
            self.assertEqual(r.status_code, 409, target)
            self.assertEqual(r.data["error"]["code"], "INVALID_TRANSITION")
        self.assertEqual(Order.objects.get(pk=order_id).status, "pending_payment")

    def test_staff_can_confirm_payment_manually_and_it_is_audited_and_notified(self):
        order_id = self.place_order().data["id"]
        self.login(self.staff)
        r = self.client.post(
            f"/api/v1/admin/orders/{order_id}/status",
            {"status": "confirmed", "note": "Paid via till, ref ABC"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["status"], "confirmed")
        self.assertIsNotNone(r.data["paidAt"])
        from apps.orders.models import OrderStatusEvent

        event = OrderStatusEvent.objects.filter(order_id=order_id).latest("created_at")
        self.assertEqual((event.to_status, event.actor_id, event.note), ("confirmed", self.staff.pk, "Paid via till, ref ABC"))
        self.assertEqual(Notification.objects.filter(kind="order_confirmed", status="queued").count(), 3)
        from apps.core.models import AuditEvent

        self.assertTrue(AuditEvent.objects.filter(action="order.status_changed").exists())

    def test_customer_cannot_confirm_their_own_order(self):
        order_id = self.place_order().data["id"]
        r = self.client.post(f"/api/v1/admin/orders/{order_id}/status", {"status": "confirmed"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Order.objects.get(pk=order_id).status, "pending_payment")

    def test_confirm_order_payment_is_idempotent_and_never_revives_cancelled_orders(self):
        order_id = self.place_order().data["id"]
        self.assertEqual(confirm_order_payment(order_id), "confirmed")
        self.assertEqual(confirm_order_payment(order_id), "already")
        self.assertEqual(Notification.objects.filter(kind="order_confirmed").count(), 3)  # not doubled
        cancelled = self.place_order(user=self.other).data["id"]
        Order.objects.filter(pk=cancelled).update(status=Order.Status.CANCELLED)
        self.assertEqual(confirm_order_payment(cancelled), "cancelled")
        self.assertEqual(Order.objects.get(pk=cancelled).status, "cancelled")

    def test_confirmation_does_not_queue_phone_notifications_without_account_phone(self):
        customer = User.objects.create_user(
            None, self.PASSWORD, name="Email Customer", email="email@example.com"
        )
        order_id = self.place_order(user=customer).data["id"]
        self.assertEqual(confirm_order_payment(order_id), "confirmed")
        notifications = Notification.objects.filter(kind="order_confirmed")
        self.assertEqual(notifications.count(), 1)
        self.assertEqual(notifications.get().channel, Notification.Channel.EMAIL)
        self.assertEqual(notifications.get().recipient, customer.email)

    def test_expired_unpaid_orders_are_cancelled_and_stock_released(self):
        key = idem()
        payload = {"items": [{"productId": "tracked", "variantId": self.tracked_v.pk, "quantity": 2}]}
        r = self.place_order(key=key, **payload)
        self.assertEqual(r.status_code, 201, r.content)
        self.tracked_v.refresh_from_db()
        self.assertEqual(self.tracked_v.stock_quantity, 1)
        self.assertEqual(expire_stale_orders(), 0)  # deadline not reached
        Order.objects.filter(pk=r.data["id"]).update(expires_at=timezone.now())
        self.assertEqual(expire_stale_orders(), 1)
        self.assertEqual(Order.objects.get(pk=r.data["id"]).status, "cancelled")
        self.tracked_v.refresh_from_db()
        self.assertEqual(self.tracked_v.stock_quantity, 3)
        self.assertEqual(expire_stale_orders(), 0)  # idempotent

    def test_order_response_has_no_payment_provider_fields(self):
        r = self.place_order()
        self.assertNotIn("payment", r.data)
        self.assertNotIn("mpesaPhone", r.data)

    def test_customer_cannot_change_status_via_staff_api(self):
        order_id = self.place_order().data["id"]
        r = self.client.post(f"/api/v1/admin/orders/{order_id}/status", {"status": "delivered"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_status_changes_are_audited(self):
        from apps.core.models import AuditEvent

        order_id = self.place_order().data["id"]
        Order.objects.filter(pk=order_id).update(status=Order.Status.CONFIRMED)
        self.login(self.staff)
        self.client.post(f"/api/v1/admin/orders/{order_id}/status", {"status": "preparing"}, format="json")
        self.assertTrue(AuditEvent.objects.filter(action="order.status_changed", actor=self.staff).exists())
