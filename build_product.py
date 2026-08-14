import os
import uuid
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

利用条件:
- 本商品は購入者本人のみ利用できます。
- 購入者本人が所有する複数端末で利用できます。
- 第三者への譲渡、共有、再配布、販売、公衆送信は禁止します。
"""


def build_product():
    if not SOURCE_FILE.is_file():
        raise FileNotFoundError(f"Source app not found: {SOURCE_FILE}")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = OUTPUT_FILE.with_name(
        f".{OUTPUT_FILE.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with ZipFile(temporary_file, "w", compression=ZIP_DEFLATED) as archive:
            archive.write(SOURCE_FILE, "index.html")
            archive.writestr("README.txt", README)
        with ZipFile(temporary_file) as archive:
            if archive.testzip() is not None:
                raise ValueError("Generated product archive is corrupt")
        temporary_file.chmod(0o644)
        os.replace(temporary_file, OUTPUT_FILE)
    finally:
        temporary_file.unlink(missing_ok=True)
    return OUTPUT_FILE


if __name__ == "__main__":
    print(build_product())
