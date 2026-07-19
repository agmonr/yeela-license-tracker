#!/bin/bash
source "$(dirname "$0")/project_dir.sh"
cd "$PROJECT_DIR"
source venv/bin/activate
python3 ./weekly_report.py
./statics/generate_report.sh
