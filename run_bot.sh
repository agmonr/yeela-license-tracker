#!/bin/bash
source "$(dirname "$0")/project_dir.sh"
cd "$PROJECT_DIR"
source venv/bin/activate
python3 ./fetch_data.py
python3 ./notify_changes.py
./push_archive.sh
