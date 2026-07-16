#!/bin/bash
# Sourced by the run_*.sh cron entry points to resolve PROJECT_DIR from
# install.ini, so the absolute deployment path (which contains the
# local username) never has to be hardcoded in a file committed to git.
INI_FILE="$(dirname "${BASH_SOURCE[0]}")/install.ini"

if [ ! -f "$INI_FILE" ]; then
    echo "Missing $INI_FILE - run ./install.sh first." >&2
    exit 1
fi

PROJECT_DIR=$(awk -F'=' '
    /^\[project\]/ { in_section=1; next }
    /^\[/ { in_section=0 }
    in_section && $1 ~ /^[ \t]*dir[ \t]*$/ {
        val=$2
        gsub(/^[ \t]+|[ \t]+$/, "", val)
        print val
        exit
    }
' "$INI_FILE")

if [ -z "$PROJECT_DIR" ]; then
    echo "Could not read [project] dir from $INI_FILE" >&2
    exit 1
fi
