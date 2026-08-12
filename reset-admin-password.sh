#!/bin/zsh
set -euo pipefail

ROOT_DIR="${0:A:h}"
ENV_FILE="$ROOT_DIR/.env"

read -s "new_password?新しい管理者パスワード: "
printf '\n'

if [[ -z "$new_password" ]]; then
  printf 'パスワードは空にできません。\n' >&2
  exit 1
fi

NEW_EDITOR_PASSWORD="$new_password" "$ROOT_DIR/.venv/bin/python" - "$ENV_FILE" <<'PY'
import os
import sys
from pathlib import Path
from dotenv import set_key

path = Path(sys.argv[1])
path.touch(exist_ok=True)
set_key(str(path), "EDITOR_PASSWORD", os.environ["NEW_EDITOR_PASSWORD"], quote_mode="always")
PY

unset new_password
printf '管理者パスワードを更新しました。サーバーを再起動してください。\n'
