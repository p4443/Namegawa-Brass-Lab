import io
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
    STORE_DOWNLOAD_LIMIT,
    STORE_PAYMENT_CACHE_TTL_SECONDS,
    STORE_REISSUE_MAX_AGE_SECONDS,
    create_app,
)


class StoreTest(unittest.TestCase):
    def test_store_setup_wizard_embedded_python_is_valid(self):
        script = (Path(__file__).parents[1] / "setup-store-env.sh").read_text()
        start = script.index(
            "import os\nimport sys\n\nimport stripe",
            script.index("Stripe接続確認"),
        )
        end_marker = "\nprint(generated_secret)"
        end = script.index(end_marker, start) + len(end_marker)

        compile(script[start:end], "stripe-validation", "exec")

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
            "STRIPE_FLOW_HARMONY_PRICE_ID": "price_flow_harmony",
            "DOWNLOAD_TOKEN_SECRET": "download-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "METRONOME_PRICE_YEN": "500",
            "FLOW_HARMONY_PRICE_YEN": "1000",
            "INVOICE_REGISTRATION_NUMBER": "T1234567890123",
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
        self.store_file.write_text('{"enabled": true}\n', encoding="utf-8")

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
            receipt_number="1234-5678",
        )
        payment_intent = types.SimpleNamespace(
            id="pi_test_paid",
            status="succeeded",
            amount_received=amount_total,
            currency=currency,
            livemode=livemode,
            latest_charge=latest_charge,
        )
        checkout = types.SimpleNamespace(
            id="cs_test_paid",
            mode="payment",
            status="complete",
            payment_status=payment_status,
            amount_total=amount_total,
            currency=currency,
            livemode=livemode,
            payment_intent=payment_intent,
            customer_details=types.SimpleNamespace(email="buyer@example.com"),
            created=created,
            metadata={
                "product_id": "trumpet-metronome",
                "price_yen": "500",
                "price_id": "price_example",
            },
        )
        session_list = types.SimpleNamespace(
            auto_paging_iter=MagicMock(return_value=iter([checkout]))
        )
        module = types.ModuleType("stripe")
        module.api_key = ""
        module.checkout = types.SimpleNamespace(
            Session=types.SimpleNamespace(
                create=MagicMock(return_value=types.SimpleNamespace(url="https://checkout.example/session")),
                retrieve=MagicMock(return_value=checkout),
                list=MagicMock(return_value=session_list),
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

        stripe = self.stripe_module()
        with patch.dict(sys.modules, {"stripe": stripe}):
            enabled = self.client.put(
                "/api/store/product",
                json={"enabled": True},
                headers={"X-Editor-Password": "editor-secret"},
            )
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.get_json()["enabled"])
        self.assertTrue(enabled.get_json()["checkout_available"])

    def test_each_app_sale_setting_can_be_updated_independently(self):
        stripe = self.stripe_module(amount_total=1000)
        with patch.dict(sys.modules, {"stripe": stripe}):
            transpose = self.client.put(
                "/api/store/trumpet-transpose-lab/product",
                json={"enabled": False},
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(transpose.status_code, 200)
        self.assertFalse(transpose.get_json()["enabled"])
        self.assertFalse(self.client.get("/api/store/product").get_json()["enabled"])

        stripe = self.stripe_module()
        with patch.dict(sys.modules, {"stripe": stripe}):
            metronome = self.client.put(
                "/api/store/product",
                json={"enabled": True},
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertTrue(metronome.get_json()["enabled"])
        self.assertFalse(
            self.client.get("/api/store/trumpet-transpose-lab/product").get_json()["enabled"]
        )

    def test_enabled_store_hides_checkout_when_stripe_is_unavailable(self):
        self.enable_store()
        stripe = self.stripe_module()
        stripe.Price.retrieve.side_effect = RuntimeError("expired key")

        with patch.dict(sys.modules, {"stripe": stripe}):
            product = self.client.get("/api/store/product")

        self.assertEqual(product.status_code, 200)
        self.assertTrue(product.get_json()["enabled"])
        self.assertFalse(product.get_json()["checkout_available"])

    def test_legal_page_displays_store_terms_and_configured_price(self):
        response = self.client.get("/legal/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("特定商取引法に基づく表記", html)
        self.assertIn("返金・キャンセル方針", html)
        self.assertIn("500円（税込）", html)
        self.assertIn("1,000円（税込）", html)
        self.assertIn("月額料金、自動更新、継続課金はありません", html)
        self.assertIn("購入時点のバージョンを期間の定めなく利用", html)
        self.assertIn("新バージョン、OS・ブラウザの仕様変更への対応は購入代金に含まれず", html)

    def test_products_hides_download_link_until_purchase_is_verified(self):
        response = self.client.get("/products/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="app-download-link" href="#" hidden', html)
        self.assertIn(".download-link[hidden]", html)
        self.assertIn("display: none;", html)
        self.assertEqual(
            html.count("月額料金・自動更新なし。購入時点版を期限なく利用可能"),
            2,
        )

    def test_download_guide_explains_one_time_purchase_terms(self):
        response = self.client.get("/download-guide/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("お支払いは購入時の1回のみで、月額料金や自動更新はありません", html)
        self.assertIn("購入時点のバージョンを期限なく利用できます", html)
        self.assertIn("ZIPファイルはご自身で保管・バックアップ", html)
        self.assertIn('href="../legal/#license"', html)

    def test_trumpet_transpose_lab_free_version_is_available(self):
        response = self.client.get("/trumpet-transpose-lab/")
        html = response.get_data(as_text=True)
        app_response = self.client.get("/trumpet-transpose-lab/app.mjs")
        app_javascript = app_response.get_data(as_text=True)
        app_response.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Trumpet Transpose Lab", html)
        self.assertIn('id="capture"', html)
        self.assertIn('id="score"', html)
        self.assertIn('id="edit"', html)
        self.assertIn('id="noteDuration"', html)
        self.assertIn('id="wavExport"', html)
        self.assertIn('id="midiExport"', html)
        self.assertIn('id="xmlExport"', html)
        self.assertEqual(app_response.status_code, 200)
        self.assertIn("location.protocol !== 'file:'", app_javascript)
        self.assertIn("button.disabled = isFreeMode", app_javascript)
        self.assertIn("無料Web版では保存できません。", app_javascript)
        self.assertIn("transcribeMonophonic", app_javascript)
        self.assertIn("trumpet-transpose-lab-v2.mid", app_javascript)
        self.assertIn("trumpet-transpose-lab-v2.musicxml", app_javascript)
        self.assertIn("function scorePitchPosition", app_javascript)
        self.assertIn("function inferredKeySignature", app_javascript)
        self.assertIn("const signaturePositions", app_javascript)
        self.assertIn("runSilentCountIn", app_javascript)
        self.assertNotIn("playMetronomeClick", app_javascript)
        self.assertNotIn("indexedDB", app_javascript)
        self.assertNotIn('id="bluetoothBtn"', html)
        self.assertNotIn('id="chordDisplay"', html)
        self.assertNotIn('id="manualHarmonyPanel"', html)
        self.assertNotIn("Counterline", app_javascript)
        self.assertNotIn('<score-part id="P2">', app_javascript)
        self.assertEqual(response.headers["Cache-Control"], "no-store, no-cache, must-revalidate, max-age=0")
        legacy = self.client.get("/flow-harmony/?mode=free")
        self.assertEqual(legacy.status_code, 308)
        self.assertEqual(legacy.headers["Location"], "/trumpet-transpose-lab/?mode=free")
        self.assertEqual(self.client.get("/trumpet-transpose-lab/index.html").status_code, 404)

    def test_products_offers_free_and_offline_flow_harmony(self):
        response = self.client.get("/products/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('../trumpet-transpose-lab/?mode=free', html)
        self.assertIn('id="flow-purchase-button" type="button" disabled>販売状況を確認中', html)
        self.assertIn("録音したフレーズを自動採譜し、音高・音価・タイミングを編集", html)
        self.assertNotIn("allow=\"microphone; autoplay; bluetooth\"", html)
        self.assertIn("無料Web版は録音・採譜・編集に対応（データ保存不可）", html)
        self.assertIn('requestStore("trumpet-transpose-lab/checkout"', html)

    def test_products_separates_free_web_and_one_time_zip_apps(self):
        response = self.client.get("/products/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('<span class="product-label">無料Web版</span>'), 2)
        self.assertEqual(html.count("<h3>買い切りアプリ版（ZIP）</h3>"), 2)
        self.assertEqual(html.count("追加料金なしの1回払い"), 2)

    def test_products_stack_vertically_on_smartphones(self):
        response = self.client.get("/products/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '<span class="mobile-scroll-copy">音楽アプリは下へスクロール</span>',
            html,
        )
        self.assertIn(".desktop-scroll-copy, .carousel-controls { display: none; }", html)
        self.assertIn("scroll-snap-type: none;", html)
        self.assertIn(".product-feature { width: 100%; min-width: 0;", html)

    def test_lesson_page_links_to_free_flow_harmony(self):
        response = self.client.get("/lesson/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('../trumpet-transpose-lab/?mode=free', html)
        self.assertIn("Trumpet Transpose Lab", html)
        self.assertIn("無料で体験", html)

    def test_flow_harmony_product_uses_one_thousand_yen_price(self):
        with patch.dict(os.environ, {"STRIPE_FLOW_HARMONY_PRICE_ID": ""}):
            response = self.client.get("/api/store/trumpet-transpose-lab/product")

        self.assertEqual(response.status_code, 200)
        product = response.get_json()
        self.assertEqual(product["product_id"], "trumpet-transpose-lab")
        self.assertEqual(product["name"], "Trumpet Transpose Lab オフライン版")
        self.assertEqual(product["price_yen"], 1000)
        self.assertFalse(product["checkout_available"])

    def test_flow_harmony_checkout_uses_configured_price(self):
        stripe = self.stripe_module(amount_total=1000)
        stripe.Price.retrieve.return_value.unit_amount = 1000
        checkout_request_id = "66e59f96-394e-4df1-9b0b-e80b888d90fc"
        with patch.dict(
            os.environ,
            {
                "STRIPE_FLOW_HARMONY_PRICE_ID": "price_flow_harmony",
                "FLOW_HARMONY_PRICE_YEN": "1000",
            },
        ), patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/trumpet-transpose-lab/checkout",
                json={"checkout_request_id": checkout_request_id},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["checkout_url"], "https://checkout.example/session")
        stripe.checkout.Session.create.assert_called_once()
        checkout_arguments = stripe.checkout.Session.create.call_args.kwargs
        self.assertEqual(checkout_arguments["line_items"], [{"price": "price_flow_harmony", "quantity": 1}])
        self.assertEqual(checkout_arguments["metadata"]["product_id"], "trumpet-transpose-lab")
        self.assertEqual(checkout_arguments["metadata"]["price_yen"], "1000")
        self.assertEqual(checkout_arguments["locale"], "ja")
        self.assertEqual(checkout_arguments["customer_creation"], "always")
        invoice_data = checkout_arguments["invoice_creation"]["invoice_data"]
        self.assertTrue(checkout_arguments["invoice_creation"]["enabled"])
        self.assertEqual(
            invoice_data["custom_fields"],
            [
                {
                    "name": "適格請求書発行事業者登録番号",
                    "value": "T1234567890123",
                }
            ],
        )

    def test_paid_flow_harmony_session_downloads_personalized_archive(self):
        stripe = self.stripe_module(amount_total=1000)
        checkout = stripe.checkout.Session.retrieve.return_value
        checkout.metadata = {
            "product_id": "trumpet-transpose-lab",
            "price_yen": "1000",
            "price_id": "price_flow_harmony",
        }
        stripe.Price.retrieve.return_value.unit_amount = 1000
        with patch.dict(sys.modules, {"stripe": stripe}):
            link_response = self.client.post(
                "/api/store/trumpet-transpose-lab/download-link",
                json={"session_id": "cs_test_paid"},
            )
            download_response = self.client.get(
                urlparse(link_response.get_json()["download_url"]).path
            )

        self.assertEqual(link_response.status_code, 200)
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response.mimetype, "application/zip")
        with ZipFile(io.BytesIO(download_response.data)) as archive:
            self.assertIn("index.html", archive.namelist())
            license_text = archive.read("LICENSE.txt").decode("utf-8")
        self.assertIn("Trumpet Transpose Lab オフライン版 利用ライセンス", license_text)
        self.assertIn("購入参照ID:", license_text)

    def test_legacy_flow_harmony_purchase_remains_downloadable(self):
        stripe = self.stripe_module(amount_total=1000)
        checkout = stripe.checkout.Session.retrieve.return_value
        checkout.metadata = {
            "product_id": "flow-harmony",
            "price_yen": "1000",
            "price_id": "price_flow_harmony",
        }
        stripe.Price.retrieve.return_value.unit_amount = 1000
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/trumpet-transpose-lab/download-link",
                json={"session_id": "cs_test_legacy_paid"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "/api/store/trumpet-transpose-lab/download/",
            response.get_json()["download_url"],
        )

    def test_products_offers_secure_purchase_recovery(self):
        response = self.client.get("/products/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('<section class="purchase-recovery"', html)
        self.assertNotIn('<details class="purchase-recovery"', html)
        self.assertIn('id="app-recovery-email"', html)
        self.assertIn('id="app-recovery-receipt"', html)
        self.assertIn('requestStore("recover-download"', html)

    def test_products_changes_purchase_button_after_purchase_is_verified(self):
        response = self.client.get("/products/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('appPurchaseButton.textContent = "ダウンロード版を使用する"', html)
        self.assertIn("if (!appDownloadLink.hidden)", html)
        self.assertIn("appDownloadLink.click()", html)

    def test_products_links_to_separate_download_guide(self):
        products_response = self.client.get("/products/")
        products_html = products_response.get_data(as_text=True)

        self.assertEqual(products_response.status_code, 200)
        self.assertEqual(products_html.count('href="../download-guide/"'), 2)

        guide_response = self.client.get("/download-guide/")
        guide_html = guide_response.get_data(as_text=True)

        self.assertEqual(guide_response.status_code, 200)
        self.assertIn("ダウンロード・使い方ガイド", guide_html)
        self.assertIn("使いたいアプリを選ぶ", guide_html)
        self.assertIn("購入ボタンを押す", guide_html)
        self.assertIn("ZIPファイルをダウンロードする", guide_html)
        self.assertIn("ZIPファイルを開く", guide_html)
        self.assertIn("アプリを開く", guide_html)
        self.assertIn("Androidの場合", guide_html)
        self.assertIn("iPhone・iPadの場合", guide_html)
        self.assertEqual(guide_html.count('<details class="device-note">'), 4)

    def test_products_retries_download_link_after_checkout_propagation_delay(self):
        response = self.client.get("/products/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("async function provideDownloadAfterCheckout(sessionId)", html)
        self.assertIn("const maximumAttempts = 5", html)
        self.assertIn("setTimeout(resolve, 1500)", html)
        self.assertIn('if (purchaseMode === "success")', html)
        self.assertIn("await provideDownloadAfterCheckout(sessionId)", html)

    def test_checkout_is_blocked_when_store_is_disabled(self):
        response = self.client.post("/api/store/checkout")
        self.assertEqual(response.status_code, 403)

    def test_store_health_requires_editor_password(self):
        response = self.client.get("/api/store/health")

        self.assertEqual(response.status_code, 401)

    def test_public_health_is_available_without_secrets(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_store_health_verifies_test_price_without_exposing_secrets(self):
        stripe = self.stripe_module()
        metronome_price = stripe.Price.retrieve.return_value
        flow_price = types.SimpleNamespace(
            active=True,
            type="one_time",
            currency="jpy",
            unit_amount=1000,
            livemode=False,
        )
        stripe.Price.retrieve.side_effect = lambda price_id: (
            flow_price if price_id == "price_flow_harmony" else metronome_price
        )
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
        self.assertTrue(result["checks"]["flow_harmony_stripe_price"])
        self.assertNotIn("secret", response.get_data(as_text=True).lower())
        self.assertEqual(
            [call.args[0] for call in stripe.Price.retrieve.call_args_list],
            ["price_example", "price_flow_harmony"],
        )

    def test_store_health_rejects_missing_flow_harmony_price(self):
        stripe = self.stripe_module()
        with patch.dict(
            os.environ,
            {"STRIPE_FLOW_HARMONY_PRICE_ID": ""},
        ), patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.get(
                "/api/store/health",
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(response.status_code, 503)
        result = response.get_json()
        self.assertFalse(result["checks"]["flow_harmony_configuration"])
        self.assertIn(
            "STRIPE_FLOW_HARMONY_PRICE_ID", result["invalid_configuration"]
        )

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

    def test_store_health_rejects_flow_harmony_price_amount_mismatch(self):
        stripe = self.stripe_module()
        metronome_price = stripe.Price.retrieve.return_value
        invalid_flow_price = types.SimpleNamespace(
            active=True,
            type="one_time",
            currency="jpy",
            unit_amount=1,
            livemode=False,
        )
        stripe.Price.retrieve.side_effect = lambda price_id: (
            invalid_flow_price
            if price_id == "price_flow_harmony"
            else metronome_price
        )
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.get(
                "/api/store/health",
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(
            response.get_json()["checks"]["flow_harmony_stripe_price"]
        )

    def test_store_health_reports_live_configuration_as_production_ready(self):
        stripe = self.stripe_module(livemode=True)
        metronome_price = stripe.Price.retrieve.return_value
        flow_price = types.SimpleNamespace(
            active=True,
            type="one_time",
            currency="jpy",
            unit_amount=1000,
            livemode=True,
        )
        stripe.Price.retrieve.side_effect = lambda price_id: (
            flow_price if price_id == "price_flow_harmony" else metronome_price
        )
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

    def test_github_pages_project_url_allows_checkout_and_cors(self):
        self.enable_store()
        stripe = self.stripe_module()
        with patch.dict(
            os.environ,
            {"PUBLIC_SITE_URL": "https://p4443.github.io/Namegawa-Brass-Lab"},
        ), patch.dict(sys.modules, {"stripe": stripe}):
            product = self.client.get(
                "/api/store/product",
                headers={"Origin": "https://p4443.github.io"},
            )
            checkout = self.client.post(
                "/api/store/checkout",
                json={
                    "checkout_request_id": "66e59f96-394e-4df1-9b0b-e80b888d90fc"
                },
            )

        self.assertTrue(product.get_json()["checkout_available"])
        self.assertEqual(
            product.headers["Access-Control-Allow-Origin"],
            "https://p4443.github.io",
        )
        self.assertEqual(checkout.status_code, 201)
        create_kwargs = stripe.checkout.Session.create.call_args.kwargs
        self.assertTrue(
            create_kwargs["success_url"].startswith(
                "https://p4443.github.io/Namegawa-Brass-Lab/products/"
            )
        )

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
        self.assertEqual(create_kwargs["locale"], "ja")
        self.assertEqual(create_kwargs["customer_creation"], "always")
        self.assertTrue(create_kwargs["invoice_creation"]["enabled"])
        self.assertEqual(
            create_kwargs["invoice_creation"]["invoice_data"]["custom_fields"][0],
            {
                "name": "適格請求書発行事業者登録番号",
                "value": "T1234567890123",
            },
        )

    def test_checkout_is_blocked_without_valid_invoice_registration_number(self):
        self.enable_store()
        stripe = self.stripe_module()
        with patch.dict(
            os.environ, {"INVOICE_REGISTRATION_NUMBER": "1234567890123"}
        ), patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/checkout",
                json={
                    "checkout_request_id": "66e59f96-394e-4df1-9b0b-e80b888d90fc"
                },
            )

        self.assertEqual(response.status_code, 503)
        stripe.checkout.Session.create.assert_not_called()

    def test_checkout_uses_public_invoice_registration_number_by_default(self):
        self.enable_store()
        stripe = self.stripe_module()
        with patch.dict(sys.modules, {"stripe": stripe}), patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop("INVOICE_REGISTRATION_NUMBER", None)
            response = self.client.post(
                "/api/store/checkout",
                json={
                    "checkout_request_id": "66e59f96-394e-4df1-9b0b-e80b888d90fc"
                },
            )

        self.assertEqual(response.status_code, 201)
        custom_field = stripe.checkout.Session.create.call_args.kwargs[
            "invoice_creation"
        ]["invoice_data"]["custom_fields"][0]
        self.assertEqual(custom_field["value"], "T2810320517878")

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

    def test_checkout_failure_returns_safe_diagnostic_code(self):
        self.enable_store()
        stripe = self.stripe_module()
        stripe_error = RuntimeError("secret Stripe response")
        stripe_error.code = "account_invalid"
        stripe.checkout.Session.create.side_effect = stripe_error
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/checkout",
                json={
                    "checkout_request_id": "66e59f96-394e-4df1-9b0b-e80b888d90fc"
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["diagnostic_code"], "account_invalid")
        self.assertNotIn("secret Stripe response", response.get_data(as_text=True))

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
        with ZipFile(io.BytesIO(download.data)) as archive:
            self.assertEqual(archive.read("index.html"), b"<html>test product</html>")
            license_text = archive.read("LICENSE.txt").decode("utf-8")
        self.assertIn("購入者本人のみ利用できます", license_text)
        self.assertIn("購入参照ID:", license_text)
        self.assertNotIn("cs_test_paid", license_text)
        self.assertEqual(
            download.headers["Access-Control-Allow-Origin"],
            "https://example.com",
        )
        self.assertEqual(download.headers["Cache-Control"], "private, max-age=3600")
        self.assertEqual(download.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("trumpet-practice-metronome.zip", download.headers["Content-Disposition"])
        download.close()

    def test_download_link_uses_forwarded_https_scheme(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/download-link",
                json={"session_id": "cs_test_paid"},
                headers={
                    "X-Forwarded-Host": "namegawa-brass-lab.onrender.com",
                    "X-Forwarded-Proto": "https",
                },
            )

        self.assertEqual(response.status_code, 200)
        download_url = urlparse(response.get_json()["download_url"])
        self.assertEqual(download_url.scheme, "https")
        self.assertEqual(download_url.netloc, "namegawa-brass-lab.onrender.com")

    def test_purchase_recovery_creates_download_link(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/recover-download",
                json={
                    "email": "buyer@example.com",
                    "receipt_number": "1234-5678",
                },
                headers={
                    "X-Forwarded-Host": "namegawa-brass-lab.onrender.com",
                    "X-Forwarded-Proto": "https",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["session_id"], "cs_test_paid")
        self.assertTrue(response.get_json()["download_url"].startswith("https://"))

    def test_purchase_recovery_rejects_mismatched_receipt(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/recover-download",
                json={
                    "email": "buyer@example.com",
                    "receipt_number": "9999-9999",
                },
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "購入情報を確認できませんでした。")

    def test_purchase_recovery_accepts_payment_intent_id(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            response = self.client.post(
                "/api/store/recover-download",
                json={
                    "email": "buyer@example.com",
                    "receipt_number": "pi_test_paid",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["session_id"], "cs_test_paid")

    def test_purchase_recovery_limits_attempts(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            for _ in range(5):
                response = self.client.post(
                    "/api/store/recover-download",
                    json={"email": "invalid", "receipt_number": "invalid"},
                )
                self.assertEqual(response.status_code, 400)
            blocked = self.client.post(
                "/api/store/recover-download",
                json={"email": "invalid", "receipt_number": "invalid"},
            )

        self.assertEqual(blocked.status_code, 429)

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
        self.assertGreater(int(download.headers["Content-Length"]), len(self.product_bytes))
        self.assertEqual(
            download.headers["Access-Control-Allow-Origin"],
            "https://example.com",
        )
        download.close()

    def test_download_limit_ignores_head_and_range_resume(self):
        stripe = self.stripe_module(payment_status="paid")
        with patch.dict(sys.modules, {"stripe": stripe}):
            link = self.client.post(
                "/api/store/download-link", json={"session_id": "cs_test_paid"}
            )

        download_path = urlparse(link.get_json()["download_url"]).path
        for _ in range(STORE_DOWNLOAD_LIMIT):
            download = self.client.get(download_path)
            self.assertEqual(download.status_code, 200)
            download.close()

        head = self.client.head(download_path)
        resumed = self.client.get(download_path, headers={"Range": "bytes=0-3"})
        blocked = self.client.get(download_path)

        self.assertEqual(head.status_code, 200)
        self.assertEqual(resumed.status_code, 206)
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("24時間後", blocked.get_json()["error"])
        head.close()
        resumed.close()
        blocked.close()

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
            headers={"Range": "bytes=1000000-"},
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
            "/products/?purchase=reissue&session_id=cs_test_paid#metronome",
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

    def test_tracked_metronome_archive_matches_web_app_source(self):
        with ZipFile(product_builder.OUTPUT_FILE) as archive:
            archived_app = archive.read("index.html")

        self.assertEqual(archived_app, product_builder.SOURCE_FILE.read_bytes())

    def test_metronome_bpm_uses_quarter_note_as_one_beat(self):
        html = product_builder.SOURCE_FILE.read_text(encoding="utf-8")

        self.assertIn(
            "function beatUnitLength(bpmValue, beatsPerMeasure, beatUnit)", html
        )
        self.assertIn("(60000 / bpmValue) * (4 / beatUnit)", html)
        self.assertIn("BPM・四分音符を1拍", html)

    def test_metronome_six_eight_measure_equals_two_quarter_notes(self):
        html = product_builder.SOURCE_FILE.read_text(encoding="utf-8")

        self.assertIn("beatsPerMeasure === 6 && beatUnit === 8", html)
        self.assertIn("(60000 / bpmValue) * (2 / 6)", html)

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
