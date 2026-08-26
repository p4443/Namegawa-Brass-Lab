# Stripe・Render 自動販売設定

## 販売構成

- 個人版：500円、1ユーザー（税込設定はStripeの商品設定に合わせます）
- 会社版：50,000円、会社管理者を含め最大100ユーザー（税込設定はStripeの商品設定に合わせます）
- 決済：Stripe Payment Links
- アプリ運用：Render
- ライセンス納品：Stripe Webhookで自動発行し、Resendからメール送信
- 領収書：Stripeの領収書メールと、ライセンスメール内の領収書リンク
- 適格請求書発行事業者登録番号：`T2810320517878`
- 消費税率：10%（税込価格、税額は1請求書・税率ごとに端数切捨て）

Payment LinkはWebサイトを用意せず、X、Instagram、Facebook、LINE、メール、QRコードなどへ直接掲載できます。

## 1. Resendの準備

1. Resendでアカウントを作成します。
2. 販売に使用する独自ドメインを登録し、案内されたDNSレコードを設定します。
3. ドメインの認証完了後、API Keyを発行します。
4. 送信元を `点呼確認簿 <license@あなたのドメイン>` の形式で決めます。

迷惑メール対策のため、購入者への自動送信にフリーメールの送信元は使用しません。

## 2. Stripeの商品と価格

Stripe Dashboardをテストモードにして、次の2商品を作成します。

| 商品名 | 支払い | 金額 | 通貨 |
| --- | --- | ---: | --- |
| 点呼確認簿 個人版（1ユーザー） | 1回限り | 500 | JPY |
| 点呼確認簿 会社版（最大100ユーザー） | 1回限り | 50,000 | JPY |

それぞれの価格詳細に表示される `price_` から始まる価格IDを控えます。商品の説明には、買い切り、利用範囲、サポート範囲、返金条件を明記します。

廃止した価格IDはRenderや文書へ残さず、Stripe Dashboardで有効な価格IDだけをRenderへ設定します。

本番モードへ切り替える際は商品と価格を本番モードで作り直し、この価格IDを本番用IDへ置き換えます。

## 3. Renderの環境変数

Render DashboardのWeb Serviceで次を設定します。

| 環境変数 | 設定値 |
| --- | --- |
| `STRIPE_SECRET_KEY` | Stripeのテスト用シークレットキー。公開後は本番キー |
| `STRIPE_PERSONAL_PRICE_ID` | 個人版の価格ID |
| `STRIPE_COMPANY_PRICE_ID` | 会社版の価格ID |
| `RESEND_API_KEY` | Resendで発行したAPI Key |
| `LICENSE_EMAIL_FROM` | `点呼確認簿 <license@あなたのドメイン>` |
| `PUBLIC_APP_URL` | Renderで公開したアプリのHTTPS URL |

`LICENSE_ENCRYPTION_KEY` はBlueprintが自動生成します。一度運用を開始した後は値を変更しないでください。変更すると、送信失敗後に保存済みキーを再送できなくなります。

設定後、次のURLを開いて `{"status":"ok"}` が返ることを確認します。不足がある場合は、秘密値そのものではなく未設定の環境変数名だけが表示されます。

```text
https://あなたのRender URL/api/sales/health
```

## 4. Stripe Webhook

Stripe WorkbenchのWebhook設定で次の送信先を追加します。

```text
https://あなたのRender URL/api/stripe/webhook
```

対象イベントは次の2件です。

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`

Webhook作成後に表示される `whsec_` から始まる署名シークレットを、Renderの `STRIPE_WEBHOOK_SECRET` に設定して再デプロイします。

## 5. Payment Link

個人版と会社版それぞれにPayment Linkを作成します。

- 購入者のメールアドレスを必須にします。
- プロモーションコードは、必要になるまで無効にします。
- 購入数量は1に固定します。
- 利用規約への同意を必須にします。
- 商品名、個人版または会社版の利用範囲、税込総額、買い切りであることを決済画面に表示します。
- 購入前に本番URLの「特定商取引法に基づく表記」と返金条件を確認できるよう、Payment Linkの商品説明または案内元へ `https://tennko-kakuninnbo.onrender.com/legal-notice` を掲載します。
- テスト決済画面で、事業者名、連絡先、販売価格、商品代金以外の費用、支払時期、提供時期、動作環境、返品・キャンセル条件へ購入確定前に到達できることを確認します。
- 決済後の案内に「ライセンスキーをメールで送信しました」と表示します。

Stripe Dashboardの「顧客へのメール」で、支払い成功時の領収書メールを有効にします。

## 6. テスト購入

1. StripeとRenderをテスト用キー・価格IDで設定します。
2. Payment LinkからStripeのテストカード `4242 4242 4242 4242` で購入します。
3. Stripeで支払い成功とWebhook成功を確認します。
4. 購入者メールへライセンスキーと領収書リンクが届くことを確認します。
5. 届いたキーで個人名または会社名を新規登録します。
6. 同じキーを別アカウントの初回登録へ再利用できないことを確認します。
7. 登録済みユーザーがメールアドレスとパスワードで再ログインでき、別の対応端末からもログインできることを確認します。

## 7. 本番公開

Stripeの事業者確認、銀行口座、特定商取引法に基づく表記、返金方針を確認します。その後、本番モードで商品、価格、Payment Link、Webhookを作り直し、RenderのStripe設定を本番値へ変更します。

SNSへはStripeのPayment Linkを直接掲載します。短縮URLを使う場合も、購入者が遷移先のStripeドメインを確認できるサービスを使用してください。

## 障害時

メール送信に失敗するとWebhookはエラーになり、Stripeが再試行します。同じCheckout Sessionでは新しいキーを発行せず、最初に発行したキーを再送します。Stripe DashboardでWebhookが失敗している場合は、Resendのドメイン認証、API Key、送信元、Renderログを確認してから再送します。
