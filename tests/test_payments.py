from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from apps.orders.models import Order
from apps.payments import daraja
from apps.payments.models import Payment
from apps.payments.services import expire_stale_orders

from .base import BaseTest, idem

CHECKOUT = "ws_CO_123456789"


def push_result(checkout=CHECKOUT):
    return daraja.StkPushResult("29115-34620561-1", checkout, "Success. Request accepted for processing")


def callback_body(code=0, amount=5400, receipt="QGR7XYZ123", checkout=CHECKOUT):
    cb = {
        "MerchantRequestID": "29115-34620561-1",
        "CheckoutRequestID": checkout,
        "ResultCode": code,
        "ResultDesc": "The service request is processed successfully." if code == 0 else "Request cancelled by user",
    }
    if code == 0:
        cb["CallbackMetadata"] = {
            "Item": [
                {"Name": "Amount", "Value": amount},
                {"Name": "MpesaReceiptNumber", "Value": receipt},
                {"Name": "TransactionDate", "Value": 20261121101500},
                {"Name": "PhoneNumber", "Value": 254712345678},
            ]
        }
    return {"Body": {"stkCallback": cb}}


class PaymentTests(BaseTest):
    URL = "/api/v1/webhooks/mpesa/s3cret"

    def setUp(self):
        super().setUp()
        self.order_id = self.place_order().data["id"]  # total 5400, logged in as customer

    def pay(self, key=None, order_id=None):
        return self.client.post(
            f"/api/v1/orders/{order_id or self.order_id}/payments/mpesa", headers={"Idempotency-Key": key or idem()}
        )

    def start_payment(self):
        with mock.patch("apps.payments.daraja.stk_push", return_value=push_result()) as push:
            r = self.pay()
        self.assertEqual(r.status_code, 202, r.content)
        return r, push

    def callback(self, body, url=None):
        return self.client.post(url or self.URL, body, format="json")

    # ---- initiation ----------------------------------------------------
    def test_initiation_uses_server_total_and_stores_ids(self):
        r, push = self.start_payment()
        kwargs = push.call_args.kwargs
        self.assertEqual(kwargs["amount_kes"], 5400)
        self.assertEqual(kwargs["phone"], "254712345678")
        self.assertEqual(set(r.data), {"orderId", "paymentId", "status", "message"})
        self.assertEqual(r.data["status"], "pending")
        p = Payment.objects.get()
        self.assertEqual((p.checkout_request_id, p.amount_kes), (CHECKOUT, 5400))

    def test_initiation_requires_idempotency_key_and_ownership(self):
        r = self.client.post(f"/api/v1/orders/{self.order_id}/payments/mpesa")
        self.assertEqual(r.status_code, 400)
        self.login(self.other)
        self.assertEqual(self.pay().status_code, 404)

    def test_replayed_key_does_not_charge_twice(self):
        key = idem()
        with mock.patch("apps.payments.daraja.stk_push", return_value=push_result()) as push:
            a = self.pay(key)
            b = self.pay(key)
        self.assertEqual(push.call_count, 1)
        self.assertEqual(a.data["paymentId"], b.data["paymentId"])
        self.assertEqual(Payment.objects.count(), 1)

    def test_second_attempt_while_prompt_open_is_rejected(self):
        self.start_payment()
        with mock.patch("apps.payments.daraja.stk_query", side_effect=daraja.DarajaPending()), mock.patch(
            "apps.payments.daraja.stk_push"
        ) as push:
            r = self.pay()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["error"]["code"], "PAYMENT_IN_PROGRESS")
        push.assert_not_called()

    def test_provider_error_marks_failed_and_allows_retry(self):
        with mock.patch("apps.payments.daraja.stk_push", side_effect=daraja.DarajaError("down")):
            r = self.pay()
        self.assertEqual(r.status_code, 502)
        self.assertEqual(Payment.objects.get().status, "failed")
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "pending_payment")
        with mock.patch("apps.payments.daraja.stk_push", return_value=push_result("ws_CO_retry")):
            self.assertEqual(self.pay().status_code, 202)

    def test_cannot_pay_cancelled_or_expired_order(self):
        Order.objects.filter(pk=self.order_id).update(expires_at=timezone.now() - timedelta(minutes=1))
        r = self.pay()
        self.assertEqual(r.data["error"]["code"], "ORDER_EXPIRED")
        Order.objects.filter(pk=self.order_id).update(status="cancelled")
        self.assertEqual(self.pay().status_code, 409)

    # ---- callbacks -------------------------------------------------------
    def test_verified_success_confirms_order_and_exposes_receipt(self):
        self.start_payment()
        with (
            mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")),
            mock.patch("apps.payments.services.notify_order_confirmed") as notify,
        ):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                r = self.callback(callback_body())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"ResultCode": 0, "ResultDesc": "Accepted"})
        p = Payment.objects.get()
        self.assertEqual((p.status, p.mpesa_receipt), ("paid", "QGR7XYZ123"))
        self.assertIsNotNone(p.verified_at)
        order = Order.objects.get(pk=self.order_id)
        self.assertEqual(order.status, "confirmed")
        detail = self.client.get(f"/api/v1/orders/{self.order_id}").data
        self.assertEqual(detail["payment"]["status"], "paid")
        self.assertEqual(detail["payment"]["receipt"], "QGR7XYZ123")
        self.assertEqual(len(callbacks), 1)
        notify.assert_called_once()

    def test_duplicate_callbacks_are_idempotent(self):
        self.start_payment()
        with (
            mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")),
            mock.patch("apps.payments.services.notify_order_confirmed") as notify,
        ):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                for _ in range(3):
                    self.assertEqual(self.callback(callback_body()).status_code, 200)
        self.assertEqual(Payment.objects.filter(status="paid").count(), 1)
        self.assertEqual(Order.objects.get(pk=self.order_id).events.filter(to_status="confirmed").count(), 1)
        self.assertEqual(len(callbacks), 1)
        notify.assert_called_once()

    def test_late_failure_callback_cannot_undo_paid(self):
        self.start_payment()
        with mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")):
            self.callback(callback_body())
        self.callback(callback_body(code=1032))
        self.assertEqual(Payment.objects.get().status, "paid")

    def test_user_cancelled_keeps_order_unpaid_and_retryable(self):
        self.start_payment()
        self.callback(callback_body(code=1032))
        p = Payment.objects.get()
        self.assertEqual((p.status, p.failure_reason), ("cancelled", "cancelled_by_user"))
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "pending_payment")
        detail = self.client.get(f"/api/v1/orders/{self.order_id}").data
        self.assertEqual(detail["payment"]["status"], "cancelled")
        self.assertEqual(detail["payment"]["receipt"], "")
        with mock.patch("apps.payments.daraja.stk_push", return_value=push_result("ws_CO_2")):
            self.assertEqual(self.pay().status_code, 202)

    def test_failed_pin_marks_failed(self):
        self.start_payment()
        body = callback_body(code=2001)
        self.callback(body)
        p = Payment.objects.get()
        self.assertEqual((p.status, p.failure_reason), ("failed", "wrong_pin"))
        self.assertNotEqual(Order.objects.get(pk=self.order_id).status, "confirmed")

    def test_amount_mismatch_never_marks_paid(self):
        self.start_payment()
        with mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")) as q:
            self.callback(callback_body(amount=10))
        q.assert_not_called()
        p = Payment.objects.get()
        self.assertEqual(p.status, "pending")
        self.assertTrue(p.needs_review)
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "pending_payment")

    def test_callback_contradicted_by_status_query_is_not_trusted(self):
        self.start_payment()
        with mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(1032, "cancelled")):
            self.callback(callback_body())
        p = Payment.objects.get()
        self.assertEqual(p.status, "pending")
        self.assertTrue(p.needs_review)
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "pending_payment")

    def test_forged_callback_for_unknown_payment_changes_nothing(self):
        self.start_payment()
        r = self.callback(callback_body(checkout="ws_CO_forged"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Payment.objects.get().status, "pending")
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "pending_payment")

    def test_invalid_payload_rejected(self):
        for bad in ({}, {"Body": {}}, {"Body": {"stkCallback": {"ResultCode": 0}}}, {"Body": {"stkCallback": {"CheckoutRequestID": "x", "ResultCode": "zz"}}}):
            self.assertEqual(self.callback(bad).status_code, 400, bad)

    def test_wrong_or_missing_secret_is_404(self):
        self.start_payment()
        self.assertEqual(self.callback(callback_body(), url="/api/v1/webhooks/mpesa/wrong").status_code, 404)
        self.assertEqual(self.callback(callback_body(), url="/api/v1/webhooks/mpesa").status_code, 404)
        self.assertEqual(Payment.objects.get().status, "pending")

    def test_success_callback_with_unreachable_query_waits_then_reconciles(self):
        self.start_payment()
        with mock.patch("apps.payments.daraja.stk_query", side_effect=daraja.DarajaError("net")):
            self.assertEqual(self.callback(callback_body()).status_code, 200)
        p = Payment.objects.get()
        self.assertEqual((p.status, p.mpesa_receipt), ("pending", "QGR7XYZ123"))
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "pending_payment")
        Payment.objects.update(created_at=timezone.now() - timedelta(minutes=5))
        with mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")):
            call_command("reconcile_payments")
        p.refresh_from_db()
        self.assertEqual((p.status, p.mpesa_receipt), ("paid", "QGR7XYZ123"))
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "confirmed")

    # ---- reconciliation / expiry ----------------------------------------
    def test_missing_callback_is_resolved_by_status_poll(self):
        self.start_payment()
        Payment.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        with mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")):
            data = self.client.get(f"/api/v1/orders/{self.order_id}").data
        self.assertEqual(data["status"], "confirmed")
        self.assertEqual(data["payment"]["status"], "paid")

    def test_poll_does_not_hammer_safaricom_for_fresh_payments(self):
        self.start_payment()
        with mock.patch("apps.payments.daraja.stk_query") as q:
            self.client.get(f"/api/v1/orders/{self.order_id}")
        q.assert_not_called()

    def test_pending_payment_is_never_shown_as_paid(self):
        self.start_payment()
        data = self.client.get(f"/api/v1/orders/{self.order_id}").data
        self.assertEqual((data["status"], data["payment"]["status"], data["payment"]["receipt"]), ("pending_payment", "pending", ""))

    def test_expired_unpaid_order_is_cancelled_and_stock_released(self):
        r = self.place_order(items=[{"productId": "tracked", "variantId": self.tracked_v.pk, "quantity": 2}])
        Order.objects.filter(pk=r.data["id"]).update(expires_at=timezone.now() - timedelta(minutes=1))
        with mock.patch("apps.payments.daraja.stk_query"):
            self.assertEqual(expire_stale_orders(), 1)
        self.assertEqual(Order.objects.get(pk=r.data["id"]).status, "cancelled")
        self.tracked_v.refresh_from_db()
        self.assertEqual(self.tracked_v.stock_quantity, 3)

    def test_payment_after_cancellation_is_flagged_for_refund_review(self):
        self.start_payment()
        Order.objects.filter(pk=self.order_id).update(expires_at=timezone.now() - timedelta(minutes=1))
        Payment.objects.update(created_at=timezone.now() - timedelta(minutes=10))
        with mock.patch("apps.payments.daraja.stk_query", side_effect=daraja.DarajaPending()):
            expire_stale_orders()
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "cancelled")
        with mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")):
            self.callback(callback_body())
        p = Payment.objects.get()
        self.assertTrue(p.needs_review)
        self.assertEqual(p.review_reason, "paid_after_order_cancelled")
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "cancelled")

    def test_late_success_after_local_timeout_is_still_honoured(self):
        self.start_payment()
        Payment.objects.update(status="failed", failure_reason="timeout")  # we gave up locally
        with mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")):
            self.callback(callback_body())
        self.assertEqual(Payment.objects.get().status, "paid")
        self.assertEqual(Order.objects.get(pk=self.order_id).status, "confirmed")

    def test_provider_final_failure_is_not_reopened_by_later_success(self):
        self.start_payment()
        self.callback(callback_body(code=2001))
        with mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")):
            self.callback(callback_body())
        self.assertEqual(Payment.objects.get().status, "failed")

    def test_cancelled_order_cannot_be_cancelled_by_customer_once_paid(self):
        self.start_payment()
        with mock.patch("apps.payments.daraja.stk_query", return_value=daraja.StkQueryResult(0, "ok")):
            self.callback(callback_body())
        self.assertEqual(self.client.post(f"/api/v1/orders/{self.order_id}/cancel").status_code, 409)


class DarajaClientTests(BaseTest):
    def test_stk_push_payload_and_response_parsing(self):
        from apps.payments import daraja

        calls = []

        def fake_post(path, payload):
            calls.append((path, payload))
            return {"_http": 200, "ResponseCode": "0", "CheckoutRequestID": "ws_CO_1", "MerchantRequestID": "m1"}

        with mock.patch.object(daraja, "_post", side_effect=fake_post):
            res = daraja.stk_push(amount_kes=100, phone="254712345678", account_reference="PP-ABCDEFGH", description="Petals")
        path, payload = calls[0]
        self.assertEqual(path, "/mpesa/stkpush/v1/processrequest")
        self.assertEqual(payload["Amount"], 100)
        self.assertEqual(payload["CallBackURL"], "https://api.example.com/api/v1/webhooks/mpesa/s3cret")
        self.assertEqual(res.checkout_request_id, "ws_CO_1")

    def test_stk_query_pending_vs_result(self):
        from apps.payments import daraja

        with mock.patch.object(daraja, "_post", return_value={"_http": 500, "errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}):
            with self.assertRaises(daraja.DarajaPending):
                daraja.stk_query("x")
        with mock.patch.object(daraja, "_post", return_value={"_http": 200, "ResultCode": "1032", "ResultDesc": "cancelled"}):
            self.assertEqual(daraja.stk_query("x").result_code, 1032)

    def test_missing_credentials_fail_cleanly(self):
        from django.test import override_settings

        from apps.payments import daraja

        with override_settings(MPESA_CONSUMER_KEY=""):
            with self.assertRaises(daraja.DarajaError):
                daraja.get_access_token()
