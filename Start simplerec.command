#!/usr/bin/env bash
# simplerec — start (double-click in Finder to launch)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure Homebrew Python is on PATH
if [[ "$(uname -m)" == "arm64" ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
else
    eval "$(/usr/local/bin/brew shellenv)" 2>/dev/null || true
fi

# If iTerm2 is installed, open a new window there and close this Terminal window.
_iterm_check=$(osascript -e 'tell application "Finder" to return exists application file id "com.googlecode.iterm2"' 2>/dev/null)
if [[ "$_iterm_check" == "true" ]]; then
    osascript - "$SCRIPT_DIR" <<'APPLESCRIPT'
on run argv
    set scriptDir to item 1 of argv
    tell application "iTerm2"
        activate
        set newWin to (create window with default profile)
        tell current session of newWin
            write text "cd " & quoted form of scriptDir & " && python3 simplerec.py; echo; printf 'Recording finished. Press ENTER to close...'; read -r _REPLY"
        end tell
    end tell
end run
APPLESCRIPT
    exit 0  # Terminal closes this window on clean exit
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
