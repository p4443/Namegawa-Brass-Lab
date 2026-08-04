# HTTPS ローカル開発用手順

## 1. 証明書の確認

生成済みの証明書は以下です。

- certs/cert.pem
- certs/key.pem

## 2. macOS で信頼する方法

1. キーチェインアクセスを開く
2. 「システム」キーチェインを選択
3. 生成した certs/cert.pem をドラッグ＆ドロップ
4. 追加後、証明書を開いて「常に信頼」に変更

## 3. ブラウザで確認

- HTTP: http://localhost:8080
- HTTPS: https://localhost:8443
