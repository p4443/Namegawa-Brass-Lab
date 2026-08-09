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
```

同じWi-Fi内のスマートフォンから `https://MacのIPアドレス:8443` を開き、「つぶやき・お役立ち情報」の「編集」ボタンからログインすると、情報の追加・変更・削除ができます。パスワードを送信するため、スマートフォンからの編集にはHTTPSを使用してください。

更新内容はホストの `data/updates.txt` に保存され、コンテナを作り直しても残ります。

## Renderで投稿を永続保存する

RenderのWeb Serviceは再起動時にコンテナ内のファイルが初期化されるため、公開サイトではPostgreSQLを使用します。

1. Render Dashboardの「New +」から「PostgreSQL」を作成します。
2. 作成したデータベースの「Internal Database URL」をコピーします。
3. `namegawa-brass-lab` Web Serviceの「Environment」を開きます。
4. Keyを`DATABASE_URL`、ValueをコピーしたURLとして追加します。
5. 「Save, rebuild, and deploy」を実行します。

初回接続時にテーブルが自動作成され、`data/updates.txt`の既存情報が取り込まれます。以後の追加・変更・削除はデータベースへ保存されます。

## レッスン予約をGoogleスプレッドシートへ保存する

教室ページのフッターから送信された予約は、Flaskを経由してGoogle Apps Scriptへ送られます。

1. Googleスプレッドシートを新規作成し、URL内の`/d/`と`/edit`の間にあるスプレッドシートIDを控えます。
2. スプレッドシートの「拡張機能」から「Apps Script」を開き、[google-apps-script/Code.gs](google-apps-script/Code.gs)の内容を貼り付けます。
3. Apps Scriptの「プロジェクトの設定」→「スクリプト プロパティ」に`SPREADSHEET_ID`と`API_SECRET`を追加します。`API_SECRET`には十分に長いランダム文字列を指定します。
4. 「デプロイ」→「新しいデプロイ」→「ウェブアプリ」を選び、実行ユーザーを「自分」、アクセスできるユーザーを「全員」にしてデプロイします。
5. 発行された`/exec`のURLを`GOOGLE_APPS_SCRIPT_URL`、同じ秘密文字列を`GOOGLE_APPS_SCRIPT_SECRET`としてRenderのEnvironmentへ登録し、再デプロイします。

ローカルでは次の環境変数を設定して起動します。

```bash
export GOOGLE_APPS_SCRIPT_URL='https://script.google.com/macros/s/デプロイID/exec'
export GOOGLE_APPS_SCRIPT_SECRET='API_SECRETと同じ文字列'
./manage-site.sh start
```

初回の予約送信時に`レッスン予約`シートと見出しが自動作成されます。予約は即時確定ではなく、シートには状態`受付`として保存されます。

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