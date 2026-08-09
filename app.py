import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, send_from_directory


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


def create_app():
    app = Flask(__name__, template_folder=".", static_folder=None)

    @app.get("/")
    def index():
        return render_template("index.html", updates=load_updates())

    @app.get("/lesson/")
    def lesson():
        return render_template("lesson/index.html")

    @app.get("/api/updates")
    def updates_api():
        public_updates = [
            {key: value for key, value in item.items() if key not in {"index", "sort_date"}}
            for item in load_updates()
        ]
        response = jsonify(public_updates)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/<any(data,pdf,video):directory>/<path:filename>")
    def public_file(directory, filename):
        return send_from_directory(BASE_DIR / directory, filename)

    return app


app = create_app()