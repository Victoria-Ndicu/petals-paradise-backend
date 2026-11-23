import logging
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings

from .models import Product

logger = logging.getLogger(__name__)
BREVO_API_BASE = "https://api.brevo.com/v3"


def _headers() -> dict[str, str]:
    return {
        "api-key": settings.BREVO_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }


def sync_product(product: Product) -> None:
    """Best-effort one-way product sync; external outages do not block admin saves."""
    if not settings.BREVO_API_KEY:
        return

    try:
        category_response = requests.post(
            f"{BREVO_API_BASE}/categories",
            headers=_headers(),
            json={
                "id": product.category.slug,
                "name": product.category.name,
                "updateEnabled": True,
            },
            timeout=5,
        )
        category_response.raise_for_status()

        prices = product.variants.filter(is_active=True).values_list("price_kes", flat=True)
        image_src = product.image_src
        image_url = urljoin(f"{settings.SITE_URL}/", image_src) if image_src else ""
        if image_url and urlparse(image_url).scheme not in {"http", "https"}:
            image_url = ""
        payload = {
            "id": product.slug,
            "name": product.name,
            "sku": product.slug,
            "url": f"{settings.SITE_URL}/#/shop",
            "price": min(prices, default=0),
            "categories": [product.category.slug],
            "isDeleted": product.status != Product.Status.PUBLISHED,
            "updateEnabled": True,
            "metaInfo": {"description": product.description[:500]},
        }
        if image_url:
            payload["imageUrl"] = image_url

        response = requests.post(
            f"{BREVO_API_BASE}/products",
            headers=_headers(),
            json=payload,
            timeout=5,
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Brevo sync failed for product %s", product.slug)
