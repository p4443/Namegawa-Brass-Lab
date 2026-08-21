import fcntl
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
import threading
import time
import uuid
from base64 import b64decode
from binascii import Error as Base64Error
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlencode, urlparse
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, jsonify, make_response, redirect, render_template, request, send_file, send_from_directory
from itsdangerous import BadData, BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
UPDATES_FILE = BASE_DIR / "data" / "updates.txt"
STORE_FILE = BASE_DIR / "data" / "store.json"
PRODUCT_FILE = BASE_DIR / "private" / "products" / "trumpet-metronome.zip"
PRODUCT_ID = "trumpet-metronome"
PRODUCT_NAME = "トランペット練習メトロノーム オフライン版"
PRODUCT_PRICE_YEN = 500
PRODUCT_REQUIRED_FILES = {"index.html", "README.txt"}
FLOW_HARMONY_PRODUCT_FILE = BASE_DIR / "private" / "products" / "flow-harmony.zip"
FLOW_HARMONY_PRODUCT_ID = "flow-harmony"
FLOW_HARMONY_PRODUCT_NAME = "Flow Harmony オフライン版"
FLOW_HARMONY_PRODUCT_PRICE_YEN = 1000
FLOW_HARMONY_SALES_ENABLED = True
STORE_PAYMENT_CACHE_TTL_SECONDS = 30
STORE_PAYMENT_CACHE_MAX_ENTRIES = 2048
STORE_REISSUE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
STORE_DOWNLOAD_LIMIT = 10
STORE_DOWNLOAD_WINDOW_SECONDS = 24 * 60 * 60
STORE_RECOVERY_LIMIT = 5
STORE_RECOVERY_WINDOW_SECONDS = 15 * 60
CHECKOUT_SESSION_PATTERN = re.compile(r"^cs_[A-Za-z0-9_]{1,255}$")
MEDIA_PATTERN = re.compile(
    r"\[(image|video|pdf|写真|動画|資料)\s*[:：]\s*([^\]]+)\]",
    re.IGNORECASE,
)
YOUTUBE_PATTERN = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
MEDIA_TYPES = {"写真": "image", "動画": "video", "資料": "pdf"}
ALLOWED_MEDIA_TYPES = {"", "image", "video", "pdf"}
LESSON_TYPES = {
    "体験レッスン",
    "無料体験レッスン",
    "小学生",
    "中学生",
    "高校生以上",
    "グループ・部活動指導",
}
LESSON_DURATION_MINUTES = {
    "体験レッスン": 30,
    "無料体験レッスン": 30,
    "小学生": 30,
    "中学生": 45,
    "高校生以上": 60,
    "グループ・部活動指導": None,
}
CONSULTATION_TIME = "要相談"
RESERVATION_STATUS_VALUES = {"受付", "調整中", "確認中", "確定", "キャンセル"}
LESSON_RESERVATION_TIMEOUT_SECONDS = 40
SLOT_STATUS_VALUES = {"空き", "調整中", "予約済", "お休み"}
CONSULTATION_MODES = {
    "allinone": "オールインワン依頼（指導・セッティング・運搬一式）",
    "planning": "イベント企画・プロデュースのみ",
    "cargo": "一般輸送・単体搬送",
}
CONSULTATION_SUPPORT_TYPES = {
    "コンクール・演奏会当日フルサポート（指導＋搬送＋セッティング）",
    "合宿・出張レッスン統合サポート",
    "定期レッスン＋楽器点検・セッティング",
}
CONSULTATION_PLANNING_TYPES = {
    "演奏会・ライブ等のプロデュース",
    "外部講師・指導者派遣の調整",
    "ワークショップ・講習会の企画",
    "その他",
}
CONSULTATION_INSTRUMENT_VALUES = {
    "300": "〜300万円まで",
    "1000": "300万円〜1,000万円",
    "3000": "1,000万円〜3,000万円",
    "over3000": "3,000万円超",
}
CONSULTATION_ATTACHMENT_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".csv",
    ".zip",
}
CONSULTATION_ATTACHMENT_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/csv",
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
}
CONSULTATION_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
LESSON_APPS_SCRIPT_VERSION = "2026-08-21-consultation-attachment-v21"


def current_japan_date():
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


def add_one_month(value):
    year = value.year + (value.month == 12)
    month = 1 if value.month == 12 else value.month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def time_range(start, end):
    current = datetime.strptime(start, "%H:%M")
    finish = datetime.strptime(end, "%H:%M")
    times = set()
    while current <= finish:
        times.add(current.strftime("%H:%M"))
        current += timedelta(minutes=15)
    return times


def reservation_slot_times(start_time, duration_minutes):
    if start_time == CONSULTATION_TIME:
        return [CONSULTATION_TIME]
    start = datetime.strptime(start_time, "%H:%M")
    return [
        (start + timedelta(minutes=offset)).strftime("%H:%M")
        for offset in range(0, duration_minutes, 15)
    ]


def normalize_slot_statuses(slots: object) -> list[dict[str, object]]:
    normalized_slots: list[dict[str, object]] = []
    for slot in slots if isinstance(slots, list) else []:
        if not isinstance(slot, dict):
            continue
        normalized_slot = {str(key): value for key, value in slot.items()}
        time_text = str(normalized_slot.get("time", "")).strip()
        if time_text != CONSULTATION_TIME:
            time_match = re.search(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\b", time_text)
            if time_match:
                normalized_slot["time"] = (
                    f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
                )
        normalized_slots.append(normalized_slot)
    return normalized_slots


def is_allowed_lesson_start(lesson_type, preferred_time, available_times):
    if lesson_type == "グループ・部活動指導":
        return preferred_time == CONSULTATION_TIME
    if preferred_time == CONSULTATION_TIME:
        return preferred_time in available_times
    if preferred_time not in available_times:
        return False
    minute = int(preferred_time[3:])
    if lesson_type in {"体験レッスン", "無料体験レッスン", "小学生"}:
        return minute in {0, 30}
    return minute == 0


WEEKDAY_RESERVATION_TIMES = {
    0: time_range("06:45", "09:00") | time_range("20:30", "22:00"),
    1: time_range("06:45", "09:00") | time_range("20:30", "22:00"),
    2: time_range("06:45", "09:00") | time_range("20:30", "22:00"),
    3: time_range("06:45", "12:00"),
    4: time_range("06:45", "17:00") | {CONSULTATION_TIME},
    5: {CONSULTATION_TIME},
    6: {CONSULTATION_TIME},
}


class LessonReservationDeliveryError(Exception):
    pass


def parse_date(date_text):
    normalized = date_text.strip().replace(".", "-").replace("/", "-")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min


def normalize_media_url(raw_url):
    media_url = raw_url.strip()
    parsed = urlparse(media_url)
    if parsed.scheme or media_url.startswith(("/", "data:", "blob:")):
        return media_url
    if "/" not in media_url:
        return f"data/media/{media_url}"
    return media_url


def parse_update_line(line, index):
    parts = line.split("|")
    date = parts[0].strip() if parts else ""
    category = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "つぶやき"
    content = "|".join(parts[2:]).strip() if len(parts) > 2 else ""

    media_type = ""
    media_url = ""
    media_match = MEDIA_PATTERN.search(content)
    if media_match:
        raw_type = media_match.group(1)
        media_type = MEDIA_TYPES.get(raw_type, raw_type.lower())
        media_url = normalize_media_url(media_match.group(2))
        content = MEDIA_PATTERN.sub("", content, count=1).strip()

    youtube_embed_url = ""
    if media_type == "video":
        youtube_match = YOUTUBE_PATTERN.search(media_url)
        if youtube_match:
            youtube_embed_url = (
                f"https://www.youtube.com/embed/{youtube_match.group(1)}?playsinline=1&rel=0"
            )

    return {
        "index": index,
        "date": date,
        "category": category,
        "content": content,
        "media_type": media_type,
        "media_url": media_url,
        "youtube_embed_url": youtube_embed_url,
        "sort_date": parse_date(date),
    }


def load_updates(path=UPDATES_FILE):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    updates = [
        parse_update_line(line.strip(), index)
        for index, line in enumerate(lines)
        if line.strip() and not line.strip().startswith("#")
    ]
    return sorted(updates, key=lambda item: (item["sort_date"], item["index"]), reverse=True)


def database_connection(database_url):
    import psycopg

    return psycopg.connect(database_url)


def initialize_database(database_url, seed_path=UPDATES_FILE):
    seed_updates = list(reversed(load_updates(seed_path)))
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(72496521)")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS updates (
                    id BIGSERIAL PRIMARY KEY,
                    update_date DATE NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    media_type TEXT NOT NULL DEFAULT '',
                    media_url TEXT NOT NULL DEFAULT ''
                )
                """
            )
            cursor.execute("SELECT COUNT(*) FROM updates")
            if cursor.fetchone()[0] == 0:
                cursor.executemany(
                    """
                    INSERT INTO updates
                        (update_date, category, content, media_type, media_url)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            item["date"],
                            item["category"],
                            item["content"],
                            item["media_type"],
                            item["media_url"],
                        )
                        for item in seed_updates
                    ],
                )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS store_settings (
                    product_id TEXT PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO store_settings (product_id, enabled)
                VALUES (%s, FALSE)
                ON CONFLICT (product_id) DO NOTHING
                """,
                (PRODUCT_ID,),
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stripe_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS store_downloads (
                    purchase_reference TEXT PRIMARY KEY,
                    window_started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    download_count INTEGER NOT NULL DEFAULT 0,
                    last_downloaded_at TIMESTAMPTZ
                )
                """
            )


def load_store_settings(path=STORE_FILE):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"enabled": False}
    return {"enabled": payload.get("enabled") is True}


def save_store_settings(enabled, path=STORE_FILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, delete=False
            ) as temporary_file:
                json.dump({"enabled": bool(enabled)}, temporary_file, ensure_ascii=False)
                temporary_file.write("\n")
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, path)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_database_store_settings(database_url):
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT enabled FROM store_settings WHERE product_id = %s",
                (PRODUCT_ID,),
            )
            row = cursor.fetchone()
    return {"enabled": bool(row[0]) if row else False}


def save_database_store_settings(database_url, enabled):
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO store_settings (product_id, enabled, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (product_id) DO UPDATE
                SET enabled = EXCLUDED.enabled, updated_at = CURRENT_TIMESTAMP
                """,
                (PRODUCT_ID, bool(enabled)),
            )


def record_stripe_event(database_url, event_id, event_type):
    if not database_url:
        return True
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO stripe_events (event_id, event_type)
                VALUES (%s, %s)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """,
                (event_id, event_type),
            )
            return cursor.fetchone() is not None


def load_database_updates(database_url):
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, update_date, category, content, media_type, media_url
                FROM updates
                ORDER BY update_date DESC, id DESC
                """
            )
            rows = cursor.fetchall()

    updates = []
    for update_id, update_date, category, content, media_type, media_url in rows:
        media_marker = f" [{media_type}:{media_url}]" if media_type else ""
        updates.append(
            parse_update_line(
                f"{update_date.isoformat()} | {category} | {content}{media_marker}",
                update_id,
            )
        )
    return updates


def create_database_update(database_url, values):
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO updates
                    (update_date, category, content, media_type, media_url)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    values["date"],
                    values["category"],
                    values["content"],
                    values["media_type"],
                    values["media_url"],
                ),
            )


def edit_database_update(database_url, update_id, values):
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE updates
                SET update_date = %s, category = %s, content = %s,
                    media_type = %s, media_url = %s
                WHERE id = %s
                """,
                (
                    values["date"],
                    values["category"],
                    values["content"],
                    values["media_type"],
                    values["media_url"],
                    update_id,
                ),
            )
            return cursor.rowcount > 0


def delete_database_update(database_url, update_id):
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM updates WHERE id = %s", (update_id,))
            return cursor.rowcount > 0


def public_update(item):
    return {key: value for key, value in item.items() if key != "sort_date"}


def validate_update(payload):
    if not isinstance(payload, dict):
        raise ValueError("入力内容を確認してください。")

    values = {
        "date": str(payload.get("date", "")).strip(),
        "category": str(payload.get("category", "")).strip(),
        "content": str(payload.get("content", "")).strip(),
        "media_type": str(payload.get("media_type", "")).strip().lower(),
        "media_url": str(payload.get("media_url", "")).strip(),
    }
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", values["date"]):
        raise ValueError("日付を正しく入力してください。")
    try:
        datetime.strptime(values["date"], "%Y-%m-%d")
    except ValueError:
        raise ValueError("日付を正しく入力してください。")
    if not values["category"] or not values["content"]:
        raise ValueError("種類と本文を入力してください。")
    if values["media_type"] not in ALLOWED_MEDIA_TYPES:
        raise ValueError("メディア種別が正しくありません。")
    if values["media_type"] and not values["media_url"]:
        raise ValueError("メディアのURLを入力してください。")
    if not values["media_type"]:
        values["media_url"] = ""
    if any("|" in value or "\n" in value or "\r" in value for value in values.values()):
        raise ValueError("入力欄に改行または | は使用できません。")
    return values


def validate_consultation(payload):
    if not isinstance(payload, dict):
        raise ValueError("入力内容を確認してください。")

    mode = str(payload.get("service_mode", "")).strip()
    values = {
        "service_mode": CONSULTATION_MODES.get(mode, ""),
        "org_name": str(payload.get("org_name", "")).strip(),
        "email": str(payload.get("email", "")).strip(),
        "event_date": "",
        "support_content": "",
        "instrument_value": "",
        "planning_type": "",
        "cargo_detail": "",
        "message": str(payload.get("message", "")).strip(),
        "attachment_name": "",
        "attachment_type": "",
        "attachment_data": "",
    }
    if not values["service_mode"]:
        raise ValueError("サービス種別を選択してください。")
    if not values["org_name"] or len(values["org_name"]) > 100:
        raise ValueError("団体名・お申込者名を100文字以内で入力してください。")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", values["email"]):
        raise ValueError("メールアドレスを正しく入力してください。")
    if len(values["message"]) > 1000:
        raise ValueError("ご相談詳細は1,000文字以内で入力してください。")
    if payload.get("terms_agree") is not True:
        raise ValueError("プライバシーポリシーへの同意が必要です。")

    attachment = payload.get("attachment")
    if attachment:
        if not isinstance(attachment, dict):
            raise ValueError("添付データを確認してください。")
        attachment_name = str(attachment.get("name", "")).strip()
        attachment_type = str(attachment.get("type", "")).strip().lower()
        attachment_data = str(attachment.get("data", "")).strip()
        extension = Path(attachment_name).suffix.lower()
        if (
            not attachment_name
            or len(attachment_name) > 120
            or Path(attachment_name).name != attachment_name
            or extension not in CONSULTATION_ATTACHMENT_EXTENSIONS
            or attachment_type not in CONSULTATION_ATTACHMENT_MIME_TYPES
        ):
            raise ValueError("添付できない形式のデータです。")
        try:
            decoded_attachment = b64decode(attachment_data, validate=True)
        except (Base64Error, ValueError) as exc:
            raise ValueError("添付データを読み取れません。") from exc
        if not decoded_attachment:
            raise ValueError("添付データが空です。")
        if len(decoded_attachment) > CONSULTATION_ATTACHMENT_MAX_BYTES:
            raise ValueError("添付データは5MB以内にしてください。")
        values.update(
            {
                "attachment_name": attachment_name,
                "attachment_type": attachment_type,
                "attachment_data": attachment_data,
            }
        )

    if mode == "allinone":
        event_date = str(payload.get("event_date", "")).strip()
        try:
            parsed_event_date = datetime.strptime(event_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("実施予定日を正しく入力してください。") from exc
        if parsed_event_date < current_japan_date():
            raise ValueError("実施予定日は本日以降の日付を指定してください。")
        support_content = str(payload.get("support_content", "")).strip()
        if support_content not in CONSULTATION_SUPPORT_TYPES:
            raise ValueError("ご希望の指導・サポート内容を選択してください。")
        instrument_value = str(payload.get("instrument_value", "")).strip()
        if instrument_value not in CONSULTATION_INSTRUMENT_VALUES:
            raise ValueError("楽器総評価額を選択してください。")
        values.update(
            {
                "event_date": event_date,
                "support_content": support_content,
                "instrument_value": CONSULTATION_INSTRUMENT_VALUES[instrument_value],
            }
        )
    elif mode == "planning":
        planning_type = str(payload.get("planning_type", "")).strip()
        if planning_type not in CONSULTATION_PLANNING_TYPES:
            raise ValueError("企画・プロデュースのご相談種別を選択してください。")
        if planning_type == "その他" and not values["message"]:
            raise ValueError("ご相談詳細・特記事項に概略・要望事項を記入してください。")
        values["planning_type"] = planning_type
    else:
        cargo_detail = str(payload.get("cargo_detail", "")).strip()
        if not cargo_detail or len(cargo_detail) > 1000:
            raise ValueError("搬送希望のお荷物・機材概要を1,000文字以内で入力してください。")
        values["cargo_detail"] = cargo_detail

    return values


def validate_lesson_reservation(payload):
    if not isinstance(payload, dict):
        raise ValueError("入力内容を確認してください。")

    values = {
        "name": str(payload.get("name", "")).strip(),
        "email": str(payload.get("email", "")).strip(),
        "phone": str(payload.get("phone", "")).strip(),
        "lesson_type": str(payload.get("lesson_type", "")).strip(),
        "preferred_date": str(payload.get("preferred_date", "")).strip(),
        "preferred_time": str(payload.get("preferred_time", "")).strip(),
        "message": str(payload.get("message", "")).strip(),
    }
    if not values["name"] or len(values["name"]) > 80:
        raise ValueError("お名前を80文字以内で入力してください。")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", values["email"]):
        raise ValueError("メールアドレスを正しく入力してください。")
    if values["phone"] and not re.fullmatch(r"[0-9+()\-\s]{8,20}", values["phone"]):
        raise ValueError("電話番号を正しく入力してください。")
    if values["lesson_type"] not in LESSON_TYPES:
        raise ValueError("レッスン種別を選択してください。")
    values["duration_minutes"] = LESSON_DURATION_MINUTES[values["lesson_type"]]
    try:
        preferred_date = datetime.strptime(values["preferred_date"], "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("希望日時を正しく入力してください。") from exc
    first_available_date = current_japan_date() + timedelta(days=1)
    last_available_date = add_one_month(current_japan_date())
    if not first_available_date <= preferred_date <= last_available_date:
        raise ValueError("予約日は明日から1か月先までの範囲で選択してください。")
    available_times = WEEKDAY_RESERVATION_TIMES[preferred_date.weekday()]
    if not is_allowed_lesson_start(
        values["lesson_type"], values["preferred_time"], available_times
    ):
        raise ValueError("選択した曜日の予約可能時間を指定してください。")
    values["occupied_times"] = reservation_slot_times(
        values["preferred_time"], values["duration_minutes"]
    )
    if len(values["message"]) > 500:
        raise ValueError("ご要望は500文字以内で入力してください。")
    return values


def validate_lesson_reservation_update(payload):
    if not isinstance(payload, dict):
        raise ValueError("入力内容を確認してください。")

    values = {}

    if "status" in payload:
        status = str(payload.get("status", "")).strip()
        if status not in RESERVATION_STATUS_VALUES:
            raise ValueError("状態は 受付・調整中・確認中・確定・キャンセル から選択してください。")
        values["status"] = status
    if "name" in payload:
        name = str(payload.get("name", "")).strip()
        if not name or len(name) > 80:
            raise ValueError("お名前を80文字以内で入力してください。")
        values["name"] = name
    if "email" in payload:
        email = str(payload.get("email", "")).strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValueError("メールアドレスを正しく入力してください。")
        values["email"] = email
    if "phone" in payload:
        phone = str(payload.get("phone", "")).strip()
        if phone and not re.fullmatch(r"[0-9+()\-\s]{8,20}", phone):
            raise ValueError("電話番号を正しく入力してください。")
        values["phone"] = phone
    if "lesson_type" in payload:
        lesson_type = str(payload.get("lesson_type", "")).strip()
        if lesson_type not in LESSON_TYPES:
            raise ValueError("レッスン種別を選択してください。")
        values["lesson_type"] = lesson_type
    if "preferred_date" in payload:
        preferred_date = str(payload.get("preferred_date", "")).strip()
        try:
            datetime.strptime(preferred_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("希望日を正しく入力してください。") from exc
        values["preferred_date"] = preferred_date
    if "preferred_time" in payload:
        preferred_time = str(payload.get("preferred_time", "")).strip()
        if preferred_time != CONSULTATION_TIME and not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", preferred_time
        ):
            raise ValueError("希望時間を正しく入力してください。")
        values["preferred_time"] = preferred_time
    if "duration_minutes" in payload:
        try:
            duration_minutes = int(payload.get("duration_minutes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("所要時間を15分単位で入力してください。") from exc
        if duration_minutes < 15 or duration_minutes > 480 or duration_minutes % 15:
            raise ValueError("所要時間を15分単位で入力してください。")
        values["duration_minutes"] = duration_minutes
    if "message" in payload:
        message = str(payload.get("message", "")).strip()
        if len(message) > 500:
            raise ValueError("ご要望は500文字以内で入力してください。")
        values["message"] = message

    if not values:
        raise ValueError("更新する項目を指定してください。")
    return values


def validate_reservation_id(value):
    reservation_id = str(value).strip()
    if not re.fullmatch(r"R-\d{8}-\d{3,}", reservation_id):
        raise ValueError("予約番号の形式が正しくありません。")
    return reservation_id


def validate_lesson_reservation_cancellation(payload):
    if not isinstance(payload, dict):
        raise ValueError("入力内容を確認してください。")
    reservation_id = validate_reservation_id(payload.get("reservation_id", ""))
    email = str(payload.get("email", "")).strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("予約時のメールアドレスを正しく入力してください。")
    return {"reservation_id": reservation_id, "email": email}


def parse_updated_count(result):
    updated_count = result.get("updatedCount", result.get("updated_count", 0))
    try:
        return max(0, int(updated_count))
    except (TypeError, ValueError):
        return 0


def validate_slot_status_request(payload):
    if not isinstance(payload, dict):
        raise ValueError("入力内容を確認してください。")

    values = {
        "start_date": str(payload.get("start_date", "")).strip(),
        "end_date": str(payload.get("end_date", "")).strip(),
        "start_time": str(payload.get("start_time", "")).strip(),
        "end_time": str(payload.get("end_time", "")).strip(),
        "status": str(payload.get("status", "")).strip(),
        "note": str(payload.get("note", "")).strip(),
    }

    try:
        start_date = datetime.strptime(values["start_date"], "%Y-%m-%d").date()
        end_date = datetime.strptime(values["end_date"], "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("開始日と終了日を正しく入力してください。") from exc

    if end_date < start_date:
        raise ValueError("終了日は開始日以降の日付を指定してください。")

    if values["status"] not in SLOT_STATUS_VALUES - {"調整中"}:
        raise ValueError("状態は 空き・予約済・お休み から指定してください。")

    def validate_time(field_name, value):
        if value == CONSULTATION_TIME:
            return value
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError(f"{field_name}を正しく入力してください。")
        return value

    start_time = validate_time("開始時間", values["start_time"])
    end_time = validate_time("終了時間", values["end_time"])

    if start_time != CONSULTATION_TIME and end_time != CONSULTATION_TIME:
        start_minutes = int(start_time[:2]) * 60 + int(start_time[3:])
        end_minutes = int(end_time[:2]) * 60 + int(end_time[3:])
        if end_minutes < start_minutes:
            raise ValueError("終了時間は開始時間以降を指定してください。")
        if start_minutes % 15 or end_minutes % 15:
            raise ValueError("時間は15分単位で指定してください。")
    elif start_time != end_time:
        raise ValueError("要相談を指定する場合は開始時間と終了時間を同じにしてください。")

    if len(values["note"]) > 200:
        raise ValueError("メモは200文字以内で入力してください。")

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "start_time": start_time,
        "end_time": end_time,
        "status": values["status"],
        "note": values["note"],
    }


def send_lesson_reservation(script_url, secret, values, action="create"):
    payload = json.dumps(
        {
            **values,
            "secret": secret,
            "action": action,
            "request_id": str(uuid.uuid4()),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    last_error = None
    attempts = 2 if action in {"create", "consultation", "update", "delete", "cancel", "upsert_slot_status_range"} else 1
    for attempt in range(attempts):
        script_request = urllib_request.Request(
            script_url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(
                script_request, timeout=LESSON_RESERVATION_TIMEOUT_SECONDS
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except (json.JSONDecodeError, OSError, urllib_error.URLError) as error:
            last_error = error
            if attempt == attempts - 1:
                raise
    else:
        raise last_error
    if not result.get("ok"):
        error_code = result.get("error", "Apps Script rejected the reservation")
        raise LessonReservationDeliveryError(error_code)
    return result


def format_update(values):
    content = values["content"]
    if values["media_type"]:
        content += f' [{values["media_type"]}:{values["media_url"]}]'
    return f'{values["date"]} | {values["category"]} | {content}'


def update_file(mutator, path=UPDATES_FILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                lines = []
            mutator(lines)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, delete=False
            ) as temporary_file:
                temporary_file.write("\n".join(lines) + "\n")
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, path)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def create_app(
    updates_file=UPDATES_FILE,
    database_url=None,
    store_file=STORE_FILE,
    product_file=PRODUCT_FILE,
):
    app = Flask(__name__, template_folder=".", static_folder=None)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    configured_database_url = (
        os.environ.get("DATABASE_URL", "") if database_url is None else database_url
    )
    verified_purchase_cache = {}
    purchase_verifications_in_flight = {}
    verified_purchase_cache_lock = threading.Lock()
    stripe_price_cache = {"key": None, "expires_at": 0.0, "valid": False}
    stripe_price_cache_lock = threading.Lock()
    product_validation_cache = {"signature": None, "valid": False}
    product_validation_lock = threading.Lock()
    local_download_counts = {}
    download_count_lock = threading.Lock()
    recovery_attempts = {}
    recovery_attempt_lock = threading.Lock()
    if configured_database_url:
        initialize_database(configured_database_url, updates_file)

    def with_lesson_reservation_cors(
        response,
        methods="POST, OPTIONS",
        headers="Content-Type",
    ):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = methods
        response.headers["Access-Control-Allow-Headers"] = headers
        response.headers["Access-Control-Max-Age"] = "600"
        return response

    def lesson_reservation_json(payload, status_code):
        response = jsonify(payload)
        response.status_code = status_code
        return with_lesson_reservation_cors(response)

    def with_store_cors(response, methods="GET, POST, PUT, OPTIONS"):
        request_origin = request.headers.get("Origin", "").rstrip("/")
        allowed_origins = {
            public_site_origin(),
            request.url_root.rstrip("/"),
        }
        if request_origin and request_origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = request_origin
            response.headers.add("Vary", "Origin")
        response.headers["Access-Control-Allow-Methods"] = methods
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Editor-Password, Stripe-Signature"
        )
        response.headers["Access-Control-Max-Age"] = "600"
        return response

    def store_json(payload, status_code=200):
        response = jsonify(payload)
        response.status_code = status_code
        response.headers["Cache-Control"] = "no-store"
        return with_store_cors(response)

    def get_updates():
        if configured_database_url:
            return load_database_updates(configured_database_url)
        return load_updates(updates_file)

    def get_store_settings():
        if configured_database_url:
            return load_database_store_settings(configured_database_url)
        return load_store_settings(store_file)

    def set_store_enabled(enabled):
        if configured_database_url:
            save_database_store_settings(configured_database_url, enabled)
        else:
            save_store_settings(enabled, store_file)

    def stripe_module():
        import stripe

        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        return stripe

    def stripe_value(value, key, default=None):
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    def stripe_secret_mode():
        secret_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
        if secret_key.startswith("sk_live_"):
            return "live"
        if secret_key.startswith("sk_test_"):
            return "test"
        return "invalid"

    def public_site_url():
        value = os.environ.get("PUBLIC_SITE_URL", "").strip().rstrip("/")
        parsed = urlparse(value)
        local_http = parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1",
            "localhost",
        }
        if (
            not value
            or (parsed.scheme != "https" and not local_http)
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            return ""
        return value

    def public_site_origin():
        site_url = public_site_url()
        if not site_url:
            return ""
        parsed = urlparse(site_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def checkout_payment_is_valid(
        checkout,
        expected_price_yen,
        expected_livemode,
        expected_product_id=PRODUCT_ID,
    ):
        metadata = stripe_value(checkout, "metadata", {}) or {}
        payment_intent = stripe_value(checkout, "payment_intent")
        latest_charge = stripe_value(payment_intent, "latest_charge")
        return all(
            (
                stripe_value(metadata, "product_id", "") == expected_product_id,
                stripe_value(checkout, "mode") == "payment",
                stripe_value(checkout, "status") == "complete",
                stripe_value(checkout, "payment_status") == "paid",
                stripe_value(checkout, "amount_total") == expected_price_yen,
                stripe_value(checkout, "currency") == "jpy",
                stripe_value(checkout, "livemode") is expected_livemode,
                stripe_value(payment_intent, "status") == "succeeded",
                stripe_value(payment_intent, "amount_received") == expected_price_yen,
                stripe_value(payment_intent, "currency") == "jpy",
                stripe_value(payment_intent, "livemode") is expected_livemode,
                stripe_value(latest_charge, "status") == "succeeded",
                stripe_value(latest_charge, "paid") is True,
                stripe_value(latest_charge, "captured") is True,
                stripe_value(latest_charge, "amount_captured") == expected_price_yen,
                stripe_value(latest_charge, "amount_refunded", 0) == 0,
                stripe_value(latest_charge, "refunded") is False,
                stripe_value(latest_charge, "disputed") is False,
                stripe_value(latest_charge, "currency") == "jpy",
                stripe_value(latest_charge, "livemode") is expected_livemode,
            )
        )

    def stripe_price_is_valid(configuration):
        price_id = os.environ.get("STRIPE_METRONOME_PRICE_ID", "").strip()
        cache_key = (
            price_id,
            configuration["price_yen"],
            configuration["stripe_mode"],
        )
        now = time.monotonic()
        with stripe_price_cache_lock:
            if (
                stripe_price_cache["key"] == cache_key
                and stripe_price_cache["expires_at"] > now
            ):
                return stripe_price_cache["valid"]

        price = stripe_module().Price.retrieve(price_id)
        valid = all(
            (
                stripe_value(price, "active") is True,
                stripe_value(price, "type") == "one_time",
                stripe_value(price, "currency") == "jpy",
                stripe_value(price, "unit_amount") == configuration["price_yen"],
                stripe_value(price, "livemode")
                is (configuration["stripe_mode"] == "live"),
            )
        )
        with stripe_price_cache_lock:
            stripe_price_cache.update(
                key=cache_key,
                expires_at=now + STORE_PAYMENT_CACHE_TTL_SECONDS,
                valid=valid,
            )
        return valid

    def stripe_price_is_ready(configuration):
        try:
            return stripe_price_is_valid(configuration)
        except Exception:
            app.logger.error("Stripe price readiness check failed")
            return False

    def retrieve_paid_product_id(
        session_id,
        expected_price_yen,
        expected_livemode,
    ):
        verification_key = (session_id, expected_price_yen, expected_livemode)
        now = time.monotonic()
        with verified_purchase_cache_lock:
            cached = verified_purchase_cache.get(verification_key)
            if cached and cached[0] > now:
                return cached[1]
            if cached:
                verified_purchase_cache.pop(verification_key, None)
            verification = purchase_verifications_in_flight.get(verification_key)
            if verification is None:
                verification = {
                    "event": threading.Event(),
                    "product_id": "",
                    "error": None,
                }
                purchase_verifications_in_flight[verification_key] = verification
                owns_verification = True
            else:
                owns_verification = False

        if not owns_verification:
            verification["event"].wait()
            if verification["error"] is not None:
                raise verification["error"]
            return verification["product_id"]

        try:
            checkout = stripe_module().checkout.Session.retrieve(
                session_id,
                expand=["payment_intent.latest_charge"],
            )
            metadata = stripe_value(checkout, "metadata", {}) or {}
            product_id = stripe_value(metadata, "product_id", "")
            try:
                purchase_price_yen = int(
                    stripe_value(metadata, "price_yen", expected_price_yen)
                )
            except (TypeError, ValueError):
                purchase_price_yen = 0
            try:
                checkout_created = int(stripe_value(checkout, "created", 0))
            except (TypeError, ValueError):
                checkout_created = 0
            checkout_age = int(time.time()) - checkout_created
            if (
                not checkout_payment_is_valid(
                    checkout,
                    purchase_price_yen,
                    expected_livemode,
                )
                or purchase_price_yen <= 0
                or checkout_created <= 0
                or checkout_age < -300
                or checkout_age > STORE_REISSUE_MAX_AGE_SECONDS
            ):
                product_id = ""
            if product_id:
                now = time.monotonic()
                with verified_purchase_cache_lock:
                    if len(verified_purchase_cache) >= STORE_PAYMENT_CACHE_MAX_ENTRIES:
                        expired = [
                            key
                            for key, (expires_at, _) in verified_purchase_cache.items()
                            if expires_at <= now
                        ]
                        for key in expired:
                            verified_purchase_cache.pop(key, None)
                        if len(verified_purchase_cache) >= STORE_PAYMENT_CACHE_MAX_ENTRIES:
                            oldest = min(
                                verified_purchase_cache,
                                key=lambda key: verified_purchase_cache[key][0],
                            )
                            verified_purchase_cache.pop(oldest, None)
                    verified_purchase_cache[verification_key] = (
                        now + STORE_PAYMENT_CACHE_TTL_SECONDS,
                        product_id,
                    )
            verification["product_id"] = product_id
            return product_id
        except Exception as exc:
            verification["error"] = exc
            raise
        finally:
            with verified_purchase_cache_lock:
                purchase_verifications_in_flight.pop(verification_key, None)
                verification["event"].set()

    def download_serializer():
        secret = os.environ.get("DOWNLOAD_TOKEN_SECRET", "").strip()
        if not secret:
            return None
        return URLSafeTimedSerializer(secret, salt="metronome-download")

    def purchase_reference(session_id):
        secret = os.environ.get("DOWNLOAD_TOKEN_SECRET", "").strip().encode("utf-8")
        return hmac.new(secret, session_id.encode("utf-8"), hashlib.sha256).hexdigest()[:16].upper()

    def personalized_product(session_id):
        license_text = (
            "トランペット練習メトロノーム 利用ライセンス\n\n"
            "本商品は購入者本人のみ利用できます。第三者への譲渡、共有、再配布、\n"
            "販売、公衆送信を禁止します。購入者本人が所有する複数端末では利用できます。\n\n"
            f"購入参照ID: {purchase_reference(session_id)}\n"
            "このIDは購入確認およびサポート対応に使用します。\n"
        ).encode("utf-8")
        output = io.BytesIO()
        with ZipFile(product_file) as source, ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for item in source.infolist():
                archive.writestr(item, source.read(item.filename))
            license_info = ZipInfo("LICENSE.txt", date_time=(2026, 1, 1, 0, 0, 0))
            license_info.compress_type = ZIP_DEFLATED
            archive.writestr(license_info, license_text)
        output.seek(0)
        return output

    def download_is_allowed(reference):
        if configured_database_url:
            with database_connection(configured_database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO store_downloads
                            (purchase_reference, window_started_at, download_count, last_downloaded_at)
                        VALUES (%s, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP)
                        ON CONFLICT (purchase_reference) DO UPDATE SET
                            window_started_at = CASE
                                WHEN store_downloads.window_started_at <
                                    CURRENT_TIMESTAMP - INTERVAL '24 hours'
                                THEN CURRENT_TIMESTAMP
                                ELSE store_downloads.window_started_at
                            END,
                            download_count = CASE
                                WHEN store_downloads.window_started_at <
                                    CURRENT_TIMESTAMP - INTERVAL '24 hours'
                                THEN 1
                                ELSE store_downloads.download_count + 1
                            END,
                            last_downloaded_at = CURRENT_TIMESTAMP
                        RETURNING download_count
                        """,
                        (reference,),
                    )
                    return cursor.fetchone()[0] <= STORE_DOWNLOAD_LIMIT

        now = time.monotonic()
        with download_count_lock:
            window_started_at, count = local_download_counts.get(reference, (now, 0))
            if now - window_started_at >= STORE_DOWNLOAD_WINDOW_SECONDS:
                window_started_at, count = now, 0
            count += 1
            local_download_counts[reference] = (window_started_at, count)
            return count <= STORE_DOWNLOAD_LIMIT

    def recovery_attempt_is_allowed(client_address):
        now = time.monotonic()
        with recovery_attempt_lock:
            window_started_at, count = recovery_attempts.get(client_address, (now, 0))
            if now - window_started_at >= STORE_RECOVERY_WINDOW_SECONDS:
                window_started_at, count = now, 0
            count += 1
            recovery_attempts[client_address] = (window_started_at, count)
            return count <= STORE_RECOVERY_LIMIT

    def product_archive_is_valid():
        path = Path(product_file)
        try:
            file_stat = path.stat()
            signature = (file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns)
        except OSError:
            return False

        with product_validation_lock:
            if product_validation_cache["signature"] == signature:
                return product_validation_cache["valid"]
            try:
                with ZipFile(path) as archive:
                    valid = (
                        PRODUCT_REQUIRED_FILES.issubset(archive.namelist())
                        and archive.testzip() is None
                    )
            except (BadZipFile, OSError, RuntimeError):
                valid = False
            product_validation_cache.update(signature=signature, valid=valid)
            return valid

    def flow_harmony_archive_is_valid():
        try:
            with ZipFile(FLOW_HARMONY_PRODUCT_FILE) as archive:
                return (
                    PRODUCT_REQUIRED_FILES.issubset(archive.namelist())
                    and archive.testzip() is None
                )
        except (BadZipFile, OSError, RuntimeError):
            return False

    def flow_harmony_configuration():
        price_text = os.environ.get(
            "FLOW_HARMONY_PRICE_YEN", str(FLOW_HARMONY_PRODUCT_PRICE_YEN)
        )
        try:
            price_yen = int(price_text)
        except ValueError:
            price_yen = FLOW_HARMONY_PRODUCT_PRICE_YEN
        price_id = os.environ.get("STRIPE_FLOW_HARMONY_PRICE_ID", "").strip()
        required = {
            "STRIPE_SECRET_KEY": stripe_secret_mode() != "invalid",
            "STRIPE_WEBHOOK_SECRET": os.environ.get(
                "STRIPE_WEBHOOK_SECRET", ""
            ).strip().startswith("whsec_"),
            "STRIPE_FLOW_HARMONY_PRICE_ID": price_id.startswith("price_"),
            "DOWNLOAD_TOKEN_SECRET": len(
                os.environ.get("DOWNLOAD_TOKEN_SECRET", "").strip()
            )
            >= 32,
            "PUBLIC_SITE_URL": bool(public_site_url()),
            "FLOW_HARMONY_PRICE_YEN": price_yen == FLOW_HARMONY_PRODUCT_PRICE_YEN,
        }
        product_ready = flow_harmony_archive_is_valid()
        return {
            "price_yen": price_yen,
            "price_id": price_id,
            "site_url": public_site_url(),
            "stripe_mode": stripe_secret_mode(),
            "ready": all(required.values()) and product_ready,
            "missing": [name for name, valid in required.items() if not valid]
            + ([] if product_ready else ["FLOW_HARMONY_PRODUCT_FILE_INVALID"]),
        }

    def flow_harmony_price_is_ready(configuration):
        try:
            price = stripe_module().Price.retrieve(configuration["price_id"])
            return all(
                (
                    stripe_value(price, "active") is True,
                    stripe_value(price, "type") == "one_time",
                    stripe_value(price, "currency") == "jpy",
                    stripe_value(price, "unit_amount") == configuration["price_yen"],
                    stripe_value(price, "livemode")
                    is (configuration["stripe_mode"] == "live"),
                )
            )
        except Exception:
            app.logger.error("Flow Harmony Stripe price readiness check failed")
            return False

    def retrieve_flow_harmony_checkout(session_id, configuration):
        checkout = stripe_module().checkout.Session.retrieve(
            session_id,
            expand=["payment_intent.latest_charge"],
        )
        if not checkout_payment_is_valid(
            checkout,
            configuration["price_yen"],
            configuration["stripe_mode"] == "live",
            FLOW_HARMONY_PRODUCT_ID,
        ):
            return None
        return checkout

    def personalized_flow_harmony_product(session_id):
        license_text = (
            "Flow Harmony オフライン版 利用ライセンス\n\n"
            "本商品は購入者本人のみ利用できます。第三者への譲渡、共有、再配布、\n"
            "販売、公衆送信を禁止します。購入者本人が所有する複数端末では利用できます。\n\n"
            f"購入参照ID: {purchase_reference(session_id)}\n"
        ).encode("utf-8")
        output = io.BytesIO()
        with ZipFile(FLOW_HARMONY_PRODUCT_FILE) as source, ZipFile(
            output, "w", compression=ZIP_DEFLATED
        ) as archive:
            for item in source.infolist():
                archive.writestr(item, source.read(item.filename))
            license_info = ZipInfo("LICENSE.txt", date_time=(2026, 1, 1, 0, 0, 0))
            license_info.compress_type = ZIP_DEFLATED
            archive.writestr(license_info, license_text)
        output.seek(0)
        return output

    def store_configuration():
        price_text = os.environ.get("METRONOME_PRICE_YEN", str(PRODUCT_PRICE_YEN))
        try:
            price_yen = int(price_text)
        except ValueError:
            price_yen = PRODUCT_PRICE_YEN
        price_valid = price_yen > 0
        if not price_valid:
            price_yen = PRODUCT_PRICE_YEN
        required = {
            "STRIPE_SECRET_KEY": stripe_secret_mode() != "invalid",
            "STRIPE_WEBHOOK_SECRET": os.environ.get(
                "STRIPE_WEBHOOK_SECRET", ""
            ).strip().startswith("whsec_"),
            "STRIPE_METRONOME_PRICE_ID": os.environ.get(
                "STRIPE_METRONOME_PRICE_ID", ""
            ).strip().startswith("price_"),
            "DOWNLOAD_TOKEN_SECRET": len(
                os.environ.get("DOWNLOAD_TOKEN_SECRET", "").strip()
            ) >= 32,
            "PUBLIC_SITE_URL": bool(public_site_url()),
            "METRONOME_PRICE_YEN": price_valid,
        }
        product_ready = product_archive_is_valid()
        return {
            "price_yen": price_yen,
            "site_url": public_site_url(),
            "stripe_mode": stripe_secret_mode(),
            "ready": all(required.values()) and product_ready,
            "missing": [name for name, valid in required.items() if not valid]
            + ([] if product_ready else ["PRODUCT_FILE_INVALID"]),
        }

    def require_editor():
        configured_password = os.environ.get("EDITOR_PASSWORD", "")
        payload = request.get_json(silent=True)
        supplied_password = request.headers.get("X-Editor-Password", "")
        if not supplied_password and isinstance(payload, dict):
            supplied_password = str(payload.get("editor_password", ""))
        if not configured_password:
            return jsonify({"error": "編集用パスワードが設定されていません。"}), 503
        if not hmac.compare_digest(
            supplied_password.encode("utf-8"), configured_password.encode("utf-8")
        ):
            return jsonify({"error": "パスワードが違います。"}), 401
        return None

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/favicon.ico")
    def favicon():
        return app.response_class(status=204)

    @app.get("/health")
    def health():
        response = jsonify({"status": "ok"})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/lesson/")
    def lesson():
        response = make_response(render_template("lesson/index.html"))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/lesson/application-form.html")
    def lesson_application_form():
        return render_template("lesson/application-form.html")

    @app.get("/products/")
    def products():
        response = make_response(render_template("products/index.html"))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/download-guide/")
    def download_guide():
        return render_template("download-guide/index.html")

    @app.get("/flow-harmony/")
    def flow_harmony():
        response = make_response(render_template("flow-harmony/index.html"))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/legal/")
    def legal():
        return render_template(
            "legal/index.html",
            product_price_yen=store_configuration()["price_yen"],
        )

    @app.get("/legal/privacy-policy.html")
    def privacy_policy():
        return render_template("legal/privacy-policy.html")

    @app.get("/schedule/")
    def schedule():
        response = make_response(render_template("schedule/index.html"))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/api/updates")
    def updates_api():
        response = jsonify([public_update(item) for item in get_updates()])
        response.headers["Cache-Control"] = "no-store"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    @app.route("/api/store/product", methods=["GET", "PUT", "OPTIONS"])
    def store_product():
        if request.method == "OPTIONS":
            return with_store_cors(app.response_class(status=204))
        if request.method == "PUT":
            error = require_editor()
            if error:
                response, status_code = error
                response.status_code = status_code
                return with_store_cors(response)
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("enabled"), bool
            ):
                return store_json({"error": "販売状態を指定してください。"}, 400)
            set_store_enabled(payload["enabled"])

        settings = get_store_settings()
        configuration = store_configuration()
        checkout_available = (
            settings["enabled"]
            and configuration["ready"]
            and stripe_price_is_ready(configuration)
        )
        return store_json(
            {
                "product_id": PRODUCT_ID,
                "name": PRODUCT_NAME,
                "price_yen": configuration["price_yen"],
                "enabled": settings["enabled"],
                "checkout_available": checkout_available,
            }
        )

    @app.route("/api/store/health", methods=["GET", "OPTIONS"])
    def store_health():
        if request.method == "OPTIONS":
            return with_store_cors(app.response_class(status=204))
        error = require_editor()
        if error:
            response, status_code = error
            response.status_code = status_code
            return with_store_cors(response)

        configuration = store_configuration()
        flow_configuration = flow_harmony_configuration()
        checks = {
            "configuration": configuration["ready"],
            "product_archive": product_archive_is_valid(),
            "public_site_url": bool(configuration["site_url"]),
            "stripe_price": False,
            "flow_harmony_configuration": flow_configuration["ready"],
            "flow_harmony_product_archive": flow_harmony_archive_is_valid(),
            "flow_harmony_stripe_price": False,
        }
        if configuration["ready"]:
            checks["stripe_price"] = stripe_price_is_ready(configuration)
        if flow_configuration["ready"]:
            checks["flow_harmony_stripe_price"] = flow_harmony_price_is_ready(
                flow_configuration
            )

        ready = all(checks.values())
        response = store_json(
            {
                "ready": ready,
                "production_ready": ready
                and configuration["stripe_mode"] == "live",
                "stripe_mode": configuration["stripe_mode"],
                "store_enabled": get_store_settings()["enabled"],
                "price_yen": configuration["price_yen"],
                "flow_harmony_price_yen": flow_configuration["price_yen"],
                "checks": checks,
                "invalid_configuration": configuration["missing"]
                + flow_configuration["missing"],
            },
            200 if ready else 503,
        )
        return response

    @app.route("/api/store/checkout", methods=["POST", "OPTIONS"])
    def create_store_checkout():
        if request.method == "OPTIONS":
            return with_store_cors(app.response_class(status=204))
        if not get_store_settings()["enabled"]:
            return store_json({"error": "現在販売を停止しています。"}, 403)
        configuration = store_configuration()
        if not configuration["ready"]:
            app.logger.error(
                "Store configuration is incomplete: %s", configuration["missing"]
            )
            return store_json({"error": "決済機能を準備中です。"}, 503)
        if not stripe_price_is_ready(configuration):
            app.logger.error("Configured Stripe price is unavailable or invalid")
            return store_json({"error": "決済価格を確認できませんでした。"}, 503)

        payload = request.get_json(silent=True)
        checkout_request_id = (
            str(payload.get("checkout_request_id", "")).strip()
            if isinstance(payload, dict)
            else ""
        )
        try:
            parsed_request_id = uuid.UUID(checkout_request_id)
        except (ValueError, TypeError, AttributeError):
            parsed_request_id = None
        if parsed_request_id is None or parsed_request_id.version != 4:
            return store_json({"error": "決済リクエストが正しくありません。"}, 400)

        site_url = configuration["site_url"]
        try:
            checkout = stripe_module().checkout.Session.create(
                mode="payment",
                line_items=[
                    {
                        "price": os.environ["STRIPE_METRONOME_PRICE_ID"],
                        "quantity": 1,
                    }
                ],
                client_reference_id=checkout_request_id,
                metadata={
                    "product_id": PRODUCT_ID,
                    "checkout_request_id": checkout_request_id,
                    "price_yen": str(configuration["price_yen"]),
                    "price_id": os.environ["STRIPE_METRONOME_PRICE_ID"],
                },
                success_url=(
                    f"{site_url}/products/?purchase=success"
                    "&session_id={CHECKOUT_SESSION_ID}#metronome"
                ),
                cancel_url=f"{site_url}/products/?purchase=cancelled#metronome",
                idempotency_key=f"{PRODUCT_ID}:{checkout_request_id}",
            )
        except Exception as exc:
            app.logger.exception("Stripe Checkout session creation failed")
            diagnostic_code = str(getattr(exc, "code", "") or type(exc).__name__)
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", diagnostic_code) is None:
                diagnostic_code = type(exc).__name__
            return store_json(
                {
                    "error": "決済画面を開始できませんでした。",
                    "diagnostic_code": diagnostic_code,
                },
                502,
            )
        return store_json({"checkout_url": stripe_value(checkout, "url")}, 201)

    @app.get("/api/store/flow-harmony/product")
    def flow_harmony_store_product():
        configuration = flow_harmony_configuration()
        return store_json(
            {
                "product_id": FLOW_HARMONY_PRODUCT_ID,
                "name": FLOW_HARMONY_PRODUCT_NAME,
                "price_yen": configuration["price_yen"],
                "enabled": FLOW_HARMONY_SALES_ENABLED,
                "checkout_available": FLOW_HARMONY_SALES_ENABLED
                and configuration["ready"]
                and flow_harmony_price_is_ready(configuration),
            }
        )

    @app.route("/api/store/flow-harmony/checkout", methods=["POST", "OPTIONS"])
    def create_flow_harmony_checkout():
        if request.method == "OPTIONS":
            return with_store_cors(app.response_class(status=204))
        if not FLOW_HARMONY_SALES_ENABLED:
            return store_json({"error": "現在公開を停止しています。"}, 503)
        configuration = flow_harmony_configuration()
        if not configuration["ready"] or not flow_harmony_price_is_ready(configuration):
            return store_json({"error": "決済機能を準備中です。"}, 503)
        payload = request.get_json(silent=True)
        checkout_request_id = (
            str(payload.get("checkout_request_id", "")).strip()
            if isinstance(payload, dict)
            else ""
        )
        try:
            parsed_request_id = uuid.UUID(checkout_request_id)
        except (ValueError, TypeError, AttributeError):
            parsed_request_id = None
        if parsed_request_id is None or parsed_request_id.version != 4:
            return store_json({"error": "決済リクエストが正しくありません。"}, 400)
        try:
            checkout = stripe_module().checkout.Session.create(
                mode="payment",
                line_items=[{"price": configuration["price_id"], "quantity": 1}],
                client_reference_id=checkout_request_id,
                metadata={
                    "product_id": FLOW_HARMONY_PRODUCT_ID,
                    "checkout_request_id": checkout_request_id,
                    "price_yen": str(configuration["price_yen"]),
                    "price_id": configuration["price_id"],
                },
                success_url=(
                    f"{configuration['site_url']}/products/?flow_purchase=success"
                    "&flow_session_id={CHECKOUT_SESSION_ID}#flow-harmony"
                ),
                cancel_url=(
                    f"{configuration['site_url']}/products/"
                    "?flow_purchase=cancelled#flow-harmony"
                ),
                idempotency_key=(
                    f"{FLOW_HARMONY_PRODUCT_ID}:{checkout_request_id}"
                ),
            )
        except Exception as exc:
            app.logger.exception("Flow Harmony Checkout session creation failed")
            return store_json(
                {
                    "error": "決済画面を開始できませんでした。",
                    "diagnostic_code": type(exc).__name__,
                },
                502,
            )
        return store_json({"checkout_url": stripe_value(checkout, "url")}, 201)

    @app.route(
        "/api/store/flow-harmony/download-link", methods=["POST", "OPTIONS"]
    )
    def create_flow_harmony_download_link():
        if request.method == "OPTIONS":
            return with_store_cors(app.response_class(status=204))
        payload = request.get_json(silent=True)
        session_id = (
            str(payload.get("session_id", "")).strip()
            if isinstance(payload, dict)
            else ""
        )
        if CHECKOUT_SESSION_PATTERN.fullmatch(session_id) is None:
            return store_json({"error": "決済情報が正しくありません。"}, 400)
        serializer = download_serializer()
        configuration = flow_harmony_configuration()
        if serializer is None or not configuration["ready"]:
            return store_json({"error": "ダウンロードを準備中です。"}, 503)
        try:
            checkout = retrieve_flow_harmony_checkout(session_id, configuration)
        except Exception:
            app.logger.exception("Flow Harmony payment verification failed")
            return store_json({"error": "決済情報を確認できませんでした。"}, 502)
        if checkout is None:
            return store_json({"error": "支払いの完了を確認できません。"}, 403)
        token = serializer.dumps(
            {"product_id": FLOW_HARMONY_PRODUCT_ID, "session_id": session_id}
        )
        return store_json(
            {
                "download_url": (
                    f"{request.url_root.rstrip('/')}/api/store/flow-harmony/download/{token}"
                ),
                "expires_in": 86400,
            }
        )

    @app.get("/api/store/flow-harmony/download/<token>")
    def download_flow_harmony_product(token):
        serializer = download_serializer()
        if serializer is None:
            return store_json({"error": "ダウンロードを準備中です。"}, 503)
        try:
            payload = serializer.loads(token, max_age=86400)
        except SignatureExpired:
            return store_json({"error": "ダウンロード期限が切れました。"}, 410)
        except BadSignature:
            return store_json({"error": "ダウンロードURLが正しくありません。"}, 403)
        if payload.get("product_id") != FLOW_HARMONY_PRODUCT_ID:
            return store_json({"error": "商品が見つかりません。"}, 404)
        session_id = str(payload.get("session_id", "")).strip()
        configuration = flow_harmony_configuration()
        try:
            checkout = retrieve_flow_harmony_checkout(session_id, configuration)
        except Exception:
            app.logger.exception("Flow Harmony payment revalidation failed")
            return store_json({"error": "決済情報を再確認できませんでした。"}, 502)
        if checkout is None:
            return store_json({"error": "現在この商品をダウンロードできません。"}, 403)
        if not download_is_allowed(purchase_reference(session_id)):
            return store_json({"error": "ダウンロード回数の上限に達しました。"}, 429)
        response = send_file(
            personalized_flow_harmony_product(session_id),
            as_attachment=True,
            download_name="flow-harmony-offline.zip",
            mimetype="application/zip",
            conditional=True,
        )
        response.headers["Cache-Control"] = "private, max-age=3600"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return with_store_cors(response)

    @app.post("/api/store/webhook")
    def stripe_webhook():
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
        if not webhook_secret:
            return jsonify({"error": "Webhookが設定されていません。"}), 503
        try:
            event = stripe_module().Webhook.construct_event(
                request.get_data(),
                request.headers.get("Stripe-Signature", ""),
                webhook_secret,
            )
        except (ValueError, Exception) as exc:
            app.logger.warning("Stripe webhook rejected: %s", exc)
            return jsonify({"error": "Webhook署名を確認できません。"}), 400

        event_id = stripe_value(event, "id", "")
        event_type = stripe_value(event, "type", "unknown")
        if event_id:
            record_stripe_event(configured_database_url, event_id, event_type)
        return jsonify({"received": True})

    @app.route("/api/store/download-link", methods=["POST", "OPTIONS"])
    def create_download_link():
        if request.method == "OPTIONS":
            return with_store_cors(app.response_class(status=204))
        payload = request.get_json(silent=True)
        session_id = str(payload.get("session_id", "")).strip() if isinstance(payload, dict) else ""
        if CHECKOUT_SESSION_PATTERN.fullmatch(session_id) is None:
            return store_json({"error": "決済情報が正しくありません。"}, 400)
        serializer = download_serializer()
        if serializer is None or not product_archive_is_valid():
            return store_json({"error": "ダウンロードを準備中です。"}, 503)
        try:
            configuration = store_configuration()
            product_id = retrieve_paid_product_id(
                session_id,
                configuration["price_yen"],
                configuration["stripe_mode"] == "live",
            )
        except Exception:
            app.logger.exception("Stripe Checkout session retrieval failed")
            return store_json({"error": "決済情報を確認できませんでした。"}, 502)
        if product_id != PRODUCT_ID:
            return store_json({"error": "支払いの完了を確認できません。"}, 403)

        token = serializer.dumps({"product_id": PRODUCT_ID, "session_id": session_id})
        download_url = f"{request.url_root.rstrip('/')}/api/store/download/{token}"
        return store_json({"download_url": download_url, "expires_in": 86400})

    @app.route("/api/store/recover-download", methods=["POST", "OPTIONS"])
    def recover_download_link():
        if request.method == "OPTIONS":
            return with_store_cors(app.response_class(status=204))
        if not recovery_attempt_is_allowed(request.remote_addr or "unknown"):
            return store_json(
                {"error": "再発行の試行回数が多すぎます。15分後にお試しください。"},
                429,
            )
        payload = request.get_json(silent=True)
        email = str(payload.get("email", "")).strip().lower() if isinstance(payload, dict) else ""
        purchase_reference_input = (
            str(payload.get("receipt_number", "")).strip()
            if isinstance(payload, dict)
            else ""
        )
        if (
            re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) is None
            or re.fullmatch(r"(?:[A-Za-z0-9-]{4,64}|pi_[A-Za-z0-9_]{8,255})", purchase_reference_input)
            is None
        ):
            return store_json({"error": "購入情報を確認できませんでした。"}, 400)
        serializer = download_serializer()
        if serializer is None or not product_archive_is_valid():
            return store_json({"error": "ダウンロードを準備中です。"}, 503)
        configuration = store_configuration()
        try:
            sessions = stripe_module().checkout.Session.list(
                limit=100,
                created={"gte": int(time.time()) - STORE_REISSUE_MAX_AGE_SECONDS},
                expand=["data.payment_intent.latest_charge"],
            )
            for checkout in sessions.auto_paging_iter():
                customer_details = stripe_value(checkout, "customer_details", {}) or {}
                payment_intent = stripe_value(checkout, "payment_intent", {}) or {}
                latest_charge = stripe_value(payment_intent, "latest_charge", {}) or {}
                payment_intent_id = str(stripe_value(payment_intent, "id", "")).strip()
                receipt_number = (
                    str(stripe_value(latest_charge, "receipt_number", ""))
                    .strip()
                    .upper()
                )
                if (
                    str(stripe_value(customer_details, "email", "")).strip().lower() != email
                    or purchase_reference_input not in {payment_intent_id, receipt_number}
                ):
                    continue
                session_id = str(stripe_value(checkout, "id", ""))
                product_id = retrieve_paid_product_id(
                    session_id,
                    configuration["price_yen"],
                    configuration["stripe_mode"] == "live",
                )
                if product_id == PRODUCT_ID:
                    token = serializer.dumps(
                        {"product_id": PRODUCT_ID, "session_id": session_id}
                    )
                    download_url = (
                        f"{request.url_root.rstrip('/')}/api/store/download/{token}"
                    )
                    return store_json(
                        {
                            "download_url": download_url,
                            "session_id": session_id,
                            "expires_in": 86400,
                        }
                    )
        except Exception:
            app.logger.exception("Stripe purchase recovery failed")
            return store_json({"error": "購入情報を確認できませんでした。"}, 502)
        return store_json({"error": "購入情報を確認できませんでした。"}, 403)

    @app.get("/api/store/download/<token>")
    def download_product(token):
        serializer = download_serializer()
        if serializer is None:
            return jsonify({"error": "ダウンロードを準備中です。"}), 503
        try:
            payload = serializer.loads(token, max_age=86400)
        except SignatureExpired as exc:
            if not isinstance(exc.payload, bytes):
                expired_payload = {}
            else:
                try:
                    expired_payload = serializer.load_payload(exc.payload)
                except BadData:
                    expired_payload = {}
            if not isinstance(expired_payload, dict):
                expired_payload = {}
            session_id = str(expired_payload.get("session_id", "")).strip()
            if (
                expired_payload.get("product_id") == PRODUCT_ID
                and CHECKOUT_SESSION_PATTERN.fullmatch(session_id) is not None
            ):
                return redirect(
                    f"/products/?{urlencode({'purchase': 'reissue', 'session_id': session_id})}"
                    "#metronome"
                )
            return store_json({"error": "ダウンロード期限が切れました。"}, 410)
        except BadSignature:
            return store_json({"error": "ダウンロードURLが正しくありません。"}, 403)
        if payload.get("product_id") != PRODUCT_ID:
            return store_json({"error": "商品が見つかりません。"}, 404)
        if not product_archive_is_valid():
            return store_json({"error": "商品ファイルを確認中です。"}, 503)
        session_id = str(payload.get("session_id", "")).strip()
        if CHECKOUT_SESSION_PATTERN.fullmatch(session_id) is None:
            return store_json({"error": "決済情報が正しくありません。"}, 403)
        try:
            configuration = store_configuration()
            product_id = retrieve_paid_product_id(
                session_id,
                configuration["price_yen"],
                configuration["stripe_mode"] == "live",
            )
        except Exception:
            app.logger.exception("Stripe payment revalidation failed before download")
            return store_json({"error": "決済情報を再確認できませんでした。"}, 502)
        if product_id != PRODUCT_ID:
            return store_json({"error": "現在この商品をダウンロードできません。"}, 403)
        if request.method != "HEAD" and "Range" not in request.headers:
            if not download_is_allowed(purchase_reference(session_id)):
                return store_json(
                    {
                        "error": "短時間に多数のダウンロードが行われました。24時間後に再度お試しいただくか、お問い合わせください。"
                    },
                    429,
                )
        product_download = personalized_product(session_id)
        response = send_file(
            product_download,
            as_attachment=True,
            download_name="trumpet-practice-metronome.zip",
            mimetype="application/zip",
            conditional=True,
        )
        response.headers["Cache-Control"] = "private, max-age=3600"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return with_store_cors(response)

    @app.route("/api/lesson-reservations", methods=["POST", "OPTIONS"])
    def create_lesson_reservation():
        if request.method == "OPTIONS":
            return with_lesson_reservation_cors(
                app.response_class(status=204),
                methods="GET, POST, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        if request.get_json(silent=True) and request.get_json(silent=True).get("website"):
            return lesson_reservation_json({"saved": True}, 201)
        try:
            values = validate_lesson_reservation(request.get_json(silent=True))
        except ValueError as exc:
            return lesson_reservation_json({"error": str(exc)}, 400)

        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            missing_settings = []
            if not script_url:
                missing_settings.append("GOOGLE_APPS_SCRIPT_URL")
            if not script_secret:
                missing_settings.append("GOOGLE_APPS_SCRIPT_SECRET")
            return lesson_reservation_json(
                {
                    "error": "現在、Web予約を利用できません。メールまたは電話でお問い合わせください。",
                    "missing_settings": missing_settings,
                },
                503,
            )
        try:
            result = send_lesson_reservation(script_url, script_secret, values)
        except LessonReservationDeliveryError as exc:
            app.logger.exception("Apps Script rejected lesson reservation")
            return lesson_reservation_json(
                {
                    "error": "予約の送信に失敗しました。時間をおいて再度お試しください。",
                    "delivery_error": str(exc),
                },
                502,
            )
        except json.JSONDecodeError:
            app.logger.exception("Apps Script returned an invalid response")
            return lesson_reservation_json(
                {
                    "error": "予約の送信に失敗しました。時間をおいて再度お試しください。",
                    "delivery_error": "INVALID_APPS_SCRIPT_RESPONSE",
                },
                502,
            )
        except (OSError, ValueError, urllib_error.URLError):
            app.logger.exception("Failed to send lesson reservation")
            return lesson_reservation_json(
                {"error": "予約の送信に失敗しました。時間をおいて再度お試しください。"},
                502,
            )

        if result.get("reservationLimit"):
            max_reservations = result.get("maxReservations", 4)
            return lesson_reservation_json(
                {
                    "saved": False,
                    "reservation_limit": True,
                    "max_reservations": max_reservations,
                    "error": f"予約は1人最大{max_reservations}枠までです。既存予約をキャンセルしてからお申し込みください。",
                },
                409,
            )

        if result.get("conflict"):
            return lesson_reservation_json(
                {
                    "saved": False,
                    "conflict": True,
                    "status": result.get("status", "調整中"),
                    "reservation_id": result.get("reservationId", ""),
                    "auto_reply_sent": False,
                    "duplicate": False,
                    "duration_minutes": values["duration_minutes"],
                },
                409,
            )

        return lesson_reservation_json(
            {
                "saved": True,
                "reservation_id": result.get("reservationId", ""),
                "status": result.get("status", "確認中"),
                "auto_reply_sent": bool(result.get("autoReplySent", False)),
                "duplicate": bool(result.get("duplicate", False)),
                "duration_minutes": values["duration_minutes"],
            },
            201,
        )

    @app.route("/api/consultation", methods=["POST", "OPTIONS"])
    def create_consultation():
        if request.method == "OPTIONS":
            return with_lesson_reservation_cors(app.response_class(status=204))
        payload = request.get_json(silent=True)
        if isinstance(payload, dict) and payload.get("website"):
            return lesson_reservation_json({"saved": True}, 201)
        try:
            values = validate_consultation(payload)
        except ValueError as exc:
            return lesson_reservation_json({"error": str(exc)}, 400)

        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            return lesson_reservation_json(
                {
                    "error": "現在、Webフォームを利用できません。メールまたは電話でお問い合わせください。"
                },
                503,
            )
        try:
            result = send_lesson_reservation(
                script_url, script_secret, values, action="consultation"
            )
        except (
            LessonReservationDeliveryError,
            json.JSONDecodeError,
            OSError,
            ValueError,
            urllib_error.URLError,
        ):
            app.logger.exception("Failed to send consultation")
            return lesson_reservation_json(
                {"error": "送信に失敗しました。時間をおいて再度お試しください。"},
                502,
            )

        return lesson_reservation_json(
            {
                "saved": True,
                "consultation_id": result.get("consultationId", ""),
                "auto_reply_sent": bool(result.get("autoReplySent", False)),
            },
            201,
        )

    @app.get("/api/lesson-reservations")
    def list_lesson_reservations():
        error = require_editor()
        if error:
            response, status_code = error
            response.status_code = status_code
            return with_lesson_reservation_cors(
                response,
                methods="GET, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )

        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            return lesson_reservation_json(
                {"error": "現在、予約一覧を取得できません。"},
                503,
            )

        try:
            result = send_lesson_reservation(
                script_url,
                script_secret,
                {},
                action="list",
            )
            response = jsonify({"reservations": result.get("reservations", [])})
            return with_lesson_reservation_cors(
                response,
                methods="GET, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        except LessonReservationDeliveryError as exc:
            app.logger.exception("Apps Script rejected lesson reservation list")
            error_message = str(exc)
            if error_message in {"Unsupported action", "OUTDATED_DEPLOYMENT"}:
                return lesson_reservation_json(
                    {"error": "Apps Scriptの公開版が古いため予約一覧を取得できません。Code.gsを新しいバージョンで再デプロイしてください。"},
                    503,
                )
            return lesson_reservation_json(
                {"error": "現在、予約一覧を取得できません。"},
                503,
            )
        except (json.JSONDecodeError, OSError, urllib_error.URLError, ValueError):
            app.logger.exception("Failed to list lesson reservations")
            return lesson_reservation_json(
                {"error": "現在、予約一覧を取得できません。"},
                503,
            )

    @app.route("/api/lesson-reservations/cancel", methods=["POST", "OPTIONS"])
    def cancel_lesson_reservation():
        if request.method == "OPTIONS":
            return with_lesson_reservation_cors(
                app.response_class(status=204),
                methods="POST, OPTIONS",
            )
        try:
            values = validate_lesson_reservation_cancellation(request.get_json(silent=True))
        except ValueError as exc:
            return lesson_reservation_json({"error": str(exc)}, 400)

        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            return lesson_reservation_json(
                {"error": "現在、予約をキャンセルできません。直接お問い合わせください。"},
                503,
            )
        try:
            result = send_lesson_reservation(
                script_url,
                script_secret,
                values,
                action="cancel",
            )
            return lesson_reservation_json(
                {
                    "cancelled": True,
                    "reservation_id": result.get("reservationId", values["reservation_id"]),
                    "released_count": parse_updated_count(result),
                    "already_cancelled": bool(result.get("alreadyCancelled", False)),
                    "cancellation_email_sent": result.get("cancellationEmailSent"),
                },
                200,
            )
        except LessonReservationDeliveryError as exc:
            if str(exc) in {"NOT_FOUND", "EMAIL_MISMATCH"}:
                return lesson_reservation_json(
                    {"error": "受付番号またはメールアドレスが一致しません。"},
                    404,
                )
            app.logger.exception("Apps Script rejected reservation cancellation")
            return lesson_reservation_json(
                {"error": "予約をキャンセルできませんでした。時間をおいて再度お試しください。"},
                502,
            )
        except (json.JSONDecodeError, OSError, ValueError, urllib_error.URLError):
            app.logger.exception("Failed to cancel lesson reservation")
            return lesson_reservation_json(
                {"error": "予約をキャンセルできませんでした。時間をおいて再度お試しください。"},
                502,
            )

    @app.get("/api/lesson-admin-health")
    def lesson_admin_health():
        error = require_editor()
        if error:
            response, status_code = error
            response.status_code = status_code
            return with_lesson_reservation_cors(
                response,
                methods="GET, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )

        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            return lesson_reservation_json(
                {"error": "Apps Scriptの接続設定が不足しています。"},
                503,
            )

        required_capabilities = {"list", "update", "delete", "cancel", "upsert_slot_status_range"}
        try:
            result = send_lesson_reservation(
                script_url,
                script_secret,
                {},
                action="health",
            )
            capabilities = set(result.get("capabilities", []))
            if (
                not required_capabilities.issubset(capabilities)
                or result.get("version") != LESSON_APPS_SCRIPT_VERSION
            ):
                raise LessonReservationDeliveryError("OUTDATED_DEPLOYMENT")
            return lesson_reservation_json(
                {
                    "ready": True,
                    "version": result.get("version", ""),
                },
                200,
            )
        except (LessonReservationDeliveryError, json.JSONDecodeError, OSError, urllib_error.URLError, ValueError):
            app.logger.exception("Apps Script admin deployment is unavailable or outdated")
            return lesson_reservation_json(
                {
                    "error": "Apps Scriptが古いデプロイです。Code.gsを新しいバージョンで再デプロイし、RenderのGOOGLE_APPS_SCRIPT_URLを最新の/exec URLへ更新してください。"
                },
                503,
            )

    @app.route("/api/lesson-slot-statuses", methods=["GET", "OPTIONS"])
    def list_lesson_slot_statuses():
        if request.method == "OPTIONS":
            return with_lesson_reservation_cors(app.response_class(status=204), methods="GET, OPTIONS")

        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            return lesson_reservation_json(
                {"error": "現在、空き状況を確認できません。"},
                503,
            )

        from_date = str(request.args.get("from", "")).strip()
        to_date = str(request.args.get("to", "")).strip()
        payload = {}
        if from_date:
            payload["from"] = from_date
        if to_date:
            payload["to"] = to_date

        try:
            result = send_lesson_reservation(
                script_url,
                script_secret,
                payload,
                action="get_slot_statuses",
            )
            return lesson_reservation_json(
                {
                    "slots": normalize_slot_statuses(result.get("slots", [])),
                    "confirmed_counts": result.get("confirmedCounts", {}),
                },
                200,
            )
        except (LessonReservationDeliveryError, json.JSONDecodeError, OSError, urllib_error.URLError, ValueError):
            app.logger.exception("Failed to get slot statuses")
            return lesson_reservation_json(
                {"error": "現在、空き状況を確認できません。"},
                503,
            )

    @app.route("/api/lesson-slot-statuses/admin", methods=["POST", "OPTIONS"])
    def manage_lesson_slot_statuses():
        if request.method == "OPTIONS":
            return with_lesson_reservation_cors(
                app.response_class(status=204),
                methods="POST, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )

        error = require_editor()
        if error:
            response, status_code = error
            response.status_code = status_code
            return with_lesson_reservation_cors(
                response,
                methods="POST, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        try:
            values = validate_slot_status_request(request.get_json(silent=True))
        except ValueError as exc:
            response = jsonify({"error": str(exc)})
            response.status_code = 400
            return with_lesson_reservation_cors(
                response,
                methods="POST, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )

        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            return lesson_reservation_json(
                {"error": "現在、予約枠を更新できません。"},
                503,
            )
        try:
            result = send_lesson_reservation(
                script_url,
                script_secret,
                values,
                action="upsert_slot_status_range",
            )
            response = jsonify({"saved": True, "updated_count": parse_updated_count(result)})
            return with_lesson_reservation_cors(
                response,
                methods="POST, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        except (LessonReservationDeliveryError, json.JSONDecodeError, OSError, urllib_error.URLError, ValueError):
            app.logger.exception("Failed to update lesson slot statuses")
            return lesson_reservation_json(
                {"error": "現在、予約枠を更新できません。"},
                503,
            )


    @app.route("/api/lesson-reservations/<reservation_id>", methods=["PUT", "DELETE", "OPTIONS"])
    def manage_lesson_reservation(reservation_id):
        if request.method == "OPTIONS":
            return with_lesson_reservation_cors(
                app.response_class(status=204),
                methods="PUT, DELETE, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )

        error = require_editor()
        if error:
            response, status_code = error
            response.status_code = status_code
            return with_lesson_reservation_cors(
                response,
                methods="PUT, DELETE, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        try:
            valid_reservation_id = validate_reservation_id(reservation_id)
        except ValueError as exc:
            response = jsonify({"error": str(exc)})
            response.status_code = 400
            return with_lesson_reservation_cors(
                response,
                methods="PUT, DELETE, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )

        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            missing_settings = []
            if not script_url:
                missing_settings.append("GOOGLE_APPS_SCRIPT_URL")
            if not script_secret:
                missing_settings.append("GOOGLE_APPS_SCRIPT_SECRET")
            response = jsonify(
                {
                    "error": "現在、予約管理を利用できません。",
                    "missing_settings": missing_settings,
                }
            )
            response.status_code = 503
            return with_lesson_reservation_cors(
                response,
                methods="PUT, DELETE, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )

        try:
            if request.method == "DELETE":
                result = send_lesson_reservation(
                    script_url,
                    script_secret,
                    {"reservation_id": valid_reservation_id},
                    action="delete",
                )
                response = jsonify({"deleted": True, "reservation_id": result.get("reservationId", "")})
                return with_lesson_reservation_cors(
                    response,
                    methods="PUT, DELETE, OPTIONS",
                    headers="Content-Type, X-Editor-Password",
                )

            values = validate_lesson_reservation_update(request.get_json(silent=True))
            result = send_lesson_reservation(
                script_url,
                script_secret,
                {"reservation_id": valid_reservation_id, **values},
                action="update",
            )
            if result.get("conflict"):
                response = jsonify(
                    {
                        "saved": False,
                        "conflict": True,
                        "reservation_id": result.get("reservationId", ""),
                        "status": result.get("status", "調整中"),
                    }
                )
                response.status_code = 409
                return with_lesson_reservation_cors(
                    response,
                    methods="PUT, DELETE, OPTIONS",
                    headers="Content-Type, X-Editor-Password",
                )
            response = jsonify(
                {
                    "saved": True,
                    "reservation_id": result.get("reservationId", ""),
                    "status": result.get("status", values.get("status", "")),
                    "updated_fields": result.get("updatedFields", []),
                    "confirmation_email_sent": result.get("confirmationEmailSent"),
                }
            )
            return with_lesson_reservation_cors(
                response,
                methods="PUT, DELETE, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        except ValueError as exc:
            response = jsonify({"error": str(exc)})
            response.status_code = 400
            return with_lesson_reservation_cors(
                response,
                methods="PUT, DELETE, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        except LessonReservationDeliveryError as exc:
            if str(exc) == "NOT_FOUND":
                response = jsonify({"error": "対象の予約が見つかりません。"})
                response.status_code = 404
                return with_lesson_reservation_cors(
                    response,
                    methods="PUT, DELETE, OPTIONS",
                    headers="Content-Type, X-Editor-Password",
                )
            app.logger.exception("Apps Script rejected reservation management action")
            response = jsonify(
                {
                    "error": "予約管理に失敗しました。時間をおいて再度お試しください。",
                    "delivery_error": str(exc),
                }
            )
            response.status_code = 502
            return with_lesson_reservation_cors(
                response,
                methods="PUT, DELETE, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        except json.JSONDecodeError:
            app.logger.exception("Apps Script returned an invalid response")
            response = jsonify(
                {
                    "error": "予約管理に失敗しました。時間をおいて再度お試しください。",
                    "delivery_error": "INVALID_APPS_SCRIPT_RESPONSE",
                }
            )
            response.status_code = 502
            return with_lesson_reservation_cors(
                response,
                methods="PUT, DELETE, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        except (OSError, urllib_error.URLError):
            app.logger.exception("Failed to manage lesson reservation")
            response = jsonify({"error": "予約管理に失敗しました。時間をおいて再度お試しください。"})
            response.status_code = 502
            return with_lesson_reservation_cors(
                response,
                methods="PUT, DELETE, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )

    @app.route("/api/editor", methods=["GET", "OPTIONS"])
    def editor_status():
        if request.method == "OPTIONS":
            return with_lesson_reservation_cors(
                app.response_class(status=204),
                methods="GET, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        error = require_editor()
        if error:
            response, status_code = error
            response.status_code = status_code
            return with_lesson_reservation_cors(
                response,
                methods="GET, OPTIONS",
                headers="Content-Type, X-Editor-Password",
            )
        return with_lesson_reservation_cors(
            jsonify({"authenticated": True}),
            methods="GET, OPTIONS",
            headers="Content-Type, X-Editor-Password",
        )

    @app.post("/api/updates")
    def create_update():
        error = require_editor()
        if error:
            return error
        try:
            values = validate_update(request.get_json(silent=True))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if configured_database_url:
            create_database_update(configured_database_url, values)
        else:
            update_file(lambda lines: lines.append(format_update(values)), updates_file)
        return jsonify({"saved": True}), 201

    @app.put("/api/updates/<int:update_index>")
    def edit_update(update_index):
        error = require_editor()
        if error:
            return error
        try:
            values = validate_update(request.get_json(silent=True))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        def replace_line(lines):
            if update_index >= len(lines) or not lines[update_index].strip().startswith(tuple("0123456789")):
                raise IndexError
            lines[update_index] = format_update(values)

        if configured_database_url:
            if not edit_database_update(configured_database_url, update_index, values):
                return jsonify({"error": "対象の情報が見つかりません。"}), 404
        else:
            try:
                update_file(replace_line, updates_file)
            except IndexError:
                return jsonify({"error": "対象の情報が見つかりません。"}), 404
        return jsonify({"saved": True})

    @app.delete("/api/updates/<int:update_index>")
    def delete_update(update_index):
        error = require_editor()
        if error:
            return error

        def remove_line(lines):
            if update_index >= len(lines) or not lines[update_index].strip().startswith(tuple("0123456789")):
                raise IndexError
            lines.pop(update_index)

        if configured_database_url:
            if not delete_database_update(configured_database_url, update_index):
                return jsonify({"error": "対象の情報が見つかりません。"}), 404
        else:
            try:
                update_file(remove_line, updates_file)
            except IndexError:
                return jsonify({"error": "対象の情報が見つかりません。"}), 404
        return jsonify({"deleted": True})

    @app.get("/<any(data,pdf,video):directory>/<path:filename>")
    def public_file(directory, filename):
        return send_from_directory(BASE_DIR / directory, filename)

    @app.get("/<any(pdf,video):directory>/")
    def public_index(directory):
        return send_from_directory(BASE_DIR / directory, "index.html")

    @app.get("/music%20App/<path:filename>")
    @app.get("/music App/<path:filename>")
    def music_app_file(filename):
        return send_from_directory(BASE_DIR / "music App", filename)

    @app.get("/music%20App/")
    @app.get("/music App/")
    def music_app_index():
        return send_from_directory(BASE_DIR / "music App", "index.html")

    return app


app = create_app()