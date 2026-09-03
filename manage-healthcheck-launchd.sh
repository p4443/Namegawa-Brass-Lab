#!/usr/bin/env bash
set -euo pipefail

LABEL="com.namegawa.hp.healthcheck"
USER_ID="$(id -u)"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${REPO_DIR}/logs"
OUT_LOG="${LOG_DIR}/healthcheck.out.log"
ERR_LOG="${LOG_DIR}/healthcheck.err.log"

BASE_URL="${BASE_URL:-https://namegawa-brass-lab.com}"
HEALTHCHECK_HOUR="${HEALTHCHECK_HOUR:-8}"
HEALTHCHECK_MINUTE="${HEALTHCHECK_MINUTE:-0}"
HEALTHCHECK_NOTIFY_WEBHOOK="${HEALTHCHECK_NOTIFY_WEBHOOK:-}"
HEALTHCHECK_NOTIFY_MENTION="${HEALTHCHECK_NOTIFY_MENTION:-}"

sq() {
  local value="$1"
  printf "%s" "$value" | sed "s/'/'\\\\''/g"
}

render_plist() {
  local env_args="BASE_URL='$(sq "${BASE_URL}")'"
  if [[ -n "${HEALTHCHECK_NOTIFY_WEBHOOK}" ]]; then
    env_args+=" HEALTHCHECK_NOTIFY_WEBHOOK='$(sq "${HEALTHCHECK_NOTIFY_WEBHOOK}")'"
  fi
  if [[ -n "${HEALTHCHECK_NOTIFY_MENTION}" ]]; then
    env_args+=" HEALTHCHECK_NOTIFY_MENTION='$(sq "${HEALTHCHECK_NOTIFY_MENTION}")'"
  fi

  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>${env_args} '${REPO_DIR}/healthcheck-prod.sh'</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${REPO_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${HEALTHCHECK_HOUR}</integer>
    <key>Minute</key>
    <integer>${HEALTHCHECK_MINUTE}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${OUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${ERR_LOG}</string>
</dict>
</plist>
EOF
}

install_job() {
  mkdir -p "${LAUNCH_AGENTS_DIR}" "${LOG_DIR}"
  render_plist > "${PLIST_PATH}"

  launchctl bootout "gui/${USER_ID}" "${PLIST_PATH}" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/${USER_ID}" "${PLIST_PATH}"
  launchctl enable "gui/${USER_ID}/${LABEL}" >/dev/null 2>&1 || true
  launchctl kickstart -k "gui/${USER_ID}/${LABEL}" >/dev/null 2>&1 || true

  echo "Installed: ${PLIST_PATH}"
  echo "Schedule: daily ${HEALTHCHECK_HOUR}:$(printf '%02d' "${HEALTHCHECK_MINUTE}")"
  echo "Logs: ${OUT_LOG} / ${ERR_LOG}"
}

uninstall_job() {
  launchctl bootout "gui/${USER_ID}" "${PLIST_PATH}" >/dev/null 2>&1 || true
  rm -f "${PLIST_PATH}"
  echo "Removed: ${PLIST_PATH}"
}

status_job() {
  if launchctl print "gui/${USER_ID}/${LABEL}" >/dev/null 2>&1; then
    echo "Status: loaded"
    echo "Label: ${LABEL}"
    echo "Plist: ${PLIST_PATH}"
    echo "Logs: ${OUT_LOG} / ${ERR_LOG}"
  else
    echo "Status: not loaded"
    echo "Plist: ${PLIST_PATH}"
    return 1
  fi
}

show_usage() {
  cat <<EOF
Usage:
  ./manage-healthcheck-launchd.sh install
  ./manage-healthcheck-launchd.sh uninstall
  ./manage-healthcheck-launchd.sh status
  ./manage-healthcheck-launchd.sh print

Environment variables:
  BASE_URL             default: https://namegawa-brass-lab.com
  HEALTHCHECK_HOUR     default: 8
  HEALTHCHECK_MINUTE   default: 0
  HEALTHCHECK_NOTIFY_WEBHOOK   optional: webhook URL for failure alerts
  HEALTHCHECK_NOTIFY_MENTION   optional: text prefix like @channel
EOF
}

command="${1:-}"
case "${command}" in
  install)
    install_job
    ;;
  uninstall)
    uninstall_job
    ;;
  status)
    status_job
    ;;
  print)
    render_plist
    ;;
  *)
    show_usage
    exit 1
    ;;
esac
