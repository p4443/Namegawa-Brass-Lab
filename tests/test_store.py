import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from app import create_app


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temporary_directory.name)
        self.store_file = self.base_path / "store.json"
        self.product_file = self.base_path / "trumpet-metronome.zip"
        self.product_file.write_bytes(b"test-product")
        self.environment = {
            "DATABASE_URL": "",
            "EDITOR_PASSWORD": "editor-secret",
            "STRIPE_SECRET_KEY": "sk_test_example",
            "STRIPE_WEBHOOK_SECRET": "whsec_example",
            "STRIPE_METRONOME_PRICE_ID": "price_example",
            "DOWNLOAD_TOKEN_SECRET": "download-secret",
            "METRONOME_PRICE_YEN": "980",
            "PUBLIC_SITE_URL": "https://example.com",
        }
        self.environment_patch = patch.dict(os.environ, self.environment, clear=False)
        self.environment_patch.start()
        self.app = create_app(
            updates_file=self.base_path / "updates.txt",
            database_url="",
            store_file=self.store_file,
            product_file=self.product_file,
        )
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.environment_patch.stop()
        self.temporary_directory.cleanup()

    def enable_store(self):
        return self.client.put(
            "/api/store/product",
            json={"enabled": True},
            headers={"X-Editor-Password": "editor-secret"},
        )

    def stripe_module(self, payment_status="paid"):
        module = types.ModuleType("stripe")
        module.api_key = ""
        module.checkout = types.SimpleNamespace(
            Session=types.SimpleNamespace(
                create=MagicMock(return_value=types.SimpleNamespace(url="https://checkout.example/session")),
                retrieve=MagicMock(
                    return_value=types.SimpleNamespace(
                        id="cs_test_paid",
                        payment_status=payment_status,
                        metadata={"product_id": "trumpet-metronome"},
                    )
                ),
            )
        )
        module.Webhook = types.SimpleNamespace(
            construct_event=MagicMock(
                return_value={"id": "evt_test", "type": "checkout.session.completed"}
            )
        )
        return module

    def test_store_defaults_to_disabled_and_requires_editor_password(self):
        product = self.client.get("/api/store/product")
        self.assertEqual(product.status_code, 200)
        self.assertFalse(product.get_json()["enabled"])
        self.assertFalse(product.get_json()["checkout_available"])

        unauthorized = self.client.put(
            "/api/store/product",
            json={"enabled": True},
            headers={"X-Editor-Password": "wrong"},
        )
        self.assertEqual(unauthorized.status_code, 401)

        enabled = self.enable_store()
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.get_json()["enabled"])
        self.assertTrue(enabled.get_json()["checkout_available"])

    def test_checkout_is_blocked_when_store_is_disabled(self):
        response = self.client.post("/api/store/checkout")
        self.assertEqual(response.status_code, 403)

    def test_enabled_store_creates_stripe_checkout(self):
        self.enable_store()
        stripe = self.stripe_module()
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post("/api/store/checkout")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["checkout_url"], "https://checkout.example/session")
        create_kwargs = stripe.checkout.Session.create.call_args.kwargs
        self.assertEqual(create_kwargs["line_items"][0]["price"], "price_example")
        self.assertIn("{CHECKOUT_SESSION_ID}", create_kwargs["success_url"])

    def test_paid_checkout_creates_download_link_and_serves_product(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )

        self.assertEqual(response.status_code, 200)
        download_path = urlparse(response.get_json()["download_url"]).path
        download = self.client.get(download_path)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"test-product")
        self.assertIn("trumpet-practice-metronome.zip", download.headers["Content-Disposition"])
        download.close()

    def test_unpaid_checkout_cannot_download(self):
        stripe = self.stripe_module(payment_status="unpaid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_unpaid"}
            )
        self.assertEqual(response.status_code, 403)

    def test_webhook_is_verified_by_stripe(self):
        stripe = self.stripe_module()
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/webhook",
                data=b"{}",
                headers={"Stripe-Signature": "signed"},
            )
        self.assertEqual(response.status_code, 200)
        stripe.Webhook.construct_event.assert_called_once_with(
            b"{}", "signed", "whsec_example"
        )


if __name__ == "__main__":
    unittest.main()
