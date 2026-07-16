#!/bin/bash
# Installs/updates this project's cron entries using the path from
# install.ini, so the schedule always points at the current checkout
# rather than a path hardcoded in a committed file.
set -euo pipefail
cd "$(dirname "$0")"
source ./project_dir.sh

DAILY_LINE="20 3 * * * $PROJECT_DIR/run_bot.sh"
WEEKLY_LINE="40 3 * * 0 $PROJECT_DIR/run_weekly_report.sh"

TMP_CRON="$(mktemp)"
trap 'rm -f "$TMP_CRON"' EXIT

(crontab -l 2>/dev/null | grep -v -E '/(run_bot|run_weekly_report)\.sh($|[[:space:]])'; true) > "$TMP_CRON"
printf '%s\n%s\n' "$DAILY_LINE" "$WEEKLY_LINE" >> "$TMP_CRON"

crontab "$TMP_CRON"
echo "Installed cron entries:"
echo "  $DAILY_LINE"
echo "  $WEEKLY_LINE"
