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
3. Apps Scriptの「プロジェクトの設定」で「appsscript.json マニフェスト ファイルをエディタで表示する」を有効にし、[google-apps-script/appsscript.json](google-apps-script/appsscript.json)の内容へ置き換えてV8ランタイムを有効にします。
4. 同じ設定画面の「スクリプト プロパティ」に`SPREADSHEET_ID`と`API_SECRET`を追加します。`API_SECRET`には十分に長いランダム文字列を指定します。
5. 「デプロイ」→「新しいデプロイ」→「ウェブアプリ」を選び、実行ユーザーを「自分」、アクセスできるユーザーを「全員」にしてデプロイします。
6. 発行された`/exec`のURLを`GOOGLE_APPS_SCRIPT_URL`、同じ秘密文字列を`GOOGLE_APPS_SCRIPT_SECRET`としてRenderのEnvironmentへ登録し、再デプロイします。

ローカルでは次の環境変数を設定して起動します。

```bash
export GOOGLE_APPS_SCRIPT_URL='https://script.google.com/macros/s/デプロイID/exec'
export GOOGLE_APPS_SCRIPT_SECRET='API_SECRETと同じ文字列'
./manage-site.sh start
```

初回の予約送信時に`レッスン予約`シートと`予約枠状態`シートが自動作成されます。

- 予約受付時の状態は`調整中`として保存されます。
- 同時に、該当する希望日時の枠へ`調整中`が自動反映されます。
- 受付完了メール（自動返信）が送信されます。
- 同一メールアドレス・希望日・希望時間の予約が短時間（10分）で重複送信された場合は、既存受付を返して新規作成しません。

レッスン種別ごとの所要時間は次のとおりです。

- 体験レッスン・小学生: 30分（15分枠を2枠使用）
- 中学生: 45分（15分枠を3枠使用）
- 高校生以上: 60分（15分枠を4枠使用）
- グループ・部活動指導: 60分枠を確保し、詳細は個別調整

予約作成時は開始時刻から必要な全枠が空いている場合だけ受け付け、全枠を`調整中`へ更新します。予約を確定すると全枠が`予約済`になり、キャンセルまたは削除するとその予約が使用していた全枠を解放します。

### 予約の更新・削除（管理API）

予約番号（例: `R-20260810-004`）を指定して、状態更新や削除ができます。

- 認証ヘッダー: `X-Editor-Password`（`EDITOR_PASSWORD`と同じ値）
- 必要な環境変数: `GOOGLE_APPS_SCRIPT_URL`, `GOOGLE_APPS_SCRIPT_SECRET`

更新（PUT）の例:

```bash
curl -X PUT 'https://namegawa-brass-lab.onrender.com/api/lesson-reservations/R-20260810-004' \
	-H 'Content-Type: application/json' \
	-H 'X-Editor-Password: ここに編集用パスワード' \
	-d '{"status":"確認中","message":"日程確認中です"}'
```

削除（DELETE）の例:

```bash
curl -X DELETE 'https://namegawa-brass-lab.onrender.com/api/lesson-reservations/R-20260810-004' \
	-H 'X-Editor-Password: ここに編集用パスワード'
```

状態に指定できる値は `受付` / `確認中` / `確定` / `キャンセル` です。

### 予約枠状態の管理API（管理者）

期間と時間帯を指定して、`予約済`または`お休み`を一括反映できます。

- エンドポイント: `POST /api/lesson-slot-statuses/admin`
- 認証ヘッダー: `X-Editor-Password`（`EDITOR_PASSWORD`と同じ値）

```bash
curl -X POST 'https://namegawa-brass-lab.onrender.com/api/lesson-slot-statuses/admin' \
	-H 'Content-Type: application/json' \
	-H 'X-Editor-Password: ここに編集用パスワード' \
	-d '{"start_date":"2026-08-20","end_date":"2026-08-22","start_time":"09:00","end_time":"10:00","status":"予約済","note":"本番前リハ"}'
```

公開用の枠状態取得API:

- エンドポイント: `GET /api/lesson-slot-statuses?from=YYYY-MM-DD&to=YYYY-MM-DD`
- 予約フォームはこのAPIを参照して、`調整中` / `予約済` / `お休み`の枠を選択不可として表示します。

公開スケジュールと管理画面:

- 公開URL: `/schedule/`
- 予約者は月間カレンダーと日別の空き時間を確認し、その日時を引き継いで予約フォームへ移動できます。
- 管理者は同じページ下部から`EDITOR_PASSWORD`でログインし、予約者一覧の確認、状態変更、削除、予約不可枠の一括設定ができます。
- 予約者の氏名・連絡先・要望は、認証済みの管理者用APIからのみ取得され、公開カレンダーには表示されません。

Apps Scriptを更新した場合は、ウェブアプリを再デプロイして最新コードを反映してください。

### 本番反映クイック手順（最短）

1. [google-apps-script/Code.gs](google-apps-script/Code.gs) を Apps Script に貼り付けて保存
2. Apps Script を「既存ウェブアプリの新バージョン」で再デプロイ
3. Render の Environment を確認
	- `GOOGLE_APPS_SCRIPT_URL`
	- `GOOGLE_APPS_SCRIPT_SECRET`
	- `EDITOR_PASSWORD`
4. Render を再デプロイ
5. 公開ページ [lesson/index.html](lesson/index.html) で予約1件を送信して確認
	- 受付状態が「調整中」
	- 自動返信メールが届く
	- メールの件名・本文・予約者名が日本語で正しく表示される
	- 該当時間枠が選択不可表示になる

### 動作確認コマンド集（順番実行）

事前に環境変数を設定してください。

```bash
export BASE_URL='https://namegawa-brass-lab.onrender.com'
export EDITOR_PASSWORD='ここに編集用パスワード'
```

1. 予約枠状態の取得（公開API）

```bash
curl -sS "${BASE_URL}/api/lesson-slot-statuses?from=2026-08-20&to=2026-08-20" | cat
```

2. 予約枠を「予約済」に一括反映（管理API）

```bash
curl -sS -X POST "${BASE_URL}/api/lesson-slot-statuses/admin" \
  -H 'Content-Type: application/json' \
  -H "X-Editor-Password: ${EDITOR_PASSWORD}" \
  -d '{"start_date":"2026-08-20","end_date":"2026-08-20","start_time":"09:00","end_time":"10:00","status":"予約済","note":"動作確認"}' | cat
```

3. 反映確認（公開API）

```bash
curl -sS "${BASE_URL}/api/lesson-slot-statuses?from=2026-08-20&to=2026-08-20" | cat
```

4. 予約状態更新（既存予約番号がある場合）

```bash
curl -sS -X PUT "${BASE_URL}/api/lesson-reservations/R-20260810-004" \
  -H 'Content-Type: application/json' \
  -H "X-Editor-Password: ${EDITOR_PASSWORD}" \
  -d '{"status":"確認中","message":"動作確認のため更新"}' | cat
```

5. ローカル単体テスト

```bash
.venv/bin/python -m unittest discover -s tests -v
```

### 段階別の実装ガイド（順番に確認しながら進める用）

ここでは、今回追加した機能を同じ順番で再実装できるように分解しています。

1. Flaskの入力検証とAPIを追加

- 変更ファイル: [app.py](app.py)
- 追加内容:
	- 枠状態更新用の入力検証を追加
	- 予約作成APIのレスポンスへ以下を追加
		- 予約状態
		- 自動返信送信結果
	- 枠状態取得APIを追加
		- GET /api/lesson-slot-statuses
	- 枠状態管理APIを追加
		- POST /api/lesson-slot-statuses/admin
- 確認ポイント:
	- 予約APIが 201 を返す
	- 枠状態取得APIが slots 配列を返す
	- 管理APIが更新件数を返す

2. Apps Scriptの保存処理とメール自動返信を追加

- 変更ファイル: [google-apps-script/Code.gs](google-apps-script/Code.gs)
- 追加内容:
	- 予約枠状態シートを自動作成
	- 予約作成時の状態を調整中で保存
	- 希望日時へ調整中を自動反映
	- 受付確認メールを自動送信
	- 枠状態の取得アクションを追加
	- 枠状態の期間一括更新アクションを追加
- 確認ポイント:
	- スプレッドシートに レッスン予約 と 予約枠状態 が作成される
	- 予約送信後に状態が調整中になる
	- 返信メールが届く

3. スケジュールページで枠状態反映と管理者UIを追加

- 変更ファイル: [schedule/index.html](schedule/index.html)、[lesson/index.html](lesson/index.html)
- 追加内容:
	- 月間カレンダーと日別の空き時間を公開
	- 選択日時を予約フォームへ引き継ぎ
	- 管理者パネルを追加
		- 予約者一覧、状態変更、削除
		- 期間
		- 時間帯
		- 状態（予約済 / お休み）
	- 入力補助ボタンとクライアント側の事前バリデーションを追加
- 確認ポイント:
	- 状態付きの時間ラベルが表示される
	- 利用不可枠が選べない
	- 管理者パネルから一括反映できる

4. テストを追加して回帰を防ぐ

- 変更ファイル: [tests/test_updates.py](tests/test_updates.py)
- 追加内容:
	- 枠状態APIのOPTIONS確認
	- 枠状態管理APIの認証確認
	- 枠状態管理APIの更新成功確認
	- 予約APIレスポンス（状態と自動返信フラグ）の確認
- 実行コマンド:
	- .venv/bin/python -m unittest discover -s tests -v

5. 本番反映

- 手順:
	1. [google-apps-script/Code.gs](google-apps-script/Code.gs) を保存
	2. Apps Script を再デプロイ
	3. Render の環境変数を確認
	4. Render を再デプロイ
	5. 予約送信と管理API更新を実地確認

この順番で進めると、どこで問題が起きたかを切り分けしやすくなります。

### 運用開始前の最終チェック表

以下を上から順に確認してください。すべてOKなら運用開始できます。

1. Apps Script

- [ ] [google-apps-script/Code.gs](google-apps-script/Code.gs) が最新内容
- [ ] スクリプトプロパティ `SPREADSHEET_ID` が設定済み
- [ ] スクリプトプロパティ `API_SECRET` が設定済み
- [ ] ウェブアプリを再デプロイ済み
- [ ] `doPost` 実行時の権限承認（Gmail送信権限含む）を完了

2. Render

- [ ] `GOOGLE_APPS_SCRIPT_URL` が最新デプロイURL
- [ ] `GOOGLE_APPS_SCRIPT_SECRET` が `API_SECRET` と一致
- [ ] `EDITOR_PASSWORD` が設定済み
- [ ] 再デプロイ成功（サービスが起動している）

3. API動作

- [ ] `GET /api/lesson-slot-statuses` が200を返す
- [ ] `POST /api/lesson-slot-statuses/admin` が200を返す
- [ ] 反映後の再取得で `予約済` または `お休み` が確認できる

4. 画面動作

- [ ] [lesson/index.html](lesson/index.html) で該当日を選ぶと状態付き時間が表示される
- [ ] `調整中` / `予約済` / `お休み` が選択不可になる
- [ ] 管理者パネルで更新件数が表示される

5. 予約フロー

- [ ] 予約送信で受付状態が `調整中` になる
- [ ] 自動返信メールが届く
- [ ] スプレッドシートへ予約行が追加される
- [ ] `予約枠状態` シートへ該当枠が反映される
- [x] 同一内容の2回目予約で `duplicate: true` と同一 `reservation_id` が返る（本番確認済み）

6. ローカル回帰テスト

- [ ] `.venv/bin/python -m unittest discover -s tests -v` が成功

### 本番実予約1件の最終確認フロー

運用開始前に、実際の予約を1件だけ流して全経路を確認します。

1. 事前変数を設定

```bash
export BASE_URL='https://namegawa-brass-lab.onrender.com'
export CHECK_DATE='2026-08-20'
export EDITOR_PASSWORD='ここに編集用パスワード'
```

2. 枠状態の反映前を取得

```bash
curl -sS "${BASE_URL}/api/lesson-slot-statuses?from=${CHECK_DATE}&to=${CHECK_DATE}" | cat
```

3. 管理APIで任意の時間帯を予約済へ反映

```bash
curl -sS -X POST "${BASE_URL}/api/lesson-slot-statuses/admin" \
  -H 'Content-Type: application/json' \
  -H "X-Editor-Password: ${EDITOR_PASSWORD}" \
  -d "{\"start_date\":\"${CHECK_DATE}\",\"end_date\":\"${CHECK_DATE}\",\"start_time\":\"09:00\",\"end_time\":\"10:00\",\"status\":\"予約済\",\"note\":\"最終確認\"}" | cat
```

4. 枠状態の反映後を再取得

```bash
curl -sS "${BASE_URL}/api/lesson-slot-statuses?from=${CHECK_DATE}&to=${CHECK_DATE}" | cat
```

5. 画面で確認

- [lesson/index.html](lesson/index.html) 相当の本番ページで `CHECK_DATE` を選択
- `09:00-10:00` の時間帯が `予約済` 表示かつ選択不可であることを確認

6. 実予約を1件送信

- 本番の予約フォームから1件送信
- 送信後に以下を確認
	- 受付状態が `調整中`
	- 自動返信メールを受信
	- スプレッドシート `レッスン予約` に行追加
	- スプレッドシート `予約枠状態` に該当枠が反映

7. 必要に応じて後片付け

- 最終確認用に追加した `予約済` 枠を戻す場合は、同じ管理APIで `status` を `お休み` または運用状態に合わせて再反映

### 障害時の即応メモ（対応順）

障害時は下記の順で切り分けると復旧が早くなります。

1. Apps Script 側の確認

- [google-apps-script/Code.gs](google-apps-script/Code.gs) が最新か
- スクリプトプロパティ `SPREADSHEET_ID` / `API_SECRET` があるか
- ウェブアプリ再デプロイ漏れがないか
- `doPost` の Gmail 権限承認が完了しているか

2. Render 側の確認

- `GOOGLE_APPS_SCRIPT_URL` が最新URLか
- `GOOGLE_APPS_SCRIPT_SECRET` が `API_SECRET` と一致しているか
- `EDITOR_PASSWORD` が期待値か
- 再デプロイ済みか

3. API 疎通確認

- `GET /api/lesson-slot-statuses` を実行して 200 を確認
- `POST /api/lesson-slot-statuses/admin` を実行して 200 を確認
- エラー時はレスポンス本文の `error` / `delivery_error` を確認

4. 典型エラーと対処

- `401`: 管理APIの `X-Editor-Password` 不一致
- `503`: 環境変数不足（URL/SECRET未設定）
- `502`: Apps Script 側で拒否、またはレスポンス不正
- メール未着: Gmail 権限未承認、または送信先アドレス不正

### 本番の毎朝1分チェック

本番APIの最低限の生存確認を、データを汚さずに実行できます。

手動実行:

```bash
./healthcheck-prod.sh
```

チェック内容:

- `GET /api/lesson-slot-statuses` が `200` を返す
- `POST /api/lesson-reservations` をハニーポット項目付きで送信し、`saved=true` を返す
- 失敗時は任意でWebhook通知（Slack/Discord等）を送信できる

`BASE_URL` と `CHECK_DATE` は必要に応じて上書きできます。

```bash
BASE_URL='https://namegawa-brass-lab.onrender.com' CHECK_DATE='2026-08-10' ./healthcheck-prod.sh
```

失敗通知（任意）:

```bash
HEALTHCHECK_NOTIFY_WEBHOOK='https://hooks.slack.com/services/XXX/YYY/ZZZ' \
HEALTHCHECK_NOTIFY_MENTION='@channel' \
./healthcheck-prod.sh
```

自動実行（macOS launchd）:

```bash
chmod +x ./manage-healthcheck-launchd.sh
./manage-healthcheck-launchd.sh install
./manage-healthcheck-launchd.sh status
```

時刻を変える例（毎日 07:30 実行）:

```bash
HEALTHCHECK_HOUR=7 HEALTHCHECK_MINUTE=30 ./manage-healthcheck-launchd.sh install
```

失敗通知付きで登録する例:

```bash
HEALTHCHECK_NOTIFY_WEBHOOK='https://hooks.slack.com/services/XXX/YYY/ZZZ' \
HEALTHCHECK_NOTIFY_MENTION='@channel' \
./manage-healthcheck-launchd.sh install
```

停止・削除:

```bash
./manage-healthcheck-launchd.sh uninstall
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