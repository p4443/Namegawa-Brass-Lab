import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import (
    LessonReservationDeliveryError,
    create_app,
    load_updates,
    normalize_media_url,
    parse_update_line,
    reservation_slot_times,
    send_lesson_reservation,
    validate_lesson_reservation,
)


class UpdatesTest(unittest.TestCase):
    def test_apps_script_requests_allow_slow_write_operations(self):
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
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 25)

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
        self.assertIn('slot.note === "受付自動設定"', release_function)
        self.assertIn("slot.source !== reservationId && !isLegacyReservationSlot", release_function)

    def test_apps_script_reads_skip_lock_and_open_spreadsheet_once(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )
        do_post = script.split("function doPost", 1)[1].split(
            "function getSpreadsheet", 1
        )[0]

        self.assertIn('var writeActions = ["create", "upsert_slot_status_range", "update", "delete"]', do_post)
        self.assertIn("if (writeActions.indexOf(action) !== -1)", do_post)
        self.assertIn("var spreadsheet = getSpreadsheet();", do_post)
        self.assertIn("getReservationSheet(spreadsheet)", do_post)
        self.assertIn("getSlotStatusSheet(spreadsheet)", do_post)
        self.assertEqual(script.count("SpreadsheetApp.openById"), 1)

    def test_apps_script_caches_update_and_delete_results_for_safe_retry(self):
        script = (Path(__file__).parents[1] / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        self.assertIn('var SCRIPT_VERSION = "2026-08-12-admin-v6";', script)
        self.assertIn('data.request_id || ""', script)
        self.assertIn('get("admin:" + requestId)', script)
        self.assertIn('put("admin:" + requestId, JSON.stringify(data), 600)', script)
        self.assertIn("return adminActionResponse({ ok: true, reservationId: reservationId }, requestId);", script)

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
        self.assertIn('href="../#main-container"', page)
        self.assertIn('id="reservation-form"', page)
        self.assertIn('class="schedule-callout"', page)
        self.assertIn('class="schedule-callout-link" href="../schedule/"', page)
        self.assertLess(page.index('id="schedule-callout-title"'), page.index('id="reservation-title"'))
        self.assertIn("const renderReservationApi =", page)
        self.assertIn('"中学生": 45', page)
        self.assertIn('"高校生以上": 60', page)
        self.assertIn("function occupiedTimes", page)
        self.assertIn("6:45〜16:00／それ以外は要相談", page)
        self.assertIn('5: [...makeTimeRange(6, 45, 16, 0), "要相談"]', page)

    def test_lesson_page_script_does_not_reference_missing_slot_inputs(self):
        client = create_app().test_client()

        response = client.get("/lesson/")

        self.assertEqual(response.status_code, 200)
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

    def test_lesson_page_does_not_show_admin_panel(self):
        client = create_app().test_client()

        response = client.get("/lesson/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertNotIn("管理者用", page)
        self.assertNotIn("slot-admin", page)

    def test_schedule_page_shares_public_calendar_and_admin_panel(self):
        client = create_app().test_client()

        response = client.get("/schedule/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('id="schedule-calendar"', page)
        self.assertIn('id="schedule-lesson-type"', page)
        self.assertIn('id="schedule-retry"', page)
        self.assertIn('id="admin-login-form"', page)
        self.assertIn("api/lesson-slot-statuses", page)
        self.assertIn("api/lesson-reservations", page)
        self.assertNotIn('requestApi("/api/lesson-admin-health"', page)
        self.assertIn('catch (error) { adminPassword = ""; adminStatus.textContent = error.message; return; }', page)
        self.assertIn("const loginForm = event.currentTarget", page)
        self.assertIn("loginForm.hidden = true", page)
        self.assertNotIn("event.currentTarget.hidden = true", page)
        self.assertIn("ログイン済みです。予約一覧の通信に失敗しました", page)
        self.assertIn('id="reservation-retry"', page)
        self.assertIn('reservationRetry.addEventListener("click"', page)
        self.assertIn('["受付","調整中","確認中","確定","キャンセル"]', page)
        self.assertIn("statusSelect.value = result.status || statusSelect.value", page)
        self.assertIn("予約状態を「${statusSelect.value}」へ更新しました。", page)
        self.assertIn("予約は削除済みです。一覧の再読み込みに失敗しました", page)
        self.assertIn('summary.textContent = scheduleReady ?', page)
        self.assertIn('"中学生": 45', page)
        self.assertIn("occupiedTimes(time, durationMinutes)", page)
        self.assertIn("controller.abort(), timeoutMs", page)
        self.assertIn("サーバー起動中は最大30秒", page)
        self.assertIn("const { timeoutMs = 30000, ...fetchOptions } = options", page)
        self.assertIn("responseError.isHttpError = true", page)
        self.assertIn("if (error.isHttpError) throw error", page)
        self.assertGreaterEqual(page.count("timeoutMs: 60000"), 3)
        self.assertIn('requestApi("/api/lesson-reservations", { headers: adminHeaders(), timeoutMs: 30000 })', page)
        self.assertIn('total ? "受付日" : "休み"', page)
        self.assertIn("空き状況を確認しています。表示後に予約時間を選択できます。", page)
        self.assertIn('5: [...makeRange(6,45,16,0), "要相談"]', page)
        self.assertIn(".panel { min-width: 0;", page)
        self.assertIn('selectedDateTitle.focus({ preventScroll: true })', page)
        self.assertIn('matchMedia("(max-width: 760px)").matches', page)
        self.assertIn('document.querySelector(".detail-panel").scrollIntoView', page)

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
                "version": "2026-08-12-admin-v1",
                "capabilities": ["list", "update", "delete", "upsert_slot_status_range"],
            }
            response = client.get("/api/lesson-admin-health", headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ready"])
        self.assertEqual(send_reservation.call_args.kwargs["action"], "health")

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
                "status": "調整中",
                "autoReplySent": True,
            }
            response = client.post("/api/lesson-reservations", json=payload)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response.json["reservation_id"], "R-20260820-001")
        self.assertEqual(response.json["status"], "調整中")
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
            }
            response = client.get("/api/lesson-slot-statuses?from=2026-08-20&to=2026-08-20")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(len(response.json["slots"]), 1)
        self.assertEqual(response.json["slots"][0]["status"], "予約済")
        self.assertEqual(send_reservation.call_args.kwargs["action"], "get_slot_statuses")

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
            "preferred_time": "06:45",
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
            "preferred_time": "06:45",
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
            "preferred_time": "06:45",
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
            for friday_time in ["06:45", "09:00", "16:00", "要相談"]:
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

    def test_index_uses_site_relative_lesson_links(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertEqual(page.count('href="lesson/"'), 2)
        self.assertNotIn('href="/lesson/"', page)
        self.assertIn("failedLoginAttempts >= 3", page)
        self.assertIn("closeEditorPanel();", page)
        self.assertIn("https://namegawa-brass-lab.onrender.com/api/updates", page)
        self.assertNotIn('class="schedule-callout"', page)

    def test_index_navigation_can_be_collapsed(self):
        client = create_app().test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('<details class="global-nav-disclosure">', page)
        self.assertIn('<summary class="global-nav-toggle">メニュー</summary>', page)


if __name__ == "__main__":
    unittest.main()