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

env_value() {
  "$PYTHON" -c 'import sys; from dotenv import dotenv_values; print(dotenv_values(sys.argv[1]).get(sys.argv[2], "") or "")' "$ENV_FILE" "$1"
}

save_env_value() {
  ENV_UPDATE_KEY="$1" ENV_UPDATE_VALUE="$2" "$PYTHON" -c '
import os
import sys
from pathlib import Path
from dotenv import set_key

path = Path(sys.argv[1])
path.touch(mode=0o600, exist_ok=True)
set_key(
    str(path),
    os.environ["ENV_UPDATE_KEY"],
    os.environ["ENV_UPDATE_VALUE"],
    quote_mode="always",
)
path.chmod(0o600)
' "$ENV_FILE"
}

if [[ ! -x "$PYTHON" ]]; then
  fail ".venvが見つかりません。先に python3 -m venv .venv && .venv/bin/pip install -r requirements.txt を実行してください。"
fi

current_stripe_secret_key="$(env_value STRIPE_SECRET_KEY)"
current_stripe_webhook_secret="$(env_value STRIPE_WEBHOOK_SECRET)"
current_stripe_price_id="$(env_value STRIPE_METRONOME_PRICE_ID)"
current_flow_harmony_price_id="$(env_value STRIPE_FLOW_HARMONY_PRICE_ID)"
current_invoice_registration_number="$(env_value INVOICE_REGISTRATION_NUMBER)"
current_download_token_secret="$(env_value DOWNLOAD_TOKEN_SECRET)"
current_public_site_url="$(env_value PUBLIC_SITE_URL)"
current_editor_password="$(env_value EDITOR_PASSWORD)"
store_api_url="${STORE_API_URL:-https://namegawa-brass-lab.onrender.com}"
if [[ "$current_stripe_secret_key" == sk_live_* ]]; then
  default_mode_selection="2"
else
  default_mode_selection="1"
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
read_visible stripe_mode_selection "番号 [$default_mode_selection]: "
stripe_mode_selection="${stripe_mode_selection:-$default_mode_selection}"
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

print_header "1/8 Stripe秘密鍵"
print -r -- "Stripe Dashboardの「開発者」→「APIキー」で取得します。
入力する値は ${secret_key_prefix} から始まります。
現在と同じモードなら、Enterだけで既存値を保持できます。"
read_secret stripe_secret_key "STRIPE_SECRET_KEY（Enterで既存値を保持）: "
if [[ -z "$stripe_secret_key" && "$current_stripe_secret_key" == ${secret_key_prefix}* ]]; then
  stripe_secret_key="$current_stripe_secret_key"
  printf 'Stripe秘密鍵: 既存値を保持しました。\n'
else
  printf 'Stripe秘密鍵: 新しい入力値を使用します。\n'
fi
[[ "$stripe_secret_key" == ${secret_key_prefix}* ]] || fail "Stripe秘密鍵は${secret_key_prefix}から始まる値を入力してください。"
if [[ "${STORE_SKIP_STRIPE_VALIDATION:-}" != "1" ]]; then
  printf 'Stripe秘密鍵の有効性を確認しています...\n'
  if ! PYTHONWARNINGS="ignore" STRIPE_SECRET_KEY_VALUE="$stripe_secret_key" "$PYTHON" -c '
import os
import sys

import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY_VALUE"]
try:
    stripe.Price.list(limit=1)
except Exception as exc:
    print(f"Stripe秘密鍵エラー: {exc.user_message or exc}", file=sys.stderr)
    raise SystemExit(1)
'; then
    fail "入力したStripe秘密鍵を利用できません。Stripe Dashboardで新しく作成したテスト用シークレットキーを入力してください。"
  fi
  printf 'Stripe秘密鍵: 有効です。\n'
  save_env_value "STRIPE_SECRET_KEY" "$stripe_secret_key"
  printf '有効なStripe秘密鍵を保存しました。\n'
fi

print_header "2/8 メトロノーム Stripe Price ID"
print -r -- 'Stripe Dashboardの「商品カタログ」で、500円・JPY・1回払いの価格を開いて取得します。
入力する値は price_ から始まります。商品ID（prod_）ではありません。'
read_visible stripe_price_id "STRIPE_METRONOME_PRICE_ID（Enterで既存値を保持）: "
stripe_price_id="${stripe_price_id:-$current_stripe_price_id}"
[[ "$stripe_price_id" == price_* ]] || fail "Price IDはprice_から始まる値を入力してください。"

print_header "3/8 Trumpet Transpose Lab Stripe Price ID"
print -r -- 'Stripe Dashboardの「商品カタログ」で、1000円・JPY・1回払いの価格を開いて取得します。
入力する値は price_ から始まります。メトロノームとは別のPrice IDを指定してください。'
read_visible flow_harmony_price_id "STRIPE_FLOW_HARMONY_PRICE_ID（Enterで既存値を保持）: "
flow_harmony_price_id="${flow_harmony_price_id:-$current_flow_harmony_price_id}"
[[ "$flow_harmony_price_id" == price_* ]] || fail "Trumpet Transpose LabのPrice IDはprice_から始まる値を入力してください。"
[[ "$flow_harmony_price_id" != "$stripe_price_id" ]] || fail "Trumpet Transpose Labにはメトロノームとは別のPrice IDを入力してください。"

print_header "4/8 Webhook署名シークレット"
print -r -- '通常は何も入力せずEnterを押してください。
Webhookが未登録なら、このウィザードが自動作成します。
すでに作成済みの場合だけ、whsec_から始まる署名シークレットを入力します。'
read_secret stripe_webhook_secret "STRIPE_WEBHOOK_SECRET（通常はEnter）: "
stripe_webhook_secret="${stripe_webhook_secret:-$current_stripe_webhook_secret}"
if [[ -n "$stripe_webhook_secret" && "$stripe_webhook_secret" != whsec_* ]]; then
  fail "Webhook署名シークレットはwhsec_から始まる値を入力してください。"
fi

print_header "5/8 適格請求書発行事業者登録番号"
print -r -- 'Stripeが発行する請求書へ記載する登録番号を入力します。
Tに続けて13桁の数字を入力してください。'
read_visible invoice_registration_number "INVOICE_REGISTRATION_NUMBER [$current_invoice_registration_number]: "
invoice_registration_number="${invoice_registration_number:-$current_invoice_registration_number}"
[[ "$invoice_registration_number" =~ '^T[0-9]{13}$' ]] || fail "登録番号はTに続けて13桁の数字で入力してください。"

print_header "6/8 公開サイトURL"
print -r -- '購入画面が表示されるサイトの公開ベースURLを入力します。
例: https://example.com
GitHub Pagesのプロジェクトサイト例: https://user.github.io/repository
末尾の /、/lesson/、?以降は入力しません。'
read_visible public_site_url "PUBLIC_SITE_URL [$current_public_site_url]: "
public_site_url="${public_site_url:-$current_public_site_url}"
[[ "$public_site_url" == https://* ]] || fail "公開サイトURLはhttps://から入力してください。"
[[ "$public_site_url" != */ ]] || fail "公開サイトURL末尾の/を削除してください。"
[[ "$public_site_url" != *\?* && "$public_site_url" != *\#* ]] || fail "公開サイトURLにクエリや#を含めないでください。"
[[ "$store_api_url" == https://* && "$store_api_url" != */ ]] || fail "STORE_API_URLは末尾の/を除いたHTTPS URLにしてください。"

print_header "7/8 管理者パスワード"
print -r -- '販売ON/OFFと診断APIで使う管理者専用パスワードです。
推測されにくい16文字以上を新しく決めて入力してください。'
read_secret editor_password "EDITOR_PASSWORD（Enterで既存値を保持）: "
editor_password="${editor_password:-$current_editor_password}"
(( ${#editor_password} >= 16 )) || fail "管理者パスワードは16文字以上にしてください。"

print_header "8/8 ダウンロード署名鍵"
print -r -- '24時間ダウンロードURLの改ざん防止に使う内部秘密値です。
Enterだけ押すと、安全なランダム値を自動生成します。
通常は自動生成を選んでください。'
read_secret download_token_secret "DOWNLOAD_TOKEN_SECRET（Enterで既存値を保持または自動生成）: "
if [[ -z "$download_token_secret" ]]; then
  if [[ -n "$current_download_token_secret" ]]; then
    download_token_secret="$current_download_token_secret"
    printf '既存値を保持しました。\n'
  else
    download_token_secret="$($PYTHON -c 'import secrets; print(secrets.token_urlsafe(48))')"
    printf '安全なランダム値を生成しました。\n'
  fi
fi
(( ${#download_token_secret} >= 32 )) || fail "ダウンロード署名鍵は32文字以上にしてください。"

print_header "Stripe接続確認"
printf 'APIキー、500円Price、1000円Price、Webhook登録を確認しています...\n'
if [[ "${STORE_SKIP_STRIPE_VALIDATION:-}" != "1" ]]; then
  stripe_validation_result=""
  if ! stripe_validation_result="$(PYTHONWARNINGS="ignore" STRIPE_SECRET_KEY_VALUE="$stripe_secret_key" \
  STRIPE_PRICE_ID_VALUE="$stripe_price_id" \
  STRIPE_FLOW_HARMONY_PRICE_ID_VALUE="$flow_harmony_price_id" \
  STRIPE_EXPECTED_MODE="$stripe_mode" \
  PUBLIC_SITE_URL_VALUE="$public_site_url" \
  STORE_API_URL_VALUE="$store_api_url" \
    STRIPE_WEBHOOK_SECRET_VALUE="$stripe_webhook_secret" \
  "$PYTHON" -c '
import os
import sys

import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY_VALUE"]
try:
  metronome_price = stripe.Price.retrieve(os.environ["STRIPE_PRICE_ID_VALUE"])
  flow_harmony_price = stripe.Price.retrieve(
    os.environ["STRIPE_FLOW_HARMONY_PRICE_ID_VALUE"]
  )
  expected_live = os.environ["STRIPE_EXPECTED_MODE"] == "live"
  def price_is_valid(price, expected_amount):
    return all((
      price.active is True,
      price.type == "one_time",
      price.currency == "jpy",
      price.unit_amount == expected_amount,
      price.livemode is expected_live,
    ))
  metronome_price_valid = price_is_valid(metronome_price, 500)
  flow_harmony_price_valid = price_is_valid(flow_harmony_price, 1000)
  target = os.environ["STORE_API_URL_VALUE"] + "/api/store/webhook"
  endpoints = stripe.WebhookEndpoint.list(limit=100).data
  endpoint = next((item for item in endpoints if item.url == target), None)
  if endpoint is None:
    endpoint = stripe.WebhookEndpoint.create(
      url=target,
      enabled_events=["checkout.session.completed"],
    )
    generated_secret = endpoint.secret
  else:
    generated_secret = ""
  webhook_valid = bool(endpoint.status == "enabled" and (
    "checkout.session.completed" in endpoint.enabled_events
    or "*" in endpoint.enabled_events
  ))
except Exception as exc:
  print(f"Stripe接続エラー: {exc.user_message or exc}", file=sys.stderr)
  raise SystemExit(1)
if not metronome_price_valid:
  print("Stripe Priceが500円・JPY・一回払い・同一モードではありません。", file=sys.stderr)
  raise SystemExit(1)
if not flow_harmony_price_valid:
  print("Trumpet Transpose LabのStripe Priceが1000円・JPY・一回払い・同一モードではありません。", file=sys.stderr)
  raise SystemExit(1)
if not webhook_valid:
  print(f"Webhookを有効化できませんでした: {target}", file=sys.stderr)
  raise SystemExit(1)
if not generated_secret and not os.environ["STRIPE_WEBHOOK_SECRET_VALUE"].startswith("whsec_"):
  print("既存Webhookの署名シークレットが必要です。", file=sys.stderr)
  raise SystemExit(1)
print(generated_secret)
')"; then
  fail "Stripe設定を確認できないため保存を中止しました。上の案内を修正して再実行してください。"
  fi
  if [[ "$stripe_validation_result" == whsec_* ]]; then
    stripe_webhook_secret="$stripe_validation_result"
    save_env_value "STRIPE_WEBHOOK_SECRET" "$stripe_webhook_secret"
    printf 'Webhookを自動作成し、署名シークレットを設定しました。\n'
  fi
fi
printf 'Stripe接続確認: OK\n'

print_header "保存前の確認"
printf 'モード                 : %s\n' "$stripe_mode"
printf 'メトロノーム販売価格   : 500円（JPY・1回払い）\n'
printf 'Trumpet Transpose Lab販売価格: 1000円（JPY・1回払い）\n'
printf '適格請求書発行事業者登録番号: %s\n' "$invoice_registration_number"
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
STRIPE_FLOW_HARMONY_PRICE_ID_VALUE="$flow_harmony_price_id" \
INVOICE_REGISTRATION_NUMBER_VALUE="$invoice_registration_number" \
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
  "STRIPE_FLOW_HARMONY_PRICE_ID": os.environ["STRIPE_FLOW_HARMONY_PRICE_ID_VALUE"],
  "INVOICE_REGISTRATION_NUMBER": os.environ["INVOICE_REGISTRATION_NUMBER_VALUE"],
  "DOWNLOAD_TOKEN_SECRET": os.environ["DOWNLOAD_TOKEN_SECRET_VALUE"],
  "METRONOME_PRICE_YEN": "500",
  "FLOW_HARMONY_PRICE_YEN": "1000",
  "PUBLIC_SITE_URL": os.environ["PUBLIC_SITE_URL_VALUE"],
  "EDITOR_PASSWORD": os.environ["EDITOR_PASSWORD_VALUE"],
}
for key, value in values.items():
  set_key(str(path), key, value, quote_mode="always")
path.chmod(0o600)
' "$ENV_FILE"

unset stripe_secret_key stripe_webhook_secret stripe_price_id flow_harmony_price_id
unset invoice_registration_number
unset download_token_secret editor_password

print_header "保存完了"
print -r -- '.envへ設定を保存しました（ファイル権限: 管理者のみ読み書き）。

次の作業:
1. ローカル確認: .venv/bin/flask --app app run --host 127.0.0.1 --port 5001
2. Render DashboardのEnvironmentへ、.envと同名の10項目を登録
3. Renderを再デプロイ
4. /api/store/health がready=trueになることを確認
5. テストモードで決済確認後にだけ本番モードへ切り替え

重要: 販売ONはproduction_ready=trueを確認してから実行してください。'