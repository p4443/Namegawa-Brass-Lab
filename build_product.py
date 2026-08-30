import os
import re
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


BASE_DIR = Path(__file__).resolve().parent
SOURCE_FILE = BASE_DIR / "music App" / "index.html"
OUTPUT_FILE = BASE_DIR / "private" / "products" / "trumpet-metronome.zip"
TRANSPOSE_LAB_SOURCE_DIR = BASE_DIR / "trumpet-transpose-lab"
TRANSPOSE_LAB_FILES = (
    "index.html",
    "styles.css",
    "app.mjs",
    "recorder-worklet.js",
    "transcription-core.mjs",
)
TRANSPOSE_LAB_OUTPUT_FILE = (
    BASE_DIR / "private" / "products" / "trumpet-transpose-lab.zip"
)
README = """トランペット練習メトロノーム オフライン版

使い方:
1. ZIPファイルを展開します。
2. index.htmlをブラウザで開きます。
3. 音声再生を許可して利用します。

インターネット接続は不要です。

利用条件:
- 本商品は購入者本人のみ利用できます。
- 購入者本人が所有する複数端末で利用できます。
- 第三者への譲渡、共有、再配布、販売、公衆送信は禁止します。
"""
TRANSPOSE_LAB_README = """Trumpet Transpose Lab オフライン版

使い方:
1. ZIPファイルを展開します。
2. index.htmlをブラウザで開きます。
3. マイクの使用を許可して利用します。

録音したフレーズを自動採譜し、音高・音価・タイミングを編集できます。
B♭トランペット譜の移調、WAV保存、MIDI・MusicXML出力にも対応します。
録音データはサーバーへ送信せず、利用者が指定した場所へ保存します。

利用条件:
- 本商品は購入者本人のみ利用できます。
- 購入者本人が所有する複数端末で利用できます。
- 第三者への譲渡、共有、再配布、販売、公衆送信は禁止します。
"""


def build_archive(source_file, output_file, readme):
    if not source_file.is_file():
        raise FileNotFoundError(f"Source app not found: {source_file}")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = output_file.with_name(
        f".{output_file.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with ZipFile(temporary_file, "w", compression=ZIP_DEFLATED) as archive:
            archive.write(source_file, "index.html")
            archive.writestr("README.txt", readme)
        with ZipFile(temporary_file) as archive:
            if archive.testzip() is not None:
                raise ValueError("Generated product archive is corrupt")
        temporary_file.chmod(0o644)
        os.replace(temporary_file, output_file)
    finally:
        temporary_file.unlink(missing_ok=True)
    return output_file


def build_transpose_lab_archive():
    source_files = [TRANSPOSE_LAB_SOURCE_DIR / name for name in TRANSPOSE_LAB_FILES]
    missing_files = [source_file.name for source_file in source_files if not source_file.is_file()]
    if missing_files:
        raise FileNotFoundError(
            f"Transpose Lab files not found: {', '.join(missing_files)}"
        )
    TRANSPOSE_LAB_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = TRANSPOSE_LAB_OUTPUT_FILE.with_name(
        f".{TRANSPOSE_LAB_OUTPUT_FILE.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with ZipFile(temporary_file, "w", compression=ZIP_DEFLATED) as archive:
            html = (TRANSPOSE_LAB_SOURCE_DIR / "index.html").read_text(
                encoding="utf-8"
            )
            stylesheet = (TRANSPOSE_LAB_SOURCE_DIR / "styles.css").read_text(
                encoding="utf-8"
            )
            core_javascript = (
                TRANSPOSE_LAB_SOURCE_DIR / "transcription-core.mjs"
            ).read_text(encoding="utf-8")
            app_javascript = (TRANSPOSE_LAB_SOURCE_DIR / "app.mjs").read_text(
                encoding="utf-8"
            )
            worklet_javascript = (
                TRANSPOSE_LAB_SOURCE_DIR / "recorder-worklet.js"
            ).read_text(encoding="utf-8")
            core_javascript = re.sub(
                r"^export\s+", "", core_javascript, flags=re.MULTILINE
            )
            app_javascript = re.sub(
                r"^import \{.*?^\} from './transcription-core\.mjs';\n",
                "",
                app_javascript,
                flags=re.MULTILINE | re.DOTALL,
            )
            html = html.replace(
                '<link rel="stylesheet" href="./styles.css">',
                f"<style>\n{stylesheet}\n</style>",
            )
            html = html.replace(
                '<script type="module" src="./app.mjs"></script>',
                '<script type="text/plain" id="recorderWorkletSource">\n'
                f"{worklet_javascript}\n</script>\n<script>\n"
                f"{core_javascript}\n{app_javascript}\n</script>",
            )
            archive.writestr("index.html", html)
            archive.writestr("README.txt", TRANSPOSE_LAB_README)
        with ZipFile(temporary_file) as archive:
            if archive.testzip() is not None:
                raise ValueError("Generated product archive is corrupt")
        temporary_file.chmod(0o644)
        os.replace(temporary_file, TRANSPOSE_LAB_OUTPUT_FILE)
    finally:
        temporary_file.unlink(missing_ok=True)
    return TRANSPOSE_LAB_OUTPUT_FILE


def build_product():
    metronome = build_archive(SOURCE_FILE, OUTPUT_FILE, README)
    build_transpose_lab_archive()
    return metronome


if __name__ == "__main__":
    print(build_product())
    print(TRANSPOSE_LAB_OUTPUT_FILE)
