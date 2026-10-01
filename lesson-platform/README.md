# kazooトランペット教室 運営ポータル

現行の `https://namegawa-brass-lab.com/lesson/` を維持したまま、教室の予約・出欠・連絡を管理するNext.jsアプリです。公開候補は `https://portal.namegawa-brass-lab.com` です。商品・アプリ販売はこのポータルの対象外とし、既存ストアからも技術的・運用的に分離します。

## 設計方針

- 公開ページはNext.js App Routerで生成し、滑川町・比企郡の検索意図に合わせたmetadata、構造化データ、sitemapを出力
- 保護者はLIFFからLINE Loginし、IDトークンをサーバー側のLINE公式APIで検証
- Supabaseのservice roleはサーバーとEdge Functionだけで使用し、公開DBアクセスはRLSで拒否
- ポータルの予約APIは公式予約APIへ登録し、成功した予約だけをSupabaseへ同期
- 公式ホームページの公開予約APIから、予約件数と空き枠だけを5分間隔で読み取り同期
- 公式予約APIが利用できない場合は予約を確定せず、ポータル内にエラーを表示
- 料金徴収、商品販売、アプリ配布、Stripe連携は扱わない

## ローカル起動

Node.js 20.9以降を用意し、次を実行します。

```bash
cp .env.example .env.local
npm install
npm run dev
```

`.env.local`へ秘密値を保存し、Gitには追加しないでください。`PORTAL_SESSION_SECRET`には32バイト以上のランダム値を使います。

## Supabase

1. Supabaseプロジェクトを作成します。
2. `supabase/migrations/202609260001_lesson_portal.sql`をSQL Editorで実行します。
3. Project URLを`SUPABASE_URL`、service role keyを`SUPABASE_SERVICE_ROLE_KEY`へ設定します。
4. `202609260003_official_booking_source.sql`までのmigrationを順番に実行します。

## LINE Login / LIFF

1. LINE DevelopersでLINE LoginチャネルとLIFFアプリを作成します。
2. Endpoint URLをポータルの公開URLへ設定します。
3. Channel IDを`LINE_CHANNEL_ID`、LIFF IDを`NEXT_PUBLIC_LINE_LIFF_ID`へ設定します。
4. LIFFのScopeは`openid`と`profile`だけに限定します。

Channel secretは本実装では使用せず、ブラウザから取得したIDトークンをLINEの検証APIで検証します。

## デプロイ順序

1. Supabase migrationを反映
2. LINEをテスト設定
3. Vercel等へ`lesson-platform`をRoot Directoryとしてプレビューデプロイ
4. `npm run typecheck && npm run lint && npm run build`を実行
5. モバイル実機でLINEログイン、予約前の空き枠確認、入力検証、予約控え画面を確認
6. `portal.namegawa-brass-lab.com`を割り当て

現行 `/lesson/`、Flask予約API、公式ホームページの案内やリンクは削除・変更しません。

## 公式ホームページとの境界

- 公式ホームページとポータルは別サービス・別ドメインでデプロイする
- ポータルの変更では、公式側の`app.py`、`lesson/`、予約データを変更しない
- ポータルは公式の公開`/api/lesson-calendar`と`/api/lesson-slot-statuses`から空き枠・予約件数だけを読み取る
- ポータルで確定する予約はポータルAPIから公式`/api/lesson-reservations`へ登録し、成功後だけSupabaseへ控えを保存する
- 表示対象は日別予約件数とレッスン種別ごとの空き枠数に限定し、予約者情報や予約内容は取得・表示しない
- 公式APIが停止しても公式予約システムへ影響を返さず、ポータル側だけで取得不可表示へ切り替える
