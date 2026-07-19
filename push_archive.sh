#!/bin/bash
# Commits/pushes any new or changed dated snapshots in archive/
# (full_licenses_YYYY-MM-DD.csv, written directly by fetch_data.py) to
# main. Re-running with unchanged data is a safe no-op.
set -euo pipefail
cd "$(dirname "$0")"

git add archive/full_licenses_*.csv
if git diff --cached --quiet; then
    echo "No new dated snapshots to commit."
    exit 0
fi

git commit -m "Archive snapshot $(date +%Y-%m-%d)"
git push origin main
