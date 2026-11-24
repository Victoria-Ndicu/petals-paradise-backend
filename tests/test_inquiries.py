from unittest.mock import patch

from django.test import override_settings

from apps.inquiries.models import Inquiry

from .base import BaseTest

VALID = {
    "name": "Amina",
    "phone": "0711000999",
    "email": "Amina@Example.com",
    "occasion": "wedding",
    "preferredContact": "whatsapp",
    "details": "June",
}


class InquiryTests(BaseTest):
    def test_public_inquiry_is_persisted(self):
        r = self.client.post("/api/v1/inquiries", VALID, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        inq = Inquiry.objects.get(pk=r.data["id"])
        self.assertEqual((inq.phone, inq.status, inq.occasion), ("254711000999", "new", "wedding"))
        self.assertEqual(inq.email, "amina@example.com")
        self.assertEqual(inq.preferred_contact, "whatsapp")

    def test_all_current_occasions_accepted(self):
        for occ in ("flower_delivery", "wedding", "event_decor", "flower_maintenance", "funeral"):
            self.assertEqual(self.client.post("/api/v1/inquiries", {**VALID, "occasion": occ}, format="json").status_code, 201)

    def test_validation_errors(self):
        r = self.client.post("/api/v1/inquiries", {**VALID, "phone": "abc", "occasion": "nope", "name": ""}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual({"phone", "occasion", "name"}, set(r.data["error"]["fields"]))
        too_long = self.client.post("/api/v1/inquiries", {**VALID, "details": "x" * 2001}, format="json")
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_honeypot_stores_nothing(self):
        r = self.client.post("/api/v1/inquiries", {**VALID, "website": "http://spam"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_rate_limited(self):
        codes = [self.client.post("/api/v1/inquiries", VALID, format="json").status_code for _ in range(7)]
        self.assertEqual(codes.count(201), 5)
        self.assertIn(429, codes)

    @patch("apps.inquiries.notifications.send_transactional_email_async")
    @override_settings(INQUIRY_ALERT_EMAILS=["ndicuvictoria@gmail.com"])
    def test_customer_acknowledgement_and_staff_alert_use_shared_email_delivery(self, send_email):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post("/api/v1/inquiries", VALID, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(send_email.call_count, 2)
        admin_email, customer_email = (call.args for call in send_email.call_args_list)
        self.assertIn("New Petals Paradise inquiry", admin_email[0])
        self.assertIn("Amina", admin_email[1])
        self.assertIn("wedding", admin_email[1].lower())
        self.assertEqual(admin_email[2], ["ndicuvictoria@gmail.com"])
        self.assertEqual(send_email.call_args_list[0].kwargs["context"], f"staff inquiry alert {response.data['id']}")
        self.assertEqual(customer_email[0], "We received your Petals Paradise inquiry")
        self.assertEqual(customer_email[2], ["amina@example.com"])
        self.assertEqual(send_email.call_args_list[1].kwargs["context"], f"inquiry acknowledgement {response.data['id']}")

    @override_settings(ORDER_ALERT_EMAILS=["orders@example.test"], INQUIRY_ALERT_EMAILS=[])
    def test_inquiry_alerts_fall_back_to_order_alert_recipients(self):
        response = self.client.post("/api/v1/inquiries", VALID, format="json")
        inquiry = Inquiry.objects.get(pk=response.data["id"])

        with patch("apps.inquiries.notifications.send_transactional_email_async") as send_email:
            from apps.inquiries.notifications import notify_inquiry_received

            notify_inquiry_received(inquiry)

        self.assertEqual(send_email.call_count, 2)
        self.assertEqual(send_email.call_args_list[0].args[2], ["orders@example.test"])

    def test_email_is_required_for_an_inquiry(self):
        response = self.client.post("/api/v1/inquiries", {key: value for key, value in VALID.items() if key != "email"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data["error"]["fields"])
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_staff_can_list_update_status_and_assign_but_not_edit_customer_fields(self):
        iid = self.client.post("/api/v1/inquiries", VALID, format="json").data["id"]
        self.login(self.staff)
        self.assertEqual(len(self.client.get("/api/v1/admin/inquiries").data["results"]), 1)
        r = self.client.patch(
            f"/api/v1/admin/inquiries/{iid}", {"status": "contacted", "assigned_to": self.staff.pk, "name": "Hacked"}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.content)
        inq = Inquiry.objects.get(pk=iid)
        self.assertEqual((inq.status, inq.assigned_to, inq.name), ("contacted", self.staff, "Amina"))

    def test_cannot_assign_to_non_staff(self):
        iid = self.client.post("/api/v1/inquiries", VALID, format="json").data["id"]
        self.login(self.staff)
        r = self.client.patch(f"/api/v1/admin/inquiries/{iid}", {"assigned_to": self.customer.pk}, format="json")
        self.assertEqual(r.status_code, 400)
