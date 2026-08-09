import fcntl
import hmac
import json
import os
import re
import tempfile
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request, send_from_directory


BASE_DIR = Path(__file__).resolve().parent
UPDATES_FILE = BASE_DIR / "data" / "updates.txt"
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
LESSON_TYPES = {"体験レッスン", "小学生", "中学生", "高校生以上", "グループ・部活動指導"}
CONSULTATION_TIME = "要相談"


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


WEEKDAY_RESERVATION_TIMES = {
    0: time_range("06:45", "09:00") | time_range("20:30", "22:00"),
    1: time_range("06:45", "09:00") | time_range("20:30", "22:00"),
    2: time_range("06:45", "09:00") | time_range("20:30", "22:00"),
    3: time_range("06:45", "12:00"),
    4: time_range("15:00", "17:00") | {CONSULTATION_TIME},
    5: set(),
    6: {CONSULTATION_TIME},
}


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
    if not values["date"] or parse_date(values["date"]) == datetime.min:
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
    try:
        preferred_date = datetime.strptime(values["preferred_date"], "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("希望日時を正しく入力してください。") from exc
    first_available_date = current_japan_date() + timedelta(days=1)
    last_available_date = add_one_month(current_japan_date())
    if not first_available_date <= preferred_date <= last_available_date:
        raise ValueError("予約日は明日から1か月先までの範囲で選択してください。")
    available_times = WEEKDAY_RESERVATION_TIMES[preferred_date.weekday()]
    if not available_times:
        raise ValueError("土曜日は予約を受け付けていません。")
    if values["preferred_time"] not in available_times:
        raise ValueError("選択した曜日の予約可能時間を指定してください。")
    if len(values["message"]) > 500:
        raise ValueError("ご要望は500文字以内で入力してください。")
    return values


def send_lesson_reservation(script_url, secret, values):
    payload = json.dumps({**values, "secret": secret}, ensure_ascii=False).encode("utf-8")
    script_request = urllib_request.Request(
        script_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib_request.urlopen(script_request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise ValueError("予約を受け付けられませんでした。")
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


def create_app(updates_file=UPDATES_FILE, database_url=None):
    app = Flask(__name__, template_folder=".", static_folder=None)
    configured_database_url = database_url or os.environ.get("DATABASE_URL", "")
    if configured_database_url:
        initialize_database(configured_database_url, updates_file)

    def get_updates():
        if configured_database_url:
            return load_database_updates(configured_database_url)
        return load_updates(updates_file)

    def require_editor():
        configured_password = os.environ.get("EDITOR_PASSWORD", "")
        supplied_password = request.headers.get("X-Editor-Password", "")
        if not configured_password:
            return jsonify({"error": "編集用パスワードが設定されていません。"}), 503
        if not hmac.compare_digest(
            supplied_password.encode("utf-8"), configured_password.encode("utf-8")
        ):
            return jsonify({"error": "パスワードが違います。"}), 401
        return None

    @app.get("/")
    def index():
        return render_template("index.html", updates=get_updates())

    @app.get("/lesson/")
    def lesson():
        return render_template("lesson/index.html")

    @app.get("/api/updates")
    def updates_api():
        response = jsonify([public_update(item) for item in get_updates()])
        response.headers["Cache-Control"] = "no-store"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    @app.post("/api/lesson-reservations")
    def create_lesson_reservation():
        if request.get_json(silent=True) and request.get_json(silent=True).get("website"):
            return jsonify({"saved": True}), 201
        try:
            values = validate_lesson_reservation(request.get_json(silent=True))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            missing_settings = []
            if not script_url:
                missing_settings.append("GOOGLE_APPS_SCRIPT_URL")
            if not script_secret:
                missing_settings.append("GOOGLE_APPS_SCRIPT_SECRET")
            return jsonify(
                {
                    "error": "現在、Web予約を利用できません。メールまたは電話でお問い合わせください。",
                    "missing_settings": missing_settings,
                }
            ), 503
        try:
            result = send_lesson_reservation(script_url, script_secret, values)
        except (OSError, ValueError, json.JSONDecodeError, urllib_error.URLError):
            app.logger.exception("Failed to send lesson reservation")
            return jsonify({"error": "予約の送信に失敗しました。時間をおいて再度お試しください。"}), 502
        return jsonify({"saved": True, "reservation_id": result.get("reservationId", "")}), 201

    @app.get("/api/editor")
    def editor_status():
        error = require_editor()
        if error:
            return error
        return jsonify({"authenticated": True})

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

    return app


app = create_app()