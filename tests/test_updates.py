import json
import os
import tempfile
import unittest
from base64 import b64encode
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import (
    LessonReservationDeliveryError,
    create_app,
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
        self.assertIn('id="contractGenerator"', page)
        self.assertIn("/api/editor", page)
        self.assertIn("X-Editor-Password", page)
        self.assertIn("let adminPassword = '';", page)
        self.assertNotIn("sessionStorage.getItem('updatesEditorPassword')", page)
        self.assertIn('id="docType"', page)
        self.assertIn('value="master"', page)
        self.assertIn('value="typeA"', page)
        self.assertIn('value="typeB"', page)
        self.assertIn('value="typeC"', page)
        self.assertIn("window.print()", page)
        self.assertIn("法令等の制定または改廃", page)
        self.assertIn("電磁的記録", page)
        self.assertIn("反社会的勢力", page)
        self.assertIn("不可抗力", page)
        self.assertIn("未成年者", page)
        self.assertIn("ハラスメント", page)
        self.assertIn("運送中止", page)
        self.assertIn("事故、滅失、毀損または遅延", page)
        self.assertIn("変更管理", page)
        self.assertIn("脆弱性", page)
        self.assertIn('class="party-trade-name">屋号：なめがわブラス・ラボ', page)
        self.assertIn(".party-trade-name { white-space: nowrap; }", page)

    def test_contract_api_saves_and_lists_by_department(self):
        with tempfile.TemporaryDirectory() as temporary_directory, patch.dict(
            os.environ, {"EDITOR_PASSWORD": "editor-secret"}
        ):
            client = create_app(
                database_url="", contracts_dir=Path(temporary_directory)
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
            self.assertEqual(listed.json["contracts"][0]["department"], "楽器輸送")
            self.assertEqual(listed.json["contracts"][0]["client_name"], "〇〇楽団")

            loaded = client.get(
                f'/api/contracts/{saved.json["contract_id"]}', headers=headers
            )
            self.assertEqual(loaded.status_code, 200)
            self.assertEqual(loaded.json["contract"]["values"]["cargo"], "管楽器一式")

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

        self.assertIn('var writeActions = ["create", "consultation", "upsert_slot_status_range", "update", "delete", "cancel"]', do_post)
        self.assertIn("if (writeActions.indexOf(action) !== -1)", do_post)
        self.assertIn("var spreadsheet = getSpreadsheet();", do_post)
        self.assertIn("getReservationSheet(spreadsheet)", do_post)
        self.assertIn("getSlotStatusSheet(spreadsheet)", do_post)
        self.assertEqual(script.count("SpreadsheetApp.openById"), 1)

    def test_apps_script_caches_update_and_delete_results_for_safe_retry(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        self.assertIn('var SCRIPT_VERSION = "2026-08-21-consultation-attachment-v21";', script)
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
        self.assertIn('["受付","調整中","確認中","確定","キャンセル"]', page)
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
                "version": "2026-08-21-consultation-attachment-v21",
                "capabilities": ["consultation", "list", "update", "delete", "cancel", "upsert_slot_status_range"],
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
        self.assertIn('<a href="cafe-live-plan-1.pdf">cafe-live-plan-1.pdf</a>', page)
        self.assertIn('<a href="dayservice.pdf">dayservice.pdf</a>', page)

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
        self.assertIn("sessionStorage.removeItem('updatesEditorPassword')", page)
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