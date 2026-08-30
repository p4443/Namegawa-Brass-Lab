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

## イベント企画PDFの管理

`/pdf/` の「管理者モード」から `EDITOR_PASSWORD` でログインすると、PDF内に表示されるタイトルを指定して15MB以内のPDFを追加できます。公開URLには安全なASCIIファイル名が自動発行され、PDF本体と表示タイトルは `data/event-pdfs` に保存されます。Renderでは `/app/data` の永続ディスク、Docker Composeではホストの `data` ボリュームに保存されます。

削除は対象確認とファイル名再入力の二段階です。完了時は公開領域、アップロード領域、設定されたサーバー複製にある同名PDFを削除し、永続削除記録によって再起動や再デプロイ後の再表示・再配信も防止します。

## 契約書の保存先

管理者用の契約書作成画面で保存した契約書は、このPCの `~/Documents/なめがわブラス・ラボ/契約書管理` に部門別で保存されます。画面の表示・保存・読込・削除には `EDITOR_PASSWORD` が必要です。削除時は確認後、契約書IDの再入力が必要です。削除が完了すると、PC側の保存先とサーバー内の旧保存先にある同一契約IDのデータをまとめて削除します。

保存先を変更する場合は `.env` に次を設定してサイトを再起動します。

```text
CONTRACTS_DIR=/保存先の絶対パス
```

## Renderで投稿を永続保存する

RenderのWeb Serviceは再起動時にコンテナ内のファイルが初期化されるため、公開サイトではPostgreSQLを使用します。

1. Render Dashboardの「New +」から「PostgreSQL」を作成します。
2. 作成したデータベースの「Internal Database URL」をコピーします。
3. `namegawa-brass-lab` Web Serviceの「Environment」を開きます。
4. Keyを`DATABASE_URL`、ValueをコピーしたURLとして追加します。
5. 「Save, rebuild, and deploy」を実行します。

初回接続時にテーブルが自動作成され、`data/updates.txt`の既存情報が取り込まれます。以後の追加・変更・削除はデータベースへ保存されます。

`render.yaml`にはDocker Web Service、`/health`による死活監視、必要な環境変数が定義されています。新規環境ではRender Dashboardの「New +」→「Blueprint」からこのリポジトリを接続します。既存のWeb Serviceでは、Blueprintを新しく作成せず、後述の環境変数を現在のServiceへ登録してください。

## 練習アプリのStripe販売

練習アプリのWeb版は無料公開し、Stripe決済後にオフライン版ZIPを24時間ダウンロードできます。各アプリの販売状態は、商品ごとの「管理者用：販売設定」から`EDITOR_PASSWORD`を使って個別に切り替えます。メトロノームの初期値はOFF、Trumpet Transpose Labの未設定時の初期値はONです。

Trumpet Transpose Labは無料Web版で全機能を公開し、オフライン版のみ1,000円で販売します。商品ページは設定と商品ZIPの検証が通った場合だけ購入ボタンを有効にします。既存環境との互換性のため、Renderでは次の環境変数名を継続利用します。

```text
STRIPE_FLOW_HARMONY_PRICE_ID=price_...
FLOW_HARMONY_PRICE_YEN=1000
```

デプロイ後は`/trumpet-transpose-lab/`がHTTP 200で表示され、`/products/`の無料版リンクと埋め込みが動作することを確認します。旧`/flow-harmony/`は新URLへリダイレクトされます。オフライン版の購入ボタンはStripeのPriceを既存ストアと同じテスト・本番モードへ揃えた場合だけ有効になります。

### 管理者向け・説明付き設定ウィザード

ターミナルで次のコマンドを実行すると、取得場所と入力例を1項目ずつ表示し、決済設定を`.env`へ安全に保存します。秘密鍵とパスワードは入力中も画面に表示されません。

```bash
cd /Users/kazuuu/hp
./setup-store-env.sh
```

事前にStripe Dashboardで次の画面を開いておくと、入力がスムーズです。

- 「開発者」→「APIキー」: `sk_test_`または`sk_live_`から始まる秘密鍵
- 「商品カタログ」: 500円・JPY・1回払いの`price_`から始まるPrice ID
- 「商品カタログ」: Trumpet Transpose Lab用の1,000円・JPY・1回払いの別のPrice ID
- 「開発者」→「Webhook」: `/api/store/webhook`送信先の`whsec_`から始まる署名シークレット

初回はウィザードの「1: テストモード」を選択してください。入力形式が正しくない場合や、本番・テストの鍵を取り違えた場合は保存前に停止します。ローカルの`.env`へ保存した後、同じ変数名をRender DashboardのEnvironmentにも登録してください。秘密値そのものはREADME、HTML、Git、チャットへ貼り付けないでください。

1. Stripe Dashboardで商品を作成し、1回払い500円の価格を登録します。
2. 発行された`price_`から始まるPrice IDを控えます。
3. StripeのWebhookへ`https://公開APIのドメイン/api/store/webhook`を登録し、`checkout.session.completed`を購読します。
4. RenderのEnvironmentへ次の値を登録して再デプロイします。

```text
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_METRONOME_PRICE_ID=price_...
STRIPE_FLOW_HARMONY_PRICE_ID=price_...
DOWNLOAD_TOKEN_SECRET=十分に長いランダム文字列
METRONOME_PRICE_YEN=500
FLOW_HARMONY_PRICE_YEN=1000
PUBLIC_SITE_URL=https://ホームページの公開ベースURL
```

`PUBLIC_SITE_URL`には購入ページを配信するHTTPSの公開ベースURLを指定し、末尾の`/`、`/lesson/`、クエリ、フラグメントは付けないでください。GitHub Pagesのプロジェクトサイトでは、例として`https://user.github.io/repository`のようにリポジトリ名まで含めます。Stripeの秘密鍵・Webhook secret・Price IDはすべて同じテストモードまたは本番モードの値を組み合わせます。

ストアAPIのCORSは`PUBLIC_SITE_URL`のオリジンとAPI自身のオリジンだけを許可します。

設定ウィザードはWebhookを既定で`https://namegawa-brass-lab.onrender.com/api/store/webhook`へ登録します。APIドメインを変更した場合だけ、`STORE_API_URL=https://新しいAPIドメイン ./setup-store-env.sh`のように実行してください。

`DOWNLOAD_TOKEN_SECRET`は次のコマンドで生成できます。秘密値はHTMLやリポジトリへ保存しないでください。

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

商品ZIPは[build_product.py](build_product.py)で生成され、Dockerビルド時にも自動更新されます。

```bash
.venv/bin/python build_product.py
```

同一購入セッションの支払い確認は30秒間キャッシュし、再発行集中時のStripe API照会を抑えながら返金・紛争を短時間で反映します。Checkout作成にはUUID v4の冪等キーを使用し、通信再試行による重複Sessionを防ぎます。Checkout開始直前にはStripe Priceが有効な一回払い・500円・JPY・同一モードであることを確認します。商品提供時と各ダウンロード時には、購入時価格・JPY・PaymentIntent成功・Charge捕捉済み・未返金・紛争なし・Stripeモード一致を再検証します。自動再発行は購入後30日以内に限定し、ダウンロードURL自体にはStripeのセッションIDを含めません。商品配信はRangeリクエストによる途中再開と1時間のブラウザ内キャッシュに対応し、Gunicornは2ワーカー×4スレッドで同時処理します。ダウンロード前にはZIPのCRCと必須ファイルを検査し、破損商品は配信しません。商品生成は一時ファイルから原子的に置き換えるため、ビルド途中のZIPが公開されることもありません。商品サイズが大きくなった場合は、アプリサーバー配信ではなく署名付きURLを利用できるオブジェクトストレージ/CDNへ移行してください。

デプロイ後は販売OFFのまま、最初にStripeテストモードで診断APIとテストカード決済を確認します。

```bash
curl -sS 'https://公開APIのドメイン/api/store/health' \
	-H 'X-Editor-Password: 編集用パスワード' | python -m json.tool
```

テストモードでは`ready: true`かつ`production_ready: false`が正常です。互換用の診断キー`checks.flow_harmony_configuration`、`checks.flow_harmony_product_archive`、`checks.flow_harmony_stripe_price`もすべて`true`になることを確認します。テスト用カードで2商品をそれぞれ決済し、Trumpet Transpose Labでは`trumpet-transpose-lab-offline.zip`をダウンロードできることを確認してください。その後RenderとStripe Webhookを本番値へ切り替えて再デプロイし、診断APIが`stripe_mode: live`かつ`production_ready: true`になった場合だけ販売を開始します。

販売ON前の確認項目:

- Stripe DashboardのPriceが有効・一回払い・500円・JPYである
- Trumpet Transpose LabのPriceが有効・一回払い・1,000円・JPYで、メトロノームとは別のPrice IDである
- Webhookの送信先が`https://公開APIのドメイン/api/store/webhook`で、署名検証付きのテスト送信がHTTP 200になる
- 診断APIが`production_ready: true`を返す
- テストモードで正常決済、キャンセル、未払い、再ダウンロードを確認済みである
- 返金または紛争状態の決済から新規リンクを発行できず、既存リンクも最大30秒後に拒否される
- `healthcheck-prod.sh`へ`STORE_HEALTH_EDITOR_PASSWORD`を設定している

本番ヘルスチェックへストア診断を含める場合は、秘密値をコマンドライン引数へ直接書かず環境変数で渡します。

```bash
export STORE_HEALTH_EDITOR_PASSWORD='編集用パスワード'
./healthcheck-prod.sh
```

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

### B契約書のGoogle Maps距離測定

B契約前見積書の運賃計算画面では、Google Maps PlatformのRoutes APIから自動車ルートの走行距離と参考所要時間を取得できます。APIキーはブラウザへ送信せず、Flaskサーバーからのみ使用します。

1. Google Cloud Consoleで請求先アカウントを設定し、対象プロジェクトの`Routes API`を有効化します。
2. APIキーを作成し、APIの制限を`Routes API`だけに設定します。
3. 固定送信元IPを利用できる環境では、アプリケーションの制限をRender等の送信元IPアドレスに限定します。固定IPを利用できない場合も、API制限、予算アラート、割り当て上限を設定します。
4. RenderのEnvironmentへ`GOOGLE_MAPS_ROUTES_API_KEY`をSecretとして登録し、再デプロイします。ローカルでは次の環境変数を設定して再起動します。

```bash
export GOOGLE_MAPS_ROUTES_API_KEY='Google Cloudで発行したRoutes APIキー'
docker compose up -d --build
```

契約書作成画面でB契約前見積書を選び、「距離・時間から参考運賃を算出」から出発地・目的地を入力して距離を測定します。Googleの自動算出距離を片道参考値とし、その2倍を実車走行距離へ自動反映します。住所変更後は再測定が必要です。参考所要時間には荷役・待機時間が含まれないため、総拘束時間は案件条件に合わせて別途入力してください。入力途中の計算失敗では見積値を変更せず、発行後に入力を変えた場合は古いシートURLを無効扱いにして再発行を案内します。

### Brass-Logi輸送明細シート

契約書作成画面のB見積書では、`Code.gs` v29の`generate_transport_sheet`を使い、輸送対象物明細、運行計画・積卸し経路図、料金規定を同じGoogleスプレッドシートへ発行します。経路シートには出発地、目的地、Google自動算出距離、2倍した実車走行距離、拘束時間、積卸し条件、Googleマップへのリンクを記録します。生成ファイルは一般公開せず、画面で指定した共有先メールアドレスへ編集権限を追加します。輸送対象外品への同意、正数の品目値、確認済み料金表、Google距離測定の証跡がない要求はAPIとApps Scriptの両方で拒否します。

実案件は次の順序で処理します。

1. `案件受付（下書き）`で取引先、経路、品目を登録します。この段階の印刷物は「見積準備票」で、金額は未確定です。
2. `外部運送会社へ見積依頼中`へ進め、運送会社名、参考車両、楽器のメーカー・型番、再調達価格と出典を入力します。
3. 外部運送会社の正式見積書を受領後、共有URL、取得日、正式な見積明細を登録し、料金根拠を`外部運送会社の正式見積で確定`へ変更します。
4. 画面の不足項目がなくなったことを確認して`正式見積の発行準備完了`へ進めます。この状態だけが「御見積書」として表示されます。

国土交通省の「標準的な運賃」は参考額の確認にのみ使用します。標準運賃の計算結果を顧客への請求額として自動確定せず、外部運送会社が提示した正式見積を最終根拠にしてください。

軽貨物車（黒ナンバー・2t未満）を選択した場合は、「軽貨物の業界参考値を読み込む」から距離制、時間制、待機、荷役、休日・深夜割増の参考表を利用できます。料金改定時は`lightCargoReferenceRateHistory`へ適用開始日付きで追加し、見積作成日以前に適用開始されたデータのうち最新のものを使用します。保存済み見積は選択時点の料金スナップショットを保持するため、後日の改定で過去の見積額は変わりません。これは全国一律の法定運賃ではなく、画面に入力した基準日現在の業界相場です。利用前に出典URLと自社が運輸支局へ届け出た運賃を照合し、確認済みにしてください。2t以上の車両、積載上限を超える案件、外部委託案件の正式請求額には使用せず、運送会社の正式見積で確定します。

- `Code.gs`更新後は既存ウェブアプリを新バージョンで再デプロイしてください。
- 専用の保存先フォルダを使う場合は、スクリプトプロパティ`TRANSPORT_DOCUMENT_FOLDER_ID`へGoogleドライブのフォルダIDを設定します。未設定時は実行ユーザーのマイドライブ直下へ作成されます。
- 楽器の評価額は初期値ゼロです。案件ごとにメーカー・型番を確認し、再調達価格と出典を登録してください。
- 国土交通省の標準運賃は参考情報として保存します。固定値や参考計算値を正式請求額として扱わないでください。
- Google Mapsや市場価格APIの認証情報はリポジトリへ保存しません。経路距離と価格は、権限を持つ管理者が公表資料または契約済みAPIで確認して入力します。

- 予約受付時の状態は`確認中`として保存されます。
- 同時に、該当する希望日時の枠へ`調整中`が自動反映されます。
- 受付完了メール（自動返信）が送信されます。
- 同一メールアドレス・希望日・希望時間の予約が短時間（10分）で重複送信された場合は、既存受付を返して新規作成しません。

レッスン種別ごとの所要時間は次のとおりです。

- 体験レッスン・小学生: 30分（15分枠を2枠使用）
- 中学生: 45分（15分枠を3枠使用）
- 高校生以上・大人: 60分（15分枠を4枠使用）
- グループ・部活動指導: 予約時は「要相談」で受け付け、管理者が開始時刻と所要時間を15分単位で決定

空き状況は15分を1枠として管理し、各レッスンには所要時間分の連続した空き枠が必要です。開始時刻は、体験レッスン・小学生が毎時00分または30分、中学生・高校生以上・大人が毎時00分です。「要相談」の枠はこの制限にかかわらず個別調整します。

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

状態に指定できる値は `確認中` / `確定` / `キャンセル` です。管理画面では確定した予約者を折りたたみ一覧へ格納します。

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
- 土曜日と日曜日は予約者から「要相談」で受け付け、管理者は予約者一覧の時刻入力へ決定時刻を入れて状態と同時に更新できます。
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