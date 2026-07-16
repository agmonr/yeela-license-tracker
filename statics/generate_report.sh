#!/bin/bash
# Generates the weekly HTML dashboard and commits/pushes it on main.
# Run after push_archive.sh, since it needs that week's dated CSV
# snapshot to already be in archive/.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 statics/generate_dashboard.py

git add statics/reports
if git diff --cached --quiet; then
    echo "No report changes to commit."
    exit 0
fi

git commit -m "Weekly report $(date +%Y-%m-%d)"
git push origin main
