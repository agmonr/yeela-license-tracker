#!/bin/bash
# Snapshots archive/full_licenses_v*.csv (the rotating working files
# used by download_trees.py/weekly_report.py/notify_changes.py, which
# stay gitignored) into dated copies - e.g. full_licenses_2026-07-16.csv -
# and commits/pushes those on main. Date names fall outside the
# full_licenses_v*.csv gitignore pattern, so no .gitignore change is
# needed, and re-running with unchanged data is a safe no-op.
set -euo pipefail
cd "$(dirname "$0")"

shopt -s nullglob
files=(archive/full_licenses_v*.csv)
if [ ${#files[@]} -eq 0 ]; then
    echo "No archive CSVs found, nothing to commit."
    exit 0
fi

dated=()
for path in "${files[@]}"; do
    date_str=$(date -r "$path" +%Y-%m-%d)
    dest="archive/full_licenses_${date_str}.csv"
    cp "$path" "$dest"
    dated+=("$dest")
done

git add "${dated[@]}"
if git diff --cached --quiet; then
    echo "No new dated snapshots to commit."
    exit 0
fi

git commit -m "Archive snapshot $(date +%Y-%m-%d)"
git push origin main
