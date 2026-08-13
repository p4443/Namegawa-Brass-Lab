#!/bin/zsh
set -euo pipefail

ROOT_DIR="${0:A:h}"
ENV_FILE="${STORE_ENV_FILE:-$ROOT_DIR/.env}"
PYTHON="$ROOT_DIR/.venv/bin/python"

print_header() {
  printf '\n============================================================\n'
  printf '%s\n' "$1"
  printf '============================================================\n'
}

fail() {
  printf '\n[入力エラー] %s\n' "$1" >&2
  exit 1
}

read_visible() {
  local variable_name="$1"
  local prompt="$2"
  local value
  read "value?$prompt"
  typeset -g "$variable_name=$value"
}

read_secret() {
  local variable_name="$1"
  local prompt="$2"
  local value
  read -s "value?$prompt"
  printf '\n'
  typeset -g "$variable_name=$value"
}

if [[ ! -x "$PYTHON" ]]; then
  fail ".venvが見つかりません。先に python3 -m venv .venv && .venv/bin/pip install -r requirements.txt を実行してください。"
fi

print_header "Stripe販売設定ウィザード"
print -r -- 'この画面は、決済に必要な値を .env へ保存します。
秘密鍵とWebhook署名シークレットは入力中も画面に表示されません。

注意:
- 最初は必ず「テストモード」で動作確認してください。
- 本番値とテスト値を混ぜないでください。
- .envをGitへ追加したり、秘密値をチャットへ貼り付けないでください。'

printf '\n設定するモードを選択してください。\n'
printf '  1: テストモード（最初はこちら）\n'
printf '  2: 本番モード（テスト決済確認後のみ）\n'
read_visible stripe_mode_selection "番号 [1]: "
stripe_mode_selection="${stripe_mode_selection:-1}"
case "$stripe_mode_selection" in
  1)
    stripe_mode="test"
    secret_key_prefix="sk_test_"
    ;;
  2)
    stripe_mode="live"
    secret_key_prefix="sk_live_"
    ;;
  *)
    fail "モードは1または2を入力してください。"
    ;;
esac

print_header "1/6 Stripe秘密鍵"
print -r -- "Stripe Dashboardの「開発者」→「APIキー」で取得します。
入力する値は ${secret_key_prefix} から始まります。"
read_secret stripe_secret_key "STRIPE_SECRET_KEY: "
[[ "$stripe_secret_key" == ${secret_key_prefix}* ]] || fail "Stripe秘密鍵は${secret_key_prefix}から始まる値を入力してください。"

print_header "2/6 Stripe Price ID"
print -r -- 'Stripe Dashboardの「商品カタログ」で、500円・JPY・1回払いの価格を開いて取得します。
入力する値は price_ から始まります。商品ID（prod_）ではありません。'
read_visible stripe_price_id "STRIPE_METRONOME_PRICE_ID: "
[[ "$stripe_price_id" == price_* ]] || fail "Price IDはprice_から始まる値を入力してください。"

print_header "3/6 Webhook署名シークレット"
print -r -- 'Stripe Dashboardの「開発者」→「Webhook」で次の送信先を作成します。
  https://あなたのRenderドメイン/api/store/webhook
送信先の「署名シークレットを表示」から取得します。
入力する値は whsec_ から始まります。'
read_secret stripe_webhook_secret "STRIPE_WEBHOOK_SECRET: "
[[ "$stripe_webhook_secret" == whsec_* ]] || fail "Webhook署名シークレットはwhsec_から始まる値を入力してください。"

print_header "4/6 公開サイトURL"
print -r -- '購入画面が表示されるサイトのオリジンを入力します。
例: https://example.com
末尾の /、/lesson/などのパス、?以降は入力しません。'
read_visible public_site_url "PUBLIC_SITE_URL: "
[[ "$public_site_url" == https://* ]] || fail "公開サイトURLはhttps://から入力してください。"
[[ "$public_site_url" != */ ]] || fail "公開サイトURL末尾の/を削除してください。"
[[ "$public_site_url" != *\?* && "$public_site_url" != *\#* ]] || fail "公開サイトURLにクエリや#を含めないでください。"

print_header "5/6 管理者パスワード"
print -r -- '販売ON/OFFと診断APIで使う管理者専用パスワードです。
推測されにくい16文字以上を新しく決めて入力してください。'
read_secret editor_password "EDITOR_PASSWORD（16文字以上）: "
(( ${#editor_password} >= 16 )) || fail "管理者パスワードは16文字以上にしてください。"

print_header "6/6 ダウンロード署名鍵"
print -r -- '24時間ダウンロードURLの改ざん防止に使う内部秘密値です。
Enterだけ押すと、安全なランダム値を自動生成します。
通常は自動生成を選んでください。'
read_secret download_token_secret "DOWNLOAD_TOKEN_SECRET（Enterで自動生成）: "
if [[ -z "$download_token_secret" ]]; then
  download_token_secret="$($PYTHON -c 'import secrets; print(secrets.token_urlsafe(48))')"
  printf '安全なランダム値を生成しました。\n'
fi
(( ${#download_token_secret} >= 32 )) || fail "ダウンロード署名鍵は32文字以上にしてください。"

print_header "保存前の確認"
printf 'モード                 : %s\n' "$stripe_mode"
printf '販売価格               : 500円（JPY・1回払い）\n'
printf '公開サイト             : %s\n' "$public_site_url"
printf 'Stripe秘密値           : 入力済み（非表示）\n'
printf '管理者パスワード       : 入力済み（非表示）\n'
printf '保存先                 : %s\n' "$ENV_FILE"
printf '\n上記を保存しますか？ y と入力した場合だけ保存します。\n'
read_visible confirmation "確認 [y/N]: "
[[ "$confirmation" == "y" || "$confirmation" == "Y" ]] || fail "保存を中止しました。"

STRIPE_SECRET_KEY_VALUE="$stripe_secret_key" \
STRIPE_WEBHOOK_SECRET_VALUE="$stripe_webhook_secret" \
STRIPE_PRICE_ID_VALUE="$stripe_price_id" \
DOWNLOAD_TOKEN_SECRET_VALUE="$download_token_secret" \
PUBLIC_SITE_URL_VALUE="$public_site_url" \
EDITOR_PASSWORD_VALUE="$editor_password" \
"$PYTHON" -c '
import os
import sys
from pathlib import Path
from dotenv import set_key

path = Path(sys.argv[1])
path.touch(mode=0o600, exist_ok=True)
values = {
  "STRIPE_SECRET_KEY": os.environ["STRIPE_SECRET_KEY_VALUE"],
  "STRIPE_WEBHOOK_SECRET": os.environ["STRIPE_WEBHOOK_SECRET_VALUE"],
  "STRIPE_METRONOME_PRICE_ID": os.environ["STRIPE_PRICE_ID_VALUE"],
  "DOWNLOAD_TOKEN_SECRET": os.environ["DOWNLOAD_TOKEN_SECRET_VALUE"],
  "METRONOME_PRICE_YEN": "500",
  "PUBLIC_SITE_URL": os.environ["PUBLIC_SITE_URL_VALUE"],
  "EDITOR_PASSWORD": os.environ["EDITOR_PASSWORD_VALUE"],
}
for key, value in values.items():
  set_key(str(path), key, value, quote_mode="always")
path.chmod(0o600)
' "$ENV_FILE"

unset stripe_secret_key stripe_webhook_secret download_token_secret editor_password

print_header "保存完了"
print -r -- '.envへ設定を保存しました（ファイル権限: 管理者のみ読み書き）。

次の作業:
1. ローカル確認: .venv/bin/flask --app app run --host 127.0.0.1 --port 5001
2. Render DashboardのEnvironmentへ、.envと同名の7項目を登録
3. Renderを再デプロイ
4. /api/store/health がready=trueになることを確認
5. テストモードで決済確認後にだけ本番モードへ切り替え

重要: 販売ONはproduction_ready=trueを確認してから実行してください。'