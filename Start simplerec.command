#!/usr/bin/env bash
# simplerec — start (double-click in Finder to launch)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure Homebrew Python is on PATH
if [[ "$(uname -m)" == "arm64" ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
else
    eval "$(/usr/local/bin/brew shellenv)" 2>/dev/null || true
fi

cd "$SCRIPT_DIR"

if [[ ! -f "simplerec.py" ]]; then
    echo "Error: simplerec.py not found in $SCRIPT_DIR"
    read -r -p "Press ENTER to close …"
    exit 1
fi

python3 simplerec.py "$@"

echo
read -r -p "Recording finished. Press ENTER to close …"
