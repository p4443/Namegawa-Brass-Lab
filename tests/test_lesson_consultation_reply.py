import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import LessonReservationDeliveryError, create_app, send_lesson_reservation


class LessonConsultationReplyTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "EDITOR_PASSWORD": "test-editor-password",
            "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/example",
            "GOOGLE_APPS_SCRIPT_SECRET": "test-secret",
            "PUBLIC_SITE_URL": "https://namegawa-brass-lab.com",
        })
        env.start()
        self.addCleanup(env.stop)
        self.client = create_app(database_url="").test_client()
        self.url = "/api/lesson-reservations/R-20261031-001/consultation-reply"
        self.headers = {"X-Editor-Password": "test-editor-password"}

    def test_requires_editor_authentication(self):
        with patch("app.send_lesson_reservation") as send:
            response = self.client.post(self.url, json={"reply_message": "回答"})
        self.assertEqual(response.status_code, 401)
        send.assert_not_called()

    def test_preflight_supports_editor_token(self):
        response = self.client.options(self.url, headers={
            "Origin": "https://namegawa-brass-lab.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-Editor-Token",
        })
        self.assertEqual(response.status_code, 204)
        self.assertIn("X-Editor-Token", response.headers["Access-Control-Allow-Headers"])
        self.assertEqual(response.headers["Access-Control-Allow-Origin"],
                         "https://namegawa-brass-lab.com")

    def test_preflight_does_not_allow_untrusted_origin(self):
        response = self.client.options(self.url, headers={"Origin": "https://untrusted.example"})
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_forwards_only_reply_and_id_without_updating_booking(self):
        login = self.client.post("/api/editor", headers=self.headers)
        token_headers = {"X-Editor-Token": login.json["editor_token"]}
        with patch("app.send_lesson_reservation", return_value={
            "ok": True, "reservationId": "R-20261031-001", "replyEmailSent": True,
        }) as send, patch("app.notify_portal_booking_status") as notify:
            response = self.client.post(self.url, headers=token_headers, json={
                "reply_message": "  10時はいかがでしょうか。\nご返信ください。  ",
                "email": "other@example.com",
                "status": "確定",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"sent": True, "reservation_id": "R-20261031-001"})
        send.assert_called_once_with(
            "https://script.google.com/example", "test-secret",
            {"reservation_id": "R-20261031-001",
             "reply_message": "10時はいかがでしょうか。\nご返信ください。"},
            action="send_consultation_reply",
        )
        notify.assert_not_called()

    def test_rejects_invalid_reply_payloads(self):
        for payload in (None, [], "text", {}, {"reply_message": None},
                        {"reply_message": 1}, {"reply_message": " \n "},
                        {"reply_message": "あ" * 2001}):
            with self.subTest(payload_type=type(payload).__name__):
                with patch("app.send_lesson_reservation") as send:
                    response = self.client.post(self.url, headers=self.headers, json=payload)
                self.assertEqual(response.status_code, 400)
                send.assert_not_called()

    def test_accepts_exact_2000_character_limit_including_emoji(self):
        with patch("app.send_lesson_reservation", return_value={
            "ok": True, "replyEmailSent": True,
        }) as send:
            response = self.client.post(
                self.url, headers=self.headers, json={"reply_message": "🎺" * 2000},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(send.call_args.args[2]["reply_message"]), 2000)

    def test_rejects_invalid_reservation_id(self):
        with patch("app.send_lesson_reservation") as send:
            response = self.client.post(
                "/api/lesson-reservations/invalid/consultation-reply",
                headers=self.headers, json={"reply_message": "回答"},
            )
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()

    def test_reports_missing_settings(self):
        for key in ("GOOGLE_APPS_SCRIPT_URL", "GOOGLE_APPS_SCRIPT_SECRET"):
            with self.subTest(key=key), patch.dict(os.environ, {key: ""}):
                with patch("app.send_lesson_reservation") as send:
                    response = self.client.post(
                        self.url, headers=self.headers, json={"reply_message": "回答"},
                    )
                self.assertEqual(response.status_code, 503)
                send.assert_not_called()

    def test_reports_rejections_and_mail_failures(self):
        for error, status in (
            ("NOT_FOUND", 404), ("NOT_PENDING_CONSULTATION", 409),
            ("INVALID_REPLY", 400), ("REPLY_EMAIL_FAILED", 502),
            ("Unsupported action", 503), ("Unauthorized", 503), ("Unexpected", 502),
        ):
            with self.subTest(error=error):
                with patch("app.send_lesson_reservation",
                           side_effect=LessonReservationDeliveryError(error)):
                    response = self.client.post(
                        self.url, headers=self.headers, json={"reply_message": "回答"},
                    )
                self.assertEqual(response.status_code, status)
                self.assertIn("error", response.json)
                self.assertNotIn("sent", response.json)

    def test_reports_unknown_delivery_result_without_claiming_success(self):
        with patch("app.send_lesson_reservation", side_effect=OSError("timeout")):
            response = self.client.post(
                self.url, headers=self.headers, json={"reply_message": "回答"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn("送信済みメール", response.json["error"])
        for result in ({"ok": True}, {"ok": True, "replyEmailSent": False}):
            with self.subTest(result=result), patch("app.send_lesson_reservation", return_value=result):
                response = self.client.post(
                    self.url, headers=self.headers, json={"reply_message": "回答"},
                )
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("sent", response.json)

    def test_transport_retry_reuses_same_request_id(self):
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = [
            b"", b'{"ok": true, "replyEmailSent": true}',
        ]
        with patch("app.urllib_request.urlopen", return_value=response) as urlopen:
            result = send_lesson_reservation(
                "https://script.google.com/example", "test-secret",
                {"reservation_id": "R-20261031-001", "reply_message": "回答"},
                action="send_consultation_reply",
            )
        self.assertTrue(result["replyEmailSent"])
        requests = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].data, requests[1].data)
        self.assertTrue(json.loads(requests[0].data)["request_id"])

    def test_schedule_has_separate_reply_controls_and_preserves_drafts(self):
        page = (Path(__file__).parents[1] / "schedule" / "index.html").read_text(encoding="utf-8")
        self.assertIn('reservation.preferred_time === "要相談" && reservation.status === "確認中"', page)
        self.assertIn("consultationReplyDrafts.get(reservation.reservation_id)", page)
        self.assertIn("consultationReplyDrafts.size === 0", page)
        self.assertIn("consultationRepliesSending.has(reservationId)", page)
        function = page.split("async function sendConsultationReply(", 1)[1].split(
            "async function updateReservation(", 1,
        )[0]
        self.assertIn("window.confirm(", function)
        self.assertIn("/consultation-reply", function)
        self.assertNotIn('method: "PUT"', function)


if __name__ == "__main__":
    unittest.main()
