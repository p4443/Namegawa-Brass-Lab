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
import unicodedata
import uuid
from base64 import b64decode
from binascii import Error as Base64Error
from calendar import monthrange
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlencode, urljoin, urlparse
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
SERVER_CONTRACTS_DIR = BASE_DIR / "data" / "contracts"
SERVER_PDF_DIR = BASE_DIR / "pdf"
PDF_UPLOAD_DIR = Path(
    os.environ.get("PDF_UPLOAD_DIR", BASE_DIR / "data" / "event-pdfs")
).expanduser()
CONTRACTS_DIR = Path(
    os.environ.get(
        "CONTRACTS_DIR",
        Path.home() / "Documents" / "なめがわブラス・ラボ" / "契約書管理",
    )
).expanduser()
PRODUCT_FILE = BASE_DIR / "private" / "products" / "trumpet-metronome.zip"
PRODUCT_ID = "trumpet-metronome"
PRODUCT_NAME = "トランペット練習メトロノーム オフライン版"
PRODUCT_PRICE_YEN = 500
PRODUCT_REQUIRED_FILES = {"index.html", "README.txt"}
FLOW_HARMONY_PRODUCT_FILE = BASE_DIR / "private" / "products" / "trumpet-transpose-lab.zip"
FLOW_HARMONY_PRODUCT_ID = "trumpet-transpose-lab"
FLOW_HARMONY_LEGACY_PRODUCT_ID = "flow-harmony"
FLOW_HARMONY_PRODUCT_NAME = "Trumpet Transpose Lab オフライン版"
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
INVOICE_REGISTRATION_NUMBER_PATTERN = re.compile(r"^T\d{13}$")
DEFAULT_INVOICE_REGISTRATION_NUMBER = "T2810320517878"
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
    "高校生以上・大人",
    "グループ・部活動指導",
}
LESSON_DURATION_MINUTES = {
    "体験レッスン": 30,
    "無料体験レッスン": 30,
    "小学生": 30,
    "中学生": 45,
    "高校生以上": 60,
    "高校生以上・大人": 60,
    "グループ・部活動指導": None,
}
CONSULTATION_TIME = "要相談"
RESERVATION_STATUS_VALUES = {"確認中", "確定", "キャンセル"}
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
EVENT_PDF_MAX_BYTES = 15 * 1024 * 1024
EVENT_PDF_MANIFEST = "documents.json"
EVENT_PDF_TITLES = {
    "dayservice.pdf": "音でつながる！懐かしのメロディと呼吸のストレッチ",
    "gakudou.pdf": "学童向け トランペット・ミニコンサート＆ワークショップ",
    "hoikuen.pdf": "見て・聴いて・あそんで楽しむ！トランペット・ミニコンサート＆リズム体験ワークショップ",
    "shukatsu.pdf": "カフェで紡ぐ「思い出のメロディ」ライブ【スタンダードプラン】",
    "cafe-live-plan-1.pdf": "音のパスポート ～トランペットで巡る 世界の街角と名曲たち～",
    "cafe-live-plan-2.pdf": "カフェ・ド・トランペット ～午後の紅茶と、心ひろがる名曲の旅～",
    "cafe-live-plan-3.pdf": "ノスタルジック・ノーツ ～トランペットの音色でたどる 昭和・ジャズ・名画の旅～",
}
CONTRACT_TYPES = {
    "master": {"department": "基本契約", "directory": "master", "keys": set()},
    "typeA": {
        "department": "音楽指導・支援",
        "directory": "music-support",
        "keys": {"work", "amount", "term", "special_terms"},
    },
    "estimateA": {
        "department": "音楽指導・支援",
        "directory": "music-support",
        "keys": {
            "subject",
            "implementation_period",
            "validity_days",
            "invoice_registration_number",
            "estimate_items",
        },
    },
    "typeB": {
        "department": "楽器輸送",
        "directory": "transport",
        "keys": {"transport_name", "estimate_reference_id", "estimate_date", "cargo", "value", "route", "amount", "cargo_document_url", "route_document_url", "fee_document_url", "special_terms"},
    },
    "estimateB": {
        "department": "楽器輸送",
        "directory": "transport",
        "keys": {
            "transport_name",
            "validity",
            "workflow_status",
            "transport_provider_mode",
            "vehicle_class",
            "pricing_basis",
            "cargo_document_url",
            "route_document_url",
            "fee_document_url",
            "transport_sheet_signature",
            "waiting_fee",
            "ancillary_fee",
            "detour_expenses",
            "cargo_restrictions_agreed",
            "cargo_contact_email",
            "external_vehicle_budget",
            "route_origin",
            "route_destination",
            "route_trip_type",
            "route_one_way_distance_km",
            "route_distance_km",
            "route_provider",
            "route_measurement_signature",
            "total_hours",
            "instrument_price_master",
            "freight_rate_master",
            "freight_operation",
            "cargo_items",
            "estimate_items",
        },
    },
    "estimateC": {
        "department": "WEB・アプリ",
        "directory": "web-app",
        "keys": {
            "project_name",
            "operating_system",
            "runtime_environment",
            "delivery_date",
            "estimate_items",
        },
    },
    "typeC": {
        "department": "WEB・アプリ",
        "directory": "web-app",
        "keys": {"deliverable", "amount", "deadline", "special_terms"},
    },
}

INSTRUMENT_PRICE_SOURCE_DOMAINS = {
    "yamaha.com": "ヤマハ",
    "buffetcrampon.com": "ビュッフェ・クランポン",
    "pearldrum.com": "パール楽器",
    "korogi.co.jp": "こおろぎ社",
    "suzuki-music.co.jp": "鈴木楽器製作所",
    "nonaka.com": "野中貿易",
    "global-inst.co.jp": "グローバル",
}
INSTRUMENT_PRICE_PAGE_MAX_BYTES = 2 * 1024 * 1024
INSTRUMENT_PRICE_SITEMAP_MAX_BYTES = 8 * 1024 * 1024
INSTRUMENT_MODEL_CODE_PATTERN = re.compile(
    r"[A-Za-z](?=[A-Za-z0-9._/-]*\d)[A-Za-z0-9._/-]{2,}"
)
GOOGLE_ROUTES_API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
NOMINATIM_SEARCH_API_URL = "https://nominatim.openstreetmap.org/search"
OSRM_ROUTE_API_URL = "https://router.project-osrm.org/route/v1/driving"


def instrument_price_source(source_url):
    parsed = urlparse(str(source_url).strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("公式メーカーのHTTPS URLを入力してください。")
    for domain, source_name in INSTRUMENT_PRICE_SOURCE_DOMAINS.items():
        if hostname == domain or hostname.endswith(f".{domain}"):
            return source_name
    raise ValueError("対応している公式メーカー・国内代理店のURLを入力してください。")


class OfficialInstrumentPriceRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url = urljoin(req.full_url, newurl)
        instrument_price_source(redirect_url)
        return super().redirect_request(
            req, fp, code, msg, headers, redirect_url
        )


class InstrumentPricePageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.current_link = None
        self.current_link_parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored_depth += 1
        elif tag == "a" and not self.ignored_depth:
            self.current_link = dict(attrs).get("href", "")
            self.current_link_parts = []

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag == "a" and self.current_link is not None:
            self.links.append((self.current_link, " ".join(self.current_link_parts)))
            self.current_link = None
            self.current_link_parts = []

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)
            if self.current_link is not None:
                self.current_link_parts.append(data)

    def text(self):
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def decode_instrument_page(body, charset):
    normalized_charset = str(charset or "utf-8").strip().lower()
    normalized_charset = {
        "windows-31j": "cp932",
        "ms932": "cp932",
        "x-sjis": "cp932",
    }.get(normalized_charset, normalized_charset)
    try:
        return body.decode(normalized_charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def instrument_candidate_priority(candidate):
    return {"exact": 0, "normalized": 1, "partial": 2}.get(candidate.get("match_type"), 3)


def instrument_url_match_priority(url, maker_model):
    model_codes = INSTRUMENT_MODEL_CODE_PATTERN.findall(maker_model)
    exact_model = max(model_codes, key=len) if model_codes else maker_model
    exact_pattern = re.compile(
        rf"(?<![a-z0-9]){re.escape(exact_model.casefold())}(?![a-z0-9])"
    )
    return 0 if exact_pattern.search(url.casefold()) else 1


def has_exact_instrument_url(urls, maker_model):
    return any(instrument_url_match_priority(url, maker_model) == 0 for url in urls)


def discover_instrument_model_urls(source_url, maker_model, links):
    source_name = instrument_price_source(source_url)
    model_codes = INSTRUMENT_MODEL_CODE_PATTERN.findall(maker_model)
    exact_model = max(model_codes, key=len) if model_codes else maker_model
    normalized_model = re.sub(r"[^a-z0-9]", "", exact_model.casefold())
    if len(normalized_model) < 3 or not any(character.isdigit() for character in normalized_model):
        return []

    product_urls = []
    for href, label in links:
        location = urljoin(source_url, str(href).strip())
        try:
            if instrument_price_source(location) != source_name:
                continue
        except ValueError:
            continue
        searchable_link = re.sub(
            r"[^a-z0-9]", "", f"{location} {label}".casefold()
        )
        link_model_codes = INSTRUMENT_MODEL_CODE_PATTERN.findall(
            unicodedata.normalize("NFKC", f"{location} {label}")
        )
        normalized_link_models = [
            re.sub(r"[^a-z0-9]", "", model.casefold())
            for model in link_model_codes
        ]
        partial_model_match = any(
            len(min((normalized_model, link_model), key=len)) >= 4
            and (normalized_model in link_model or link_model in normalized_model)
            for link_model in normalized_link_models
        )
        if (normalized_model in searchable_link or partial_model_match) and location not in product_urls:
            product_urls.append(location)
    product_urls.sort(key=lambda url: instrument_url_match_priority(url, maker_model))
    return product_urls[:20]


def discover_instrument_catalog_urls(source_url, maker_model, opener, initial_links):
    parsed_source = urlparse(source_url)
    source_name = instrument_price_source(source_url)
    catalog_path = parsed_source.path.rstrip("/") + "/"
    pending = [(urljoin(source_url, href), 1) for href, _label in initial_links]
    visited = {source_url}
    product_urls = discover_instrument_model_urls(source_url, maker_model, initial_links)

    while pending and len(visited) < 120 and not product_urls:
        current_url, depth = pending.pop(0)
        if current_url in visited or depth > 3:
            continue
        parsed_current = urlparse(current_url)
        try:
            if instrument_price_source(current_url) != source_name:
                continue
        except ValueError:
            continue
        if not parsed_current.path.startswith(catalog_path):
            continue
        if parsed_current.path.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".zip")):
            continue
        visited.add(current_url)
        page_request = urllib_request.Request(
            current_url,
            headers={"User-Agent": "NamegawaBrassLab-PriceLookup/1.0"},
        )
        try:
            with opener.open(page_request, timeout=8) as response:
                final_url = response.geturl()
                instrument_price_source(final_url)
                if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                    continue
                body = response.read(INSTRUMENT_PRICE_PAGE_MAX_BYTES + 1)
                if len(body) > INSTRUMENT_PRICE_PAGE_MAX_BYTES:
                    continue
                charset = response.headers.get_content_charset() or "utf-8"
        except (ValueError, OSError, urllib_error.URLError, UnicodeError):
            continue
        parser = InstrumentPricePageParser()
        parser.feed(decode_instrument_page(body, charset))
        discovered = discover_instrument_model_urls(final_url, maker_model, parser.links)
        for product_url in discovered:
            if product_url not in product_urls:
                product_urls.append(product_url)
        if depth < 3:
            pending.extend(
                (urljoin(final_url, href), depth + 1)
                for href, _label in parser.links
            )
    product_urls.sort(key=lambda url: instrument_url_match_priority(url, maker_model))
    return product_urls[:20]


def fetch_instrument_price_candidates(source_url, maker_model, opener=None, allow_discovery=True):
    source_url = str(source_url).strip()
    maker_model = unicodedata.normalize("NFKC", str(maker_model).strip())
    source_name = instrument_price_source(source_url)
    if not 2 <= len(maker_model) <= 120:
        raise ValueError("照会するメーカー・型番を入力してください。")

    page_opener = opener or urllib_request.build_opener(
        OfficialInstrumentPriceRedirectHandler()
    )
    page_request = urllib_request.Request(
        source_url,
        headers={"User-Agent": "NamegawaBrassLab-PriceLookup/1.0"},
    )
    with page_opener.open(page_request, timeout=8) as response:
        final_url = response.geturl()
        instrument_price_source(final_url)
        content_type = response.headers.get_content_type()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("現在は公式製品ページのHTML価格表示に対応しています。")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > INSTRUMENT_PRICE_PAGE_MAX_BYTES:
            raise ValueError("価格ページのサイズが上限を超えています。")
        body = response.read(INSTRUMENT_PRICE_PAGE_MAX_BYTES + 1)
        if len(body) > INSTRUMENT_PRICE_PAGE_MAX_BYTES:
            raise ValueError("価格ページのサイズが上限を超えています。")
        charset = response.headers.get_content_charset() or "utf-8"

    parser = InstrumentPricePageParser()
    parser.feed(decode_instrument_page(body, charset))
    page_text = unicodedata.normalize("NFKC", parser.text())
    search_text = page_text.casefold()
    model_codes = INSTRUMENT_MODEL_CODE_PATTERN.findall(maker_model)
    exact_model = max(model_codes, key=len) if model_codes else maker_model
    exact_model_folded = exact_model.casefold()
    model_pattern = re.compile(
        rf"(?<![a-z0-9]){re.escape(exact_model_folded)}(?![a-z0-9])"
    )
    model_matches = [
        (match.start(), exact_model, "exact")
        for match in model_pattern.finditer(search_text)
    ][:20]
    normalized_model = re.sub(r"[^a-z0-9]", "", exact_model_folded)
    matched_positions = {position for position, _model, _match_type in model_matches}
    for match in INSTRUMENT_MODEL_CODE_PATTERN.finditer(page_text):
        if match.start() in matched_positions:
            continue
        catalog_model = match.group(0)
        normalized_catalog_model = re.sub(
            r"[^a-z0-9]", "", catalog_model.casefold()
        )
        shorter_model = min(
            (normalized_model, normalized_catalog_model), key=len
        )
        if len(shorter_model) >= 4 and any(character.isdigit() for character in shorter_model) and (
            normalized_model in normalized_catalog_model
            or normalized_catalog_model in normalized_model
        ):
            match_type = (
                "normalized"
                if normalized_model == normalized_catalog_model
                else "partial"
            )
            model_matches.append((match.start(), catalog_model, match_type))
            matched_positions.add(match.start())
            if len(model_matches) >= 20:
                break
    if not model_matches and not allow_discovery:
        raise ValueError("公式ページ内に一致または一部一致する型番が見つかりませんでした。")

    price_pattern = re.compile(
        r"(?:[¥￥]\s*([0-9][0-9,]{2,})|([0-9][0-9,]{2,})\s*円)"
    )
    candidates = []
    seen_prices = set()
    for candidate_match_type in ("exact", "normalized", "partial"):
        for position, matched_model, match_type in model_matches:
            if match_type != candidate_match_type:
                continue
            snippet_start = max(0, position - 500)
            snippet_end = min(len(page_text), position + len(matched_model) + 1200)
            snippet = page_text[snippet_start:snippet_end]
            model_offset = position - snippet_start
            for match in price_pattern.finditer(snippet):
                if match.start() < model_offset:
                    continue
                between_end = max(model_offset + len(matched_model), match.end())
                intervening_models = INSTRUMENT_MODEL_CODE_PATTERN.findall(
                    snippet[model_offset:between_end]
                )
                if any(model.casefold() != matched_model.casefold() for model in intervening_models):
                    continue
                price = int((match.group(1) or match.group(2)).replace(",", ""))
                if not 1000 <= price <= 100000000 or price in seen_prices:
                    continue
                seen_prices.add(price)
                context_start = max(0, match.start() - 70)
                context_end = min(len(snippet), match.end() + 70)
                context = snippet[context_start:context_end].strip()
                normalized_context = context.casefold()
                if "税込" in normalized_context or "消費税込" in normalized_context:
                    tax_status = "tax_included"
                elif "税別" in normalized_context or "税抜" in normalized_context or "本体価格" in normalized_context:
                    tax_status = "tax_excluded"
                else:
                    tax_status = "unknown"
                candidates.append(
                    {
                        "price": price,
                        "context": context,
                        "tax_status": tax_status,
                        "matched_model": matched_model,
                        "match_type": match_type,
                    }
                )
                if len(candidates) >= 10:
                    break
            if len(candidates) >= 10:
                break
    if not candidates:
        if allow_discovery:
            linked_results = []
            for product_url in discover_instrument_catalog_urls(
                final_url, maker_model, page_opener, parser.links
            ):
                try:
                    linked_results.append(fetch_instrument_price_candidates(
                        product_url, maker_model, page_opener, False
                    ))
                except (ValueError, OSError, urllib_error.URLError, UnicodeError):
                    continue
            if linked_results:
                linked_candidates = [
                    {**candidate, "source_url": result["source_url"]}
                    for result in linked_results
                    for candidate in result["candidates"]
                ]
                linked_candidates.sort(key=instrument_candidate_priority)
                adopted_source_url = linked_candidates[0]["source_url"]
                adopted_result = next(
                    result for result in linked_results
                    if result["source_url"] == adopted_source_url
                )
                adopted_result["candidates"] = linked_candidates[:10]
                return adopted_result
        if not model_matches:
            raise ValueError("公式ページ内に一致または一部一致する型番が見つかりませんでした。")
        raise ValueError("型番付近に円価格が見つかりませんでした。")
    candidates.sort(key=lambda candidate: (
        instrument_candidate_priority(candidate),
        0 if candidate["tax_status"] == "tax_included" else 1,
    ))
    return {
        "source_name": source_name,
        "source_url": final_url,
        "checked_at": datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat(),
        "maker_model": maker_model,
        "exact_model": exact_model,
        "match_type": model_matches[0][2],
        "candidates": candidates,
    }


def fetch_instrument_catalog_prices(source_urls, maker_model, fetcher=None):
    if not isinstance(source_urls, list) or not 1 <= len(source_urls) <= 7:
        raise ValueError("公式カタログURLを1件以上7件以内で入力してください。")
    urls = list(dict.fromkeys(str(url).strip() for url in source_urls if str(url).strip()))
    if not urls:
        raise ValueError("公式カタログURLを入力してください。")
    for source_url in urls:
        instrument_price_source(source_url)

    price_fetcher = fetcher or fetch_instrument_price_candidates
    results = []
    failures = []
    for source_url in urls:
        try:
            results.append(price_fetcher(source_url, maker_model))
        except (ValueError, OSError, urllib_error.URLError, UnicodeError) as exc:
            failure_message = str(exc)
            failure_reason = (
                "model_not_found"
                if "型番が見つかりません" in failure_message
                else "price_not_found"
                if "円価格が見つかりません" in failure_message
                else "lookup_failed"
            )
            failures.append({
                "source_url": source_url,
                "error": failure_message,
                "reason": failure_reason,
            })
    if not results:
        failure_reasons = {failure["reason"] for failure in failures}
        return {
            "checked_at": datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat(),
            "catalog_year": datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()[:4],
            "maker_model": str(maker_model).strip(),
            "source_urls": urls,
            "candidates": [],
            "recommended_price": None,
            "recommended_source_name": "",
            "recommended_source_url": "",
            "manual_entry_required": True,
            "failure_reason": failure_reasons.pop() if len(failure_reasons) == 1 else "lookup_failed",
            "failures": failures,
        }

    candidates = []
    for result in results:
        candidates.extend(
            {
                **candidate,
                "source_name": result["source_name"],
                "source_url": candidate.get("source_url", result["source_url"]),
            }
            for candidate in result["candidates"]
        )
    candidates.sort(key=instrument_candidate_priority)
    recommended = candidates[0]
    checked_at = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    return {
        "checked_at": checked_at,
        "catalog_year": checked_at[:4],
        "maker_model": str(maker_model).strip(),
        "source_urls": [result["source_url"] for result in results],
        "candidates": candidates,
        "recommended_price": recommended["price"],
        "recommended_source_name": recommended["source_name"],
        "recommended_source_url": recommended["source_url"],
        "failures": failures,
    }


def normalize_route_query(value: object) -> str:
    query = "" if value is None else str(value).strip()
    if not query or len(query) > 300:
        raise ValueError("出発地と目的地を300文字以内で入力してください。")
    return query


def compute_google_route(origin, destination, api_key, urlopen=None):
    origin = normalize_route_query(origin)
    destination = normalize_route_query(destination)
    api_key = str(api_key).strip()
    if not api_key:
        raise ValueError("Google Routes APIキーが設定されていません。")
    request_body = json.dumps(
        {
            "origin": {"address": origin},
            "destination": {"address": destination},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_UNAWARE",
            "computeAlternativeRoutes": False,
            "languageCode": "ja-JP",
            "units": "METRIC",
        }
    ).encode("utf-8")
    route_request = urllib_request.Request(
        GOOGLE_ROUTES_API_URL,
        data=request_body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "routes.distanceMeters,routes.duration",
        },
        method="POST",
    )
    request_opener = urlopen or urllib_request.urlopen
    with request_opener(route_request, timeout=8) as response:
        result = json.loads(response.read(256 * 1024).decode("utf-8"))
    routes = result.get("routes", []) if isinstance(result, dict) else []
    if not routes or not isinstance(routes[0], dict):
        raise ValueError("Google Mapsで自動車ルートを確認できませんでした。")
    distance_meters = routes[0].get("distanceMeters")
    duration_text = str(routes[0].get("duration", ""))
    if not isinstance(distance_meters, int) or distance_meters <= 0:
        raise ValueError("Google Mapsから走行距離を取得できませんでした。")
    duration_match = re.fullmatch(r"(\d+(?:\.\d+)?)s", duration_text)
    duration_seconds = float(duration_match.group(1)) if duration_match else 0
    return {
        "origin": origin,
        "destination": destination,
        "distance_km": round(distance_meters / 1000, 1),
        "duration_minutes": max(1, round(duration_seconds / 60)) if duration_seconds else 0,
        "maps_url": "https://www.google.com/maps/dir/?api=1&"
        + urlencode({"origin": origin, "destination": destination, "travelmode": "driving"}),
        "provider": "Google Maps",
    }


def resolve_japan_location(query, urlopen=None):
    query = normalize_route_query(query)
    search_url = NOMINATIM_SEARCH_API_URL + "?" + urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "countrycodes": "jp",
            "addressdetails": "1",
            "limit": "1",
        }
    )
    search_request = urllib_request.Request(
        search_url,
        headers={"User-Agent": "namegawa-brass-lab-contract-route/1.0"},
    )
    request_opener = urlopen or urllib_request.urlopen
    with request_opener(search_request, timeout=8) as response:
        result = json.loads(response.read(128 * 1024).decode("utf-8"))
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        raise ValueError(f"国内の場所を特定できませんでした。住所・郵便番号・施設名・店舗名などを確認してください：{query}")
    candidate = result[0]
    try:
        latitude = float(candidate["lat"])
        longitude = float(candidate["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"国内の場所の位置を特定できませんでした：{query}") from exc
    display_name = str(candidate.get("display_name", "")).strip()
    if not display_name:
        raise ValueError(f"国内の場所の住所を特定できませんでした：{query}")
    return {"query": query, "address": display_name, "latitude": latitude, "longitude": longitude}


def compute_public_route(origin, destination, urlopen=None):
    request_opener = urlopen or urllib_request.urlopen
    resolved_origin = resolve_japan_location(origin, request_opener)
    resolved_destination = resolve_japan_location(destination, request_opener)
    coordinates = (
        f'{resolved_origin["longitude"]},{resolved_origin["latitude"]};'
        f'{resolved_destination["longitude"]},{resolved_destination["latitude"]}'
    )
    route_url = f"{OSRM_ROUTE_API_URL}/{coordinates}?" + urlencode(
        {"overview": "false", "steps": "false"}
    )
    route_request = urllib_request.Request(
        route_url,
        headers={"User-Agent": "namegawa-brass-lab-contract-route/1.0"},
    )
    with request_opener(route_request, timeout=8) as response:
        result = json.loads(response.read(256 * 1024).decode("utf-8"))
    routes = result.get("routes", []) if isinstance(result, dict) else []
    if not routes or not isinstance(routes[0], dict):
        raise ValueError("指定地点間の自動車ルートを確認できませんでした。")
    distance_meters = routes[0].get("distance")
    duration_seconds = routes[0].get("duration")
    if not isinstance(distance_meters, (int, float)) or distance_meters <= 0:
        raise ValueError("指定地点間の走行距離を取得できませんでした。")
    return {
        "origin": str(origin).strip(),
        "destination": str(destination).strip(),
        "resolved_origin": resolved_origin["address"],
        "resolved_destination": resolved_destination["address"],
        "distance_km": round(distance_meters / 1000, 1),
        "duration_minutes": max(1, round(float(duration_seconds) / 60))
        if isinstance(duration_seconds, (int, float)) and duration_seconds > 0
        else 0,
        "maps_url": "https://www.google.com/maps/dir/?api=1&"
        + urlencode(
            {
                "origin": resolved_origin["address"],
                "destination": resolved_destination["address"],
                "travelmode": "driving",
            }
        ),
        "provider": "OpenStreetMap / OSRM",
    }
LESSON_APPS_SCRIPT_VERSION = "2026-08-31-google-routes-required-v30"


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


def load_store_settings(path=STORE_FILE, product_id=PRODUCT_ID, default_enabled=False):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"enabled": bool(default_enabled)}
    products = payload.get("products", {})
    if isinstance(products, dict) and isinstance(products.get(product_id), dict):
        return {"enabled": products[product_id].get("enabled") is True}
    if product_id == PRODUCT_ID:
        return {"enabled": payload.get("enabled") is True}
    return {"enabled": bool(default_enabled)}


def save_store_settings(enabled, path=STORE_FILE, product_id=PRODUCT_ID):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                payload = {}
            products = payload.get("products")
            if not isinstance(products, dict):
                products = {}
            if "enabled" in payload and PRODUCT_ID not in products:
                products[PRODUCT_ID] = {"enabled": payload.get("enabled") is True}
            products[product_id] = {"enabled": bool(enabled)}
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, delete=False
            ) as temporary_file:
                json.dump({"products": products}, temporary_file, ensure_ascii=False)
                temporary_file.write("\n")
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, path)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_database_store_settings(database_url, product_id=PRODUCT_ID, default_enabled=False):
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT enabled FROM store_settings WHERE product_id = %s",
                (product_id,),
            )
            row = cursor.fetchone()
    return {"enabled": bool(row[0]) if row else bool(default_enabled)}


def save_database_store_settings(database_url, enabled, product_id=PRODUCT_ID):
    with database_connection(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO store_settings (product_id, enabled, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (product_id) DO UPDATE
                SET enabled = EXCLUDED.enabled, updated_at = CURRENT_TIMESTAMP
                """,
                (product_id, bool(enabled)),
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


def validate_contract(payload):
    if not isinstance(payload, dict):
        raise ValueError("契約書の入力内容を確認してください。")
    doc_type = str(payload.get("doc_type", "")).strip()
    configuration = CONTRACT_TYPES.get(doc_type)
    if not configuration:
        raise ValueError("契約書の種類を選択してください。")
    client_name = str(payload.get("client_name", "")).strip()
    client_representative = str(payload.get("client_representative", "")).strip()
    contract_date = str(payload.get("contract_date", "")).strip()
    if not client_name or len(client_name) > 120:
        raise ValueError("取引先名を120文字以内で入力してください。")
    if len(client_representative) > 80:
        raise ValueError("代表者名を80文字以内で入力してください。")
    try:
        datetime.strptime(contract_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("契約締結日を正しく入力してください。") from exc
    raw_values = payload.get("values", {})
    if not isinstance(raw_values, dict):
        raise ValueError("契約条件を確認してください。")
    transport_workflow_status = str(
        raw_values.get("workflow_status", "quote_pending")
    ).strip()
    transport_is_pending = (
        doc_type == "estimateB" and transport_workflow_status != "ready"
    )
    transport_pending_optional_fields = {
        "cargo_document_url",
        "route_document_url",
        "fee_document_url",
        "cargo_contact_email",
        "route_origin",
        "route_destination",
        "route_one_way_distance_km",
        "route_provider",
    }
    if doc_type == "estimateB" and transport_workflow_status == "ready":
        ready_cargo_items = raw_values.get("cargo_items", [])
        ready_rate_master = raw_values.get("freight_rate_master", {})
        ready_instrument_master = raw_values.get("instrument_price_master", {})
        refresh_interval_days = str(
            ready_instrument_master.get("refresh_interval_days", "30")
        ).strip() if isinstance(ready_instrument_master, dict) else ""
        price_refresh_valid = (
            refresh_interval_days.isdigit()
            and 1 <= int(refresh_interval_days) <= 365
        )
        stale_catalog_price = False
        if price_refresh_valid and isinstance(ready_cargo_items, list):
            estimate_day = datetime.strptime(contract_date, "%Y-%m-%d").date()
            for item in ready_cargo_items:
                if not isinstance(item, dict) or item.get("valuation_mode") != "master":
                    continue
                try:
                    checked_day = datetime.strptime(
                        str(item.get("price_checked_at", "")).strip(), "%Y-%m-%d"
                    ).date()
                except ValueError:
                    stale_catalog_price = True
                    break
                if (estimate_day - checked_day).days > int(refresh_interval_days):
                    stale_catalog_price = True
                    break
        cargo_values_ready = isinstance(ready_cargo_items, list) and all(
            isinstance(item, dict)
            and str(item.get("maker_model", "")).strip()
            and str(item.get("unit_value", "")).strip().isdigit()
            and int(str(item.get("unit_value", "0")).strip()) > 0
            for item in ready_cargo_items
        )
        cargo_volume = sum(
            float(str(item.get("volume_points", "0")))
            * int(str(item.get("quantity", "0")))
            for item in ready_cargo_items
            if isinstance(item, dict)
        ) if isinstance(ready_cargo_items, list) else 0
        light_cargo_over_capacity = cargo_volume >= 100
        if (
            raw_values.get("transport_provider_mode") != "self_light_cargo"
            or raw_values.get("vehicle_class") != "light_cargo"
            or raw_values.get("pricing_basis") not in {"self_light_cargo_rate", "light_cargo_reference"}
            or not isinstance(ready_rate_master, dict)
            or ready_rate_master.get("verified") is not True
            or not cargo_values_ready
            or not price_refresh_valid
            or stale_catalog_price
            or light_cargo_over_capacity
        ):
            raise ValueError("正式見積の発行準備に必要な自社軽貨物の積載条件・料金・楽器評価額を確認してください。積載上限を超える場合は品目の見直しまたは分割運行が必要です。")
    values = {}
    for key in configuration["keys"]:
        if key == "workflow_status":
            if transport_workflow_status not in {"draft", "quote_pending", "ready"}:
                raise ValueError("輸送案件の進行状態を選択してください。")
            values[key] = transport_workflow_status
            continue
        if key == "transport_provider_mode":
            provider_mode = str(
                raw_values.get(key, "self_light_cargo")
            ).strip()
            if provider_mode != "self_light_cargo":
                raise ValueError("B見積の運送実施形態は自社軽貨物に限定されています。")
            values[key] = provider_mode
            continue
        if key == "vehicle_class":
            vehicle_class = str(raw_values.get(key, "light_cargo")).strip()
            if vehicle_class != "light_cargo":
                raise ValueError("B見積の車両は軽貨物車に限定されています。")
            values[key] = vehicle_class
            continue
        if key == "pricing_basis":
            pricing_basis = str(raw_values.get(key, "self_light_cargo_rate")).strip()
            if pricing_basis not in {"self_light_cargo_rate", "light_cargo_reference"}:
                raise ValueError("B見積の料金根拠は軽貨物料金に限定されています。")
            values[key] = pricing_basis
            continue
        if key == "cargo_restrictions_agreed":
            agreed = raw_values.get(key) is True
            if not transport_is_pending and not agreed:
                raise ValueError("貴重品等を輸送対象外とする確認への同意が必要です。")
            values[key] = agreed
            continue
        if key == "freight_rate_master":
            raw_master = raw_values.get(key, {})
            rate_keys = {
                "distance_base_20",
                "distance_per_km_21_50",
                "distance_per_km_51_100",
                "distance_per_km_101_plus",
                "distance_per_km_101_150",
                "distance_per_km_151_plus",
                "charter_2h",
                "charter_4h",
                "charter_8h",
                "extra_30m",
                "extra_hour",
                "waiting_per_30m",
                "loading_base",
                "loading_per_15m",
                "loading_per_30m",
                "loading_per_25_points",
                "holiday_percent",
                "night_percent",
                "fuel_reference_price",
                "fuel_current_price",
                "fuel_per_km_per_yen",
            }
            light_cargo_rate_keys = {
                "distance_per_km_101_150",
                "distance_per_km_151_plus",
                "charter_2h",
                "extra_30m",
                "loading_per_15m",
                "loading_per_30m",
                "holiday_percent",
                "night_percent",
            }
            if not isinstance(raw_master, dict):
                raise ValueError("料金マスターを確認してください。")
            verified = raw_master.get("verified") is True
            if not transport_is_pending and not verified:
                raise ValueError("料金マスターの出典を確認し、確認済みにしてください。")
            effective_date = str(raw_master.get("effective_date", "")).strip()
            source_url = str(raw_master.get("source_url", "")).strip()
            if verified or effective_date:
                try:
                    datetime.strptime(effective_date, "%Y-%m-%d")
                except ValueError as exc:
                    raise ValueError("料金マスターの基準日を正しく入力してください。") from exc
                if effective_date > contract_date:
                    raise ValueError("見積作成日以前に適用開始された料金マスターを使用してください。")
            if verified or source_url:
                if re.fullmatch(r"https?://[^\s]+", source_url) is None:
                    raise ValueError("料金マスターの出典URLを入力してください。")
            rate_master = {
                "effective_date": effective_date,
                "source_url": source_url,
                "verified": verified,
                "tax_included": raw_master.get("tax_included") is True,
            }
            for rate_key in rate_keys:
                default_rate = "0" if rate_key in light_cargo_rate_keys else ""
                rate_value = str(raw_master.get(rate_key, default_rate)).strip()
                if re.fullmatch(r"\d{1,9}", rate_value) is None:
                    raise ValueError("料金マスターの金額・係数を0以上の整数で入力してください。")
                rate_master[rate_key] = rate_value
            values[key] = rate_master
            continue
        if key == "freight_operation":
            raw_operation = raw_values.get(key, {})
            if not isinstance(raw_operation, dict):
                raise ValueError("運賃計算条件を確認してください。")
            operation = {}
            for operation_key in {"waiting_minutes", "loading_minutes", "actual_expenses", "instrument_surcharge_amount", "special_work_amount"}:
                operation_value = str(raw_operation.get(operation_key, "0")).strip()
                if re.fullmatch(r"\d{1,9}", operation_value) is None:
                    raise ValueError("運賃計算条件の時間・金額は0以上の整数で入力してください。")
                operation[operation_key] = operation_value
            loading_support_mode = str(raw_operation.get("loading_support_mode", "carrier")).strip()
            cancellation_type = str(raw_operation.get("cancellation_type", "none")).strip()
            surcharge_mode = str(raw_operation.get("instrument_surcharge_mode", "none")).strip()
            if loading_support_mode not in {"carrier", "customer_assisted", "customer_loads"}:
                raise ValueError("積卸し方法を確認してください。")
            if cancellation_type not in {"none", "previous_day", "same_day_before", "same_day_after"}:
                raise ValueError("キャンセル区分を確認してください。")
            if surcharge_mode not in {"none", "securement", "overnight", "assistant", "custom"}:
                raise ValueError("楽器等運搬の上乗せ区分を確認してください。")
            operation.update({"loading_support_mode": loading_support_mode, "cancellation_type": cancellation_type, "instrument_surcharge_mode": surcharge_mode, "holiday": raw_operation.get("holiday") is True, "night": raw_operation.get("night") is True})
            values[key] = operation
            continue
        if key == "instrument_price_master":
            raw_master = raw_values.get(key, {})
            if not isinstance(raw_master, dict):
                raise ValueError("楽器価格マスターを確認してください。")
            verified = raw_master.get("verified") is True
            effective_date = str(raw_master.get("effective_date", "")).strip()
            source_url = str(raw_master.get("source_url", "")).strip()
            source_urls = raw_master.get("source_urls", [])
            catalog_year = str(raw_master.get("catalog_year", "")).strip()
            refresh_interval_days = str(raw_master.get("refresh_interval_days", "30")).strip()
            if (
                re.fullmatch(r"\d{1,3}", refresh_interval_days) is None
                or not 1 <= int(refresh_interval_days) <= 365
            ):
                raise ValueError("楽器価格の更新間隔は1日以上365日以内で入力してください。")
            if not isinstance(source_urls, list) or len(source_urls) > 7:
                raise ValueError("楽器価格の公式カタログURLを確認してください。")
            source_urls = list(dict.fromkeys(str(url).strip() for url in source_urls if str(url).strip()))
            if not source_urls and source_url:
                source_urls = [source_url]
            if source_urls and not source_url:
                source_url = source_urls[0]
            for catalog_url in source_urls:
                instrument_price_source(catalog_url)
            if verified:
                try:
                    datetime.strptime(effective_date, "%Y-%m-%d")
                except ValueError as exc:
                    raise ValueError("楽器価格マスターの基準日を正しく入力してください。") from exc
                if effective_date > contract_date:
                    raise ValueError("見積作成日以前に確認された楽器価格マスターを使用してください。")
                if re.fullmatch(r"https?://[^\s]+", source_url) is None:
                    raise ValueError("楽器価格マスターの出典URLを入力してください。")
                instrument_price_source(source_url)
                if not source_urls:
                    raise ValueError("楽器価格の公式カタログURLを入力してください。")
                if source_url not in source_urls:
                    raise ValueError("楽器価格マスターの出典URLとカタログ一覧が一致しません。")
                if catalog_year != contract_date[:4]:
                    raise ValueError("見積作成年の公開カタログで楽器価格を再照会してください。")
            values[key] = {
                "effective_date": effective_date,
                "source_url": source_url,
                "source_urls": source_urls,
                "catalog_year": catalog_year,
                "refresh_interval_days": refresh_interval_days,
                "verified": verified,
            }
            continue
        if key == "cargo_items":
            raw_items = raw_values.get(key, [])
            if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 10:
                raise ValueError("輸送対象物は1件以上10件以内で入力してください。")
            cargo_items = []
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    raise ValueError("輸送対象物明細を正しく入力してください。")
                item = {
                    "category": str(raw_item.get("category", "")).strip(),
                    "instrument_key": str(raw_item.get("instrument_key", "")).strip(),
                    "description": str(raw_item.get("description", "")).strip(),
                    "maker_model": str(raw_item.get("maker_model", "")).strip(),
                    "quantity": str(raw_item.get("quantity", "")).strip(),
                    "condition": str(raw_item.get("condition", "")).strip(),
                    "valuation_mode": str(raw_item.get("valuation_mode", "")).strip(),
                    "unit_value": str(raw_item.get("unit_value", "")).strip(),
                    "catalog_price": str(raw_item.get("catalog_price", raw_item.get("unit_value", ""))).strip(),
                    "total_value": str(raw_item.get("total_value", "")).strip(),
                    "volume_points": str(raw_item.get("volume_points", "")).strip(),
                    "notes": str(raw_item.get("notes", "")).strip(),
                    "lookup_source_url": str(raw_item.get("lookup_source_url", "")).strip(),
                    "price_source_url": str(raw_item.get("price_source_url", "")).strip(),
                    "price_checked_at": str(raw_item.get("price_checked_at", "")).strip(),
                }
                if (
                    not item["category"]
                    or len(item["category"]) > 40
                    or not item["instrument_key"]
                    or len(item["instrument_key"]) > 60
                    or not item["description"]
                    or len(item["description"]) > 120
                    or len(item["maker_model"]) > 120
                    or re.fullmatch(r"\d{1,4}", item["quantity"]) is None
                    or not item["condition"]
                    or len(item["condition"]) > 80
                    or item["valuation_mode"] not in {"master", "manual"}
                    or re.fullmatch(r"\d{1,12}", item["unit_value"]) is None
                    or re.fullmatch(r"\d{1,12}", item["catalog_price"]) is None
                    or re.fullmatch(r"\d{1,12}", item["total_value"]) is None
                    or re.fullmatch(r"\d{1,4}(?:\.\d{1,2})?", item["volume_points"])
                    is None
                    or len(item["notes"]) > 200
                    or len(item["lookup_source_url"]) > 500
                    or len(item["price_source_url"]) > 500
                    or int(item["quantity"]) * int(item["unit_value"])
                    != int(item["total_value"])
                ):
                    raise ValueError("輸送対象物明細の入力内容と評価額を確認してください。")
                if item["lookup_source_url"]:
                    instrument_price_source(item["lookup_source_url"])
                if item["price_source_url"]:
                    instrument_price_source(item["price_source_url"])
                    try:
                        datetime.strptime(item["price_checked_at"], "%Y-%m-%d")
                    except ValueError as exc:
                        raise ValueError("楽器価格の照会日を正しく入力してください。") from exc
                    if item["price_checked_at"] > contract_date:
                        raise ValueError("見積作成日以前に照会した楽器価格を使用してください。")
                if item["valuation_mode"] == "master" and (
                    not item["price_source_url"]
                    or not item["price_checked_at"]
                ):
                    raise ValueError("公式価格を使う楽器は照会元・照会日・税込／税別を確認してください。")
                cargo_items.append(item)
            values[key] = cargo_items
            continue
        if key == "estimate_items":
            raw_items = raw_values.get(key, [])
            if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 10:
                raise ValueError("見積明細は1件以上10件以内で入力してください。")
            estimate_items = []
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    raise ValueError("見積明細を正しく入力してください。")
                item = {
                    "role": str(raw_item.get("role", "")).strip(),
                    "description": str(raw_item.get("description", "")).strip(),
                    "quantity": str(raw_item.get("quantity", "")).strip(),
                    "unit": str(raw_item.get("unit", "")).strip(),
                    "unit_price": str(raw_item.get("unit_price", "")).strip(),
                    "amount": str(raw_item.get("amount", "")).strip(),
                    "details": str(raw_item.get("details", "")).strip(),
                }
                if (
                    (item["role"] and re.fullmatch(r"[a-z_]{1,40}", item["role"]) is None)
                    or
                    not item["description"]
                    or len(item["description"]) > 120
                    or not item["quantity"]
                    or len(item["quantity"]) > 20
                    or not item["unit"]
                    or len(item["unit"]) > 20
                    or re.fullmatch(r"\d{1,9}", item["unit_price"]) is None
                    or re.fullmatch(r"\d{1,9}", item["amount"]) is None
                    or len(item["details"]) > 300
                ):
                    raise ValueError("見積明細の入力内容を確認してください。")
                estimate_items.append(item)
            values[key] = estimate_items
            continue
        if key in {"transport_sheet_signature", "route_measurement_signature"}:
            signature = str(raw_values.get(key, "")).strip()
            signature_pattern = r"v1-[0-9a-f]{8}" if key == "transport_sheet_signature" else r"route-v1-[0-9a-f]{8}"
            if signature and re.fullmatch(signature_pattern, signature) is None:
                raise ValueError("輸送書類または距離測定の内容署名を確認してください。")
            values[key] = signature
            continue
        value = str(raw_values.get(key, "")).strip()
        if doc_type == "typeB" and key in {"transport_name", "estimate_reference_id", "estimate_date", "amount", "cargo_document_url", "route_document_url", "fee_document_url"} and not value:
            values[key] = ""
            continue
        if transport_is_pending and key in transport_pending_optional_fields and not value:
            values[key] = ""
            continue
        maximum_length = 3000 if key == "special_terms" else 500
        if not value or len(value) > maximum_length:
            raise ValueError(
                f"契約条件は{maximum_length}文字以内で入力してください。"
            )
        if key == "validity_days" and (
            re.fullmatch(r"\d{1,3}", value) is None or not 1 <= int(value) <= 365
        ):
            raise ValueError("見積有効期限は1日以上365日以内で入力してください。")
        if key.endswith("_url") and re.fullmatch(r"https?://[^\s]+", value) is None:
            raise ValueError("添付書類URLはhttpまたはhttps形式で入力してください。")
        if key == "carrier_quote_date":
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("正式見積の取得日を正しく入力してください。") from exc
        if key == "cargo_contact_email" and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) is None:
            raise ValueError("シート共有先メールアドレスを正しく入力してください。")
        if key in {"external_vehicle_budget", "route_distance_km", "total_hours"} and re.fullmatch(r"\d{1,9}(?:\.\d{1,2})?", value) is None:
            raise ValueError("距離・時間・予算は0以上の数値で入力してください。")
        values[key] = value
    if doc_type == "estimateB":
        instrument_master = values["instrument_price_master"]
        for item in values["cargo_items"]:
            if item["lookup_source_url"] and item["lookup_source_url"] not in instrument_master["source_urls"]:
                raise ValueError("対象物の公式価格サイトをカタログ候補へ登録してください。")
            if item["valuation_mode"] == "master" and (
                instrument_price_source(item["price_source_url"])
                != instrument_price_source(item["lookup_source_url"])
            ):
                raise ValueError("価格掲載ページは選択した公式カタログと同じメーカーにしてください。")
    return {
        "doc_type": doc_type,
        "department": configuration["department"],
        "client_name": client_name,
        "client_representative": client_representative,
        "contract_date": contract_date,
        "values": values,
    }


def save_contract(values, contracts_dir=CONTRACTS_DIR):
    configuration = CONTRACT_TYPES[values["doc_type"]]
    contracts_root = Path(contracts_dir)
    department_dir = contracts_root / configuration["directory"]
    department_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    contracts_root.chmod(0o700)
    department_dir.chmod(0o700)
    contract_id = (
        f'{values["doc_type"]}-{values["contract_date"].replace("-", "")}-'
        f'{uuid.uuid4().hex[:8]}'
    )
    saved_at = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    contract = {"contract_id": contract_id, "saved_at": saved_at, **values}
    target_path = department_dir / f"{contract_id}.json"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=department_dir, delete=False
    ) as temporary_file:
        json.dump(contract, temporary_file, ensure_ascii=False, indent=2)
        temporary_path = Path(temporary_file.name)
    os.replace(temporary_path, target_path)
    return contract


def list_contracts(contracts_dir=CONTRACTS_DIR):
    contracts = []
    scanned_directories = set()
    for configuration in CONTRACT_TYPES.values():
        directory = configuration["directory"]
        if directory in scanned_directories:
            continue
        scanned_directories.add(directory)
        department_dir = Path(contracts_dir) / directory
        for contract_path in department_dir.glob("*.json") if department_dir.exists() else []:
            try:
                contract = json.loads(contract_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(contract, dict):
                contracts.append(contract)
    return sorted(contracts, key=lambda item: str(item.get("saved_at", "")), reverse=True)


def load_contract(contract_id, contracts_dir=CONTRACTS_DIR):
    contract_id = str(contract_id).strip()
    match = re.fullmatch(
        r"(master|estimateA|typeA|typeB|estimateB|estimateC|typeC)-\d{8}-[a-f0-9]{8}", contract_id
    )
    if not match:
        return None
    configuration = CONTRACT_TYPES[match.group(1)]
    contract_path = Path(contracts_dir) / configuration["directory"] / f"{contract_id}.json"
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return contract if isinstance(contract, dict) else None


def delete_contract(
    contract_id,
    confirmation_id,
    contracts_dir=CONTRACTS_DIR,
    replica_dirs=(SERVER_CONTRACTS_DIR,),
):
    contract_id = str(contract_id).strip()
    confirmation_id = str(confirmation_id).strip()
    if not hmac.compare_digest(contract_id, confirmation_id):
        raise ValueError("確認用の契約書IDが一致しません。")
    match = re.fullmatch(
        r"(master|estimateA|typeA|typeB|estimateB|estimateC|typeC)-\d{8}-[a-f0-9]{8}", contract_id
    )
    if not match:
        return 0
    configuration = CONTRACT_TYPES[match.group(1)]
    storage_roots = {Path(contracts_dir).expanduser().resolve()}
    storage_roots.update(Path(directory).expanduser().resolve() for directory in replica_dirs)
    deleted_count = 0
    for storage_root in storage_roots:
        contract_path = storage_root / configuration["directory"] / f"{contract_id}.json"
        try:
            contract_path.unlink()
            deleted_count += 1
        except FileNotFoundError:
            continue
    return deleted_count


def load_event_pdf_manifest(pdf_upload_dir=PDF_UPLOAD_DIR):
    manifest_path = Path(pdf_upload_dir) / EVENT_PDF_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def save_event_pdf_manifest(manifest, pdf_upload_dir=PDF_UPLOAD_DIR):
    upload_dir = Path(pdf_upload_dir)
    upload_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    manifest_path = upload_dir / EVENT_PDF_MANIFEST
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=upload_dir, delete=False
    ) as temporary_file:
        json.dump(manifest, temporary_file, ensure_ascii=False, indent=2)
        temporary_path = Path(temporary_file.name)
    os.replace(temporary_path, manifest_path)


def list_event_pdfs(pdf_dir=SERVER_PDF_DIR, pdf_upload_dir=PDF_UPLOAD_DIR):
    manifest = load_event_pdf_manifest(pdf_upload_dir)
    deleted_filenames = set(manifest.get("__deleted__", []))
    documents = []
    seen = set()
    upload_dir = Path(pdf_upload_dir)
    static_dir = Path(pdf_dir)
    for filename, title in manifest.items():
        if (
            isinstance(filename, str)
            and not filename.startswith("__")
            and filename not in deleted_filenames
            and isinstance(title, str)
            and title.strip()
            and ((upload_dir / filename).is_file() or (static_dir / filename).is_file())
        ):
            documents.append({"filename": filename, "title": title.strip()})
            seen.add(filename)
    for filename, title in EVENT_PDF_TITLES.items():
        if (
            filename not in seen
            and filename not in deleted_filenames
            and (static_dir / filename).is_file()
        ):
            documents.append({"filename": filename, "title": title})
    return documents


def save_event_pdf(upload, title, pdf_upload_dir=PDF_UPLOAD_DIR):
    title = str(title).strip()
    if not title or len(title) > 160:
        raise ValueError("PDF内の表示タイトルを160文字以内で入力してください。")
    original_name = str(getattr(upload, "filename", "") or "").strip()
    if Path(original_name).suffix.lower() != ".pdf":
        raise ValueError("PDFファイルを選択してください。")
    content = upload.stream.read(EVENT_PDF_MAX_BYTES + 1)
    if len(content) > EVENT_PDF_MAX_BYTES:
        raise ValueError("PDFファイルは15MB以内にしてください。")
    if not content.startswith(b"%PDF-"):
        raise ValueError("正しいPDFファイルを選択してください。")
    upload_dir = Path(pdf_upload_dir)
    upload_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    filename = f"event-{uuid.uuid4().hex}.pdf"
    target_path = upload_dir / filename
    with tempfile.NamedTemporaryFile("wb", dir=upload_dir, delete=False) as temporary_file:
        temporary_file.write(content)
        temporary_path = Path(temporary_file.name)
    os.replace(temporary_path, target_path)
    manifest = load_event_pdf_manifest(upload_dir)
    manifest[filename] = title
    save_event_pdf_manifest(manifest, upload_dir)
    return {"filename": filename, "title": title}


def delete_event_pdf(
    filename,
    confirmation_filename,
    pdf_dir=SERVER_PDF_DIR,
    pdf_upload_dir=PDF_UPLOAD_DIR,
    replica_dirs=(),
):
    filename = str(filename).strip()
    confirmation_filename = str(confirmation_filename).strip()
    if not hmac.compare_digest(filename, confirmation_filename):
        raise ValueError("確認用のPDFファイル名が一致しません。")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.pdf", filename) is None:
        return 0
    storage_roots = {
        Path(pdf_dir).expanduser().resolve(),
        Path(pdf_upload_dir).expanduser().resolve(),
    }
    storage_roots.update(Path(directory).expanduser().resolve() for directory in replica_dirs)
    deleted_count = 0
    for storage_root in storage_roots:
        pdf_path = storage_root / filename
        try:
            pdf_path.unlink()
            deleted_count += 1
        except FileNotFoundError:
            pass
        manifest = load_event_pdf_manifest(storage_root)
        if filename in manifest:
            del manifest[filename]
            save_event_pdf_manifest(manifest, storage_root)
    if deleted_count:
        manifest = load_event_pdf_manifest(pdf_upload_dir)
        manifest.pop(filename, None)
        deleted_filenames = set(manifest.get("__deleted__", []))
        deleted_filenames.add(filename)
        manifest["__deleted__"] = sorted(deleted_filenames)
        save_event_pdf_manifest(manifest, pdf_upload_dir)
    return deleted_count


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
            raise ValueError("状態は 確認中・確定・キャンセル から選択してください。")
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
    attempts = 2 if action in {"create", "consultation", "generate_transport_sheet", "update", "delete", "cancel", "upsert_slot_status_range"} else 1
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
    contracts_dir=CONTRACTS_DIR,
    contract_replica_dirs=(SERVER_CONTRACTS_DIR,),
    pdf_dir=SERVER_PDF_DIR,
    pdf_upload_dir=PDF_UPLOAD_DIR,
    pdf_replica_dirs=(),
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

    def get_store_settings(product_id=PRODUCT_ID):
        default_enabled = product_id == FLOW_HARMONY_PRODUCT_ID and FLOW_HARMONY_SALES_ENABLED
        if configured_database_url:
            return load_database_store_settings(
                configured_database_url, product_id, default_enabled
            )
        return load_store_settings(store_file, product_id, default_enabled)

    def set_store_enabled(enabled, product_id=PRODUCT_ID):
        if configured_database_url:
            save_database_store_settings(configured_database_url, enabled, product_id)
        else:
            save_store_settings(enabled, store_file, product_id)

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

    def invoice_registration_number():
        value = os.environ.get(
            "INVOICE_REGISTRATION_NUMBER", DEFAULT_INVOICE_REGISTRATION_NUMBER
        ).strip().upper()
        if INVOICE_REGISTRATION_NUMBER_PATTERN.fullmatch(value) is None:
            return ""
        return value

    def japanese_checkout_options():
        registration_number = invoice_registration_number()
        return {
            "locale": "ja",
            "customer_creation": "always",
            "invoice_creation": {
                "enabled": True,
                "invoice_data": {
                    "description": "税込価格（消費税を含みます）",
                    "custom_fields": [
                        {
                            "name": "適格請求書発行事業者登録番号",
                            "value": registration_number,
                        }
                    ],
                },
            },
        }

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
            "INVOICE_REGISTRATION_NUMBER": bool(invoice_registration_number()),
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
            app.logger.error("Trumpet Transpose Lab Stripe price readiness check failed")
            return False

    def retrieve_flow_harmony_checkout(session_id, configuration):
        checkout = stripe_module().checkout.Session.retrieve(
            session_id,
            expand=["payment_intent.latest_charge"],
        )
        valid_product = any(
            checkout_payment_is_valid(
                checkout,
                configuration["price_yen"],
                configuration["stripe_mode"] == "live",
                product_id,
            )
            for product_id in (
                FLOW_HARMONY_PRODUCT_ID,
                FLOW_HARMONY_LEGACY_PRODUCT_ID,
            )
        )
        if not valid_product:
            return None
        return checkout

    def personalized_flow_harmony_product(session_id):
        license_text = (
            "Trumpet Transpose Lab オフライン版 利用ライセンス\n\n"
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
            "INVOICE_REGISTRATION_NUMBER": bool(invoice_registration_number()),
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
        supplied_token = request.headers.get("X-Editor-Token", "")
        if configured_password and supplied_token:
            try:
                token_payload = URLSafeTimedSerializer(
                    configured_password, salt="editor-session"
                ).loads(supplied_token, max_age=8 * 60 * 60)
                if isinstance(token_payload, dict) and token_payload.get("scope") == "editor":
                    return None
            except (BadData, SignatureExpired):
                return jsonify({"error": "管理者セッションの有効期限が切れました。再ログインしてください。"}), 401
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
        response = make_response(render_template("index.html"))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/favicon.ico")
    def favicon():
        return app.response_class(status=204)

    @app.get("/back-navigation.js")
    def back_navigation_script():
        return send_file(BASE_DIR / "back-navigation.js", mimetype="application/javascript")

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

    @app.get("/trumpet-transpose-lab/")
    def trumpet_transpose_lab():
        response = make_response(render_template("trumpet-transpose-lab/index.html"))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.get("/trumpet-transpose-lab/<path:asset>")
    def trumpet_transpose_lab_asset(asset):
        if asset not in {
            "styles.css",
            "app.mjs",
            "recorder-worklet.js",
            "transcription-core.mjs",
        }:
            return app.response_class(status=404)
        return send_from_directory(BASE_DIR / "trumpet-transpose-lab", asset)

    @app.get("/flow-harmony/")
    def flow_harmony_legacy_redirect():
        query = f"?{request.query_string.decode('utf-8')}" if request.query_string else ""
        return redirect(f"/trumpet-transpose-lab/{query}", code=308)

    @app.get("/contract-generator/")
    def contract_generator():
        response = make_response(render_template("contract-generator/index.html"))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.route("/api/contracts", methods=["GET", "POST"])
    def contracts_api():
        error = require_editor()
        if error:
            return error
        if request.method == "GET":
            return jsonify(
                {
                    "contracts": list_contracts(contracts_dir),
                    "storage_path": str(Path(contracts_dir).expanduser().resolve()),
                }
            )
        try:
            values = validate_contract(request.get_json(silent=True))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        contract = save_contract(values, contracts_dir)
        return jsonify(contract), 201

    @app.post("/api/contracts/instrument-price-lookup")
    def instrument_price_lookup():
        error = require_editor()
        if error:
            return error
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "価格照会内容を確認してください。"}), 400
        try:
            source_urls = payload.get("source_urls")
            if source_urls is None:
                source_urls = [payload.get("source_url", "")]
            if not isinstance(source_urls, list) or len(source_urls) != 1:
                raise ValueError("価格照会する公式カタログを1件だけ指定してください。")
            result = fetch_instrument_catalog_prices(source_urls, payload.get("maker_model", ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except (OSError, urllib_error.URLError, UnicodeError):
            app.logger.exception("Official instrument price lookup failed")
            return jsonify({"error": "公式価格ページを取得できませんでした。"}), 502
        return jsonify(result)

    @app.post("/api/contracts/route-distance")
    def contract_route_distance():
        error = require_editor()
        if error:
            return error
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "距離測定内容を確認してください。"}), 400
        api_key = os.environ.get("GOOGLE_MAPS_ROUTES_API_KEY", "").strip()
        origin = payload.get("origin", "")
        destination = payload.get("destination", "")
        if not api_key:
            return jsonify(
                {
                    "error": (
                        "Google Maps Routes APIキーが設定されていません。"
                        "RenderのGOOGLE_MAPS_ROUTES_API_KEYを設定してください。"
                    )
                }
            ), 503
        try:
            result = compute_google_route(origin, destination, api_key)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except urllib_error.HTTPError as exc:
            app.logger.warning("Google Routes API rejected route request: %s", exc.code)
            return jsonify(
                {
                    "error": (
                        "Google Maps Routes APIが距離測定を受け付けませんでした。"
                        "APIキー、Routes APIの有効化、請求先設定、住所を確認してください。"
                    )
                }
            ), 502
        except (OSError, urllib_error.URLError, json.JSONDecodeError, UnicodeError):
            app.logger.exception("Google Routes API request failed")
            return jsonify(
                {"error": "Google Maps Routes APIから距離を取得できませんでした。"}
            ), 502
        return jsonify(result)

    @app.post("/api/contracts/transport-sheet")
    def create_transport_sheet():
        error = require_editor()
        if error:
            return error
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "シート発行内容を確認してください。"}), 400
        client_name = str(payload.get("client_name", "")).strip()
        transport_name = str(payload.get("transport_name", "")).strip()
        editor_email = str(payload.get("editor_email", "")).strip()
        cargo_items = payload.get("cargo_items")
        rate_master = payload.get("freight_rate_master")
        instrument_master = payload.get("instrument_price_master")
        cargo_restrictions_agreed = payload.get("cargo_restrictions_agreed") is True
        workflow_status = str(payload.get("workflow_status", "draft")).strip()
        transport_provider_mode = str(payload.get("transport_provider_mode", "external_carrier")).strip()
        vehicle_class = str(payload.get("vehicle_class", "undecided")).strip()
        pricing_basis = str(payload.get("pricing_basis", "mlit_reference")).strip()
        route_origin = str(payload.get("route_origin", "")).strip()
        route_destination = str(payload.get("route_destination", "")).strip()
        route_trip_type = str(payload.get("route_trip_type", "one_way")).strip()
        route_one_way_distance_km = str(payload.get("route_one_way_distance_km", "")).strip()
        route_distance_km = str(payload.get("route_distance_km", "")).strip()
        route_provider = str(payload.get("route_provider", "")).strip()
        route_measurement_signature = str(payload.get("route_measurement_signature", "")).strip()
        total_hours = str(payload.get("total_hours", "")).strip()
        freight_operation = payload.get("freight_operation", {})
        if not client_name or len(client_name) > 120 or not transport_name or len(transport_name) > 160:
            return jsonify({"error": "取引先名と輸送案件名を入力してください。"}), 400
        if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", editor_email) is None:
            return jsonify({"error": "シート共有先メールアドレスを正しく入力してください。"}), 400
        if not isinstance(cargo_items, list) or not 1 <= len(cargo_items) <= 10 or not isinstance(rate_master, dict) or not isinstance(instrument_master, dict):
            return jsonify({"error": "輸送対象物と料金マスターを確認してください。"}), 400
        if not cargo_restrictions_agreed:
            return jsonify({"error": "輸送対象外品の確認へ同意してください。"}), 400
        valid_cargo_items = all(
            isinstance(item, dict)
            and 0 < len(str(item.get("description", "")).strip()) <= 160
            and re.fullmatch(r"\d{1,4}(?:\.\d{1,2})?", str(item.get("quantity", "")).strip()) is not None
            and float(str(item.get("quantity", "0")).strip()) > 0
            and re.fullmatch(r"\d{1,12}", str(item.get("unit_value", "")).strip()) is not None
            and int(str(item.get("unit_value", "0")).strip()) > 0
            and re.fullmatch(r"\d{1,6}(?:\.\d{1,2})?", str(item.get("volume_points", "")).strip()) is not None
            for item in cargo_items
        )
        if not valid_cargo_items:
            return jsonify({"error": "輸送対象物の名称、数量、単価・評価額、容積ポイントを確認してください。"}), 400
        if not route_origin or len(route_origin) > 200 or not route_destination or len(route_destination) > 200 or route_trip_type not in {"one_way", "round_trip"} or re.fullmatch(r"\d{1,9}(?:\.\d{1,2})?", route_one_way_distance_km) is None or float(route_one_way_distance_km) <= 0 or re.fullmatch(r"\d{1,9}(?:\.\d{1,2})?", route_distance_km) is None or float(route_distance_km) <= 0 or route_provider != "Google Maps" or re.fullmatch(r"route-v1-[0-9a-f]{8}", route_measurement_signature) is None or re.fullmatch(r"\d{1,9}(?:\.\d{1,2})?", total_hours) is None or float(total_hours) <= 0 or not isinstance(freight_operation, dict):
            return jsonify({"error": "運行経路、実車走行距離、総拘束時間を確認してください。"}), 400
        if workflow_status not in {"draft", "quote_pending", "ready"} or transport_provider_mode != "self_light_cargo" or vehicle_class != "light_cargo" or pricing_basis not in {"self_light_cargo_rate", "light_cargo_reference"}:
            return jsonify({"error": "輸送案件の進行状態を確認してください。"}), 400
        if rate_master.get("verified") is not True or not str(rate_master.get("effective_date", "")).strip() or re.fullmatch(r"https://[^\s]+", str(rate_master.get("source_url", "")).strip()) is None:
            return jsonify({"error": "確認済みの軽貨物料金マスターと出典URLを指定してください。"}), 400
        script_url = os.environ.get("GOOGLE_APPS_SCRIPT_URL", "").strip()
        script_secret = os.environ.get("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        if not script_url or not script_secret:
            return jsonify({"error": "Google Apps Scriptの接続設定が不足しています。"}), 503
        try:
            result = send_lesson_reservation(
                script_url,
                script_secret,
                {
                    "client_name": client_name,
                    "transport_name": transport_name,
                    "editor_email": editor_email,
                    "workflow_status": workflow_status,
                    "transport_provider_mode": transport_provider_mode,
                    "vehicle_class": vehicle_class,
                    "pricing_basis": pricing_basis,
                    "cargo_restrictions_agreed": cargo_restrictions_agreed,
                    "route_origin": route_origin,
                    "route_destination": route_destination,
                    "route_trip_type": route_trip_type,
                    "route_one_way_distance_km": route_one_way_distance_km,
                    "route_distance_km": route_distance_km,
                    "route_provider": route_provider,
                    "route_measurement_signature": route_measurement_signature,
                    "total_hours": total_hours,
                    "freight_operation": freight_operation,
                    "cargo_items": cargo_items,
                    "freight_rate_master": rate_master,
                    "instrument_price_master": instrument_master,
                },
                action="generate_transport_sheet",
            )
        except (LessonReservationDeliveryError, OSError, urllib_error.URLError, json.JSONDecodeError):
            app.logger.exception("Apps Script rejected transport sheet generation")
            return jsonify({"error": "輸送明細シートを発行できませんでした。"}), 502
        generated_urls = [result.get("cargoUrl", ""), result.get("routeUrl", ""), result.get("feeUrl", "")]
        valid_generated_urls = all(
            re.fullmatch(r"https://docs\.google\.com/spreadsheets/[^\s]+#gid=\d+", str(url))
            for url in generated_urls
        )
        if not valid_generated_urls or len(set(generated_urls)) != 3:
            return jsonify({"error": "Apps Scriptを運行計画・積卸し経路図対応版へ更新してください。"}), 502
        return jsonify(
            {"cargo_url": result.get("cargoUrl", ""), "route_url": result.get("routeUrl", ""), "fee_url": result.get("feeUrl", "")}
        ), 201

    @app.route("/api/contracts/<contract_id>", methods=["GET", "DELETE"])
    def contract_api(contract_id):
        error = require_editor()
        if error:
            return error
        if request.method == "DELETE":
            payload = request.get_json(silent=True)
            confirmation_id = payload.get("confirmation_id", "") if isinstance(payload, dict) else ""
            try:
                deleted_count = delete_contract(
                    contract_id,
                    confirmation_id,
                    contracts_dir,
                    contract_replica_dirs,
                )
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            if not deleted_count:
                return jsonify({"error": "保存済み契約書が見つかりません。"}), 404
            return jsonify(
                {
                    "deleted": True,
                    "deleted_count": deleted_count,
                    "contract_id": contract_id,
                }
            )
        contract = load_contract(contract_id, contracts_dir)
        if not contract:
            return jsonify({"error": "保存済み契約書が見つかりません。"}), 404
        return jsonify({"contract": contract})

    @app.post("/api/event-pdfs")
    def upload_event_pdf():
        error = require_editor()
        if error:
            return error
        upload = request.files.get("pdf")
        if upload is None:
            return jsonify({"error": "PDFファイルを選択してください。"}), 400
        try:
            document = save_event_pdf(upload, request.form.get("title", ""), pdf_upload_dir)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"saved": True, "document": document}), 201

    @app.delete("/api/event-pdfs/<filename>")
    def remove_event_pdf(filename):
        error = require_editor()
        if error:
            return error
        payload = request.get_json(silent=True)
        confirmation_filename = (
            payload.get("confirmation_filename", "") if isinstance(payload, dict) else ""
        )
        try:
            deleted_count = delete_event_pdf(
                filename,
                confirmation_filename,
                pdf_dir,
                pdf_upload_dir,
                pdf_replica_dirs,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not deleted_count:
            return jsonify({"error": "PDFファイルが見つかりません。"}), 404
        return jsonify({"deleted": True, "deleted_count": deleted_count, "filename": filename})

    @app.get("/pdf/")
    def event_pdf_index():
        return render_template(
            "pdf/index.html",
            pdf_documents=list_event_pdfs(pdf_dir, pdf_upload_dir),
        )

    @app.get("/pdf/<path:filename>")
    def event_pdf_file(filename):
        if Path(filename).name != filename:
            return app.response_class(status=404)
        deleted_filenames = set(
            load_event_pdf_manifest(pdf_upload_dir).get("__deleted__", [])
        )
        if filename in deleted_filenames:
            return app.response_class(status=404)
        for directory in (Path(pdf_upload_dir), Path(pdf_dir)):
            if (directory / filename).is_file():
                return send_from_directory(directory, filename)
        return app.response_class(status=404)

    @app.get("/legal/")
    def legal():
        return render_template(
            "legal/index.html",
            product_price_yen=store_configuration()["price_yen"],
            flow_harmony_price_yen=flow_harmony_configuration()["price_yen"],
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
                **japanese_checkout_options(),
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

    @app.route(
        "/api/store/trumpet-transpose-lab/product", methods=["GET", "PUT", "OPTIONS"]
    )
    @app.route("/api/store/flow-harmony/product", methods=["GET", "PUT", "OPTIONS"])
    def flow_harmony_store_product():
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
            set_store_enabled(payload["enabled"], FLOW_HARMONY_PRODUCT_ID)

        settings = get_store_settings(FLOW_HARMONY_PRODUCT_ID)
        configuration = flow_harmony_configuration()
        return store_json(
            {
                "product_id": FLOW_HARMONY_PRODUCT_ID,
                "name": FLOW_HARMONY_PRODUCT_NAME,
                "price_yen": configuration["price_yen"],
                "enabled": settings["enabled"],
                "checkout_available": settings["enabled"]
                and configuration["ready"]
                and flow_harmony_price_is_ready(configuration),
            }
        )

    @app.route(
        "/api/store/trumpet-transpose-lab/checkout", methods=["POST", "OPTIONS"]
    )
    @app.route("/api/store/flow-harmony/checkout", methods=["POST", "OPTIONS"])
    def create_flow_harmony_checkout():
        if request.method == "OPTIONS":
            return with_store_cors(app.response_class(status=204))
        if not get_store_settings(FLOW_HARMONY_PRODUCT_ID)["enabled"]:
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
                **japanese_checkout_options(),
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
                    "&flow_session_id={CHECKOUT_SESSION_ID}#trumpet-transpose-lab"
                ),
                cancel_url=(
                    f"{configuration['site_url']}/products/"
                    "?flow_purchase=cancelled#trumpet-transpose-lab"
                ),
                idempotency_key=(
                    f"{FLOW_HARMONY_PRODUCT_ID}:{checkout_request_id}"
                ),
            )
        except Exception as exc:
            app.logger.exception("Trumpet Transpose Lab Checkout session creation failed")
            return store_json(
                {
                    "error": "決済画面を開始できませんでした。",
                    "diagnostic_code": type(exc).__name__,
                },
                502,
            )
        return store_json({"checkout_url": stripe_value(checkout, "url")}, 201)

    @app.route(
        "/api/store/trumpet-transpose-lab/download-link", methods=["POST", "OPTIONS"]
    )
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
            app.logger.exception("Trumpet Transpose Lab payment verification failed")
            return store_json({"error": "決済情報を確認できませんでした。"}, 502)
        if checkout is None:
            return store_json({"error": "支払いの完了を確認できません。"}, 403)
        token = serializer.dumps(
            {"product_id": FLOW_HARMONY_PRODUCT_ID, "session_id": session_id}
        )
        return store_json(
            {
                "download_url": (
                    f"{request.url_root.rstrip('/')}/api/store/trumpet-transpose-lab/download/{token}"
                ),
                "expires_in": 86400,
            }
        )

    @app.get("/api/store/trumpet-transpose-lab/download/<token>")
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
        if payload.get("product_id") not in {
            FLOW_HARMONY_PRODUCT_ID,
            FLOW_HARMONY_LEGACY_PRODUCT_ID,
        }:
            return store_json({"error": "商品が見つかりません。"}, 404)
        session_id = str(payload.get("session_id", "")).strip()
        configuration = flow_harmony_configuration()
        try:
            checkout = retrieve_flow_harmony_checkout(session_id, configuration)
        except Exception:
            app.logger.exception("Trumpet Transpose Lab payment revalidation failed")
            return store_json({"error": "決済情報を再確認できませんでした。"}, 502)
        if checkout is None:
            return store_json({"error": "現在この商品をダウンロードできません。"}, 403)
        if not download_is_allowed(purchase_reference(session_id)):
            return store_json({"error": "ダウンロード回数の上限に達しました。"}, 429)
        response = send_file(
            personalized_flow_harmony_product(session_id),
            as_attachment=True,
            download_name="trumpet-transpose-lab-offline.zip",
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

        required_capabilities = {"generate_transport_sheet", "list", "update", "delete", "cancel", "upsert_slot_status_range"}
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

    @app.route("/api/editor", methods=["GET", "POST", "OPTIONS"])
    def editor_status():
        if request.method == "OPTIONS":
            return with_lesson_reservation_cors(
                app.response_class(status=204),
                methods="GET, POST, OPTIONS",
                headers="Content-Type, X-Editor-Password, X-Editor-Token",
            )
        error = require_editor()
        if error:
            response, status_code = error
            response.status_code = status_code
            return with_lesson_reservation_cors(
                response,
                methods="GET, POST, OPTIONS",
                headers="Content-Type, X-Editor-Password, X-Editor-Token",
            )
        result = {"authenticated": True}
        if request.method == "POST":
            configured_password = os.environ.get("EDITOR_PASSWORD", "")
            result["editor_token"] = URLSafeTimedSerializer(
                configured_password, salt="editor-session"
            ).dumps({"scope": "editor"})
        return with_lesson_reservation_cors(
            jsonify(result),
            methods="GET, POST, OPTIONS",
            headers="Content-Type, X-Editor-Password, X-Editor-Token",
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