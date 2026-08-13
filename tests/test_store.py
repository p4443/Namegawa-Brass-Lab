import os
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse
from zipfile import ZIP_DEFLATED, ZipFile

from itsdangerous import SignatureExpired, URLSafeTimedSerializer

import build_product as product_builder
from app import (
    STORE_PAYMENT_CACHE_TTL_SECONDS,
    STORE_REISSUE_MAX_AGE_SECONDS,
    create_app,
)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temporary_directory.name)
        self.store_file = self.base_path / "store.json"
        self.product_file = self.base_path / "trumpet-metronome.zip"
        with ZipFile(self.product_file, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("index.html", "<html>test product</html>")
            archive.writestr("README.txt", "test product")
        self.product_bytes = self.product_file.read_bytes()
        self.environment = {
            "DATABASE_URL": "",
            "EDITOR_PASSWORD": "editor-secret",
            "STRIPE_SECRET_KEY": "sk_test_example",
            "STRIPE_WEBHOOK_SECRET": "whsec_example",
            "STRIPE_METRONOME_PRICE_ID": "price_example",
            "DOWNLOAD_TOKEN_SECRET": "download-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "METRONOME_PRICE_YEN": "500",
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

    def stripe_module(
        self,
        payment_status="paid",
        created=None,
        amount_total=500,
        currency="jpy",
        refunded=False,
        amount_refunded=0,
        disputed=False,
        livemode=False,
    ):
        if created is None:
            created = int(time.time())
        latest_charge = types.SimpleNamespace(
            status="succeeded",
            paid=True,
            captured=True,
            amount_captured=amount_total,
            amount_refunded=amount_refunded,
            refunded=refunded,
            disputed=disputed,
            currency=currency,
            livemode=livemode,
        )
        payment_intent = types.SimpleNamespace(
            status="succeeded",
            amount_received=amount_total,
            currency=currency,
            livemode=livemode,
            latest_charge=latest_charge,
        )
        module = types.ModuleType("stripe")
        module.api_key = ""
        module.checkout = types.SimpleNamespace(
            Session=types.SimpleNamespace(
                create=MagicMock(return_value=types.SimpleNamespace(url="https://checkout.example/session")),
                retrieve=MagicMock(
                    return_value=types.SimpleNamespace(
                        id="cs_test_paid",
                        mode="payment",
                        status="complete",
                        payment_status=payment_status,
                        amount_total=amount_total,
                        currency=currency,
                        livemode=livemode,
                        payment_intent=payment_intent,
                        created=created,
                        metadata={
                            "product_id": "trumpet-metronome",
                            "price_yen": "500",
                            "price_id": "price_example",
                        },
                    )
                ),
            )
        )
        module.Price = types.SimpleNamespace(
            retrieve=MagicMock(
                return_value=types.SimpleNamespace(
                    active=True,
                    type="one_time",
                    currency="jpy",
                    unit_amount=500,
                    livemode=livemode,
                )
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

    def test_legal_page_displays_store_terms_and_configured_price(self):
        response = self.client.get("/legal/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("特定商取引法に基づく表記", response.get_data(as_text=True))
        self.assertIn("返金・キャンセル方針", response.get_data(as_text=True))
        self.assertIn("500円（税込）", response.get_data(as_text=True))

    def test_lesson_hides_download_link_until_purchase_is_verified(self):
        response = self.client.get("/lesson/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="app-download-link" href="#" hidden', html)
        self.assertIn(".app-download-link[hidden]", html)
        self.assertIn("display: none;", html)

    def test_checkout_is_blocked_when_store_is_disabled(self):
        response = self.client.post("/api/store/checkout")
        self.assertEqual(response.status_code, 403)

    def test_store_health_requires_editor_password(self):
        response = self.client.get("/api/store/health")

        self.assertEqual(response.status_code, 401)

    def test_store_health_verifies_test_price_without_exposing_secrets(self):
        stripe = self.stripe_module()
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.get(
                "/api/store/health",
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result["ready"])
        self.assertFalse(result["production_ready"])
        self.assertEqual(result["stripe_mode"], "test")
        self.assertNotIn("secret", response.get_data(as_text=True).lower())
        stripe.Price.retrieve.assert_called_once_with("price_example")

    def test_store_health_rejects_price_amount_mismatch(self):
        stripe = self.stripe_module()
        stripe.Price.retrieve.return_value.unit_amount = 1
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.get(
                "/api/store/health",
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()["checks"]["stripe_price"])

    def test_store_health_reports_live_configuration_as_production_ready(self):
        stripe = self.stripe_module(livemode=True)
        with patch.dict(
            os.environ,
            {"STRIPE_SECRET_KEY": "sk_live_example"},
        ), patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.get(
                "/api/store/health",
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["production_ready"])
        self.assertEqual(response.get_json()["stripe_mode"], "live")

    def test_invalid_public_site_url_blocks_checkout(self):
        self.enable_store()
        stripe = self.stripe_module()
        with patch.dict(
            os.environ,
            {"PUBLIC_SITE_URL": "https://example.com/untrusted-path"},
        ), patch.dict(sys.modules, {"stripe": stripe}):
            product = self.client.get("/api/store/product")
            checkout = self.client.post(
                "/api/store/checkout",
                json={
                    "checkout_request_id": "66e59f96-394e-4df1-9b0b-e80b888d90fc"
                },
            )

        self.assertFalse(product.get_json()["checkout_available"])
        self.assertEqual(checkout.status_code, 503)
        stripe.checkout.Session.create.assert_not_called()

    def test_enabled_store_creates_stripe_checkout(self):
        self.enable_store()
        stripe = self.stripe_module()
        checkout_request_id = "66e59f96-394e-4df1-9b0b-e80b888d90fc"
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/checkout",
                json={"checkout_request_id": checkout_request_id},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["checkout_url"], "https://checkout.example/session")
        create_kwargs = stripe.checkout.Session.create.call_args.kwargs
        self.assertEqual(create_kwargs["line_items"][0]["price"], "price_example")
        self.assertIn("{CHECKOUT_SESSION_ID}", create_kwargs["success_url"])
        self.assertEqual(create_kwargs["client_reference_id"], checkout_request_id)
        self.assertEqual(
            create_kwargs["idempotency_key"],
            f"trumpet-metronome:{checkout_request_id}",
        )
        self.assertEqual(create_kwargs["metadata"]["price_yen"], "500")

    def test_checkout_is_blocked_when_stripe_price_does_not_match(self):
        self.enable_store()
        stripe = self.stripe_module()
        stripe.Price.retrieve.return_value.unit_amount = 1
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/checkout",
                json={
                    "checkout_request_id": "66e59f96-394e-4df1-9b0b-e80b888d90fc"
                },
            )

        self.assertEqual(response.status_code, 503)
        stripe.checkout.Session.create.assert_not_called()

    def test_checkout_rejects_invalid_idempotency_request_id(self):
        self.enable_store()
        stripe = self.stripe_module()
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/checkout",
                json={"checkout_request_id": "not-a-uuid"},
            )

        self.assertEqual(response.status_code, 400)
        stripe.checkout.Session.create.assert_not_called()

    def test_paid_checkout_creates_download_link_and_serves_product(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )

        self.assertEqual(response.status_code, 200)
        download_url = urlparse(response.get_json()["download_url"])
        self.assertEqual(parse_qs(download_url.query), {})
        download_path = download_url.path
        download = self.client.get(
            download_path,
            headers={"Origin": "https://example.com"},
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, self.product_bytes)
        self.assertEqual(
            download.headers["Access-Control-Allow-Origin"],
            "https://example.com",
        )
        self.assertEqual(download.headers["Cache-Control"], "private, max-age=3600")
        self.assertEqual(download.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("trumpet-practice-metronome.zip", download.headers["Content-Disposition"])
        download.close()

    def test_reissuing_same_purchase_uses_cached_stripe_verification(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            first = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )
            second = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        stripe.checkout.Session.retrieve.assert_called_once_with(
            "cs_test_paid",
            expand=["payment_intent.latest_charge"],
        )

    def test_concurrent_reissues_share_one_stripe_verification(self):
        request_count = 12
        start = threading.Barrier(request_count)
        stripe = self.stripe_module(payment_status="paid")

        def request_download_link():
            start.wait()
            with self.app.test_client() as client:
                return client.post(
                    "/api/store/download-link",
                    json={"session_id": "cs_test_paid"},
                ).status_code

        with patch.dict(sys.modules, {"stripe": stripe}):
            with ThreadPoolExecutor(max_workers=request_count) as executor:
                requests = [
                    executor.submit(request_download_link)
                    for request_number in range(request_count)
                ]
                statuses = [request.result() for request in requests]

        self.assertEqual(statuses, [200] * request_count)
        stripe.checkout.Session.retrieve.assert_called_once_with(
            "cs_test_paid",
            expand=["payment_intent.latest_charge"],
        )

    def test_product_download_supports_range_resume(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            link = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )

        download_path = urlparse(link.get_json()["download_url"]).path
        download = self.client.get(download_path, headers={"Range": "bytes=0-3"})

        self.assertEqual(download.status_code, 206)
        self.assertEqual(download.data, self.product_bytes[:4])
        self.assertEqual(download.headers["Accept-Ranges"], "bytes")
        download.close()

    def test_product_download_head_returns_metadata_without_body(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            link = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )

        download_path = urlparse(link.get_json()["download_url"]).path
        download = self.client.head(
            download_path,
            headers={"Origin": "https://example.com"},
        )

        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"")
        self.assertEqual(download.headers["Content-Type"], "application/zip")
        self.assertEqual(int(download.headers["Content-Length"]), len(self.product_bytes))
        self.assertEqual(
            download.headers["Access-Control-Allow-Origin"],
            "https://example.com",
        )
        download.close()

    def test_store_cors_rejects_untrusted_browser_origin(self):
        response = self.client.get(
            "/api/store/product",
            headers={"Origin": "https://attacker.example"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_product_download_rejects_unsatisfiable_range(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            link = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )

        download_path = urlparse(link.get_json()["download_url"]).path
        download = self.client.get(
            download_path,
            headers={"Range": f"bytes={len(self.product_bytes) + 100}-"},
        )

        self.assertEqual(download.status_code, 416)
        download.close()

    def test_corrupt_product_is_not_offered_or_verified_with_stripe(self):
        self.product_file.write_bytes(b"not-a-zip")
        stripe = self.stripe_module(payment_status="paid")

        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )

        self.assertEqual(response.status_code, 503)
        stripe.checkout.Session.retrieve.assert_not_called()

    def test_product_missing_required_file_is_not_offered(self):
        with ZipFile(self.product_file, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("index.html", "<html>incomplete</html>")
        stripe = self.stripe_module(payment_status="paid")

        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )

        self.assertEqual(response.status_code, 503)
        stripe.checkout.Session.retrieve.assert_not_called()

    def test_product_corrupted_after_link_issue_is_not_downloaded(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            link = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )
        self.product_file.write_bytes(b"corrupt-after-issue")

        download_path = urlparse(link.get_json()["download_url"]).path
        download = self.client.get(download_path)

        self.assertEqual(download.status_code, 503)
        self.assertEqual(download.get_json()["error"], "商品ファイルを確認中です。")

    def test_download_rejects_tampered_token(self):
        response = self.client.get("/api/store/download/tampered-token")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_download_link_rejects_malformed_checkout_session(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link",
                json={"session_id": "cs_test_paid/../other"},
            )

        self.assertEqual(response.status_code, 400)
        stripe.checkout.Session.retrieve.assert_not_called()

    def test_expired_download_redirects_to_automatic_reissue_mode(self):
        serializer = URLSafeTimedSerializer(
            "download-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            salt="metronome-download",
        )
        expired = SignatureExpired(
            "expired",
            payload=serializer.dump_payload(
                {
                    "product_id": "trumpet-metronome",
                    "session_id": "cs_test_paid",
                }
            ),
        )
        with patch.object(
            URLSafeTimedSerializer,
            "loads",
            side_effect=expired,
        ):
            response = self.client.get("/api/store/download/expired")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            "/lesson/?purchase=reissue&session_id=cs_test_paid#practice-apps-title",
        )

    def test_expired_download_with_invalid_payload_returns_gone(self):
        expired = SignatureExpired("expired", payload=None)
        with patch.object(
            URLSafeTimedSerializer,
            "loads",
            side_effect=expired,
        ):
            response = self.client.get("/api/store/download/expired")

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.get_json()["error"], "ダウンロード期限が切れました。")

    def test_purchase_older_than_reissue_window_cannot_get_new_link(self):
        stripe = self.stripe_module(
            payment_status="paid",
            created=int(time.time()) - STORE_REISSUE_MAX_AGE_SECONDS - 1,
        )
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_old"}
            )

        self.assertEqual(response.status_code, 403)

    def test_wrong_payment_amount_cannot_download(self):
        stripe = self.stripe_module(amount_total=1)
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_wrong_amount"}
            )

        self.assertEqual(response.status_code, 403)

    def test_wrong_payment_currency_cannot_download(self):
        stripe = self.stripe_module(currency="usd")
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_wrong_currency"}
            )

        self.assertEqual(response.status_code, 403)

    def test_refunded_payment_cannot_download(self):
        stripe = self.stripe_module(refunded=True, amount_refunded=500)
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_refunded"}
            )

        self.assertEqual(response.status_code, 403)

    def test_existing_download_link_stops_after_refund(self):
        stripe = self.stripe_module()
        initial_monotonic = time.monotonic()
        with patch.dict(sys.modules, {"stripe": stripe}):
            link = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_refund_later"}
            )
            charge = (
                stripe.checkout.Session.retrieve.return_value.payment_intent.latest_charge
            )
            charge.refunded = True
            charge.amount_refunded = 500
            with patch(
                "app.time.monotonic",
                return_value=initial_monotonic + STORE_PAYMENT_CACHE_TTL_SECONDS + 1,
            ):
                download_path = urlparse(link.get_json()["download_url"]).path
                download = self.client.get(download_path)

        self.assertEqual(link.status_code, 200)
        self.assertEqual(download.status_code, 403)

    def test_price_change_does_not_block_purchase_with_recorded_price(self):
        stripe = self.stripe_module(amount_total=500)
        with patch.dict(
            os.environ,
            {"METRONOME_PRICE_YEN": "1200"},
        ), patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_old_price"}
            )

        self.assertEqual(response.status_code, 200)

    def test_partially_refunded_payment_cannot_download(self):
        stripe = self.stripe_module(amount_refunded=100)
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_partial_refund"}
            )

        self.assertEqual(response.status_code, 403)

    def test_disputed_payment_cannot_download(self):
        stripe = self.stripe_module(disputed=True)
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_disputed"}
            )

        self.assertEqual(response.status_code, 403)

    def test_payment_from_wrong_stripe_mode_cannot_download(self):
        stripe = self.stripe_module(livemode=True)
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_live_wrong_mode"}
            )

        self.assertEqual(response.status_code, 403)

    def test_unexpanded_payment_intent_cannot_download(self):
        stripe = self.stripe_module()
        stripe.checkout.Session.retrieve.return_value.payment_intent = "pi_test"
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_unexpanded"}
            )

        self.assertEqual(response.status_code, 403)

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

    def test_product_builder_atomically_creates_valid_archive(self):
        source_file = self.base_path / "source.html"
        output_file = self.base_path / "built-product.zip"
        source_file.write_text("<html>built product</html>", encoding="utf-8")

        with patch.object(product_builder, "SOURCE_FILE", source_file), patch.object(
            product_builder, "OUTPUT_FILE", output_file
        ):
            result = product_builder.build_product()

        self.assertEqual(result, output_file)
        with ZipFile(output_file) as archive:
            self.assertEqual(archive.testzip(), None)
            self.assertIn("index.html", archive.namelist())
            self.assertIn("README.txt", archive.namelist())

    def test_product_builder_keeps_existing_archive_when_source_is_missing(self):
        source_file = self.base_path / "missing.html"
        output_file = self.base_path / "existing-product.zip"
        output_file.write_bytes(b"existing-product")

        with patch.object(product_builder, "SOURCE_FILE", source_file), patch.object(
            product_builder, "OUTPUT_FILE", output_file
        ):
            with self.assertRaises(FileNotFoundError):
                product_builder.build_product()

        self.assertEqual(output_file.read_bytes(), b"existing-product")


if __name__ == "__main__":
    unittest.main()
