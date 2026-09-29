# HTTPS ローカル開発用手順

## 1. 証明書の生成

リポジトリ直下で次を実行し、端末ごとの証明書を生成します。

```bash
./generate-local-cert.sh
```

次のファイルはGit管理されません。

- certs/cert.pem
- certs/key.pem

## 2. macOS で信頼する方法

次のスクリプトを実行します。macOSの管理者パスワードが必要です。

```bash
./trust-local-cert.sh
```

## 3. ブラウザで確認

- HTTP: <http://localhost:8080>
- HTTPS: <https://localhost:8443>
