#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-https://namegawa-brass-lab.onrender.com}"
CHECK_DATE="${CHECK_DATE:-$(date +%F)}"
HEALTHCHECK_NOTIFY_WEBHOOK="${HEALTHCHECK_NOTIFY_WEBHOOK:-}"
HEALTHCHECK_NOTIFY_MENTION="${HEALTHCHECK_NOTIFY_MENTION:-}"

json_escape() {
  local text="$1"
  text=${text//\\/\\\\}
  text=${text//\"/\\\"}
  text=${text//$'\n'/\\n}
  printf '%s' "$text"
}

notify_failure() {
  local message="$1"
  if [[ -z "$HEALTHCHECK_NOTIFY_WEBHOOK" ]]; then
    return
  fi

  local prefix=""
  if [[ -n "$HEALTHCHECK_NOTIFY_MENTION" ]]; then
    prefix="${HEALTHCHECK_NOTIFY_MENTION} "
  fi

  local payload
  payload=$(printf '{"text":"%s"}' "$(json_escape "${prefix}[HP Healthcheck] ${message}")")

  curl -sS -X POST "$HEALTHCHECK_NOTIFY_WEBHOOK" \
    -H 'Content-Type: application/json' \
    -d "$payload" >/dev/null || true
}

pass() {
  printf '[PASS] %s\n' "$1"
}

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  notify_failure "$1"
  exit 1
}

# Usage: request METHOD URL [DATA]
request() {
  local method="$1"
  local url="$2"
  local data="${3:-}"
  local body_file="${TMPDIR:-/tmp}/hp-healthcheck-$$.json"

  if [[ -n "$data" ]]; then
    if ! status_code=$(curl -sS -o "$body_file" -w '%{http_code}' -X "$method" "$url" -H 'Content-Type: application/json' -d "$data"); then
      fail "Network error on ${method} ${url}"
    fi
  else
    if ! status_code=$(curl -sS -o "$body_file" -w '%{http_code}' -X "$method" "$url"); then
      fail "Network error on ${method} ${url}"
    fi
  fi

  response_body=$(cat "$body_file")
  rm -f "$body_file"
}

printf 'Healthcheck target: %s\n' "$BASE_URL"
printf 'Check date: %s\n' "$CHECK_DATE"

# 1) Public slot status API should return 200 and JSON with slots.
request "GET" "${BASE_URL}/api/lesson-slot-statuses?from=${CHECK_DATE}&to=${CHECK_DATE}"
if [[ "$status_code" != "200" ]]; then
  fail "GET /api/lesson-slot-statuses returned HTTP ${status_code}: ${response_body}"
fi
if [[ "$response_body" != *'"slots"'* ]]; then
  fail "GET /api/lesson-slot-statuses body missing slots field: ${response_body}"
fi
pass "GET /api/lesson-slot-statuses"

# 2) Non-destructive reservation probe via honeypot field.
request "POST" "${BASE_URL}/api/lesson-reservations" '{"website":"healthcheck-probe"}'
if [[ "$status_code" != "201" ]]; then
  fail "POST /api/lesson-reservations probe returned HTTP ${status_code}: ${response_body}"
fi
if [[ "$response_body" != *'"saved":true'* ]]; then
  fail "POST /api/lesson-reservations probe did not return saved=true: ${response_body}"
fi
pass "POST /api/lesson-reservations (non-destructive probe)"

printf 'All checks passed.\n'
