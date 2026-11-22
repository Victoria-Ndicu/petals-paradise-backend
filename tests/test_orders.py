from datetime import date, timedelta

from apps.catalogue.models import BlockedDate, ProductVariant
from apps.orders.models import Order

from .base import BaseTest, idem


class OrderCreationTests(BaseTest):
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

    def test_same_key_different_cart_conflicts(self):
        key = idem()
        self.place_order(key=key)
        r = self.place_order(
            key=key, items=[{"productId": "roses", "variantId": self.large.pk, "presentationId": "bouquet", "quantity": 1}]
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_validation_errors_use_envelope_with_camel_fields(self):
        r = self.place_order(recipientPhone="123", recipientName="", mpesaPhone="abc")
        self.assertEqual(r.status_code, 400)
        fields = r.data["error"]["fields"]
        self.assertIn("recipientPhone", fields)
        self.assertIn("recipientName", fields)
        self.assertIn("mpesaPhone", fields)

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

    def test_staff_cannot_mark_unpaid_order_fulfilled_or_confirmed(self):
        order_id = self.place_order().data["id"]
        self.login(self.staff)
        for target in ("delivered", "confirmed", "preparing"):
            r = self.client.post(f"/api/v1/admin/orders/{order_id}/status", {"status": target}, format="json")
            self.assertEqual(r.status_code, 409, target)
            self.assertEqual(r.data["error"]["code"], "PAYMENT_REQUIRED")
        self.assertEqual(Order.objects.get(pk=order_id).status, "pending_payment")

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
