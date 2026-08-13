from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


BASE_DIR = Path(__file__).resolve().parent
SOURCE_FILE = BASE_DIR / "music App" / "index.html"
OUTPUT_FILE = BASE_DIR / "private" / "products" / "trumpet-metronome.zip"
README = """トランペット練習メトロノーム オフライン版

使い方:
1. ZIPファイルを展開します。
2. index.htmlをブラウザで開きます。
3. 音声再生を許可して利用します。

インターネット接続は不要です。
"""


def build_product():
    if not SOURCE_FILE.is_file():
        raise FileNotFoundError(f"Source app not found: {SOURCE_FILE}")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUTPUT_FILE, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(SOURCE_FILE, "index.html")
        archive.writestr("README.txt", README)
    return OUTPUT_FILE


if __name__ == "__main__":
    print(build_product())
