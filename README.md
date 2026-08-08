# HP Site (Python)

Flaskで配信するWebサイトです。`data/updates.txt`の解析、日付順の並び替え、画像・動画・PDFの判定とHTML生成はPythonで行います。

更新情報は次の形式で追記します。

```text
2026-08-09 | お役立ち | 本文 [image:data/media/example.jpg]
```

メディア種別には`image`、`video`、`pdf`を指定できます。

## 起動

```bash
./manage-site.sh start
```

## 停止

```bash
./manage-site.sh stop
```

## ブラウザで見る

http://localhost:8080
https://localhost:8443

## 証明書を信頼する（macOS）

```bash
./trust-local-cert.sh
```

## ひとまとめコマンド

```bash
./manage-site.sh start
./manage-site.sh stop
./manage-site.sh trust
```

## テスト

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```
