# HP Site (Python)

Flaskで配信するWebサイトです。`data/updates.txt`の解析、日付順の並び替え、画像・動画・PDFの判定とHTML生成はPythonで行います。

更新情報は次の形式で追記します。

```text
2026-08-09 | お役立ち | 本文 [image:data/media/example.jpg]
```

メディア種別には`image`、`video`、`pdf`を指定できます。

## 起動

編集用パスワードを設定して起動します。パスワードはHTMLやリポジトリには保存されません。

```bash
EDITOR_PASSWORD='自分で決めたパスワード' ./manage-site.sh start

同じWi-Fi内のスマートフォンから `https://MacのIPアドレス:8443` を開き、「つぶやき・お役立ち情報」の「編集」ボタンからログインすると、情報の追加・変更・削除ができます。パスワードを送信するため、スマートフォンからの編集にはHTTPSを使用してください。

更新内容はホストの `data/updates.txt` に保存され、コンテナを作り直しても残ります。

## 停止

```b
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