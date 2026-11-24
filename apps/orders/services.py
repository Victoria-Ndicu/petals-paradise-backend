"""Order business logic. Every price, fee and total is computed here from
database records; nothing the browser sends for money is ever trusted."""
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from apps.catalogue.models import BlockedDate, DeliveryOption, DeliverySlot, Product, ProductPresentation, ProductVariant
from apps.core.exceptions import ApiError
from apps.core.models import record_audit

from .models import Order, OrderItem, OrderStatusEvent
from .notifications import notify_new_order, notify_order_confirmed

logger = logging.getLogger(__name__)

_REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L


def _new_reference() -> str:
    for _ in range(20):
        ref = "PP-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))
        if not Order.objects.filter(reference=ref).exists():
            return ref
    raise RuntimeError("Could not allocate an order reference.")


def business_now():
    return timezone.now().astimezone(ZoneInfo(settings.BUSINESS_TIME_ZONE))


def fingerprint(payload: dict) -> str:
    """Stable hash of the *business-relevant* request, used to detect an
    idempotency key being reused with a different cart."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------- state machine
def transition_order(order: Order, new_status: str, *, actor=None, note: str = "") -> Order:
    """Validate and apply an order state change. Caller must hold a row lock
    (select_for_update) when concurrency matters."""
    if new_status == order.status:
        return order
    allowed = Order.TRANSITIONS.get(order.status, set())
    if new_status not in allowed:
        raise ApiError(
            "INVALID_TRANSITION",
            f"An order that is '{order.status}' cannot move to '{new_status}'.",
            status.HTTP_409_CONFLICT,
        )
    old = order.status
    order.status = new_status
    fields = ["status", "updated_at"]
    now = timezone.now()
    if new_status == Order.Status.CONFIRMED:
        order.paid_at = order.paid_at or now
        fields.append("paid_at")
    if new_status == Order.Status.CANCELLED:
        order.cancelled_at = now
        fields.append("cancelled_at")
    order.save(update_fields=fields)
    OrderStatusEvent.objects.create(order=order, from_status=old, to_status=new_status, actor=actor, note=note[:200])
    if actor is not None and getattr(actor, "is_staff", False):
        record_audit(actor, "order.status_changed", order, **{"from": old, "to": new_status})
    if new_status == Order.Status.CANCELLED:
        release_stock(order)
    return order


def release_stock(order: Order) -> None:
    """Return reserved stock for tracked products. Safe to call twice."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.stock_released:
        return
    for item in locked.items.select_related("variant"):
        if item.stock_reserved and item.variant_id:
            variant = ProductVariant.objects.select_for_update().get(pk=item.variant_id)
            variant.stock_quantity = (variant.stock_quantity or 0) + item.stock_reserved
            variant.save(update_fields=["stock_quantity"])
    locked.stock_released = True
    locked.save(update_fields=["stock_released", "updated_at"])
    order.stock_released = True


def cancel_unpaid_order(order_id, *, actor=None, note="cancelled") -> bool:
    """Cancel an order only if it is still awaiting payment. Returns True if cancelled."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.Status.PENDING_PAYMENT:
            return False
        transition_order(order, Order.Status.CANCELLED, actor=actor, note=note)
        return True


def confirm_order_payment(order_id, *, actor=None, note: str = "Payment confirmed") -> str:
    """Single entry point for "this order has been paid".

    Used today by staff (manual confirmation) and intended to be the integration point for the
    separate payments service. Idempotent. Returns:
      "confirmed"   - the order moved from pending_payment to confirmed now
      "already"     - it was already confirmed or further along; nothing changed
      "cancelled"   - the order had been cancelled; the caller must handle the money (refund/review)
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().select_related("customer").get(pk=order_id)
        if order.status == Order.Status.CANCELLED:
            return "cancelled"
        if order.status != Order.Status.PENDING_PAYMENT:
            return "already"
        transition_order(order, Order.Status.CONFIRMED, actor=actor, note=note)
        transaction.on_commit(lambda order=order: notify_order_confirmed(order))
        return "confirmed"


def expire_stale_orders() -> int:
    """Cancel orders still awaiting payment past their deadline and release their stock."""
    cancelled = 0
    for order in Order.objects.filter(status=Order.Status.PENDING_PAYMENT, expires_at__lte=timezone.now()):
        if cancel_unpaid_order(order.pk, note="payment timeout"):
            cancelled += 1
    return cancelled


# --------------------------------------------------------------- order creation
def _validate_delivery(data: dict):
    area = DeliveryOption.objects.filter(slug=data["delivery_area"], is_active=True).first()
    if not area:
        raise ApiError(
            "DELIVERY_AREA_UNAVAILABLE", "We don't deliver to that area.", 400, {"deliveryArea": "Choose a supported area."}
        )
    slot = DeliverySlot.objects.filter(pk=data["delivery_slot"], is_active=True).first()
    if not slot:
        raise ApiError(
            "SLOT_UNAVAILABLE", "That delivery time slot isn't available.", 400, {"deliverySlot": "Choose a time slot."}
        )

    tz = ZoneInfo(settings.BUSINESS_TIME_ZONE)
    now = business_now()
    date = data["delivery_date"]
    if date < now.date():
        raise ApiError("DATE_IN_PAST", "Delivery date is in the past.", 400, {"deliveryDate": "Choose a future date."})
    if date > now.date() + timedelta(days=settings.MAX_DELIVERY_DAYS_AHEAD):
        raise ApiError(
            "DATE_TOO_FAR", "That delivery date is too far ahead.", 400, {"deliveryDate": "Choose an earlier date."}
        )
    if BlockedDate.objects.filter(date=date).exists():
        raise ApiError(
            "DATE_UNAVAILABLE", "We can't deliver on that date.", 400, {"deliveryDate": "Choose another date."}
        )
    slot_start = datetime.combine(date, slot.start_time, tzinfo=tz)
    if slot_start < now + timedelta(hours=settings.MIN_DELIVERY_LEAD_HOURS):
        raise ApiError(
            "SLOT_TOO_SOON",
            f"Orders need at least {settings.MIN_DELIVERY_LEAD_HOURS} hours' notice for that slot.",
            400,
            {"deliverySlot": "Choose a later slot or date."},
        )
    return area, slot


def create_order(*, customer, data: dict, idempotency_key: str) -> tuple[Order, bool]:
    """Create a pending order. Returns (order, created). A replay of the same
    idempotency key with the same cart returns the original order."""
    fp = fingerprint(
        {
            "items": data["items"],
            "area": data["delivery_area"],
            "slot": data["delivery_slot"],
            "date": data["delivery_date"],
            "address": data["delivery_address"],
            "rname": data["recipient_name"],
            "rphone": data["recipient_phone"],
            "card": data.get("card_message", ""),
        }
    )

    existing = Order.objects.filter(customer=customer, idempotency_key=idempotency_key).first()
    if existing:
        return _replay(existing, fp), False

    area, slot = _validate_delivery(data)

    try:
        with transaction.atomic():
            order = _build_order(customer, data, area, slot, idempotency_key, fp)
            transaction.on_commit(lambda order=order: notify_new_order(order))
        return order, True
    except IntegrityError:
        # Two identical requests raced; the unique constraint kept only one.
        existing = Order.objects.filter(customer=customer, idempotency_key=idempotency_key).first()
        if existing:
            return _replay(existing, fp), False
        raise


def _replay(existing: Order, fp: str) -> Order:
    if existing.request_fingerprint != fp:
        raise ApiError(
            "IDEMPOTENCY_CONFLICT",
            "This Idempotency-Key was already used for a different order.",
            status.HTTP_409_CONFLICT,
        )
    return existing


def _build_order(customer, data, area, slot, idempotency_key, fp) -> Order:
    lines = []  # (product, variant, presentation_link, qty, unit_price)
    needed_by_variant: dict[int, int] = {}

    for index, line in enumerate(data["items"]):
        field = f"items[{index}]"
        product = (
            Product.objects.select_related("category").filter(slug=line["product_id"]).first()
        )
        if not product or not product.is_orderable:
            raise ApiError(
                "PRODUCT_UNAVAILABLE",
                "An item in your cart is no longer available.",
                status.HTTP_409_CONFLICT,
                {f"{field}.productId": "No longer available."},
            )
        # Lock the variant row so concurrent checkouts can't oversell tracked stock.
        variant = ProductVariant.objects.select_for_update().filter(pk=line["variant_id"], product=product).first()
        if not variant or not variant.is_active:
            raise ApiError(
                "VARIANT_UNAVAILABLE",
                "A selected size/option is no longer available.",
                status.HTTP_409_CONFLICT,
                {f"{field}.variantId": "No longer available."},
            )

        links = list(ProductPresentation.objects.filter(product=product, presentation__is_active=True).select_related("presentation"))
        pres_slug = line.get("presentation_id") or ""
        pres_link = None
        if links:
            pres_link = next((l for l in links if l.presentation.slug == pres_slug), None)
            if pres_link is None:
                raise ApiError(
                    "INVALID_PRESENTATION",
                    "Choose a valid presentation for this item.",
                    400,
                    {f"{field}.presentationId": "Not available for this product."},
                )
        elif pres_slug:
            raise ApiError(
                "INVALID_PRESENTATION",
                "This product has no presentation options.",
                400,
                {f"{field}.presentationId": "Not available for this product."},
            )

        qty = line["quantity"]
        needed_by_variant[variant.pk] = needed_by_variant.get(variant.pk, 0) + qty
        unit_price = variant.price_kes + (pres_link.price_adjustment_kes if pres_link else 0)
        if unit_price <= 0:
            raise ApiError("PRICE_ERROR", "This item cannot be ordered right now.", status.HTTP_409_CONFLICT)
        lines.append((product, variant, pres_link, qty, unit_price))

    # Stock reservation for tracked products (done after totals per variant are known).
    reserved: dict[int, int] = {}
    for product, variant, _pl, qty, _price in lines:
        if product.stock_policy == Product.StockPolicy.TRACKED:
            if needed_by_variant[variant.pk] > (variant.stock_quantity or 0):
                raise ApiError(
                    "OUT_OF_STOCK",
                    f"Sorry, there isn't enough stock of {product.name} ({variant.label}).",
                    status.HTTP_409_CONFLICT,
                    {"items": f"{product.name} is out of stock."},
                )
            reserved[variant.pk] = reserved.get(variant.pk, 0) + qty

    subtotal = sum(price * qty for _p, _v, _pl, qty, price in lines)
    total = subtotal + area.fee_kes

    order = Order.objects.create(
        reference=_new_reference(),
        customer=customer,
        recipient_name=data["recipient_name"],
        recipient_phone=data["recipient_phone"],
        delivery_address=data["delivery_address"],
        delivery_area_slug=area.slug,
        delivery_area_name=area.name,
        delivery_date=data["delivery_date"],
        delivery_slot_label=slot.label,
        card_message=data.get("card_message", ""),
        subtotal_kes=subtotal,
        delivery_fee_kes=area.fee_kes,
        total_kes=total,
        currency=settings.CURRENCY,
        idempotency_key=idempotency_key,
        request_fingerprint=fp,
        expires_at=timezone.now() + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES),
    )

    reserved_left = dict(reserved)
    for product, variant, pres_link, qty, price in lines:
        take = 0
        if variant.pk in reserved_left:
            take = qty
            reserved_left[variant.pk] -= qty
        OrderItem.objects.create(
            order=order,
            product=product,
            variant=variant,
            product_slug=product.slug,
            product_name=product.name,
            variant_label=variant.label,
            presentation_slug=pres_link.presentation.slug if pres_link else "",
            presentation_name=pres_link.presentation.name if pres_link else "",
            quantity=qty,
            unit_price_kes=price,
            line_total_kes=price * qty,
            stock_reserved=take,
        )

    for variant_id, qty in reserved.items():
        variant = ProductVariant.objects.get(pk=variant_id)
        variant.stock_quantity = (variant.stock_quantity or 0) - qty
        variant.save(update_fields=["stock_quantity"])

    OrderStatusEvent.objects.create(order=order, from_status="", to_status=order.status, actor=customer, note="created")
    return order
