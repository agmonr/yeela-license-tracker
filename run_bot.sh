#!/bin/bash
cd /home/ram/scripts/yeela 
source venv/bin/activate
python3 ./fetch_data.py
python3 ./notify_changes.py
