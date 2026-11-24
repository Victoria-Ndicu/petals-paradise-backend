"""M-Pesa payment flow.

Rules enforced here:
* The amount sent to Safaricom is always ``order.total_kes`` computed server-side.
* A payment becomes ``paid`` (and the order ``confirmed``) only after a callback
  reporting success that matches our stored request AND an STK status query
  confirming it (or, if the callback never arrives, the status query alone).
* Callbacks/queries are idempotent: replays never create a second transition.
"""
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from apps.core.exceptions import ApiError
from apps.orders.models import Order
from apps.orders.notifications import notify_order_confirmed
from apps.orders.services import transition_order

from . import daraja
from .models import Payment

logger = logging.getLogger(__name__)

# A prompt that has been open this long without a result is treated as timed out.
STALE_PENDING_SECONDS = 120
RECONCILE_MIN_AGE_SECONDS = 30
RECONCILE_MIN_INTERVAL_SECONDS = 15

# Reasons we assign ourselves when closing a payment WITHOUT a final answer from
# Safaricom. A later success report must still be honoured so money is never lost.
LOCAL_CLOSE_REASONS = {"timeout", "order_cancelled"}

FAILURE_REASONS = {
    1: "insufficient_funds",
    1019: "transaction_expired",
    1025: "prompt_error",
    1032: "cancelled_by_user",
    1037: "timeout",
    2001: "wrong_pin",
    9999: "prompt_error",
}


# ------------------------------------------------------------------ initiation
def initiate_mpesa_payment(order_id, idempotency_key: str) -> Payment:
    """Start (or replay) an STK Push for an order. Returns the Payment."""
    # Resolve any earlier pending attempt first, outside the lock (it does HTTP).
    for stale in Payment.objects.filter(order_id=order_id, status=Payment.Status.PENDING).exclude(
        idempotency_key=idempotency_key
    ):
        reconcile_payment(stale.pk, force=True)

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)

        replay = Payment.objects.filter(order=order, idempotency_key=idempotency_key).first()
        if replay:
            return replay

        if order.payments.filter(status=Payment.Status.PAID).exists() or order.status not in (
            Order.Status.PENDING_PAYMENT,
        ):
            code = "ORDER_ALREADY_PAID" if order.status != Order.Status.CANCELLED else "ORDER_NOT_PAYABLE"
            raise ApiError(code, "This order can't be paid for right now.", status.HTTP_409_CONFLICT)
        if order.expires_at <= timezone.now():
            raise ApiError("ORDER_EXPIRED", "This order has expired. Please place it again.", status.HTTP_409_CONFLICT)

        now = timezone.now()
        for pending in order.payments.select_for_update().filter(status=Payment.Status.PENDING):
            age = (now - pending.created_at).total_seconds()
            if age < STALE_PENDING_SECONDS:
                raise ApiError(
                    "PAYMENT_IN_PROGRESS",
                    "A payment prompt is already on your phone. Please complete or wait for it to expire.",
                    status.HTTP_409_CONFLICT,
                )
            pending.status = Payment.Status.FAILED
            pending.failure_reason = "timeout"
            pending.save(update_fields=["status", "failure_reason", "updated_at"])

        try:
            payment = Payment.objects.create(
                order=order,
                amount_kes=order.total_kes,  # server-calculated, never from the client
                currency=order.currency,
                phone=order.mpesa_phone,
                idempotency_key=idempotency_key,
            )
        except IntegrityError:
            return Payment.objects.get(order=order, idempotency_key=idempotency_key)

    # Call Safaricom outside the DB transaction so we never hold locks over HTTP.
    try:
        result = daraja.stk_push(
            amount_kes=payment.amount_kes,
            phone=payment.phone,
            account_reference=order.reference,
            description="Petals",
        )
    except daraja.DarajaError as exc:
        Payment.objects.filter(pk=payment.pk).update(
            status=Payment.Status.FAILED, failure_reason="initiation_failed", updated_at=timezone.now()
        )
        logger.error("STK push failed for order %s: %s", order.reference, exc)
        raise ApiError(
            "PAYMENT_PROVIDER_ERROR",
            "We couldn't start the M-Pesa payment. Please try again in a moment.",
            status.HTTP_502_BAD_GATEWAY,
        )

    Payment.objects.filter(pk=payment.pk).update(
        merchant_request_id=result.merchant_request_id,
        checkout_request_id=result.checkout_request_id,
        updated_at=timezone.now(),
    )
    payment.refresh_from_db()
    return payment


# -------------------------------------------------------------------- results
def _locally_closed(payment: Payment) -> bool:
    return (
        payment.status in (Payment.Status.FAILED, Payment.Status.CANCELLED)
        and payment.failure_reason in LOCAL_CLOSE_REASONS
    )


def _accepts_result(payment: Payment, success: bool) -> bool:
    """A pending payment accepts any result; one we closed locally accepts only success."""
    if payment.status == Payment.Status.PENDING:
        return True
    return success and _locally_closed(payment)


def _failure_status(code: int) -> tuple[str, str]:
    if code == 1032:
        return Payment.Status.CANCELLED, FAILURE_REASONS[1032]
    return Payment.Status.FAILED, FAILURE_REASONS.get(code, f"result_{code}")


def _mark_failed(payment: Payment, code: int) -> None:
    new_status, reason = _failure_status(code)
    payment.status = new_status
    payment.failure_reason = reason
    payment.save(update_fields=["status", "failure_reason", "updated_at"])


def _mark_paid(payment: Payment, receipt: str = "") -> None:
    now = timezone.now()
    payment.status = Payment.Status.PAID
    payment.paid_at = now
    payment.verified_at = now
    if receipt and not payment.mpesa_receipt:
        payment.mpesa_receipt = receipt[:40]
    payment.failure_reason = ""
    payment.save()

    order = Order.objects.select_for_update().select_related("customer").get(pk=payment.order_id)
    if order.status == Order.Status.PENDING_PAYMENT:
        transition_order(order, Order.Status.CONFIRMED, actor=None, note="M-Pesa payment verified")
        transaction.on_commit(lambda order=order: notify_order_confirmed(order))
    elif order.status == Order.Status.CANCELLED:
        # Customer paid after we cancelled (late callback): never lose the money silently.
        payment.needs_review = True
        payment.review_reason = "paid_after_order_cancelled"
        payment.save(update_fields=["needs_review", "review_reason", "updated_at"])
        logger.error("Payment %s received for cancelled order %s", payment.pk, order.reference)
    else:
        payment.needs_review = True
        payment.review_reason = "duplicate_payment_for_order"
        payment.save(update_fields=["needs_review", "review_reason", "updated_at"])
        logger.error("Duplicate payment %s for order %s", payment.pk, order.reference)


def _apply_query_result(payment_id, result: daraja.StkQueryResult) -> Payment:
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        payment.last_status_check_at = timezone.now()
        if not _accepts_result(payment, result.result_code == 0):
            payment.save(update_fields=["last_status_check_at"])
            return payment
        if result.result_code == 0:
            _mark_paid(payment, receipt=payment.mpesa_receipt)
        else:
            payment.callback_result_code = result.result_code
            payment.callback_result_desc = result.result_desc
            payment.save(update_fields=["callback_result_code", "callback_result_desc"])
            _mark_failed(payment, result.result_code)
        return payment


def reconcile_payment(payment_id, force: bool = False) -> bool:
    """Ask Safaricom for the outcome of a pending payment. True if it changed."""
    payment = Payment.objects.filter(pk=payment_id).first()
    if not payment or not payment.checkout_request_id:
        return False
    if payment.status != Payment.Status.PENDING and not _locally_closed(payment):
        return False
    now = timezone.now()
    if not force:
        if (now - payment.created_at).total_seconds() < RECONCILE_MIN_AGE_SECONDS:
            return False
        last = payment.last_status_check_at
        if last and (now - last).total_seconds() < RECONCILE_MIN_INTERVAL_SECONDS:
            return False
    try:
        result = daraja.stk_query(payment.checkout_request_id)
    except daraja.DarajaPending:
        Payment.objects.filter(pk=payment.pk).update(last_status_check_at=now)
        return False
    except daraja.DarajaError as exc:
        Payment.objects.filter(pk=payment.pk).update(last_status_check_at=now)
        logger.warning("Status query failed for payment %s: %s", payment.pk, exc)
        return False
    was = payment.status
    updated = _apply_query_result(payment.pk, result)
    return updated.status != was


def maybe_reconcile(order: Order) -> bool:
    """Used when the browser polls: resolve a long-pending payment ourselves."""
    changed = False
    for p in order.payments.all():
        if p.status == Payment.Status.PENDING and p.checkout_request_id:
            changed = reconcile_payment(p.pk) or changed
    return changed


# ------------------------------------------------------------------ callbacks
class InvalidCallback(ValueError):
    pass


def parse_callback(body) -> dict:
    """Validate the structure of a Daraja STK callback. Raises InvalidCallback."""
    try:
        cb = body["Body"]["stkCallback"]
        checkout_id = str(cb["CheckoutRequestID"]).strip()
        code = int(cb["ResultCode"])
        desc = str(cb.get("ResultDesc", ""))[:255]
        items = {}
        for item in (cb.get("CallbackMetadata") or {}).get("Item", []) or []:
            items[str(item["Name"])] = item.get("Value")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise InvalidCallback("malformed callback") from exc
    if not checkout_id or len(checkout_id) > 80:
        raise InvalidCallback("bad CheckoutRequestID")
    return {"checkout_id": checkout_id, "code": code, "desc": desc, "items": items}


def handle_callback(body) -> str:
    """Process a Daraja callback idempotently. Returns an outcome label (for logs/tests)."""
    cb = parse_callback(body)
    checkout_id, code = cb["checkout_id"], cb["code"]

    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(checkout_request_id=checkout_id).first()
        if payment is None:
            logger.warning("Callback for unknown CheckoutRequestID")
            return "unknown_payment"
        if not _accepts_result(payment, code == 0):
            return "already_processed"  # duplicate / replayed / out-of-order callback

        payment.callback_received_at = timezone.now()
        payment.callback_result_code = code
        payment.callback_result_desc = cb["desc"]

        if code != 0:
            payment.save(update_fields=["callback_received_at", "callback_result_code", "callback_result_desc", "updated_at"])
            _mark_failed(payment, code)
            return "failed"

        receipt = str(cb["items"].get("MpesaReceiptNumber") or "").strip()
        try:
            amount = Decimal(str(cb["items"].get("Amount")))
        except (InvalidOperation, TypeError):
            amount = None
        payment.callback_amount = amount
        if not receipt or amount is None or amount != Decimal(payment.amount_kes):
            payment.needs_review = True
            payment.review_reason = "callback_amount_or_receipt_mismatch"
            payment.save()
            logger.error("Callback mismatch for payment %s", payment.pk)
            return "mismatch"
        payment.mpesa_receipt = receipt[:40]
        payment.save()

    # Verify with Safaricom outside the lock before trusting the callback.
    try:
        result = daraja.stk_query(checkout_id)
    except (daraja.DarajaPending, daraja.DarajaError):
        # Callback is stored; reconcile_payments / the status poll will finish verification.
        logger.warning("Could not verify callback yet for payment %s", payment.pk)
        return "awaiting_verification"

    if result.result_code != 0:
        Payment.objects.filter(pk=payment.pk).update(
            needs_review=True, review_reason="callback_query_mismatch", updated_at=timezone.now()
        )
        logger.error("Callback said success but status query disagrees for payment %s", payment.pk)
        return "verification_mismatch"

    _apply_query_result(payment.pk, result)
    return "paid"


def expire_stale_orders() -> int:
    """Cancel unpaid orders past their deadline and release their stock."""
    from apps.orders.services import cancel_unpaid_order

    cancelled = 0
    now = timezone.now()
    for order in Order.objects.filter(status=Order.Status.PENDING_PAYMENT, expires_at__lte=now):
        # Give any in-flight prompt a final chance to resolve.
        for p in order.payments.filter(status=Payment.Status.PENDING):
            reconcile_payment(p.pk, force=True)
        still_pending = order.payments.filter(
            status=Payment.Status.PENDING, created_at__gte=now - timedelta(seconds=STALE_PENDING_SECONDS * 2)
        ).exists()
        if still_pending:
            continue
        if cancel_unpaid_order(order.pk, note="payment timeout"):
            cancelled += 1
    return cancelled
