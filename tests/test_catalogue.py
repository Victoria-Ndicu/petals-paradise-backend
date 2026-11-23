from unittest.mock import Mock, patch

from apps.catalogue.models import BlockedDate

from .base import BaseTest


class CatalogueTests(BaseTest):
    def test_public_catalogue_responses_include_cdn_cache_headers(self):
        for path in ("/api/v1/products", "/api/v1/products/roses", "/api/v1/delivery-options"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                cache_control = response.headers["Cache-Control"]
                self.assertIn("public", cache_control)
                self.assertIn("max-age=30", cache_control)
                self.assertIn("s-maxage=60", cache_control)
                self.assertIn("stale-while-revalidate=300", cache_control)

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

    def test_product_image_uses_legacy_image_url_when_no_upload_exists(self):
        self.roses.image_url = "https://images.example.test/roses.jpg"
        self.roses.save(update_fields=["image_url"])
        p = self.client.get("/api/v1/products/roses").data
        self.assertEqual(p["image"], "https://images.example.test/roses.jpg")

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

    @patch("apps.catalogue.brevo.requests.post")
    def test_brevo_sync_sends_product_and_category(self, post):
        from django.test import override_settings

        from apps.catalogue.brevo import sync_product

        post.return_value = Mock(status_code=200)
        post.return_value.raise_for_status.return_value = None
        self.roses.image_url = "https://images.example.test/roses.jpg"
        self.roses.save(update_fields=["image_url"])

        with override_settings(BREVO_API_KEY="test-api-key", SITE_URL="https://shop.example.test"):
            sync_product(self.roses)

        self.assertEqual(post.call_count, 2)
        category_call, product_call = post.call_args_list
        self.assertTrue(category_call.args[0].endswith("/categories"))
        self.assertEqual(category_call.kwargs["json"]["id"], "flowers")
        self.assertTrue(product_call.args[0].endswith("/products"))
        self.assertEqual(product_call.kwargs["headers"]["api-key"], "test-api-key")
        self.assertEqual(product_call.kwargs["json"]["price"], 2500)
        self.assertEqual(product_call.kwargs["json"]["imageUrl"], "https://images.example.test/roses.jpg")
        self.assertEqual(product_call.kwargs["json"]["url"], "https://shop.example.test/#/shop")

    @patch("apps.catalogue.brevo.requests.post")
    def test_brevo_sync_failure_does_not_raise(self, post):
        import requests
        from django.test import override_settings

        from apps.catalogue.brevo import sync_product

        post.side_effect = requests.Timeout("Brevo unavailable")
        with override_settings(BREVO_API_KEY="test-api-key", SITE_URL="https://shop.example.test"):
            with self.assertLogs("apps.catalogue.brevo", level="ERROR"):
                sync_product(self.roses)
