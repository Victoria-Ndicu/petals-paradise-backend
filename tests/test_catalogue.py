from apps.catalogue.models import BlockedDate

from .base import BaseTest


class CatalogueTests(BaseTest):
    def test_lists_only_published_products(self):
        r = self.client.get("/api/v1/products")
        self.assertEqual(r.status_code, 200)
        ids = [p["id"] for p in r.data["results"]]
        self.assertIn("roses", ids)
        self.assertNotIn("draft-item", ids)

    def test_product_shape_and_variants(self):
        p = self.client.get("/api/v1/products/roses").data
        self.assertEqual(p["category"], "flowers")
        self.assertEqual([v["priceKes"] for v in p["variants"]], [2500, 4000])
        self.assertEqual(p["startingPriceKes"], 2500)
        self.assertEqual(p["variants"][0]["availability"], "made_to_order")
        self.assertEqual({x["id"] for x in p["presentations"]}, {"bouquet", "box"})

    def test_tracked_product_reports_stock_state_not_a_fake_count(self):
        p = self.client.get("/api/v1/products/tracked").data
        self.assertEqual(p["variants"][0]["availability"], "in_stock")
        self.assertNotIn("stockQuantity", p["variants"][0])

    def test_draft_product_detail_404(self):
        self.assertEqual(self.client.get("/api/v1/products/draft-item").status_code, 404)

    def test_filter_search_and_sort(self):
        r = self.client.get("/api/v1/products?category=fruit-gifts")
        self.assertEqual([p["id"] for p in r.data["results"]], ["tracked"])
        r = self.client.get("/api/v1/products?search=ros")
        self.assertEqual([p["id"] for p in r.data["results"]], ["roses"])
        r = self.client.get("/api/v1/products?ordering=-price")
        self.assertEqual([p["id"] for p in r.data["results"]][0], "roses")

    def test_delivery_options(self):
        BlockedDate.objects.create(date=__import__("datetime").date.today() + __import__("datetime").timedelta(days=5))
        r = self.client.get("/api/v1/delivery-options")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([a["id"] for a in r.data["areas"]], ["westlands"])
        self.assertEqual(r.data["areas"][0]["feeKes"], 400)
        self.assertEqual(len(r.data["blockedDates"]), 1)
        self.assertEqual(r.data["currency"], "KES")
