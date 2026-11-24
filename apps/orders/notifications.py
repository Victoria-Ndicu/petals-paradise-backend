import logging
import threading

from django.conf import settings

from apps.core.email import send_transactional_email

from .models import Order

logger = logging.getLogger(__name__)


def _format_admin_order(order: Order) -> str:
    customer_name = order.customer.name
    customer_contact = order.customer.phone or order.customer.email or "No contact on account"
    lines = [
        f"New order {order.reference}",
        f"Customer: {customer_name} ({customer_contact})",
        f"Recipient: {order.recipient_name} ({order.recipient_phone})",
        f"Delivery: {order.delivery_date:%d %b %Y}, {order.delivery_slot_label}",
        f"Area: {order.delivery_area_name}",
        f"Address: {order.delivery_address}",
        f"Total: {order.currency} {order.total_kes:,}",
        "Items:",
    ]
    for item in order.items.all():
        description = item.variant_label
        if item.presentation_name:
            description = f"{description}, {item.presentation_name}"
        lines.append(f"- {item.quantity} x {item.product_name} ({description})")
    return "\n".join(lines)


def _format_customer_order(order: Order) -> str:
    lines = [
        f"Hello {order.customer.name},",
        "",
        f"We've received your order {order.reference}.",
        "Your order is awaiting payment confirmation. We'll update you when its status changes.",
        "",
        f"Delivery: {order.delivery_date:%d %b %Y}, {order.delivery_slot_label}",
        f"Area: {order.delivery_area_name}",
        f"Address: {order.delivery_address}",
        f"Total: {order.currency} {order.total_kes:,}",
        "",
        "Items:",
    ]
    for item in order.items.all():
        description = item.variant_label
        if item.presentation_name:
            description = f"{description}, {item.presentation_name}"
        lines.append(f"- {item.quantity} x {item.product_name} ({description})")
    lines.extend(["", "Thank you for choosing Petals Paradise."])
    return "\n".join(lines)


def _send_email(recipient: str, subject: str, body: str, *, alert_type: str) -> None:
    try:
        send_transactional_email(subject, body, None, [recipient])
    except Exception:
        logger.exception("%s email failed", alert_type)


def _send_alerts(
    admin_recipients: list[str],
    customer_email: str,
    order_reference: str,
    admin_body: str,
    customer_body: str,
) -> None:
    if admin_recipients:
        try:
            send_transactional_email(
                f"New order {order_reference}",
                admin_body,
                None,
                admin_recipients,
            )
        except Exception:
            logger.exception("Order alert email failed for order %s", order_reference)

    if customer_email:
        _send_email(
            customer_email,
            f"We received your order {order_reference}",
            customer_body,
            alert_type=f"Customer confirmation for order {order_reference}",
        )


def notify_new_order(order: Order) -> None:
    """Email admins and the customer after the order transaction commits."""
    admin_recipients = settings.ORDER_ALERT_EMAILS
    customer_email = order.customer.email or ""
    if not admin_recipients and not customer_email:
        return

    try:
        admin_body = _format_admin_order(order) if admin_recipients else ""
        customer_body = _format_customer_order(order) if customer_email else ""
        thread = threading.Thread(
            target=_send_alerts,
            args=(admin_recipients, customer_email, order.reference, admin_body, customer_body),
            daemon=True,
        )
        thread.start()
    except Exception:
        logger.exception("Could not start email notifications for order %s", order.reference)
