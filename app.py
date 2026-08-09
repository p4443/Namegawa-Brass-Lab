import fcntl
import hmac
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

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