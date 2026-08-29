import json
import os
import tempfile
import unittest
from io import BytesIO
from email.message import Message
from base64 import b64encode
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import (
    LessonReservationDeliveryError,
    compute_google_route,
    compute_public_route,
    create_app,
    fetch_instrument_catalog_prices,
    fetch_instrument_price_candidates,
    load_updates,
    normalize_media_url,
    normalize_slot_statuses,
    parse_update_line,
    reservation_slot_times,
    send_lesson_reservation,
    validate_consultation,
    validate_lesson_reservation,
    validate_lesson_reservation_update,
    validate_update,
)


class UpdatesTest(unittest.TestCase):
    def test_compute_google_route_returns_distance_without_exposing_api_key(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                return b'{"routes":[{"distanceMeters":1234,"duration":"165s"}]}'

        api_key = "secret-google-key"
        urlopen = MagicMock(return_value=FakeResponse())

        result = compute_google_route("東京駅", "東京タワー", api_key, urlopen)

        self.assertEqual(result["distance_km"], 1.2)
        self.assertEqual(result["duration_minutes"], 3)
        self.assertNotIn(api_key, json.dumps(result, ensure_ascii=False))
        route_request = urlopen.call_args.args[0]
        self.assertEqual(route_request.full_url, "https://routes.googleapis.com/directions/v2:computeRoutes")
        self.assertEqual(route_request.get_header("X-goog-api-key"), api_key)
        self.assertEqual(route_request.get_method(), "POST")

    def test_compute_public_route_resolves_japanese_place_names(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

        responses = iter([
            FakeResponse([{"display_name": "埼玉県滑川町役場", "lat": "36.065", "lon": "139.361"}]),
            FakeResponse([{"display_name": "埼玉県滑川町文化スポーツセンター", "lat": "36.070", "lon": "139.350"}]),
            FakeResponse({"routes": [{"distance": 4200.0, "duration": 780.0}]}),
        ])
        urlopen = MagicMock(side_effect=lambda request, timeout: next(responses))

        result = compute_public_route("滑川町役場", "滑川町文化スポーツセンター", urlopen)

        self.assertEqual(result["resolved_origin"], "埼玉県滑川町役場")
        self.assertEqual(result["resolved_destination"], "埼玉県滑川町文化スポーツセンター")
        self.assertEqual(result["distance_km"], 4.2)
        self.assertEqual(result["duration_minutes"], 13)
        self.assertEqual(result["provider"], "OpenStreetMap / OSRM")
        self.assertEqual(urlopen.call_count, 3)

    def test_contract_route_distance_api_requires_editor_and_falls_back_without_google_key(self):
        payload = {"origin": "東京駅", "destination": "東京タワー"}
        fallback_route = {
            "origin": "東京駅",
            "destination": "東京タワー",
            "resolved_origin": "東京都千代田区丸の内一丁目 東京駅",
            "resolved_destination": "東京都港区芝公園四丁目 東京タワー",
            "distance_km": 4.2,
            "duration_minutes": 14,
            "maps_url": "https://www.google.com/maps/dir/?api=1",
            "provider": "OpenStreetMap / OSRM",
        }
        with patch.dict(os.environ, {"EDITOR_PASSWORD": "editor-secret"}, clear=False), patch(
            "app.compute_public_route", return_value=fallback_route
        ) as compute_public_route:
            os.environ.pop("GOOGLE_MAPS_ROUTES_API_KEY", None)
            client = create_app(database_url="").test_client()
            self.assertEqual(
                client.post("/api/contracts/route-distance", json=payload).status_code,
                401,
            )
            missing = client.post(
                "/api/contracts/route-distance",
                json=payload,
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.json["resolved_origin"], fallback_route["resolved_origin"])
        self.assertEqual(missing.json["provider"], "OpenStreetMap / OSRM")
        compute_public_route.assert_called_once_with("東京駅", "東京タワー")

    def test_contract_route_distance_api_returns_google_route(self):
        payload = {"origin": "東京駅", "destination": "東京タワー"}
        route = {
            **payload,
            "distance_km": 4.1,
            "duration_minutes": 13,
            "maps_url": "https://www.google.com/maps/dir/?api=1",
        }
        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "editor-secret",
                "GOOGLE_MAPS_ROUTES_API_KEY": "secret-google-key",
            },
        ), patch("app.compute_google_route", return_value=route) as compute_route:
            response = create_app(database_url="").test_client().post(
                "/api/contracts/route-distance",
                json=payload,
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["distance_km"], 4.1)
        compute_route.assert_called_once_with(
            payload["origin"], payload["destination"], "secret-google-key"
        )

    def test_instrument_price_lookup_extracts_model_nearby_official_prices(self):
        class FakeResponse:
            def __init__(self):
                self.headers = Message()
                self.headers["Content-Type"] = "text/html; charset=utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://jp.yamaha.com/products/ytr-8335.html"

            def read(self, size):
                return """<html><body><h1>YTR-8335</h1><p>希望小売価格：423,500円（税込）</p><p>YTR-8335RS 希望小売価格：900,000円（税込）</p><script>YTR-8335 1,000円</script></body></html>""".encode()

        opener = MagicMock()
        opener.open.return_value = FakeResponse()

        result = fetch_instrument_price_candidates(
            "https://jp.yamaha.com/products/ytr-8335.html", "YAMAHA YTR-8335", opener
        )

        self.assertEqual(result["source_name"], "ヤマハ")
        self.assertEqual(result["exact_model"], "YTR-8335")
        self.assertEqual([candidate["price"] for candidate in result["candidates"]], [423500])
        self.assertIn("YTR-8335 希望小売価格:423,500円", result["candidates"][0]["context"])

    def test_instrument_price_lookup_falls_back_to_partial_model_match(self):
        class FakeResponse:
            def __init__(self):
                self.headers = Message()
                self.headers["Content-Type"] = "text/html; charset=utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://jp.yamaha.com/products/ytr-8335gii.html"

            def read(self, size):
                return "<p>YTR-8335GII 希望小売価格 456,500円（税込）</p>".encode()

        opener = MagicMock()
        opener.open.return_value = FakeResponse()

        result = fetch_instrument_price_candidates(
            "https://jp.yamaha.com/products/ytr-8335gii.html", "YTR-8335", opener
        )

        self.assertEqual(result["match_type"], "partial")
        self.assertEqual(result["candidates"][0]["matched_model"], "YTR-8335GII")
        self.assertEqual(result["candidates"][0]["price"], 456500)

    def test_instrument_price_lookup_accepts_model_fragment(self):
        class FakeResponse:
            def __init__(self):
                self.headers = Message()
                self.headers["Content-Type"] = "text/html; charset=utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://jp.yamaha.com/products/ytr-8335gii.html"

            def read(self, size):
                return "<p>YTR-8335GII 希望小売価格 456,500円（税込）</p>".encode()

        opener = MagicMock()
        opener.open.return_value = FakeResponse()

        result = fetch_instrument_price_candidates(
            "https://jp.yamaha.com/products/ytr-8335gii.html", "8335G", opener
        )

        self.assertEqual(result["match_type"], "partial")
        self.assertEqual(result["candidates"][0]["price"], 456500)

    def test_instrument_price_lookup_discovers_product_from_catalog_url(self):
        class FakeResponse:
            def __init__(self, url, body, content_type="text/html"):
                self.url = url
                self.body = body.encode()
                self.headers = Message()
                self.headers["Content-Type"] = f"{content_type}; charset=utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return self.url

            def read(self, size):
                return self.body

        pages = {
            "https://jp.yamaha.com/products/musical_instruments/winds/": FakeResponse(
                "https://jp.yamaha.com/products/musical_instruments/winds/",
                '<h1>管楽器製品一覧</h1><a href="/products/ytr-8335.html">YTR-8335</a>',
            ),
            "https://jp.yamaha.com/products/ytr-8335.html": FakeResponse(
                "https://jp.yamaha.com/products/ytr-8335.html",
                "<p>YTR-8335 希望小売価格 500,000円（税込）</p>",
            ),
        }
        opener = MagicMock()
        opener.open.side_effect = lambda request, timeout: pages[request.full_url]

        result = fetch_instrument_price_candidates(
            "https://jp.yamaha.com/products/musical_instruments/winds/", "YTR-8335", opener
        )

        self.assertEqual(result["source_url"], "https://jp.yamaha.com/products/ytr-8335.html")
        self.assertEqual(result["candidates"][0]["price"], 500000)

    def test_instrument_price_lookup_allows_official_subdomain_product_url(self):
        class FakeResponse:
            def __init__(self, url, body, content_type="text/html"):
                self.url = url
                self.body = body.encode()
                self.headers = Message()
                self.headers["Content-Type"] = f"{content_type}; charset=utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return self.url

            def read(self, size):
                return self.body

        pages = {
            "https://nonaka.com/catalog/": FakeResponse(
                "https://nonaka.com/catalog/",
                '<h1>製品一覧</h1><a href="https://www.nonaka.com/products/model-100.html">MODEL-100</a>',
            ),
            "https://www.nonaka.com/products/model-100.html": FakeResponse(
                "https://www.nonaka.com/products/model-100.html",
                "<p>MODEL-100 希望小売価格 435,000円（税別）</p>",
            ),
        }
        opener = MagicMock()
        opener.open.side_effect = lambda request, timeout: pages[request.full_url]

        result = fetch_instrument_price_candidates(
            "https://nonaka.com/catalog/", "MODEL-100", opener
        )

        self.assertEqual(result["source_name"], "野中貿易")
        self.assertEqual(result["candidates"][0]["price"], 435000)

    def test_instrument_price_lookup_rejects_non_official_url_before_request(self):
        opener = MagicMock()

        with self.assertRaisesRegex(ValueError, "対応している公式"):
            fetch_instrument_price_candidates(
                "https://example.com/ytr-8335", "YTR-8335", opener
            )

        opener.open.assert_not_called()

    def test_instrument_catalog_prices_adopts_first_matching_price(self):
        catalog_results = {
            "https://jp.yamaha.com/catalog-a": {
                "source_name": "ヤマハ",
                "source_url": "https://jp.yamaha.com/catalog-a",
                "candidates": [{"price": 420000, "context": "2026価格表"}],
            },
            "https://www.nonaka.com/catalog-b": {
                "source_name": "野中貿易",
                "source_url": "https://www.nonaka.com/catalog-b",
                "candidates": [{"price": 435000, "context": "2026カタログ"}],
            },
        }

        result = fetch_instrument_catalog_prices(
            list(catalog_results), "MODEL-100", lambda source_url, maker_model: catalog_results[source_url]
        )

        self.assertEqual(result["recommended_price"], 420000)
        self.assertEqual(result["recommended_source_name"], "ヤマハ")
        self.assertEqual(result["catalog_year"], "2026")

    def test_instrument_catalog_prices_requests_manual_entry_when_lookup_fails(self):
        def unavailable_fetcher(source_url, maker_model):
            raise ValueError("公式ページ内に型番が見つかりませんでした。")

        result = fetch_instrument_catalog_prices(
            ["https://www.nonaka.com/"], "180ML37", unavailable_fetcher
        )

        self.assertTrue(result["manual_entry_required"])
        self.assertIsNone(result["recommended_price"])
        self.assertEqual(result["source_urls"], ["https://www.nonaka.com/"])
        self.assertEqual(
            result["failures"][0]["error"],
            "公式ページ内に型番が見つかりませんでした。",
        )

    def test_instrument_price_candidates_reports_tax_status(self):
        class FakeResponse:
            headers = Message()

            def __init__(self):
                self.headers["Content-Type"] = "text/html; charset=utf-8"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return "https://jp.yamaha.com/products/model-100.html"

            def read(self, size):
                return "<p>MODEL-100 メーカー希望小売価格 435,000円（税込）</p>".encode()

        opener = MagicMock()
        opener.open.return_value = FakeResponse()

        result = fetch_instrument_price_candidates(
            "https://jp.yamaha.com/products/model-100.html", "MODEL-100", opener
        )

        self.assertEqual(result["candidates"][0]["tax_status"], "tax_included")

    def test_instrument_price_lookup_api_aggregates_catalog_urls(self):
        payload = {
            "source_urls": [
                "https://jp.yamaha.com/catalog-a",
                "https://www.nonaka.com/catalog-b",
            ],
            "maker_model": "MODEL-100",
        }
        result = {
            "checked_at": "2026-08-24",
            "catalog_year": "2026",
            "recommended_price": 435000,
            "recommended_source_url": payload["source_urls"][1],
            "candidates": [],
            "failures": [],
        }
        with patch.dict(os.environ, {"EDITOR_PASSWORD": "editor-secret"}), patch(
            "app.fetch_instrument_catalog_prices", return_value=result
        ) as lookup:
            client = create_app(database_url="").test_client()
            self.assertEqual(
                client.post("/api/contracts/instrument-price-lookup", json=payload).status_code,
                401,
            )
            response = client.post(
                "/api/contracts/instrument-price-lookup",
                json=payload,
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["recommended_price"], 435000)
        lookup.assert_called_once_with(payload["source_urls"], payload["maker_model"])

    def test_consultation_validation_requires_mode_specific_fields(self):
        valid = {
            "service_mode": "allinone",
            "org_name": "〇〇高等学校吹奏楽部",
            "email": "music@example.com",
            "event_date": "2026-10-10",
            "support_content": "コンクール・演奏会当日フルサポート（指導＋搬送＋セッティング）",
            "instrument_value": "1000",
            "message": "見積りを希望します。",
            "terms_agree": True,
        }

        values = validate_consultation(valid)

        self.assertEqual(values["service_mode"], "オールインワン依頼（指導・セッティング・運搬一式）")
        self.assertEqual(values["instrument_value"], "300万円〜1,000万円")
        with self.assertRaisesRegex(ValueError, "実施予定日"):
            validate_consultation({**valid, "event_date": ""})

    def test_consultation_validation_accepts_safe_attachment_and_other_planning(self):
        payload = {
            "service_mode": "planning",
            "org_name": "地域イベント実行委員会",
            "email": "event@example.com",
            "planning_type": "その他",
            "message": "屋外ライブの運営支援について相談したいです。",
            "attachment": {
                "name": "plan.pdf",
                "type": "application/pdf",
                "data": b64encode(b"%PDF-1.4 test").decode("ascii"),
            },
            "terms_agree": True,
        }

        values = validate_consultation(payload)

        self.assertEqual(values["planning_type"], "その他")
        self.assertEqual(values["attachment_name"], "plan.pdf")
        self.assertEqual(values["attachment_type"], "application/pdf")
        self.assertEqual(values["attachment_data"], payload["attachment"]["data"])
        with self.assertRaisesRegex(ValueError, "概略・要望事項"):
            validate_consultation({**payload, "message": ""})
        with self.assertRaisesRegex(ValueError, "添付できない"):
            validate_consultation(
                {
                    **payload,
                    "attachment": {
                        "name": "script.html",
                        "type": "text/html",
                        "data": b64encode(b"<script></script>").decode("ascii"),
                    },
                }
            )

    def test_consultation_api_sends_validated_request_to_apps_script(self):
        payload = {
            "service_mode": "planning",
            "org_name": "地域音楽会実行委員会",
            "email": "event@example.com",
            "planning_type": "ワークショップ・講習会の企画",
            "message": "子ども向け企画を相談したいです。",
            "terms_agree": True,
        }
        with patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_request:
            send_request.return_value = {
                "ok": True,
                "consultationId": "C-20260821-001",
                "autoReplySent": True,
            }

            response = create_app(database_url="").test_client().post(
                "/api/consultation", json=payload
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["consultation_id"], "C-20260821-001")
        self.assertEqual(send_request.call_args.kwargs["action"], "consultation")
        self.assertEqual(
            send_request.call_args.args[2]["service_mode"],
            "イベント企画・プロデュースのみ",
        )

    def test_contract_route_distance_api_falls_back_when_google_fails(self):
        payload = {"origin": "東京駅", "destination": "東京タワー"}
        fallback_route = {
            **payload,
            "resolved_origin": "東京都千代田区 東京駅",
            "resolved_destination": "東京都港区 東京タワー",
            "distance_km": 4.2,
            "duration_minutes": 14,
            "maps_url": "https://www.google.com/maps/dir/?api=1",
            "provider": "OpenStreetMap / OSRM",
        }
        with patch.dict(
            os.environ,
            {"EDITOR_PASSWORD": "editor-secret", "GOOGLE_MAPS_ROUTES_API_KEY": "invalid-key"},
        ), patch("app.compute_google_route", side_effect=ValueError("Google route unavailable")), patch(
            "app.compute_public_route", return_value=fallback_route
        ) as compute_public_route:
            response = create_app(database_url="").test_client().post(
                "/api/contracts/route-distance",
                json=payload,
                headers={"X-Editor-Password": "editor-secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["provider"], "OpenStreetMap / OSRM")
        compute_public_route.assert_called_once_with("東京駅", "東京タワー")

    def test_apps_script_stores_consultation_attachment_in_drive(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        self.assertIn('"添付ファイル名"', script)
        self.assertIn('"添付ファイルURL"', script)
        self.assertIn("saveConsultationAttachment(data, consultationId)", script)
        self.assertIn('DriveApp.getFoldersByName("企画・輸送相談添付")', script)
        self.assertIn("Utilities.base64Decode(attachmentData)", script)
        self.assertIn("folder.createFile(blob).getUrl()", script)

    def test_index_embeds_event_consultation_form(self):
        page = create_app(database_url="").test_client().get("/").get_data(as_text=True)

        self.assertIn('href="#event-consultation"', page)
        self.assertIn('id="event-consultation"', page)
        self.assertIn('class="consultation-disclosure"', page)
        self.assertIn('<summary', page)
        self.assertIn('id="nblConsultationForm"', page)
        self.assertIn('data-mode="allinone"', page)
        self.assertIn('data-mode="planning"', page)
        self.assertIn('data-mode="cargo"', page)
        self.assertIn("fetch('/api/consultation'", page)
        self.assertIn('href="legal/privacy-policy.html"', page)
        self.assertIn('id="consultationAttachment"', page)
        self.assertIn('演奏会・ライブ等のプロデュース', page)
        self.assertIn('<option value="その他">その他</option>', page)
        self.assertIn('id="consultationPlanningOtherHint"', page)
        self.assertIn("orgNameExamples", page)

    def test_index_links_admin_contract_generator_from_services_heading(self):
        response = create_app(database_url="").test_client().get("/")
        page = response.get_data(as_text=True)

        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn('class="services-heading"', page)
        self.assertIn('href="contract-generator/"', page)
        self.assertIn("契約書作成", page)
        self.assertNotIn('class="services-admin-link" href="contract-generator/" target="_blank"', page)

    def test_contract_generator_requires_editor_login_in_page(self):
        response = create_app(database_url="").test_client().get("/contract-generator/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        page = response.get_data(as_text=True)
        self.assertIn('id="contractLoginForm"', page)
        self.assertIn('id="contractLoginButton"', page)
        self.assertIn('id="contractGenerator"', page)
        self.assertIn("/api/editor", page)
        self.assertIn("X-Editor-Token", page)
        self.assertIn("let editorToken = '';", page)
        self.assertIn("JSON.stringify({ editor_password: password })", page)
        self.assertIn("const token = await authenticate(passwordInput.value);", page)
        self.assertIn("editorToken = token;\n        showGenerator();\n        passwordInput.value = '';", page)
        self.assertIn("catch (error) {\n        editorToken = '';", page)
        self.assertIn("passwordInput.focus();", page)
        self.assertNotIn("X-Editor-Password': adminPassword", page)
        self.assertNotIn("sessionStorage.getItem('updatesEditorPassword')", page)
        self.assertIn('id="contractLogout" type="button">戻る（ログアウト）</button>', page)
        self.assertNotIn('id="contractLogout" type="button" data-history-back', page)
        self.assertNotIn('data-fallback="../#web">戻る（ログアウト）</button>', page)
        self.assertIn("passwordInput.value = '';", page)
        self.assertIn('id="contractStoragePath"', page)
        self.assertIn("const initialContractValues = JSON.parse(JSON.stringify(contractValues));", page)
        self.assertIn("function resetContractEditor()", page)
        self.assertIn("docType.value = 'master';", page)
        self.assertIn("clientName.value = '';", page)
        self.assertIn("clientRepresentative.value = '';", page)
        self.assertIn("dynamicFields.replaceChildren();", page)
        self.assertIn("preview.replaceChildren();", page)
        self.assertIn("resetContractEditor();", page)
        self.assertIn('id="deleteContract"', page)
        self.assertIn('id="deleteConfirmStep"', page)
        self.assertIn('id="deleteIdStep" hidden', page)
        self.assertIn("deleteDialog.showModal()", page)
        self.assertIn("method: 'DELETE'", page)
        self.assertIn("PC内・サーバー内の保存済み契約書を削除しました。", page)
        self.assertIn("const lightCargoReferenceRateHistory = [{", page)
        self.assertIn("function latestRateForEstimateDate(rateHistory, estimateDate)", page)
        self.assertIn("rate.effective_date <= estimateDate", page)
        self.assertIn("latestRateForEstimateDate(lightCargoReferenceRateHistory, contractDate.value)", page)
        self.assertIn('id="docType"', page)
        self.assertIn('value="master"', page)
        self.assertIn('value="typeA"', page)
        self.assertIn('value="estimateA"', page)
        self.assertIn("A契約前 御見積書（音楽指導・地域支援）", page)
        self.assertIn("A契約前見積書の編集", page)
        self.assertIn("function renderMusicSupportEstimate(date, safeClient)", page)
        self.assertIn("音楽指導および地域クラブ移行支援に関する御見積", page)
        self.assertIn("type === 'estimateA' ? 0 : Math.round(subtotal * 0.1)", page)
        self.assertIn("単価（税込・円）", page)
        self.assertIn("金額（税込・円）", page)
        self.assertIn("明細合計（税込）", page)
        self.assertIn("data-add-estimate-row", page)
        self.assertIn("data-remove-estimate-row", page)
        self.assertIn('value="typeB"', page)
        self.assertIn('value="estimateB"', page)
        self.assertIn("B契約前 次世代型御見積書", page)
        self.assertIn('value="estimateC"', page)
        self.assertIn("C契約前 御見積書（選択・任意入力対応）", page)
        self.assertIn("C契約前見積書の編集", page)
        self.assertIn('value="typeC"', page)
        self.assertIn('id="exportContractCsv"', page)
        self.assertIn('id="exportContractExcel"', page)
        self.assertIn("function flattenContractData(value, prefix = '', rows = [])", page)
        self.assertIn("'text/csv;charset=utf-8', 'csv'", page)
        self.assertIn("'application/vnd.ms-excel;charset=utf-8', 'xls'", page)
        self.assertIn('id="printPageSelection"', page)
        self.assertIn('value="front"', page)
        self.assertIn('value="back"', page)
        self.assertIn("function printContractPages()", page)
        self.assertIn("preview.dataset.printPages = selection", page)
        self.assertIn('.document[data-print-pages="front"] .contract-page:not([data-page-number="1"])', page)
        self.assertIn('.document[data-print-pages="back"] .contract-page[data-page-number="1"]', page)
        self.assertIn("window.print()", page)
        self.assertIn("法令等の制定または改廃", page)
        self.assertIn("電磁的記録", page)
        self.assertIn("反社会的勢力", page)
        self.assertIn("不可抗力", page)
        self.assertIn("未成年者", page)
        self.assertIn("@page { size: A4 portrait; margin: 0; }", page)
        self.assertIn('data-paper-size="A4" data-orientation="portrait"', page)
        self.assertIn("A4縦 PDF出力 / 印刷", page)
        self.assertIn(".login-screen, .sidebar, dialog { display: none !important; }", page)
        self.assertIn("html, body, .generator, .preview-container { width: var(--paper-width); }", page)
        self.assertIn("height: auto;", page)
        self.assertIn("overflow: visible;", page)
        self.assertNotIn("aspect-ratio: 210 / 297;", page)
        self.assertIn('<optgroup label="共通契約書">', page)
        self.assertIn('<optgroup label="個別契約書">', page)
        self.assertIn("function renderMasterContract(date, safeClient, parties)", page)
        self.assertIn("function renderIndividualContract(type, date, parties)", page)
        self.assertIn("function renderWebAppEstimate(date, safeClient)", page)
        self.assertEqual(page.count('<section class="contract-page" data-page-number="2">'), 1)
        self.assertIn(".contract-page:last-child { break-after: auto; }", page)
        self.assertIn("width: var(--paper-width);", page)
        self.assertIn("height: var(--paper-height);", page)
        self.assertIn("padding: 16mm 18mm;", page)
        self.assertIn("break-inside: avoid;", page)
        self.assertIn('class="individual-contract-table"', page)
        self.assertIn(".individual-contract-table th { border-right: 1.5px solid #222222; }", page)
        self.assertIn(".individual-contract-table td:last-child { border-right: 1.5px solid #222222; }", page)
        self.assertIn('class="party-divider" aria-hidden="true"', page)
        self.assertIn("renderMasterContract(date, safeClient, parties)", page)
        self.assertIn("renderIndividualContract(type, date, parties)", page)
        self.assertIn("preview.dataset.contractType = type;", page)
        self.assertIn("print-color-adjust: exact;", page)
        self.assertIn("ハラスメント", page)
        self.assertIn("運送中止", page)
        self.assertIn("事故、滅失、毀損または遅延", page)
        self.assertIn("待機料・付帯作業料", page)
        self.assertIn("燃油特別付加運賃", page)
        self.assertIn("事業許可証、管轄営業所情報、運行管理者情報", page)
        self.assertIn("function renderTransportEstimate(date, safeClient)", page)
        self.assertIn("輸送対象物明細・評価額証明（付属書）", page)
        self.assertIn("function calculateCargoValuation()", page)
        self.assertIn("function calculateCargoVolume()", page)
        self.assertIn("function calculateOptimalFreight(distance, totalHours, operation = {})", page)
        self.assertIn("2026年度 軽貨物運送事業 自社料金規定", page)
        self.assertIn("effective_date: '2026-04-01'", page)
        self.assertIn("distance_base_20: '5000'", page)
        self.assertIn("distance_per_km_21_50: '200'", page)
        self.assertIn("distance_per_km_51_100: '150'", page)
        self.assertIn("distance_per_km_101_150: '120'", page)
        self.assertIn("charter_4h: '12000'", page)
        self.assertIn("charter_8h: '22000'", page)
        self.assertIn("extra_hour: '3000'", page)
        self.assertIn("waiting_per_30m: '1500'", page)
        self.assertIn("loading_per_30m: '1500'", page)
        self.assertIn("前日キャンセル：お見積り運賃の50%", page)
        self.assertIn("resolved_origin", page)
        self.assertIn("2026年度 自社料金規定を読み込む", page)
        self.assertIn("標準車両：自社軽貨物車", page)
        self.assertIn('data-rate-key="distance_per_km_151_plus"', page)
        self.assertIn('data-rate-key="loading_per_30m"', page)
        self.assertIn('data-operation-input="holiday"', page)
        self.assertIn("助手追加・特殊作業は別途見積", page)
        self.assertIn("楽器等運搬の上乗せ区分", page)
        self.assertIn("一般貨物のみ（上乗せなし）", page)
        self.assertIn("固定・養生・棚設置（市場参考 1,500円）", page)
        self.assertIn("車内積み置き（市場参考 5,500円～）", page)
        self.assertIn("追加スタッフ1名（市場参考 15,000円）", page)
        self.assertIn("instrumentTransportSurchargePresets", page)
        self.assertIn("https://www.taiho-unyu.co.jp/price10new.html", page)
        self.assertIn("https://rentora.com/gakki/", page)
        self.assertIn("https://rental.after-beat.co.jp/guide/transport.html", page)
        self.assertIn("function updateInstrumentSurchargePreset", page)
        self.assertIn("当方が積卸し（荷役料を算定）", page)
        self.assertIn("お客様のお手伝いあり（追加スタッフ不要）", page)
        self.assertIn("お客様が積卸し（荷役料なし）", page)
        self.assertIn("loadingSupportMode === 'customer_loads'", page)
        self.assertIn("role: 'freight'", page)
        self.assertIn("role: 'loading'", page)
        self.assertIn("const itemByRole = role", page)
        self.assertIn("if (items[rowIndex]?.role) return;", page)
        self.assertIn("values.freight_operation", page)
        self.assertIn("[50, 'distance_per_km_101_150']", page)
        self.assertIn("[Infinity, 'distance_per_km_151_plus']", page)
        self.assertIn("data-partner-2t-requested", page)
        self.assertIn("発行準備完了後に確定", page)
        self.assertIn('data-freight-input="route_origin"', page)
        self.assertIn("data-measure-google-route", page)
        self.assertIn("function measureGoogleRouteDistance()", page)
        self.assertIn("function googleMapsRouteUrl(origin, destination)", page)
        self.assertIn('data-open-route-map href="${escapeHtml(googleMapsRouteUrl(values.route_origin, values.route_destination))}"', page)
        self.assertIn("経路図を作成するには、出発地と目的地を入力して", page)
        self.assertIn("a.secondary-button { display: inline-flex; align-items: center; justify-content: center;", page)
        self.assertIn("@media (max-width: 520px)", page)
        self.assertIn(".document-tools { grid-template-columns: 1fr; }", page)
        self.assertIn("/api/contracts/route-distance", page)
        self.assertIn("result.provider || 'ルート検索'", page)
        self.assertIn("再調達価格・評価根拠の確認", page)
        self.assertIn("輸送品目を追加", page)
        self.assertIn('<fieldset class="cargo-editor-fieldset" data-cargo-editor>', page)
        self.assertNotIn("data-cargo-editor${values.cargo_restrictions_agreed ? '' : ' disabled'}", page)
        self.assertIn("輸送書類・経路図の作成", page)
        self.assertIn("経路図を作成・確認", page)
        self.assertIn("元の見積書作成へ戻る", page)
        self.assertIn("function closeFreightCalculator()", page)
        self.assertIn("T2810320517878", page)
        self.assertIn("function estimateIssuerHtml", page)
        self.assertGreaterEqual(page.count('class="estimate-grand-total"'), 3)
        self.assertIn(".type-b-contract-page { height: auto;", page)
        self.assertIn("公開カタログURL（1行1件・最大7件）", page)
        self.assertIn("掲載順先頭の候補 ${formatEstimateYen(result.recommended_price)}円を反映", page)
        self.assertIn("指定カタログ内で型番価格を検索・反映", page)
        self.assertIn("function instrumentLookupReady(rowIndex)", page)
        self.assertIn("async function lookupInstrumentPriceWithFeedback(rowIndex)", page)
        self.assertIn("const instrumentLookupRequests = new Map();", page)
        self.assertIn("if (instrumentLookupRequests.has(rowIndex))", page)
        self.assertIn("instrumentLookupRequests.delete(rowIndex);", page)
        self.assertIn("result.manual_entry_required", page)
        self.assertIn("公式サイトから価格を自動取得できない掲載形式です。", page)
        self.assertIn("指定カタログから価格を自動取得できませんでした。", page)
        self.assertIn("行評価額 ${formatEstimateYen(estimateNumber(item.total_value))}円を反映しました。", page)
        self.assertIn('dynamicFields.addEventListener(\'change\', async event => {', page)
        self.assertIn("価格の税区分", page)
        self.assertIn("function updateCargoTaxStatus(select)", page)
        self.assertIn("event.target.matches('[data-cargo-item-key=\"price_tax_status\"]')", page)
        self.assertIn("if (cargoKey === 'price_tax_status') return;", page)
        self.assertIn('data-cargo-item-key="catalog_price"', page)
        self.assertIn("if (cargoKey === 'catalog_price')", page)
        self.assertNotIn("data-instrument-master-verified", page)
        self.assertNotIn("型番・最高額候補・税込／税別を確認済み", page)
        self.assertNotIn("instrumentPricesConfirmable", page)
        self.assertIn("Boolean(select.value && estimateNumber(item.catalog_price) > 0)", page)
        self.assertIn("window.location.protocol === 'file:'", page)
        self.assertIn("https://namegawa-brass-lab.onrender.com/contract-generator/", page)
        self.assertIn("見積作成年の公開カタログで再照会", page)
        self.assertIn("master.source_urls = [...new Set([...(master.source_urls || []), ...result.source_urls])]", page)
        self.assertIn('data-instrument-master-key="effective_date" type="date" max=', page)
        self.assertNotIn('data-instrument-master-key="effective_date" type="date" value="${escapeHtml(values.instrument_price_master.effective_date)}" readonly', page)
        self.assertIn("価格基準日 ${master.effective_date}を記録しました。", page)
        self.assertIn("dynamicFields.querySelectorAll('[data-instrument-price-results]').forEach", page)
        self.assertIn("if (!item.lookup_source_url) item.lookup_source_url = selectedSourceUrl;", page)
        self.assertNotIn("master.effective_date = '';", page)
        self.assertNotIn("master.catalog_year = '';", page)
        self.assertIn("function cargoPriceSourceOptions(sourceUrls, selectedUrl)", page)
        self.assertIn("data-cargo-price-source", page)
        self.assertIn("source_urls: [sourceUrl]", page)
        self.assertIn("lookup_source_url: item.lookup_source_url", page)
        self.assertIn("</small></article>`).join('')}", page)
        self.assertNotIn("</small></article>`).join(')}", page)
        self.assertIn("function transportReadiness(values)", page)
        self.assertIn("function transportValidationIssues(values)", page)
        self.assertIn("const instrumentLookupErrors = new Map();", page)
        self.assertIn("function reportTransportError(message, rowIndex = null)", page)
        self.assertIn("instrumentLookupErrors.has(rowIndex)", page)
        self.assertIn("if (instrumentLookupErrors.size || (requiresCompleteTransport && issues.length))", page)
        self.assertIn("案件受付（下書き）", page)
        self.assertIn("軽貨物運賃・日程を調整中", page)
        self.assertIn("標準車両：自社軽貨物車", page)
        self.assertNotIn("field('正式見積書の共有URL', 'carrier_quote_url'", page)
        self.assertIn("data-cargo-consent", page)
        self.assertIn("data-generate-transport-sheet", page)
        self.assertIn("協力会社 小型2t車の代理調整", page)
        self.assertIn('data-cargo-item-key="unit_value"', page)
        self.assertIn('data-cargo-total="valuation"', page)
        self.assertIn("変更管理", page)
        self.assertIn("生成AI支援 アプリケーション実装費", page)
        self.assertIn("検収完了後14日以内", page)
        self.assertIn("売り切り（買い切り）契約", page)
        self.assertIn('id="estimateProjectPresets"', page)
        self.assertIn('id="estimateItemPresets"', page)
        self.assertIn('data-estimate-row="${index}"', page)
        self.assertIn('data-estimate-preset="project_name"', page)
        self.assertIn('data-estimate-preset-key="${key}"', page)
        self.assertIn("function applyEstimatePreset(select)", page)
        self.assertIn("function estimatePreviewEditor(key, value, rowIndex = '')", page)
        self.assertIn("data-preview-estimate-key", page)
        self.assertIn("function calculateEstimateTotals(type = activeEstimateType())", page)
        self.assertIn("脆弱性", page)
        self.assertIn('class="party-trade-name">屋号：なめがわブラス・ラボ', page)
        self.assertIn(".party-trade-name { white-space: nowrap; }", page)

    def test_contract_api_saves_web_app_estimate(self):
        with tempfile.TemporaryDirectory() as temporary_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            client = create_app(
                database_url="", contracts_dir=Path(temporary_directory)
            ).test_client()
            response = client.post(
                "/api/contracts",
                headers={"X-Editor-Password": "editor-secret"},
                json={
                    "doc_type": "estimateC",
                    "client_name": "株式会社テスト",
                    "client_representative": "担当者 山田様",
                    "contract_date": "2026-08-23",
                    "values": {
                        "project_name": "生成AI活用型Webアプリ開発",
                        "operating_system": "Windows 11 / macOS 最新版",
                        "runtime_environment": "Google Chrome 最新版",
                        "delivery_date": "双方協議のうえ定める日",
                        "estimate_items": [
                            {
                                "description": "要件定義・プロンプト設計費",
                                "quantity": "1",
                                "unit": "式",
                                "unit_price": "40000",
                                "amount": "40000",
                                "details": "要件定義と設計",
                            }
                        ],
                    },
                },
            )

            self.assertEqual(response.status_code, 201, response.get_json())
            self.assertRegex(
                response.json["contract_id"],
                r"^estimateC-20260823-[a-f0-9]{8}$",
            )
            self.assertEqual(response.json["department"], "WEB・アプリ")
            self.assertEqual(
                response.json["values"]["estimate_items"][0]["amount"], "40000"
            )

    def test_contract_api_saves_music_support_estimate(self):
        with tempfile.TemporaryDirectory() as temporary_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            client = create_app(
                database_url="", contracts_dir=Path(temporary_directory)
            ).test_client()
            response = client.post(
                "/api/contracts",
                headers={"X-Editor-Password": "editor-secret"},
                json={
                    "doc_type": "estimateA",
                    "client_name": "テスト吹奏楽部",
                    "client_representative": "担当者 山田様",
                    "contract_date": "2026-08-24",
                    "values": {
                        "subject": "音楽指導および地域クラブ移行支援に関する御見積",
                        "implementation_period": "2026年9月から2027年3月まで",
                        "validity_days": "30",
                        "invoice_registration_number": "該当なし",
                        "estimate_items": [{
                            "description": "合奏指導",
                            "quantity": "2",
                            "unit": "回",
                            "unit_price": "15000",
                            "amount": "30000",
                            "details": "吹奏楽部の合奏指導",
                        }],
                    },
                },
            )

            self.assertEqual(response.status_code, 201, response.get_json())
            self.assertRegex(
                response.json["contract_id"],
                r"^estimateA-20260824-[a-f0-9]{8}$",
            )
            self.assertEqual(response.json["department"], "音楽指導・支援")

    def test_contract_api_saves_transport_estimate(self):
        with tempfile.TemporaryDirectory() as temporary_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            client = create_app(
                database_url="", contracts_dir=Path(temporary_directory)
            ).test_client()
            response = client.post(
                "/api/contracts",
                headers={"X-Editor-Password": "editor-secret"},
                json={
                    "doc_type": "estimateB",
                    "client_name": "〇〇楽団",
                    "client_representative": "代表 山田様",
                    "contract_date": "2026-08-23",
                    "values": {
                        "workflow_status": "ready",
                        "transport_provider_mode": "self_light_cargo",
                        "vehicle_class": "light_cargo",
                        "pricing_basis": "self_light_cargo_rate",
                        "transport_name": "楽器輸送業務一式",
                        "validity": "発行日より30日間",
                        "permit_number": "許可番号を入力",
                        "office_information": "管轄営業所情報を入力",
                        "operation_manager": "運行管理者情報を入力",
                        "cargo_document_url": "https://example.com/cargo",
                        "route_document_url": "https://example.com/route",
                        "compliance_document_url": "https://example.com/compliance",
                        "fee_document_url": "https://example.com/fees",
                        "waiting_fee": "30分毎に5,000円",
                        "ancillary_fee": "1名1時間毎に8,000円",
                        "detour_expenses": "実費精算",
                        "cargo_restrictions_agreed": True,
                        "cargo_contact_email": "music@example.com",
                        "external_vehicle_budget": "150000",
                        "route_origin": "〇〇高等学校",
                        "route_destination": "〇〇市民ホール",
                        "route_distance_km": "30",
                        "total_hours": "8",
                        "freight_operation": {
                            "waiting_minutes": "90",
                            "loading_minutes": "60",
                            "loading_support_mode": "customer_assisted",
                            "cancellation_type": "none",
                            "actual_expenses": "1000",
                            "instrument_surcharge_mode": "securement",
                            "instrument_surcharge_amount": "1500",
                            "special_work_amount": "0",
                            "holiday": False,
                            "night": False,
                        },
                        "instrument_price_master": {
                            "effective_date": "2026-08-23",
                            "source_url": "https://jp.yamaha.com/products/model-100.html",
                            "source_urls": ["https://jp.yamaha.com/products/model-100.html"],
                            "catalog_year": "2026",
                            "verified": False,
                        },
                        "freight_rate_master": {
                            "effective_date": "2026-08-23",
                            "source_url": "https://www.mlit.go.jp/example",
                            "verified": True,
                            "distance_base_20": "5000",
                            "distance_per_km_21_50": "200",
                            "distance_per_km_51_100": "180",
                            "distance_per_km_101_plus": "160",
                            "charter_4h": "15000",
                            "charter_8h": "25000",
                            "extra_hour": "3000",
                            "waiting_per_30m": "2000",
                            "loading_base": "5000",
                            "loading_per_25_points": "1500",
                            "fuel_reference_price": "170",
                            "fuel_current_price": "180",
                            "fuel_per_km_per_yen": "2",
                            "external_2t_charter": "120000",
                        },
                        "cargo_items": [
                            {
                                "category": "金管楽器",
                                "instrument_key": "trumpet",
                                "description": "トランペット",
                                "maker_model": "YAMAHA YTR-8335",
                                "quantity": "10",
                                "condition": "良好",
                                "valuation_mode": "master",
                                "unit_value": "500000",
                                "catalog_price": "500000",
                                "total_value": "5000000",
                                "volume_points": "1",
                                "notes": "ハードケース入り",
                                "lookup_source_url": "https://jp.yamaha.com/products/model-100.html",
                                "price_source_url": "https://jp.yamaha.com/products/model-100.html",
                                "price_checked_at": "2026-08-23",
                                "price_tax_status": "tax_included",
                            }
                        ],
                        "estimate_items": [
                            {
                                "description": "基本運賃（貸切）",
                                "quantity": "1",
                                "unit": "運行",
                                "unit_price": "100000",
                                "amount": "100000",
                                "details": "走行距離および拘束時間に基づく基本運賃",
                            }
                        ],
                    },
                },
            )

            self.assertEqual(response.status_code, 201, response.get_json())
            self.assertRegex(
                response.json["contract_id"],
                r"^estimateB-20260823-[a-f0-9]{8}$",
            )
            self.assertEqual(response.json["department"], "楽器輸送")
            self.assertEqual(response.json["values"]["pricing_basis"], "self_light_cargo_rate")
            self.assertEqual(response.json["values"]["freight_operation"]["waiting_minutes"], "90")
            self.assertEqual(
                response.json["values"]["cargo_items"][0]["total_value"],
                "5000000",
            )
            self.assertEqual(
                response.json["values"]["cargo_items"][0]["catalog_price"],
                "500000",
            )
            self.assertEqual(
                response.json["values"]["cargo_items"][0]["price_tax_status"],
                "tax_included",
            )
            self.assertEqual(
                response.json["values"]["cargo_items"][0]["lookup_source_url"],
                "https://jp.yamaha.com/products/model-100.html",
            )
            self.assertTrue(response.json["values"]["cargo_restrictions_agreed"])
            self.assertEqual(
                response.json["values"]["freight_rate_master"]["external_2t_charter"],
                "120000",
            )

    def test_contract_api_saves_transport_case_while_formal_quote_is_pending(self):
        with tempfile.TemporaryDirectory() as temporary_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            client = create_app(
                database_url="", contracts_dir=Path(temporary_directory)
            ).test_client()
            response = client.post(
                "/api/contracts",
                headers={"X-Editor-Password": "editor-secret"},
                json={
                    "doc_type": "estimateB",
                    "client_name": "実案件受付テスト",
                    "client_representative": "",
                    "contract_date": "2026-08-23",
                    "values": {
                        "workflow_status": "quote_pending",
                        "transport_provider_mode": "self_light_cargo",
                        "vehicle_class": "light_cargo",
                        "pricing_basis": "light_cargo_reference",
                        "carrier_name": "",
                        "carrier_quote_url": "",
                        "carrier_quote_date": "",
                        "transport_name": "楽器輸送業務一式",
                        "validity": "正式見積取得後に確定",
                        "permit_number": "",
                        "office_information": "",
                        "operation_manager": "",
                        "cargo_document_url": "",
                        "route_document_url": "",
                        "compliance_document_url": "",
                        "fee_document_url": "",
                        "waiting_fee": "正式見積取得後に確定",
                        "ancillary_fee": "正式見積取得後に確定",
                        "detour_expenses": "実費精算",
                        "cargo_restrictions_agreed": False,
                        "cargo_contact_email": "",
                        "external_vehicle_budget": "0",
                        "route_origin": "",
                        "route_destination": "",
                        "route_distance_km": "0",
                        "total_hours": "0",
                        "freight_operation": {
                            "waiting_minutes": "0",
                            "loading_minutes": "0",
                            "loading_support_mode": "customer_assisted",
                            "cancellation_type": "none",
                            "actual_expenses": "0",
                            "instrument_surcharge_mode": "none",
                            "instrument_surcharge_amount": "0",
                            "special_work_amount": "0",
                            "holiday": False,
                            "night": False,
                        },
                        "instrument_price_master": {
                            "effective_date": "",
                            "source_url": "",
                            "verified": False,
                        },
                        "freight_rate_master": {
                            "effective_date": "2024-03-22",
                            "source_url": "https://www.mlit.go.jp/jidosha/jidosha_tk4_000118.html",
                            "verified": False,
                            "distance_base_20": "0",
                            "distance_per_km_21_50": "0",
                            "distance_per_km_51_100": "0",
                            "distance_per_km_101_plus": "0",
                            "distance_per_km_101_150": "154",
                            "distance_per_km_151_plus": "132",
                            "charter_2h": "6050",
                            "charter_4h": "0",
                            "charter_8h": "0",
                            "extra_30m": "1375",
                            "extra_hour": "0",
                            "waiting_per_30m": "0",
                            "loading_base": "0",
                            "loading_per_15m": "550",
                            "loading_per_25_points": "0",
                            "holiday_percent": "20",
                            "night_percent": "30",
                            "tax_included": True,
                            "fuel_reference_price": "0",
                            "fuel_current_price": "0",
                            "fuel_per_km_per_yen": "0",
                            "external_2t_charter": "0",
                        },
                        "cargo_items": [{
                            "category": "その他",
                            "instrument_key": "other",
                            "description": "案件受付後に入力",
                            "maker_model": "",
                            "quantity": "1",
                            "condition": "要確認",
                            "valuation_mode": "manual",
                            "unit_value": "0",
                            "total_value": "0",
                            "volume_points": "0",
                            "notes": "型番・再調達価格を案件ごとに確認",
                        }],
                        "estimate_items": [{
                            "description": "外部運送会社の正式見積",
                            "quantity": "1",
                            "unit": "式",
                            "unit_price": "0",
                            "amount": "0",
                            "details": "国土交通省標準運賃は参考のみ。正式見積取得後に確定",
                        }],
                    },
                },
            )

            self.assertEqual(response.status_code, 201, response.get_json())
            self.assertEqual(response.json["values"]["workflow_status"], "quote_pending")
            self.assertEqual(response.json["values"]["vehicle_class"], "light_cargo")
            self.assertEqual(response.json["values"]["pricing_basis"], "light_cargo_reference")
            self.assertEqual(response.json["values"]["freight_operation"]["loading_support_mode"], "customer_assisted")
            self.assertEqual(response.json["values"]["freight_rate_master"]["distance_per_km_151_plus"], "132")
            self.assertTrue(response.json["values"]["freight_rate_master"]["tax_included"])
            self.assertFalse(response.json["values"]["freight_rate_master"]["verified"])

            future_rate_payload = response.get_json()
            future_rate_payload["values"]["freight_rate_master"]["effective_date"] = "2026-08-24"
            rejected_future_rate = client.post(
                "/api/contracts",
                headers={"X-Editor-Password": "editor-secret"},
                json=future_rate_payload,
            )
            self.assertEqual(rejected_future_rate.status_code, 400)
            self.assertIn("見積作成日以前", rejected_future_rate.json["error"])

            ready_payload = response.get_json()
            ready_payload["values"]["workflow_status"] = "ready"
            rejected = client.post(
                "/api/contracts",
                headers={"X-Editor-Password": "editor-secret"},
                json=ready_payload,
            )
            self.assertEqual(rejected.status_code, 400)
            self.assertIn("正式見積の発行準備", rejected.json["error"])

    def test_transport_sheet_api_uses_authenticated_apps_script(self):
        payload = {
            "client_name": "〇〇高等学校吹奏楽部",
            "transport_name": "演奏会楽器輸送",
            "editor_email": "music@example.com",
            "workflow_status": "quote_pending",
            "transport_provider_mode": "external_carrier",
            "vehicle_class": "medium",
            "pricing_basis": "carrier_quote",
            "carrier_name": "テスト運送株式会社",
            "carrier_quote_url": "https://example.com/quote.pdf",
            "carrier_quote_date": "2026-08-23",
            "cargo_items": [{"description": "チューバ", "quantity": "2"}],
            "freight_rate_master": {"effective_date": "2026-08-23"},
            "instrument_price_master": {"effective_date": "2026-08-23"},
        }
        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "editor-secret",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_request:
            send_request.return_value = {
                "ok": True,
                "cargoUrl": "https://docs.google.com/spreadsheets/d/cargo/edit",
                "feeUrl": "https://docs.google.com/spreadsheets/d/cargo/edit#gid=2",
            }
            response = create_app(database_url="").test_client().post(
                "/api/contracts/transport-sheet",
                headers={"X-Editor-Password": "editor-secret"},
                json=payload,
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["cargo_url"], send_request.return_value["cargoUrl"])
        self.assertEqual(response.json["fee_url"], send_request.return_value["feeUrl"])
        self.assertEqual(send_request.call_args.kwargs["action"], "generate_transport_sheet")
        self.assertEqual(send_request.call_args.args[2]["editor_email"], "music@example.com")
        self.assertEqual(send_request.call_args.args[2]["workflow_status"], "quote_pending")
        self.assertEqual(send_request.call_args.args[2]["carrier_name"], "テスト運送株式会社")

    def test_admin_pages_hide_password_form_and_require_explicit_logout(self):
        client = create_app(database_url="").test_client()
        index_page = client.get("/").get_data(as_text=True)
        schedule_page = client.get("/schedule/").get_data(as_text=True)

        self.assertIn('id="updates-editor-logout" type="button" data-history-back', index_page)
        self.assertIn("let updatesEditorKey = '';", index_page)
        self.assertNotIn("updatesEditorPassword', password", index_page)
        self.assertIn("updatesEditorLogin.reset();", index_page)
        self.assertIn('id="admin-logout" type="button" data-history-back', schedule_page)
        self.assertIn('passwordInput.value = "";', schedule_page)

    def test_all_back_links_use_shared_previous_page_navigation(self):
        client = create_app(database_url="").test_client()
        page_paths = (
            "/lesson/",
            "/lesson/application-form.html",
            "/products/",
            "/download-guide/",
            "/legal/",
            "/legal/privacy-policy.html",
            "/schedule/",
            "/video/",
        )

        for page_path in page_paths:
            with self.subTest(page_path=page_path):
                page = client.get(page_path).get_data(as_text=True)
                self.assertIn("data-history-back", page)
                self.assertIn('src="../back-navigation.js"', page)

    def test_docker_image_includes_contract_generator(self):
        dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(
            encoding="utf-8"
        )

        self.assertIn("apt-get install -y --no-install-recommends curl", dockerfile)
        self.assertIn("COPY healthcheck-prod.sh ./", dockerfile)
        self.assertIn("COPY contract-generator ./contract-generator", dockerfile)
        self.assertIn("COPY app.py index.html back-navigation.js build_product.py ./", dockerfile)

        compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("CONTRACTS_DIR: /contracts", compose)
        self.assertIn("契約書管理:/contracts", compose)

    def test_production_healthcheck_requires_transpose_checkout(self):
        healthcheck = (Path(__file__).resolve().parents[1] / "healthcheck-prod.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("Metronome checkout is unavailable", healthcheck)
        self.assertIn("Trumpet Transpose Lab checkout is unavailable", healthcheck)
        self.assertIn("'\"enabled\":true'", healthcheck)
        self.assertIn("'\"checkout_available\":true'", healthcheck)
        self.assertNotIn("Trumpet Transpose Lab is not in unpublished mode", healthcheck)

    def test_contract_api_saves_individual_contracts_a_and_c(self):
        cases = [
            (
                "typeA",
                "音楽指導・支援",
                {
                    "work": "吹奏楽部の合奏指導",
                    "amount": "税込30,000円",
                    "term": "2026年9月1日",
                    "special_terms": "安全管理体制を事前に確認する。",
                },
                "work",
            ),
            (
                "typeC",
                "WEB・アプリ",
                {
                    "deliverable": "予約管理Webアプリケーション一式",
                    "amount": "税込220,000円",
                    "deadline": "2026年10月31日",
                    "special_terms": "指定環境で検収を行う。",
                },
                "deliverable",
            ),
        ]
        for doc_type, department, values, preserved_key in cases:
            with self.subTest(doc_type=doc_type), tempfile.TemporaryDirectory() as temporary_directory, patch.dict(
                os.environ, {"EDITOR_PASSWORD": "editor-secret"}
            ):
                client = create_app(
                    database_url="", contracts_dir=Path(temporary_directory)
                ).test_client()
                response = client.post(
                    "/api/contracts",
                    headers={"X-Editor-Password": "editor-secret"},
                    json={
                        "doc_type": doc_type,
                        "client_name": "契約先テスト",
                        "client_representative": "代表 山田 太郎",
                        "contract_date": "2026-08-24",
                        "values": values,
                    },
                )

                self.assertEqual(response.status_code, 201)
                self.assertRegex(
                    response.json["contract_id"],
                    rf"^{doc_type}-20260824-[a-f0-9]{{8}}$",
                )
                self.assertEqual(response.json["department"], department)
                self.assertEqual(
                    response.json["values"][preserved_key], values[preserved_key]
                )

    def test_contract_api_saves_and_lists_by_department(self):
        with tempfile.TemporaryDirectory() as temporary_directory, tempfile.TemporaryDirectory() as server_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            client = create_app(
                database_url="",
                contracts_dir=Path(temporary_directory),
                contract_replica_dirs=(Path(server_directory),),
            ).test_client()
            headers = {"X-Editor-Password": "editor-secret"}
            payload = {
                "doc_type": "typeB",
                "client_name": "〇〇楽団",
                "client_representative": "代表 山田 太郎",
                "contract_date": "2026-08-21",
                "values": {
                    "cargo": "管楽器一式",
                    "value": "金 1,000万円",
                    "route": "滑川町から会場まで",
                    "special_terms": "申告内容と補償条件を事前に確認する。",
                },
            }

            saved = client.post("/api/contracts", json=payload, headers=headers)
            listed = client.get("/api/contracts", headers=headers)

            self.assertEqual(saved.status_code, 201)
            self.assertRegex(saved.json["contract_id"], r"^typeB-20260821-[a-f0-9]{8}$")
            contract_path = Path(temporary_directory) / "transport" / f'{saved.json["contract_id"]}.json'
            self.assertTrue(contract_path.is_file())
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json["storage_path"], str(Path(temporary_directory).resolve()))
            self.assertEqual(len(listed.json["contracts"]), 1)
            self.assertEqual(listed.json["contracts"][0]["department"], "楽器輸送")
            self.assertEqual(listed.json["contracts"][0]["client_name"], "〇〇楽団")

            loaded = client.get(
                f'/api/contracts/{saved.json["contract_id"]}', headers=headers
            )
            self.assertEqual(loaded.status_code, 200)
            self.assertEqual(loaded.json["contract"]["values"]["cargo"], "管楽器一式")

            server_contract_path = (
                Path(server_directory) / "transport" / f'{saved.json["contract_id"]}.json'
            )
            server_contract_path.parent.mkdir(parents=True)
            server_contract_path.write_text(contract_path.read_text(encoding="utf-8"), encoding="utf-8")

            first_confirmation = client.delete(
                f'/api/contracts/{saved.json["contract_id"]}',
                json={"confirmation_id": "incorrect-id"},
                headers=headers,
            )
            self.assertEqual(first_confirmation.status_code, 400)
            self.assertTrue(contract_path.is_file())
            deleted = client.delete(
                f'/api/contracts/{saved.json["contract_id"]}',
                json={"confirmation_id": saved.json["contract_id"]},
                headers=headers,
            )

            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(deleted.json["deleted"])
            self.assertEqual(deleted.json["deleted_count"], 2)
            self.assertFalse(contract_path.exists())
            self.assertFalse(server_contract_path.exists())

    def test_contract_api_requires_editor_password(self):
        with tempfile.TemporaryDirectory() as temporary_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            client = create_app(
                database_url="", contracts_dir=Path(temporary_directory)
            ).test_client()

            self.assertEqual(client.get("/api/contracts").status_code, 401)
            self.assertEqual(
                client.post("/api/contracts", json={}).status_code, 401
            )
            self.assertEqual(
                client.delete(
                    "/api/contracts/typeB-20260821-1234abcd",
                    json={"confirmation_id": "typeB-20260821-1234abcd"},
                ).status_code,
                401,
            )

    def test_explicit_empty_database_url_disables_database_initialization(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://example/db"}):
            with patch("app.initialize_database") as initialize_database:
                create_app(database_url="")

        initialize_database.assert_not_called()

    def test_missing_favicon_is_handled_without_a_404(self):
        test_app = create_app(database_url="")
        response = test_app.test_client().get("/favicon.ico")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.data, b"")

    def test_shared_back_navigation_script_is_served(self):
        response = create_app(database_url="").test_client().get("/back-navigation.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/javascript")
        script = response.get_data(as_text=True)
        self.assertIn("window.history.back()", script)
        self.assertIn("[data-history-back]", script)
        self.assertIn("window.siteHistoryBack = goBack", script)

    def test_static_app_directory_urls_serve_index_without_exposing_data_root(self):
        test_app = create_app(database_url="")
        test_app.testing = True
        client = test_app.test_client()

        for path in ("/pdf/", "/video/", "/music%20App/"):
            with self.subTest(path=path):
                with client.get(path) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.mimetype, "text/html")

        self.assertEqual(client.get("/data/").status_code, 404)

    def test_admin_password_reset_quotes_special_characters(self):
        reset_script = (Path(__file__).parents[1] / "reset-admin-password.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("from dotenv import set_key", reset_script)
        self.assertIn('quote_mode="always"', reset_script)

    def test_app_loads_local_environment_settings(self):
        app_source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")
        requirements = (Path(__file__).parents[1] / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn("from dotenv import load_dotenv", app_source)
        self.assertIn("load_dotenv(BASE_DIR / \".env\")", app_source)
        self.assertIn("python-dotenv==", requirements)

    def test_apps_script_requests_allow_slow_write_operations(self):
        dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("--timeout 120", dockerfile)

        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'

        with patch("app.urllib_request.urlopen", return_value=response) as urlopen:
            result = send_lesson_reservation(
                "https://script.google.com/example",
                "test-secret",
                {},
                action="list",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 40)

    def test_apps_script_request_retries_temporary_html_response(self):
        html_response = MagicMock()
        html_response.__enter__.return_value.read.return_value = b"<html>Error</html>"
        json_response = MagicMock()
        json_response.__enter__.return_value.read.return_value = b'{"ok": true}'

        with patch(
            "app.urllib_request.urlopen",
            side_effect=[html_response, json_response],
        ) as urlopen:
            result = send_lesson_reservation(
                "https://script.google.com/example",
                "test-secret",
                {},
                action="update",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(urlopen.call_count, 2)
        first_payload = urlopen.call_args_list[0].args[0].data
        second_payload = urlopen.call_args_list[1].args[0].data
        self.assertEqual(first_payload, second_payload)

    def test_apps_script_read_request_does_not_extend_page_timeout(self):
        html_response = MagicMock()
        html_response.__enter__.return_value.read.return_value = b"<html>Error</html>"

        with patch(
            "app.urllib_request.urlopen",
            return_value=html_response,
        ) as urlopen:
            with self.assertRaises(json.JSONDecodeError):
                send_lesson_reservation(
                    "https://script.google.com/example",
                    "test-secret",
                    {},
                    action="list",
                )

        self.assertEqual(urlopen.call_count, 1)

    def test_apps_script_request_stops_after_second_invalid_response(self):
        html_response = MagicMock()
        html_response.__enter__.return_value.read.return_value = b"<html>Error</html>"

        with patch(
            "app.urllib_request.urlopen",
            return_value=html_response,
        ) as urlopen:
            with self.assertRaises(json.JSONDecodeError):
                send_lesson_reservation(
                    "https://script.google.com/example",
                    "test-secret",
                    {},
                    action="delete",
                )

        self.assertEqual(urlopen.call_count, 2)

    def test_apps_script_bulk_slot_update_uses_single_batch_write(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        function = script.split("function upsertSlotStatusRange", 1)[1].split(
            "function upsertSlotStatus", 1
        )[0]

        self.assertIn("rowIndexes = {}", function)
        self.assertIn("rowUpdatedTimes = {}", function)
        self.assertIn("setValues(rows)", function)
        self.assertNotIn("upsertSlotStatus(sheet", function)

    def test_apps_script_uses_latest_duplicate_slot_state(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        list_function = script.split("function listSlotStatuses", 1)[1].split(
            "function upsertSlotStatusRange", 1
        )[0]
        find_function = script.split("function findSlotRow", 1)[1].split(
            "function getSlotStatus", 1
        )[0]

        self.assertIn("slotsByKey = {}", list_function)
        self.assertIn("slotUpdatedAt(row[4])", list_function)
        self.assertIn("Object.keys(slotsByKey)", list_function)
        self.assertIn("slotUpdatedAt(values[index][4])", find_function)
        self.assertIn("return selectedRow", find_function)

    def test_apps_script_consultation_requests_do_not_occupy_shared_slot(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        create_action = script.split('if (action === "create")', 1)[1].split(
            'if (action === "get_slot_statuses")', 1
        )[0]
        update_action = script.split('if (action === "update")', 1)[1].split(
            'if (action === "cancel")', 1
        )[0]
        conflict_function = script.split("function findReservationSlotConflict", 1)[1].split(
            "function getSlotRecord", 1
        )[0]
        active_statuses_function = script.split("function activeReservationSlotStatuses", 1)[1].split(
            "function upsertSlotStatusRange", 1
        )[0]

        self.assertIn('if (time === "要相談")', create_action)
        self.assertIn('nextTimes[0] !== "要相談"', update_action)
        self.assertIn('times[index] === "要相談" && slot.status !== "お休み"', conflict_function)
        self.assertIn('if (startTime === "要相談")', active_statuses_function)

    def test_apps_script_hides_cancelled_reservations_from_admin_list(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        list_function = script.split("function listReservations", 1)[1].split(
            "function reservationStatusToSlotStatus", 1
        )[0]

        self.assertIn("values.filter(function (row)", list_function)
        self.assertIn('trim() !== "キャンセル"', list_function)

    def test_apps_script_updates_legacy_reservation_slots(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        update_action = script.split('if (action === "update")', 1)[1].split(
            'if (action === "delete")', 1
        )[0]
        release_function = script.split("function releaseReservationSlots", 1)[1].split(
            "function expandTimes", 1
        )[0]

        self.assertIn("reservationSlotsMatch(", update_action)
        self.assertIn('nextSlotStatus === "空き" || keepsCurrentSlots', update_action)
        self.assertIn('if (!keepsCurrentSlots || nextSlotStatus === "空き")', update_action)
        self.assertIn("upsertSlotStatusRange(", update_action)
        self.assertIn('slot.note === "受付自動設定"', release_function)
        self.assertIn("slot.source !== reservationId && !isLegacyReservationSlot", release_function)

    def test_apps_script_reconciles_orphaned_legacy_slots_on_read(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        do_post = script.split("function doPost", 1)[1].split(
            "function getSpreadsheet", 1
        )[0]
        list_function = script.split("function listSlotStatuses", 1)[1].split(
            "function upsertSlotStatusRange", 1
        )[0]

        self.assertIn('"get_slot_statuses"', do_post.split("needsReservationSheet", 1)[1])
        self.assertIn("listSlotStatuses(slotSheet, sheet, from, to)", do_post)
        self.assertIn("activeReservationSlotStatuses(reservationSheet)", list_function)
        self.assertIn('if (note === "受付自動設定")', list_function)
        self.assertIn('status = reservationStatuses[key] || "空き"', list_function)
        self.assertIn("function activeReservationSlotStatuses", list_function)

    def test_apps_script_reads_skip_lock_and_open_spreadsheet_once(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        do_post = script.split("function doPost", 1)[1].split(
            "function getSpreadsheet", 1
        )[0]

        self.assertIn('var writeActions = ["create", "consultation", "generate_transport_sheet", "upsert_slot_status_range", "update", "delete", "cancel"]', do_post)
        self.assertIn("if (writeActions.indexOf(action) !== -1)", do_post)
        self.assertIn("var spreadsheet = getSpreadsheet();", do_post)
        self.assertIn("getReservationSheet(spreadsheet)", do_post)
        self.assertIn("getSlotStatusSheet(spreadsheet)", do_post)
        self.assertEqual(script.count("SpreadsheetApp.openById"), 1)

    def test_apps_script_caches_update_and_delete_results_for_safe_retry(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        self.assertIn('var SCRIPT_VERSION = "2026-08-27-lesson-types-v26";', script)
        self.assertIn("confirmedReservationCounts(sheet, slotSheet, from, to)", script)
        self.assertIn('source.indexOf("admin:") !== 0 && source !== "admin"', script)
        self.assertIn('return "admin:" + Utilities.getUuid();', script)
        self.assertIn("legacyAdminTimes[dateText].push", script)
        self.assertIn("minutes - minuteValues[index - 1] > 15", script)
        self.assertIn('data.request_id || ""', script)
        self.assertIn('get("admin:" + requestId)', script)
        self.assertIn('put("admin:" + requestId, JSON.stringify(data), 600)', script)
        self.assertIn("return adminActionResponse({ ok: true, reservationId: reservationId }, requestId);", script)

    def test_apps_script_email_has_utf8_html_fallback(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        function = script.split("function sendReservationAutoReply", 1)[1].split(
            "function safeCell", 1
        )[0]

        self.assertIn('charset="UTF-8"', function)
        self.assertIn("htmlBody:", function)
        self.assertIn("sanitizeMailHeader(data.email)", function)
        self.assertIn("sanitizeMailHeader", script)
        self.assertIn('var ADMIN_NOTIFICATION_EMAIL = "zuomuj924@gmail.com";', script)
        self.assertIn("bcc: ADMIN_NOTIFICATION_EMAIL", function)

    def test_apps_script_sends_confirmation_email_on_first_confirmed_transition(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        create_action = script.split('if (action === "create")', 1)[1].split(
            'if (action === "get_slot_statuses")', 1
        )[0]
        update_action = script.split('if (action === "update")', 1)[1].split(
            'if (action === "cancel")', 1
        )[0]
        reply_function = script.split("function sendReservationAutoReply", 1)[1].split(
            "function sendReservationConfirmation", 1
        )[0]
        confirmation_function = script.split("function sendReservationConfirmation", 1)[1].split(
            "function sanitizeMailHeader", 1
        )[0]

        self.assertIn('"確認中"', create_action)
        self.assertIn("現在の状態: 確認中", reply_function)
        self.assertIn('nextStatus === "確定" && currentReservation.status !== "確定"', update_action)
        self.assertIn("sendReservationConfirmation({", update_action)
        self.assertIn("name: nextName", update_action)
        self.assertIn("email: nextEmail", update_action)
        self.assertIn("confirmationEmailSent: confirmationEmailSent", update_action)
        self.assertIn("レッスン予約確定のお知らせ", confirmation_function)
        self.assertIn("確定日:", confirmation_function)
        self.assertIn("確定時間:", confirmation_function)

    def test_lesson_admin_uses_current_statuses_and_collapses_confirmed_reservations(self):
        page = (Path(__file__).parents[1] / "schedule" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('["確認中","確定","キャンセル"]', page)
        self.assertNotIn('["受付","調整中","確認中","確定","キャンセル"]', page)
        self.assertIn("確定した予約者一覧", page)
        self.assertIn("空き状況は15分を1枠として管理", page)

        for removed_status in ("受付", "調整中"):
            with self.assertRaisesRegex(ValueError, "確認中・確定・キャンセル"):
                validate_lesson_reservation_update({"status": removed_status})

    def test_lesson_type_names_high_school_students_and_adults(self):
        lesson_page = (Path(__file__).parents[1] / "lesson" / "index.html").read_text(
            encoding="utf-8"
        )
        schedule_page = (Path(__file__).parents[1] / "schedule" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("高校生以上・大人", lesson_page)
        self.assertIn("高校生以上・大人", schedule_page)

    def test_apps_script_reservation_ids_do_not_reuse_deleted_rows(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        create_action = script.split('if (action === "create")', 1)[1].split(
            'if (action === "get_slot_statuses")', 1
        )[0]
        function = script.split("function createReservationId", 1)[1].split(
            "function findReservationRowById", 1
        )[0]

        self.assertIn("createReservationId(now, sheet)", create_action)
        self.assertIn("sheet.getRange(2, 2", function)
        self.assertIn("highestSequence", function)

    def test_apps_script_persists_custom_group_duration(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        self.assertIn('"所要時間（分）"', script)
        self.assertIn('{ key: "duration_minutes", column: 11 }', script)
        self.assertIn("durationMinutes: getLessonDuration(values[4], values[8])", script)
        self.assertIn("duration_minutes: getLessonDuration(row[6], row[10]) || null", script)

    def test_apps_script_code_is_es5_compatible(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        for unsupported in (
            r"\bconst\b",
            r"\blet\b",
            r"=>",
            r"`",
            r"new Map",
            r"\.padStart\(",
            r"Number\.isNaN",
            r"console\.",
        ):
            self.assertNotRegex(script, unsupported)

    def test_apps_script_transport_sheet_includes_case_workflow(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        self.assertIn('["案件進行状態", safeCell(data.workflow_status)]', script)
        self.assertIn('["外部運送会社名", safeCell(data.carrier_name)]', script)
        self.assertIn('["正式見積書URL", safeCell(data.carrier_quote_url)]', script)
        self.assertIn('["参考運賃出典URL", safeCell(rateMaster.source_url)]', script)
        self.assertIn('["軽貨物参考値の注意", data.pricing_basis === "light_cargo_reference"', script)
        self.assertIn('["151km以上 1km加算", Number(rateMaster.distance_per_km_151_plus', script)
        self.assertIn('["軽貨物2時間・20kmまで", Number(rateMaster.charter_2h', script)
        self.assertIn('["休日割増率", Number(rateMaster.holiday_percent', script)
        self.assertIn('feeSheet.getRange(16, 2, 14, 1).setNumberFormat("¥#,##0")', script)
        self.assertIn('feeSheet.getRange(30, 2, 2, 1).setNumberFormat(\'0"%"\')', script)
        self.assertIn('feeSheet.getRange(34, 2).setNumberFormat("0.##")', script)

    def test_apps_script_avoids_trailing_commas_in_function_calls(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        self.assertNotRegex(script, r",\s*\n\s*[\)\}\]]")

    def test_apps_script_manifest_enables_v8_runtime(self):
        manifest = json.loads(
            (Path(__file__).parents[1] / "google-apps-script" / "appsscript.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(manifest["runtimeVersion"], "V8")

    def test_apps_script_do_post_handles_direct_editor_run(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        self.assertIn("if (!event || !event.postData || !event.postData.contents)", script)
        self.assertIn("if (lockAcquired)", script)

    def test_newest_and_later_same_day_items_come_first(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "updates.txt"
            path.write_text(
                "2026-08-05 | A | first\n"
                "2026-08-04 | B | older\n"
                "2026-08-05 | C | later\n",
                encoding="utf-8",
            )

            updates = load_updates(path)

        self.assertEqual([item["content"] for item in updates], ["later", "first", "older"])

    def test_media_and_youtube_are_parsed(self):
        update = parse_update_line(
            "2026-08-05 | 動画 | おすすめです [video:https://youtu.be/Abc_123?x=1]",
            0,
        )

        self.assertEqual(update["content"], "おすすめです")
        self.assertEqual(update["media_type"], "video")
        self.assertEqual(
            update["youtube_embed_url"],
            "https://www.youtube.com/embed/Abc_123?playsinline=1&rel=0",
        )

    def test_bare_media_filename_uses_media_directory(self):
        self.assertEqual(normalize_media_url("photo.jpg"), "data/media/photo.jpg")

    def test_updates_api_returns_public_fields_without_cache(self):
        client = create_app().test_client()

        response = client.get("/api/updates")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertGreater(len(response.json), 0)
        self.assertNotIn("sort_date", response.json[0])
        self.assertIn("index", response.json[0])

    def test_editor_api_requires_password_for_all_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "updates.txt"
            path.write_text("2026-08-01 | つぶやき | 最初の投稿\n", encoding="utf-8")
            client = create_app(path).test_client()
            payload = {
                "date": "2026-08-09",
                "category": "お役立ち",
                "content": "スマートフォンから追加",
                "media_type": "",
                "media_url": "",
            }

            with patch.dict(os.environ, {"EDITOR_PASSWORD": "correct-password"}):
                self.assertEqual(client.post("/api/updates", json=payload).status_code, 401)
                self.assertEqual(
                    client.post(
                        "/api/updates",
                        json=payload,
                        headers={"X-Editor-Password": "wrong-password"},
                    ).status_code,
                    401,
                )

            self.assertEqual(path.read_text(encoding="utf-8"), "2026-08-01 | つぶやき | 最初の投稿\n")

    def test_editor_can_create_edit_and_delete_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "updates.txt"
            path.write_text("2026-08-01 | つぶやき | 最初の投稿\n", encoding="utf-8")
            client = create_app(path).test_client()
            headers = {"X-Editor-Password": "correct-password"}
            created = {
                "date": "2026-08-09",
                "category": "お役立ち",
                "content": "スマートフォンから追加",
                "media_type": "image",
                "media_url": "photo.jpg",
            }
            edited = {**created, "content": "変更後の本文", "media_type": "", "media_url": ""}

            with patch.dict(os.environ, {"EDITOR_PASSWORD": "correct-password"}):
                self.assertEqual(client.get("/api/editor", headers=headers).status_code, 200)
                self.assertEqual(client.post("/api/updates", json=created, headers=headers).status_code, 201)
                self.assertEqual(client.put("/api/updates/1", json=edited, headers=headers).status_code, 200)
                self.assertEqual(client.delete("/api/updates/0", headers=headers).status_code, 200)

            self.assertEqual(path.read_text(encoding="utf-8"), "2026-08-09 | お役立ち | 変更後の本文\n")

    def test_editor_password_accepts_non_ascii_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "updates.txt"
            path.write_text("2026-08-01 | つぶやき | 投稿\n", encoding="utf-8")
            client = create_app(path).test_client()

            with patch.dict(os.environ, {"EDITOR_PASSWORD": "編集用パスワード"}):
                response = client.get(
                    "/api/editor",
                    headers={"X-Editor-Password": "編集用パスワード"},
                )

            self.assertEqual(response.status_code, 200)

    def test_contract_editor_uses_token_after_non_ascii_password_login(self):
        with tempfile.TemporaryDirectory() as temporary_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "契約管理パスワード"}
        ):
            client = create_app(
                database_url="", contracts_dir=Path(temporary_directory)
            ).test_client()
            login = client.post(
                "/api/editor",
                json={"editor_password": "契約管理パスワード"},
            )

            self.assertEqual(login.status_code, 200)
            self.assertTrue(login.json["editor_token"])
            listed = client.get(
                "/api/contracts",
                headers={"X-Editor-Token": login.json["editor_token"]},
            )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json["contracts"], [])

    def test_update_editor_requires_iso_calendar_date(self):
        valid_payload = {
            "date": "2026-08-16",
            "category": "お知らせ",
            "content": "本文",
            "media_type": "",
            "media_url": "",
        }

        self.assertEqual(validate_update(valid_payload)["date"], "2026-08-16")
        for invalid_date in ("2026/08/16", "2026.08.16", "2026-08-16T12:30:00", "2026-02-30"):
            with self.subTest(invalid_date=invalid_date):
                with self.assertRaisesRegex(ValueError, "日付を正しく"):
                    validate_update({**valid_payload, "date": invalid_date})

    def test_lesson_page_is_available(self):
        client = create_app().test_client()

        response = client.get("/lesson/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Cache-Control"],
            "no-store, no-cache, must-revalidate, max-age=0",
        )
        page = response.get_data(as_text=True)
        self.assertIn("kazooささきトランペット教室", page)
        self.assertIn("講師プロフィール", page)
        self.assertIn("佐々木 久和", page)
        self.assertIn("グループレッスン・部活動指導", page)
        self.assertIn("別途相談", page)
        self.assertIn('href="../"', page)
        self.assertIn('id="reservation-form"', page)
        self.assertNotIn('id="app-purchase-button"', page)
        self.assertIn('class="schedule-callout"', page)
        self.assertIn('class="schedule-callout-link" href="../schedule/"', page)
        self.assertLess(page.index('id="schedule-callout-title"'), page.index('id="reservation-title"'))
        self.assertIn("const renderReservationApi =", page)
        self.assertIn('"中学生": 45', page)
        self.assertIn('"高校生以上": 60', page)
        self.assertIn("function occupiedTimes", page)
        self.assertIn("06:45〜17:00／それ以外は要相談", page)
        self.assertIn('1: [...makeTimeRange(6, 45, 9, 0), ...makeTimeRange(20, 30, 22, 0)]', page)
        self.assertIn('4: makeTimeRange(6, 45, 12, 0)', page)
        self.assertIn('5: [...makeTimeRange(6, 45, 17, 0), "要相談"]', page)
        self.assertIn('6: ["要相談"]', page)
        self.assertIn("土・日：要相談", page)
        self.assertIn("体験レッスン・小学生は毎時00分／30分開始", page)
        self.assertIn('id="elementary-lesson-toggle" type="button" aria-expanded="false"', page)
        self.assertIn('id="elementary-trial" aria-labelledby="elementary-trial-title" hidden', page)
        elementary_card_start = page.index('<article class="price-card">')
        elementary_card_end = page.index("</article>", elementary_card_start)
        self.assertIn('id="elementary-trial"', page[elementary_card_start:elementary_card_end])
        self.assertIn("トランペット体験レッスン！憧れの音を鳴らしてみよう♪", page)
        self.assertIn("鳴らなくてもOK！感覚を掴もう", page)
        self.assertIn('id="junior-high-lesson-toggle" type="button" aria-expanded="false"', page)
        self.assertIn('id="junior-high-trial" aria-labelledby="junior-high-trial-title" hidden', page)
        self.assertIn("中学生のためのトランペットレッスン！", page)
        self.assertIn("コンクール・ソロ曲の攻略", page)
        self.assertIn('id="high-school-adult-lesson-toggle" type="button" aria-expanded="false"', page)
        self.assertIn('id="high-school-adult-trial" aria-labelledby="high-school-adult-trial-title" hidden', page)
        self.assertIn("【高校生・大人向け】トランペット オーダーメイド・レッスン", page)
        self.assertIn("本格的な技術UP・音大受験対策", page)
        self.assertIn('content: "詳細を見る ＋"', page)
        self.assertIn('document.querySelectorAll(".lesson-detail-toggle")', page)
        self.assertIn("detail.hidden = true", page)
        self.assertIn("detail.hidden = isOpen", page)
        self.assertIn("scroll-snap-type: none", page)
        self.assertIn("function availableTimesForLesson", page)
        self.assertIn('lessonType === "グループ・部活動指導"', page)
        self.assertIn('time === "要相談"', page)
        self.assertIn('status === "お休み"', page)

    def test_lesson_page_script_does_not_reference_missing_slot_inputs(self):
        client = create_app().test_client()

        response = client.get("/lesson/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        page = response.get_data(as_text=True)
        self.assertNotIn("slotStartDateInput", page)
        self.assertNotIn("slotEndDateInput", page)

    def test_lesson_reservation_options_supports_cors_preflight(self):
        client = create_app().test_client()

        response = client.options("/api/lesson-reservations")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response.headers["Access-Control-Allow-Methods"], "GET, POST, OPTIONS")
        self.assertEqual(
            response.headers["Access-Control-Allow-Headers"],
            "Content-Type, X-Editor-Password",
        )

    def test_lesson_slot_statuses_options_supports_cors_preflight(self):
        client = create_app().test_client()

        response = client.options("/api/lesson-slot-statuses")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response.headers["Access-Control-Allow-Methods"], "GET, OPTIONS")

    def test_products_page_only_shows_store_admin_controls(self):
        client = create_app().test_client()

        response = client.get("/products/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("管理者用：販売設定", page)
        self.assertIn('id="app-sales-form"', page)
        self.assertIn('id="app-sales-password"', page)
        self.assertIn('id="app-sales-enabled"', page)
        self.assertIn('id="app-sales-save"', page)
        self.assertIn('appSalesForm.addEventListener("submit"', page)
        self.assertIn('"X-Editor-Password": password', page)
        self.assertIn("編集用パスワードを入力してください。", page)
        self.assertIn("metronome-purchase-session", page)
        self.assertIn('purchaseMode === "reissue"', page)
        self.assertIn("リンクを再発行しています…", page)
        self.assertIn('method: "HEAD"', page)
        self.assertIn("downloadInProgress", page)
        self.assertIn("location.assign(downloadUrl)", page)
        self.assertIn("history.replaceState", page)
        self.assertIn("crypto.randomUUID", page)
        self.assertIn("checkout_request_id", page)
        self.assertIn("appStoreStatus.textContent = checkoutErrorMessage", page)
        self.assertNotIn("URL.createObjectURL", page)
        self.assertNotIn("slot-admin", page)

    def test_main_page_links_to_products_without_embedded_app_or_reservation(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('href="products/"', page)
        self.assertIn('href="legal/#tokusho"', page)
        self.assertIn('href="legal/privacy-policy.html"', page)
        self.assertIn(".footer-address span:last-child", page)
        self.assertIn("font-size: 0.84rem", page)
        self.assertIn('.footer-links a[href="lesson/"]', page)
        self.assertNotIn('id="reservation-form"', page)
        self.assertNotIn('<iframe class="app-frame"', page)

    def test_lesson_page_shows_duration_and_requires_privacy_consent(self):
        client = create_app().test_client()

        response = client.get("/lesson/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('id="reservation-duration"', page)
        self.assertIn('name="duration_minutes"', page)
        self.assertIn("reservationDurationMinutes.value = durationMinutes", page)
        self.assertIn("reservationType.value = requestedType;\n        updateReservationDuration();", page)
        self.assertIn('name="privacy_agreed"', page)
        self.assertIn('href="../legal/privacy-policy.html"', page)

    def test_schedule_page_shares_public_calendar_and_admin_panel(self):
        client = create_app().test_client()

        response = client.get("/schedule/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('id="schedule-calendar"', page)
        self.assertIn('id="schedule-lesson-type"', page)
        self.assertIn('id="schedule-retry"', page)
        self.assertIn('id="admin-login-form"', page)
        self.assertIn('id="admin-logout"', page)
        self.assertIn('adminLogout.addEventListener("click"', page)
        self.assertIn('adminStatus.textContent = "ログアウトしました。"', page)
        self.assertIn("reservationList.replaceChildren()", page)
        self.assertIn("api/lesson-slot-statuses", page)
        self.assertIn("api/lesson-reservations", page)
        self.assertIn('requestApi("/api/lesson-admin-health"', page)
        self.assertIn('catch (error) { adminPassword = ""; adminStatus.textContent = error.message; return; }', page)
        self.assertIn("const loginForm = event.currentTarget", page)
        self.assertIn("loginForm.hidden = true", page)
        self.assertNotIn("event.currentTarget.hidden = true", page)
        self.assertIn("ログイン済みです。予約一覧の通信に失敗しました", page)
        self.assertIn('id="reservation-retry"', page)
        self.assertIn('id="reservation-save-all"', page)
        self.assertIn('id="admin-slot-calendar"', page)
        self.assertIn('id="admin-slot-list"', page)
        self.assertIn("function renderAdminSlotCalendar()", page)
        self.assertIn("function renderAdminSlotDetails(value)", page)
        self.assertIn("scheduleSlots = result.slots || []", page)
        self.assertIn('reservationRetry.addEventListener("click"', page)
        self.assertIn('reservationSaveAll.addEventListener("click"', page)
        self.assertIn("for (const editor of editors)", page)
        self.assertIn("let result = await updateReservation", page)
        self.assertIn("result = await updateReservation", page)
        self.assertIn("const failures = []", page)
        self.assertIn('failures.join(" / ")', page)
        self.assertIn('result?.error || "原因不明"', page)
        self.assertIn("if (changedReservationIds.size === 0) await loadReservations()", page)
        self.assertIn("changedReservationIds.size === 0", page)
        self.assertIn('["確認中","確定","キャンセル"]', page)
        self.assertIn("statusSelect.value = result.status || statusSelect.value", page)
        self.assertIn("予約状態を「${statusSelect.value}」へ更新しました。", page)
        self.assertIn("予約は削除済みです。一覧の再読み込みに失敗しました", page)
        self.assertIn('summary.textContent = hasLessonType', page)
        self.assertIn('available ? `空き ${available}` : "満席"', page)
        self.assertIn('` / 予約済 ${confirmed}件`', page)
        self.assertIn("result.confirmed_counts || {}", page)
        self.assertIn('(total ? "受付日" : "休み")', page)
        self.assertIn('"中学生": 45', page)
        self.assertIn("occupiedTimes(time, durationMinutes)", page)
        self.assertIn("controller.abort(), timeoutMs", page)
        self.assertIn("サーバー起動中は最大30秒", page)
        self.assertIn("const { timeoutMs = 30000, ...fetchOptions } = options", page)
        self.assertIn("responseError.isHttpError = true", page)
        self.assertIn("result.delivery_error || result.detail", page)
        self.assertIn("if (error.isHttpError) throw error", page)
        self.assertGreaterEqual(page.count("timeoutMs: 60000"), 1)
        self.assertGreaterEqual(page.count("timeoutMs: 120000"), 2)
        self.assertIn('requestApi("/api/lesson-reservations", { headers: adminHeaders(), timeoutMs: 30000 })', page)
        self.assertIn('reservation.status !== "キャンセル"', page)
        self.assertIn("setInterval(() =>", page)
        self.assertIn("}, 30000);", page)
        self.assertIn('total ? "受付日" : "休み"', page)
        self.assertIn("空き状況を確認しています。表示後に予約時間を選択できます。", page)
        self.assertIn('5: [...makeRange(6,45,17,0), "要相談"]', page)
        self.assertIn('6: ["要相談"]', page)
        self.assertIn('time === "要相談"', page)
        self.assertIn('status === "お休み"', page)
        self.assertIn('timeInput.type = "time"', page)
        self.assertIn("preferred_time: timeInput.value", page)
        self.assertIn('durationInput.step = "15"', page)
        self.assertIn("payload.duration_minutes = Number(durationInput.value)", page)
        self.assertIn("グループレッスン・部活動指導は、開始時刻と所要時間を個別に調整します。", page)
        self.assertIn(".panel { min-width: 0;", page)
        self.assertIn('selectedDateTitle.focus({ preventScroll: true })', page)
        self.assertIn('matchMedia("(max-width: 760px)").matches', page)
        self.assertIn('document.querySelector(".detail-panel").scrollIntoView', page)
        self.assertIn('<option value="空き">予約可</option>', page)

    def test_lesson_page_has_public_cancellation_form(self):
        client = create_app().test_client()

        response = client.get("/lesson/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('id="reservation-cancel-form"', page)
        self.assertIn('name="reservation_id"', page)
        self.assertIn('name="email"', page)
        self.assertIn("api/lesson-reservations/cancel", page)

    def test_lesson_reservation_list_requires_editor_password(self):
        client = create_app().test_client()

        with patch.dict(os.environ, {"EDITOR_PASSWORD": "correct-password"}):
            response = client.get("/api/lesson-reservations")

        self.assertEqual(response.status_code, 401)

    def test_lesson_admin_health_requires_current_apps_script_deployment(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "version": "2026-08-27-lesson-types-v26",
                "capabilities": ["consultation", "generate_transport_sheet", "list", "update", "delete", "cancel", "upsert_slot_status_range"],
            }
            response = client.get("/api/lesson-admin-health", headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ready"])
        self.assertEqual(send_reservation.call_args.kwargs["action"], "health")

    def test_lesson_admin_health_rejects_wrong_apps_script_version(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "version": "2026-08-18-admin-v15",
                "capabilities": ["list", "update", "delete", "cancel", "upsert_slot_status_range"],
            }
            response = client.get("/api/lesson-admin-health", headers=headers)

        self.assertEqual(response.status_code, 503)
        self.assertIn("古いデプロイ", response.json["error"])

    def test_lesson_admin_health_rejects_outdated_apps_script_deployment(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch(
            "app.send_lesson_reservation",
            side_effect=LessonReservationDeliveryError("Unsupported action"),
        ):
            response = client.get("/api/lesson-admin-health", headers=headers)

        self.assertEqual(response.status_code, 503)
        self.assertIn("古いデプロイ", response.json["error"])

    def test_lesson_reservation_list_reports_outdated_apps_script(self):
        client = create_app().test_client()

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch(
            "app.send_lesson_reservation",
            side_effect=LessonReservationDeliveryError("Unsupported action"),
        ):
            response = client.get(
                "/api/lesson-reservations",
                headers={"X-Editor-Password": "correct-password"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("再デプロイ", response.json["error"])

    def test_lesson_reservation_list_returns_admin_data(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "reservations": [
                    {
                        "reservation_id": "R-20260820-001",
                        "status": "調整中",
                        "name": "予約 太郎",
                        "email": "taro@example.com",
                        "phone": "090-1234-5678",
                        "lesson_type": "体験レッスン",
                        "preferred_date": "2026-08-20",
                        "preferred_time": "09:00",
                        "message": "初心者です。",
                    }
                ],
            }
            response = client.get("/api/lesson-reservations", headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["reservations"]), 1)
        self.assertEqual(response.json["reservations"][0]["name"], "予約 太郎")
        self.assertEqual(send_reservation.call_args.kwargs["action"], "list")

    def test_lesson_slot_admin_updates_schedule(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}
        payload = {
            "start_date": "2026-08-20",
            "end_date": "2026-08-20",
            "start_time": "09:00",
            "end_time": "10:00",
            "status": "お休み",
            "note": "休講",
        }

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {"ok": True, "updatedCount": 5}
            response = client.post(
                "/api/lesson-slot-statuses/admin",
                json=payload,
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["updated_count"], 5)
        self.assertEqual(
            send_reservation.call_args.kwargs["action"],
            "upsert_slot_status_range",
        )

    def test_lesson_slot_admin_can_restore_available_mode(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}
        payload = {
            "start_date": "2026-08-20",
            "end_date": "2026-08-20",
            "start_time": "09:00",
            "end_time": "09:30",
            "status": "空き",
            "note": "予約解除",
        }

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation", return_value={"ok": True, "updatedCount": 3}) as send_reservation:
            response = client.post(
                "/api/lesson-slot-statuses/admin",
                json=payload,
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_reservation.call_args.args[2]["status"], "空き")

    def test_user_can_cancel_confirmed_reservation_and_release_slots(self):
        client = create_app().test_client()
        payload = {
            "reservation_id": "R-20260820-001",
            "email": "USER@example.com",
        }

        with patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "reservationId": "R-20260820-001",
                "status": "キャンセル",
                "updatedCount": 4,
                "alreadyCancelled": False,
                "cancellationEmailSent": True,
            }
            response = client.post("/api/lesson-reservations/cancel", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["cancelled"])
        self.assertEqual(response.json["released_count"], 4)
        self.assertFalse(response.json["already_cancelled"])
        self.assertTrue(response.json["cancellation_email_sent"])
        self.assertEqual(send_reservation.call_args.kwargs["action"], "cancel")
        self.assertEqual(send_reservation.call_args.args[2]["email"], "user@example.com")

    def test_user_cancellation_rejects_mismatched_email(self):
        client = create_app().test_client()

        with patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch(
            "app.send_lesson_reservation",
            side_effect=LessonReservationDeliveryError("EMAIL_MISMATCH"),
        ):
            response = client.post(
                "/api/lesson-reservations/cancel",
                json={"reservation_id": "R-20260820-001", "email": "wrong@example.com"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertIn("一致しません", response.json["error"])

    def test_apps_script_user_cancellation_releases_owned_slots(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        cancel_action = script.split('if (action === "cancel")', 1)[1].split(
            'if (action === "delete")', 1
        )[0]

        self.assertIn('storedEmail !== email', cancel_action)
        self.assertIn('setValue("キャンセル")', cancel_action)
        self.assertIn("releaseReservationSlots(", cancel_action)
        self.assertIn("updatedCount: releasedCount", cancel_action)
        self.assertIn('reservation.status === "キャンセル"', cancel_action)
        self.assertIn("updatedCount: repairedCount", cancel_action)
        self.assertIn("sendReservationCancellation({", cancel_action)
        self.assertIn("cancellationEmailSent: cancellationEmailSent", cancel_action)

        cancellation_function = script.split("function sendReservationCancellation", 1)[1].split(
            "function sanitizeMailHeader", 1
        )[0]
        self.assertIn("レッスン予約キャンセル完了", cancellation_function)
        self.assertIn("現在の状態: キャンセル", cancellation_function)

    def test_lesson_reservation_manage_options_supports_cors_preflight(self):
        client = create_app().test_client()

        response = client.options("/api/lesson-reservations/R-20260810-001")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(
            response.headers["Access-Control-Allow-Methods"], "PUT, DELETE, OPTIONS"
        )
        self.assertEqual(
            response.headers["Access-Control-Allow-Headers"],
            "Content-Type, X-Editor-Password",
        )

    def test_lesson_reservation_is_validated_and_forwarded(self):
        client = create_app().test_client()
        payload = {
            "name": "予約 太郎",
            "email": "taro@example.com",
            "phone": "090-1234-5678",
            "lesson_type": "体験レッスン",
            "preferred_date": "2026-08-20",
            "preferred_time": "09:00",
            "message": "初心者です。",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 9)), patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "reservationId": "R-20260820-001",
                "status": "確認中",
                "autoReplySent": True,
            }
            response = client.post("/api/lesson-reservations", json=payload)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response.json["reservation_id"], "R-20260820-001")
        self.assertEqual(response.json["status"], "確認中")
        self.assertEqual(response.json["duration_minutes"], 30)
        self.assertTrue(response.json["auto_reply_sent"])
        send_reservation.assert_called_once_with(
            "https://script.google.com/example",
            "test-secret",
            {
                **payload,
                "duration_minutes": 30,
                "occupied_times": ["09:00", "09:15"],
            },
        )

    def test_lesson_duration_is_derived_from_lesson_type(self):
        base_payload = {
            "name": "予約 太郎",
            "email": "taro@example.com",
            "phone": "",
            "preferred_date": "2026-08-20",
            "preferred_time": "09:00",
            "message": "",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 9)):
            durations = {
                lesson_type: validate_lesson_reservation(
                    {**base_payload, "lesson_type": lesson_type}
                )["duration_minutes"]
                for lesson_type in ["体験レッスン", "小学生", "中学生", "高校生以上"]
            }

        self.assertEqual(
            durations,
            {"体験レッスン": 30, "小学生": 30, "中学生": 45, "高校生以上": 60},
        )

    def test_lesson_duration_expands_to_fifteen_minute_slots(self):
        self.assertEqual(reservation_slot_times("09:00", 30), ["09:00", "09:15"])
        self.assertEqual(
            reservation_slot_times("09:00", 45),
            ["09:00", "09:15", "09:30"],
        )
        self.assertEqual(
            reservation_slot_times("09:00", 60),
            ["09:00", "09:15", "09:30", "09:45"],
        )
        self.assertEqual(reservation_slot_times("要相談", 60), ["要相談"])

    def test_lesson_start_minutes_follow_lesson_type(self):
        base_payload = {
            "name": "予約 太郎",
            "email": "taro@example.com",
            "phone": "",
            "preferred_date": "2026-08-14",
            "message": "",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 9)):
            for lesson_type, accepted, rejected in (
                ("体験レッスン", "09:30", "09:15"),
                ("小学生", "09:30", "09:45"),
                ("中学生", "09:00", "09:30"),
                ("高校生以上", "09:00", "09:30"),
            ):
                validate_lesson_reservation({
                    **base_payload,
                    "lesson_type": lesson_type,
                    "preferred_time": accepted,
                })
                with self.assertRaisesRegex(ValueError, "予約可能時間"):
                    validate_lesson_reservation({
                        **base_payload,
                        "lesson_type": lesson_type,
                        "preferred_time": rejected,
                    })

    def test_group_lesson_is_consultation_without_fixed_duration(self):
        payload = {
            "name": "団体 代表",
            "email": "group@example.com",
            "phone": "",
            "lesson_type": "グループ・部活動指導",
            "preferred_date": "2026-08-14",
            "preferred_time": "要相談",
            "message": "",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 9)):
            values = validate_lesson_reservation(payload)
            with self.assertRaisesRegex(ValueError, "予約可能時間"):
                validate_lesson_reservation({**payload, "preferred_time": "09:00"})

        self.assertIsNone(values["duration_minutes"])
        self.assertEqual(values["occupied_times"], ["要相談"])

    def test_lesson_reservation_duplicate_is_reported(self):
        client = create_app().test_client()
        payload = {
            "name": "予約 太郎",
            "email": "taro@example.com",
            "phone": "090-1234-5678",
            "lesson_type": "体験レッスン",
            "preferred_date": "2026-08-20",
            "preferred_time": "09:00",
            "message": "初心者です。",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 9)), patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "reservationId": "R-20260820-001",
                "status": "調整中",
                "autoReplySent": False,
                "duplicate": True,
            }
            response = client.post("/api/lesson-reservations", json=payload)

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json["duplicate"])
        self.assertFalse(response.json["auto_reply_sent"])

    def test_lesson_reservation_conflict_is_reported(self):
        client = create_app().test_client()
        payload = {
            "name": "予約 太郎",
            "email": "taro@example.com",
            "phone": "090-1234-5678",
            "lesson_type": "体験レッスン",
            "preferred_date": "2026-08-20",
            "preferred_time": "09:00",
            "message": "初心者です。",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 9)), patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "reservationId": "",
                "status": "conflict",
                "autoReplySent": False,
                "conflict": True,
            }
            response = client.post("/api/lesson-reservations", json=payload)

        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json["conflict"])
        self.assertFalse(response.json["saved"])

    def test_lesson_reservation_rejects_fifth_active_booking(self):
        client = create_app().test_client()
        payload = {
            "name": "予約 太郎",
            "email": "user@example.com",
            "phone": "",
            "lesson_type": "体験レッスン",
            "preferred_date": "2026-08-20",
            "preferred_time": "09:00",
            "message": "",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 9)), patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "reservationLimit": True,
                "maxReservations": 4,
            }
            response = client.post("/api/lesson-reservations", json=payload)

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json["saved"])
        self.assertTrue(response.json["reservation_limit"])
        self.assertEqual(response.json["max_reservations"], 4)
        self.assertIn("最大4枠", response.json["error"])

    def test_apps_script_counts_only_active_future_reservations_for_limit(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        create_action = script.split('if (action === "create")', 1)[1].split(
            'if (action === "get_slot_statuses")', 1
        )[0]
        count_function = script.split("function countActiveReservationsByEmail", 1)[1].split(
            "function normalizeReservationDate", 1
        )[0]

        self.assertLess(
            create_action.index("findDuplicateReservation(sheet, data, now)"),
            create_action.index("countActiveReservationsByEmail(sheet, data.email, now)"),
        )
        self.assertIn("MAX_ACTIVE_RESERVATIONS_PER_EMAIL = 4", script)
        self.assertIn('status === "キャンセル"', count_function)
        self.assertIn("preferredDate < today", count_function)
        self.assertIn("rowEmail !== email", count_function)

    def test_lesson_slot_statuses_returns_slots(self):
        client = create_app().test_client()

        with patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "slots": [{"date": "2026-08-20", "time": "09:00", "status": "予約済"}],
                "confirmedCounts": {"2026-08-20": 1},
            }
            response = client.get("/api/lesson-slot-statuses?from=2026-08-20&to=2026-08-20")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(len(response.json["slots"]), 1)
        self.assertEqual(response.json["slots"][0]["status"], "予約済")
        self.assertEqual(response.json["confirmed_counts"], {"2026-08-20": 1})
        self.assertEqual(send_reservation.call_args.kwargs["action"], "get_slot_statuses")

    def test_lesson_slot_statuses_normalizes_apps_script_date_times(self):
        slots = normalize_slot_statuses(
            [
                {
                    "date": "2026-08-20",
                    "time": "Sat Dec 30 1899 07:45:00 GMT+0900 (日本標準時)",
                    "status": "予約済",
                },
                {"date": "2026-08-20", "time": "要相談", "status": "空き"},
            ]
        )

        self.assertEqual(slots[0]["time"], "07:45")
        self.assertEqual(slots[1]["time"], "要相談")

    def test_lesson_slot_statuses_reports_unavailable_service(self):
        client = create_app().test_client()

        with patch.dict(
            os.environ,
            {"GOOGLE_APPS_SCRIPT_URL": "", "GOOGLE_APPS_SCRIPT_SECRET": ""},
        ):
            response = client.get("/api/lesson-slot-statuses?from=2026-08-20&to=2026-08-20")

        self.assertEqual(response.status_code, 503)
        self.assertIn("空き状況", response.json["error"])

    def test_lesson_reservation_rejects_invalid_input(self):
        client = create_app().test_client()

        response = client.post(
            "/api/lesson-reservations",
            json={"name": "予約 太郎", "email": "invalid"},
        )

        self.assertEqual(response.status_code, 400)

    def test_lesson_reservation_reports_missing_server_settings(self):
        client = create_app().test_client()
        payload = {
            "name": "予約 太郎",
            "email": "taro@example.com",
            "phone": "",
            "lesson_type": "体験レッスン",
            "preferred_date": "2026-08-11",
            "preferred_time": "07:00",
            "message": "",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 10)), patch.dict(
            os.environ,
            {"GOOGLE_APPS_SCRIPT_URL": "", "GOOGLE_APPS_SCRIPT_SECRET": ""},
        ):
            response = client.post("/api/lesson-reservations", json=payload)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json["missing_settings"],
            ["GOOGLE_APPS_SCRIPT_URL", "GOOGLE_APPS_SCRIPT_SECRET"],
        )

    def test_lesson_reservation_reports_apps_script_rejection(self):
        client = create_app().test_client()
        payload = {
            "name": "予約 太郎",
            "email": "taro@example.com",
            "phone": "",
            "lesson_type": "体験レッスン",
            "preferred_date": "2026-08-11",
            "preferred_time": "07:00",
            "message": "",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 10)), patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch(
            "app.send_lesson_reservation",
            side_effect=LessonReservationDeliveryError("Unauthorized"),
        ):
            response = client.post("/api/lesson-reservations", json=payload)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["delivery_error"], "Unauthorized")

    def test_lesson_reservation_enforces_date_range_and_weekday_hours(self):
        client = create_app().test_client()
        payload = {
            "name": "予約 太郎",
            "email": "taro@example.com",
            "phone": "",
            "lesson_type": "体験レッスン",
            "preferred_date": "2026-08-10",
            "preferred_time": "07:00",
            "message": "",
        }

        with patch("app.current_japan_date", return_value=date(2026, 8, 9)), patch.dict(
            os.environ,
            {
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation", return_value={"ok": True}):
            self.assertEqual(client.post("/api/lesson-reservations", json=payload).status_code, 201)
            invalid_reservations = [
                {**payload, "preferred_date": "2026-08-09"},
                {**payload, "preferred_date": "2026-09-10"},
                {**payload, "preferred_date": "2026-08-15", "preferred_time": "09:00"},
            ]
            for reservation in invalid_reservations:
                self.assertEqual(
                    client.post("/api/lesson-reservations", json=reservation).status_code,
                    400,
                )
            for friday_time in ["07:00", "09:00", "16:00", "要相談"]:
                self.assertEqual(
                    client.post(
                        "/api/lesson-reservations",
                        json={
                            **payload,
                            "preferred_date": "2026-08-14",
                            "preferred_time": friday_time,
                        },
                    ).status_code,
                    201,
                )
            self.assertEqual(
                client.post(
                    "/api/lesson-reservations",
                    json={**payload, "preferred_date": "2026-08-16", "preferred_time": "要相談"},
                ).status_code,
                201,
            )
            self.assertEqual(
                client.post(
                    "/api/lesson-reservations",
                    json={**payload, "preferred_date": "2026-08-15", "preferred_time": "要相談"},
                ).status_code,
                201,
            )

    def test_schedule_uses_the_reservation_weekday_hours(self):
        schedule_source = (Path(__file__).parents[1] / "schedule" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("makeRange(6,45,9,0), ...makeRange(20,30,22,0)", schedule_source)
        self.assertIn("4: makeRange(6,45,12,0)", schedule_source)
        self.assertIn('5: [...makeRange(6,45,17,0), "要相談"]', schedule_source)

    def test_schedule_clamps_one_month_limit_at_month_end(self):
        schedule_source = (Path(__file__).parents[1] / "schedule" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("const targetMonth = lastDate.getMonth() + 1", schedule_source)
        self.assertIn("lastDate.setDate(Math.min(today.getDate(), lastDate.getDate()))", schedule_source)

    def test_lesson_reservation_manage_requires_editor_password(self):
        client = create_app().test_client()
        payload = {"status": "確認中"}

        with patch.dict(os.environ, {"EDITOR_PASSWORD": "correct-password"}):
            self.assertEqual(
                client.put("/api/lesson-reservations/R-20260810-001", json=payload).status_code,
                401,
            )
            self.assertEqual(
                client.delete(
                    "/api/lesson-reservations/R-20260810-001",
                    headers={"X-Editor-Password": "wrong-password"},
                ).status_code,
                401,
            )

    def test_lesson_reservation_manage_can_update_and_delete(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.side_effect = [
                {
                    "ok": True,
                    "reservationId": "R-20260810-001",
                    "status": "確認中",
                    "updatedFields": ["status", "message"],
                },
                {"ok": True, "reservationId": "R-20260810-001"},
            ]

            update_response = client.put(
                "/api/lesson-reservations/R-20260810-001",
                json={"status": "確認中", "message": "折り返し予定"},
                headers=headers,
            )
            delete_response = client.delete(
                "/api/lesson-reservations/R-20260810-001",
                headers=headers,
            )

        self.assertEqual(update_response.status_code, 200)
        self.assertTrue(update_response.json["saved"])
        self.assertEqual(update_response.json["status"], "確認中")
        self.assertEqual(update_response.json["updated_fields"], ["status", "message"])
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json["deleted"])
        self.assertEqual(send_reservation.call_count, 2)
        self.assertEqual(
            send_reservation.call_args_list[0].kwargs["action"],
            "update",
        )
        self.assertEqual(
            send_reservation.call_args_list[1].kwargs["action"],
            "delete",
        )

    def test_lesson_reservation_confirm_reports_confirmation_email(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "reservationId": "R-20260810-001",
                "status": "確定",
                "updatedFields": ["status"],
                "confirmationEmailSent": True,
            }
            response = client.put(
                "/api/lesson-reservations/R-20260810-001",
                json={"status": "確定"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["confirmation_email_sent"])
        self.assertEqual(send_reservation.call_args.kwargs["action"], "update")

    def test_lesson_reservation_manage_reports_slot_conflict(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "reservationId": "R-20260810-001",
                "status": "予約済",
                "conflict": True,
            }
            response = client.put(
                "/api/lesson-reservations/R-20260810-001",
                json={"preferred_date": "2026-08-20", "preferred_time": "09:00"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json["saved"])
        self.assertTrue(response.json["conflict"])

    def test_lesson_reservation_manage_reports_not_found(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch(
            "app.send_lesson_reservation",
            side_effect=LessonReservationDeliveryError("NOT_FOUND"),
        ):
            response = client.delete(
                "/api/lesson-reservations/R-20260810-999",
                headers=headers,
            )

        self.assertEqual(response.status_code, 404)

    def test_lesson_reservation_manage_rejects_invalid_update_payload(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ):
            response = client.put(
                "/api/lesson-reservations/R-20260810-001",
                json={"status": "不正状態"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 400)

    def test_lesson_reservation_manage_accepts_admin_selected_time(self):
        client = create_app().test_client()
        headers = {"X-Editor-Password": "correct-password"}

        with patch.dict(
            os.environ,
            {
                "EDITOR_PASSWORD": "correct-password",
                "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
                "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            },
        ), patch("app.send_lesson_reservation") as send_reservation:
            send_reservation.return_value = {
                "ok": True,
                "reservationId": "R-20260816-001",
                "status": "確定",
            }
            response = client.put(
                "/api/lesson-reservations/R-20260816-001",
                json={"status": "確定", "preferred_time": "13:15"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_reservation.call_args.args[2]["preferred_time"], "13:15")

    def test_lesson_reservation_manage_rejects_invalid_admin_time(self):
        with self.assertRaisesRegex(ValueError, "希望時間"):
            validate_lesson_reservation_update({"preferred_time": "午後1時"})

    def test_lesson_reservation_manage_accepts_group_duration(self):
        self.assertEqual(
            validate_lesson_reservation_update({"duration_minutes": 90}),
            {"duration_minutes": 90},
        )
        with self.assertRaisesRegex(ValueError, "所要時間"):
            validate_lesson_reservation_update({"duration_minutes": 70})

    def test_index_uses_site_relative_products_and_lesson_links(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('href="products/"', page)
        self.assertIn('href="lesson/"', page)
        self.assertIn('href="pdf/"', page)
        self.assertIn("イベント企画PDFを見る", page)
        self.assertNotIn('src="music%20App/"', page)
        self.assertNotIn("namegawa-brass-lab.onrender.com/api/lesson", page)

    def test_event_planning_pdf_index_is_available(self):
        client = create_app().test_client()

        response = client.get("/pdf/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('id="adminToggle"', page)
        self.assertIn('id="adminLoginForm"', page)
        self.assertIn('id="pdfUploadForm" hidden', page)
        self.assertIn('id="deleteStepOne"', page)
        self.assertIn('id="deleteStepTwo" hidden', page)
        self.assertNotIn("sessionStorage", page)
        expected_documents = {
            "dayservice.pdf": "音でつながる！懐かしのメロディと呼吸のストレッチ",
            "gakudou.pdf": "学童向け トランペット・ミニコンサート＆ワークショップ",
            "hoikuen.pdf": "見て・聴いて・あそんで楽しむ！トランペット・ミニコンサート＆リズム体験ワークショップ",
            "shukatsu.pdf": "カフェで紡ぐ「思い出のメロディ」ライブ【スタンダードプラン】",
            "cafe-live-plan-1.pdf": "音のパスポート ～トランペットで巡る 世界の街角と名曲たち～",
            "cafe-live-plan-2.pdf": "カフェ・ド・トランペット ～午後の紅茶と、心ひろがる名曲の旅～",
            "cafe-live-plan-3.pdf": "ノスタルジック・ノーツ ～トランペットの音色でたどる 昭和・ジャズ・名画の旅～",
        }
        for filename, title in expected_documents.items():
            with self.subTest(filename=filename):
                self.assertIn(f'<a href="{filename}">{title}</a>', page)
                self.assertNotIn(f'>{filename}</a>', page)

    def test_admin_can_upload_and_delete_event_pdf_from_server_replicas(self):
        with tempfile.TemporaryDirectory() as static_directory, tempfile.TemporaryDirectory() as upload_directory, tempfile.TemporaryDirectory() as replica_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            client = create_app(
                database_url="",
                pdf_dir=Path(static_directory),
                pdf_upload_dir=Path(upload_directory),
                pdf_replica_dirs=(Path(replica_directory),),
            ).test_client()
            headers = {"X-Editor-Password": "editor-secret"}

            uploaded = client.post(
                "/api/event-pdfs",
                data={
                    "title": "新しいイベント企画書",
                    "pdf": (BytesIO(b"%PDF-1.4\n%%EOF"), "proposal.pdf"),
                },
                headers=headers,
                content_type="multipart/form-data",
            )

            self.assertEqual(uploaded.status_code, 201)
            filename = uploaded.json["document"]["filename"]
            upload_path = Path(upload_directory) / filename
            replica_path = Path(replica_directory) / filename
            self.assertTrue(upload_path.is_file())
            replica_path.write_bytes(upload_path.read_bytes())
            page = client.get("/pdf/").get_data(as_text=True)
            self.assertIn(f'<a href="{filename}">新しいイベント企画書</a>', page)
            served = client.get(f"/pdf/{filename}")
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served.mimetype, "application/pdf")

            rejected = client.delete(
                f"/api/event-pdfs/{filename}",
                json={"confirmation_filename": "wrong.pdf"},
                headers=headers,
            )
            self.assertEqual(rejected.status_code, 400)
            self.assertTrue(upload_path.is_file())
            deleted = client.delete(
                f"/api/event-pdfs/{filename}",
                json={"confirmation_filename": filename},
                headers=headers,
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(deleted.json["deleted_count"], 2)
            self.assertFalse(upload_path.exists())
            self.assertFalse(replica_path.exists())
            self.assertEqual(client.get(f"/pdf/{filename}").status_code, 404)
            manifest = json.loads(
                (Path(upload_directory) / "documents.json").read_text(encoding="utf-8")
            )
            self.assertIn(filename, manifest["__deleted__"])

    def test_deleted_bundled_pdf_stays_hidden_when_server_file_returns(self):
        with tempfile.TemporaryDirectory() as static_directory, tempfile.TemporaryDirectory() as upload_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            static_path = Path(static_directory) / "dayservice.pdf"
            static_path.write_bytes(b"%PDF-1.4\n%%EOF")
            client = create_app(
                database_url="",
                pdf_dir=Path(static_directory),
                pdf_upload_dir=Path(upload_directory),
            ).test_client()
            headers = {"X-Editor-Password": "editor-secret"}

            deleted = client.delete(
                "/api/event-pdfs/dayservice.pdf",
                json={"confirmation_filename": "dayservice.pdf"},
                headers=headers,
            )
            self.assertEqual(deleted.status_code, 200)
            static_path.write_bytes(b"%PDF-1.4\n%%EOF")

            self.assertNotIn("dayservice.pdf", client.get("/pdf/").get_data(as_text=True))
            self.assertEqual(client.get("/pdf/dayservice.pdf").status_code, 404)

    def test_event_pdf_management_requires_admin_password_and_valid_pdf(self):
        with tempfile.TemporaryDirectory() as upload_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            client = create_app(
                database_url="", pdf_upload_dir=Path(upload_directory)
            ).test_client()
            invalid_upload = {
                "title": "不正ファイル",
                "pdf": (BytesIO(b"not a pdf"), "invalid.pdf"),
            }

            self.assertEqual(
                client.post(
                    "/api/event-pdfs",
                    data=invalid_upload,
                    content_type="multipart/form-data",
                ).status_code,
                401,
            )
            response = client.post(
                "/api/event-pdfs",
                data={
                    "title": "不正ファイル",
                    "pdf": (BytesIO(b"not a pdf"), "invalid.pdf"),
                },
                headers={"X-Editor-Password": "editor-secret"},
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("正しいPDF", response.json["error"])

    def test_index_uses_sticky_responsive_navigation(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        header_css = page.split("header {", 1)[1].split("}", 1)[0]
        self.assertIn("position: sticky", header_css)
        self.assertIn('class="nav-menu" id="navMenu"', page)
        self.assertIn('class="hamburger" id="hamburgerBtn"', page)
        self.assertIn("navMenu.classList.toggle('active')", page)
        header_nav = page.split('<ul class="nav-menu" id="navMenu">', 1)[1].split("</ul>", 1)[0]
        footer_nav = page.split('<div class="footer-links">', 1)[1].split("</div>", 1)[0]
        for navigation in (header_nav, footer_nav):
            self.assertIn('<a href="lesson/">kazooささきトランペット教室</a>', navigation)
            self.assertIn('<a href="#trumpet">事業・サービス内容</a>', navigation)
            self.assertNotIn(">イベント企画</a>", navigation)
            self.assertNotIn(">アプリ・商品</a>", navigation)

    def test_products_embed_app_and_lesson_owns_reservation_form(self):
        client = create_app().test_client()

        products_response = client.get("/products/")
        lesson_response = client.get("/lesson/")

        self.assertEqual(products_response.status_code, 200)
        self.assertEqual(lesson_response.status_code, 200)
        products_page = products_response.get_data(as_text=True)
        lesson_page = lesson_response.get_data(as_text=True)
        self.assertIn('id="metronome"', products_page)
        self.assertIn('class="app-window"', products_page)
        self.assertNotIn('id="reservation-form"', products_page)
        self.assertIn('class="hero-photo"', lesson_page)
        self.assertIn('src="../data/media/lesson-header-photo.jpg"', lesson_page)
        self.assertIn('id="reservation-form"', lesson_page)
        self.assertIn('id="reservation-cancel-form"', lesson_page)
        self.assertIn('レッスンで<span class="mobile-break"></span>大切にしていること', lesson_page)
        self.assertIn('体験・各種レッスンの<span class="mobile-break"></span>お問い合わせ', lesson_page)
        self.assertNotIn("renderStoreBase", lesson_page)
        self.assertNotIn("appPurchaseButton", lesson_page)
        self.assertNotIn(".app-store", lesson_page)

    def test_index_uses_real_contact_map_and_hero_media(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('src="video/intro.mp4"', page)
        self.assertIn('src="data/media/profile-photo.jpg"', page)
        self.assertIn('alt="トランペットを持つ佐々木久和"', page)
        hero_label_css = page.split(".hero-visual-label {", 1)[1].split("}", 1)[0]
        self.assertIn("border-top: 4px solid var(--accent-color)", hero_label_css)
        self.assertNotIn("border-left:", hero_label_css)
        self.assertIn("埼玉県比企郡滑川町月の輪5丁目1-3", page)

    def test_index_renders_update_media_as_reliable_external_links(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertNotIn("{% if updates %}", page)
        self.assertNotIn("{% for update in updates %}", page)
        self.assertIn("function createUpdateCard(update)", page)
        self.assertIn("const updatesApiCandidates", page)
        self.assertIn("update-image-link", page)
        self.assertIn("写真を見る", page)
        self.assertIn("update-video-link", page)
        self.assertIn("YouTubeで見る", page)
        self.assertNotRegex(page, r'<img[^>]+src="data/media/updates/')
        self.assertNotIn('<iframe src="https://www.youtube.com/embed/', page)
        self.assertIn("content.textContent = card.dataset.updateContent", page)
        self.assertIn("updatesGrid.replaceChildren(...updates.map(createUpdateCard))", page)
        self.assertNotIn("↓こちら↓", page)
        self.assertNotIn("↓写真↓", page)
        self.assertIn('id="youtube-channel-title">公式YouTubeチャンネル', page)
        self.assertIn('href="https://youtube.com/@kazoo-ci8mf?si=NGFhv8QfwX7oMrYr"', page)
        self.assertIn('rel="noopener noreferrer"', page)

    def test_index_provides_compact_monthly_updates_window_and_editor(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('id="updates-month"', page)
        self.assertIn('class="updates-window" id="updates-window"', page)
        updates_window_css = page.split(".updates-window {", 1)[1].split("}", 1)[0]
        self.assertIn("max-width: 980px", updates_window_css)
        self.assertIn("height: 520px", updates_window_css)
        self.assertIn("overflow-y: auto", updates_window_css)
        self.assertNotIn("overscroll-behavior:", updates_window_css)
        self.assertNotIn("getElementById('updates-window')?.scrollTo", page)
        self.assertIn("location.port === '5500'", page)
        self.assertIn("if (location.protocol === 'file:' || isLiveServer)", page)
        self.assertIn("location.replace(`http://localhost:8080/", page)
        self.assertRegex(page, r"\.updates-grid\s*\{[^}]*grid-template-columns: 1fr;")
        self.assertIn("scroll-margin-top: 94px", page)
        self.assertIn("grid-template-columns: minmax(240px, 36%) minmax(0, 1fr)", page)
        self.assertRegex(page, r"\.update-media\s*\{[^}]*min-height: 140px;")
        self.assertRegex(page, r"@media \(max-width: 600px\)[\s\S]*?\.update-media\s*\{[^}]*min-height: 104px;")
        self.assertNotRegex(page, r"\.updates-section\s*\{\s*display: none;")
        self.assertIn('class="updates-nav-item"', page)
        self.assertIn('id="updates-admin-toggle"', page)
        self.assertIn('id="updates-editor-login"', page)
        self.assertIn('id="updates-editor-form"', page)
        self.assertIn("filterUpdatesByMonth", page)
        self.assertIn("/api/editor", page)
        self.assertIn("method: updateIndex ? 'PUT' : 'POST'", page)
        self.assertIn("method: 'DELETE'", page)
        self.assertIn("updatesEditorSave.disabled = true", page)
        self.assertIn("updatesEditorSave.disabled = false", page)
        self.assertIn("timeZone: 'Asia/Tokyo'", page)
        self.assertNotIn("sessionStorage.setItem('updatesEditorPassword'", page)
        self.assertIn("通信できませんでした。時間をおいて再度お試しください。", page)
        self.assertIn("zuomuj924@gmail.com", page)
        self.assertNotIn("info@example.com", page)
        self.assertNotIn("配置案", page)

    def test_index_presents_three_community_feature_rings(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('音楽と文化で育む、<span class="mobile-break"></span>広がる3つの輪', page)
        self.assertIn('音楽とITで、滑川町から<span class="mobile-break"></span>未来の可能性を広げる。', page)
        self.assertIn('居場所づくり、<span class="mobile-break"></span>イベント企画・輸送', page)
        self.assertIn('佐々木 久和<span class="profile-name-reading">（ささき ひさかず）</span>', page)
        self.assertIn('class="profile-role-secondary">Webエンジニア・軽貨物事業主', page)
        self.assertIn('kazooささき<span class="mobile-break"></span>トランペット教室', page)
        self.assertIn('地域<span class="mobile-break"></span>クラブ移行支援サポート', page)
        self.assertIn('滑川町こどもの居場所<span class="mobile-break"></span>ネットワークへの参加', page)
        self.assertIn('屋　号', page)
        self.assertIn('代表・<span class="mobile-break"></span>講師', page)
        self.assertIn('拠　点', page)
        self.assertIn("みんなの居場所づくり", page)
        self.assertIn("健康と笑顔の支援", page)
        self.assertIn("世代を超えた交流", page)
        self.assertIn('href="video/?v=20260817-3"', page)
        self.assertIn('aria-label="世代を超えた交流の動画を見る"', page)
        self.assertIn("交流動画を見る ▶", page)
        self.assertEqual(page.count('class="feature-ring"'), 3)
        self.assertIn("padding-top: 144px", page)
        self.assertNotIn('class="feature-ring" aria-hidden="true">01</div>', page)

        video_response = client.get("/video/")
        self.assertEqual(video_response.status_code, 200)
        video_page = video_response.get_data(as_text=True)
        self.assertIn("滑川町ふれあいコンサート", video_page)
        self.assertIn("2026年5月10日（日）", video_page)
        self.assertIn("滑川町コミュニティセンターにて", video_page)
        self.assertIn('src="generations.mp4?v=20260817"', video_page)
        self.assertIn('class="play-button"', video_page)
        self.assertIn("video.currentTime = 0", video_page)
        self.assertIn("video.muted = false", video_page)
        self.assertIn("video.volume = 1", video_page)
        self.assertNotIn("playButton.hidden = true", video_page)
        self.assertIn("まるっと！2026年5月18日号", video_page)
        self.assertIn("【制作：東松山ケーブルテレビ】", video_page)
        self.assertRegex(video_page, r"\.credit-telop strong\s*\{[^}]*display: block;")

    def test_index_presents_five_reorganized_services(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("音楽・地域連携・イベント・デジタルをつなぐ5つの事業", page)
        self.assertIn('部活動の地域連携・地域<span class="mobile-break"></span>クラブ移行支援', page)
        self.assertIn('滑川町こどもの居場所<span class="mobile-break"></span>ネットワークへの参加', page)
        self.assertIn("イベント企画・プロデュース／輸送業務", page)
        self.assertIn('class="events-service-layout"', page)
        self.assertIn('src="data/media/event-preschool-performance.png"', page)
        self.assertIn('alt="保育園で子どもたちが演奏を鑑賞している様子"', page)
        self.assertRegex(page, r"\.service-card-events-visual img\s*\{[^}]*object-fit: cover;")
        self.assertRegex(page, r"\.service-card-events-visual::after\s*\{[^}]*backdrop-filter: blur\(7px\);")
        self.assertIn("grid-template-columns: minmax(0, 1.35fr) minmax(108px, 0.65fr);", page)
        self.assertIn("WEB制作・アプリ開発販売", page)
        self.assertIn('kazooささき<span class="mobile-break"></span>トランペット教室', page)
        self.assertEqual(page.count('<div class="service-card'), 5)
        self.assertNotIn("kazoo イベント・楽器運搬", page)


if __name__ == "__main__":
    unittest.main()