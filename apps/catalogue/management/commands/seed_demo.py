from datetime import time

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalogue.models import (
    Category,
    DeliveryOption,
    DeliverySlot,
    Presentation,
    Product,
    ProductPresentation,
    ProductVariant,
)


class Command(BaseCommand):
    help = (
        "Load PLACEHOLDER catalogue and delivery data for development/staging. "
        "Prices, areas and fees here are NOT verified business values - replace them in the admin before launch."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        flowers, _ = Category.objects.get_or_create(slug="flowers", defaults={"name": "Flowers", "sort_order": 1})
        gifts, _ = Category.objects.get_or_create(slug="fruit-gifts", defaults={"name": "Fruit & gifts", "sort_order": 2})

        bouquet, _ = Presentation.objects.get_or_create(slug="bouquet", defaults={"name": "Bouquet"})
        box, _ = Presentation.objects.get_or_create(slug="box", defaults={"name": "Gift box"})
        basket, _ = Presentation.objects.get_or_create(slug="basket", defaults={"name": "Basket"})

        samples = [
            ("sample-rose-bouquet", flowers, "Sample Rose Bouquet", [("Small", 2500), ("Medium", 3800), ("Large", 5500)], [bouquet, box]),
            ("sample-mixed-blooms", flowers, "Sample Mixed Blooms", [("Small", 2200), ("Large", 4800)], [bouquet]),
            ("sample-fruit-basket", gifts, "Sample Fruit Basket", [("Small", 3000), ("Large", 6000)], [basket]),
        ]
        for slug, category, name, variants, presentations in samples:
            product, created = Product.objects.get_or_create(
                slug=slug,
                defaults={
                    "category": category,
                    "name": name,
                    "description": "Placeholder product - replace in the admin.",
                    "status": Product.Status.PUBLISHED,
                    "stock_policy": Product.StockPolicy.MADE_TO_ORDER,
                },
            )
            if created:
                for i, (label, price) in enumerate(variants):
                    ProductVariant.objects.create(product=product, label=label, price_kes=price, sort_order=i)
                for pres in presentations:
                    ProductPresentation.objects.create(product=product, presentation=pres)

        for i, (slug, name, fee) in enumerate(
            [("cbd", "Nairobi CBD", 300), ("westlands", "Westlands", 400), ("karen", "Karen", 600)]
        ):
            DeliveryOption.objects.get_or_create(slug=slug, defaults={"name": name, "fee_kes": fee, "sort_order": i})

        for label, start, end in [
            ("9am - 12pm", time(9), time(12)),
            ("12pm - 3pm", time(12), time(15)),
            ("3pm - 6pm", time(15), time(18)),
        ]:
            DeliverySlot.objects.get_or_create(label=label, defaults={"start_time": start, "end_time": end})

        self.stdout.write(self.style.WARNING("Seeded PLACEHOLDER data. Confirm real prices, zones and fees before launch."))
