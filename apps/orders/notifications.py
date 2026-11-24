from django.conf import settings

from apps.core.email import send_transactional_email_async

from .models import Order


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


def notify_new_order(order: Order) -> None:
    """Email admins and the customer after the order transaction commits."""
    admin_recipients = settings.ORDER_ALERT_EMAILS
    customer_email = order.customer.email or ""
    if not admin_recipients and not customer_email:
        return

    if admin_recipients:
        send_transactional_email_async(
            f"New order {order.reference}",
            _format_admin_order(order),
            admin_recipients,
            context=f"admin order alert {order.reference}",
        )
    if customer_email:
        send_transactional_email_async(
            f"We received your order {order.reference}",
            _format_customer_order(order),
            [customer_email],
            context=f"customer order confirmation {order.reference}",
        )


def notify_order_confirmed(order: Order) -> None:
    customer_email = order.customer.email or ""
    if not customer_email:
        return

    send_transactional_email_async(
        f"Payment confirmed for order {order.reference}",
        (
            f"Hello {order.customer.name},\n\n"
            f"Payment for order {order.reference} has been confirmed. "
            "We are preparing your order and will keep you updated.\n\n"
            f"Delivery: {order.delivery_date:%d %b %Y}, {order.delivery_slot_label}\n"
            f"Total: {order.currency} {order.total_kes:,}\n\n"
            "Thank you for choosing Petals Paradise."
        ),
        [customer_email],
        context=f"payment confirmation {order.reference}",
    )
