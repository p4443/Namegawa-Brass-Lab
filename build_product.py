import os
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


BASE_DIR = Path(__file__).resolve().parent
SOURCE_FILE = BASE_DIR / "music App" / "index.html"
OUTPUT_FILE = BASE_DIR / "private" / "products" / "trumpet-metronome.zip"
TRANSPOSE_LAB_SOURCE_FILE = BASE_DIR / "trumpet-transpose-lab" / "index.html"
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


def build_product():
    metronome = build_archive(SOURCE_FILE, OUTPUT_FILE, README)
    build_archive(
        TRANSPOSE_LAB_SOURCE_FILE,
        TRANSPOSE_LAB_OUTPUT_FILE,
        TRANSPOSE_LAB_README,
    )
    return metronome


if __name__ == "__main__":
    print(build_product())
    print(TRANSPOSE_LAB_OUTPUT_FILE)
