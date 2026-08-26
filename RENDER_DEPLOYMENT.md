# Render本番公開手順

## 構成

- Render Web Service: Starter、Singapore、1インスタンス
- Docker: Node.js 22
- 永続ディスク: `/data`、5GB
- SQLite: `/data/tennko.sqlite`
- ヘルスチェック: `/api/health`
- デプロイ: 手動実行

SQLiteと永続ディスクを使用するため、複数インスタンス化はできません。デプロイ時には数秒の停止が発生します。

## 公開準備

1. このプロジェクトを非公開のGitHubリポジトリへ登録します。
2. RenderへGitHubアカウントを接続します。
3. Render Dashboardで `New > Blueprint` を選びます。
4. 非公開リポジトリを選び、ルートの `render.yaml` を適用します。
5. デプロイ完了後、`https://<サービス名>.onrender.com/api/health` が `{"status":"ok"}` を返すことを確認します。
6. Stripe・Resend設定後、`https://<サービス名>.onrender.com/api/sales/health` も `{"status":"ok"}` を返すことを確認します。

ソースを購入者へ渡さないため、GitHubリポジトリとRenderワークスペースは販売者だけがアクセスできる状態にしてください。

## 初回ライセンス発行

Render Dashboardの対象サービスで `Shell` を開きます。

```sh
npm run --silent license:create
```

表示されたキーは一度しか確認できません。パスワード管理ツールへ記録し、購入者へ本番URLと一緒に送付します。

## バックアップ

アプリは起動時と24時間ごとにSQLiteオンラインバックアップを作り、`/data/backups` に新しい14件を保持します。各バックアップは作成直後にSQLite整合性検査を通します。

Renderは永続ディスクを24時間ごとに暗号化スナップショットしますが、SQLite復旧時はディスク全体ではなく `/data/backups` 内のDBバックアップを使用します。月1回以上、Render SSHとSCPを使って最新バックアップを販売者の暗号化された別保管先へコピーしてください。

```sh
scp -s <RenderのSSH接続先>:/data/backups/<バックアップ名>.sqlite ./
```

## 更新

1. ローカルで修正します。
2. Dockerビルドと主要操作を確認します。
3. 非公開GitHubへ反映します。
4. Renderの `Manual Deploy > Deploy latest commit` を実行します。
5. ヘルスチェック、ログイン、記録、添付、印刷を確認します。

`autoDeployTrigger: off` のため、GitHubへ反映しただけでは本番更新されません。

## 公開前チェック

- [ ] [利用規約](TERMS_OF_USE.md)の角括弧を実情報へ置換
- [ ] [プライバシーポリシー](PRIVACY_POLICY.md)へRender、Singapore、保存期間を明記
- [ ] [購入者ガイド](BUYER_GUIDE.md)へ本番URLと連絡先を記載
- [ ] 独自ドメインとHTTPSを確認
- [ ] 初回ライセンスキーを発行
- [ ] `/api/sales/health` が正常
- [ ] Stripeテスト決済でライセンスメールと領収書を受信
- [ ] バックアップをSCPで別保管先へ取得
- [ ] 障害時の連絡先と復旧担当を決定