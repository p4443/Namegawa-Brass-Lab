# HP Site

このディレクトリは静的サイトを Docker で表示するための構成です。

## 起動

```bash
docker compose up -d --build
```

## 停止

```bash
docker compose down
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
