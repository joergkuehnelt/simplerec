#!/usr/bin/env bash
# simplerec — start (double-click in Finder to launch)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Re-launch inside iTerm2 when available (and not already running in it) ───
# $ITERM_SESSION_ID is set by iTerm2 in every shell it spawns.
if [[ -z "$ITERM_SESSION_ID" && -d "/Applications/iTerm.app" ]]; then
    # Choose the right Homebrew init for this arch
    if [[ "$(uname -m)" == "arm64" ]]; then
        BREW_INIT='eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true'
    else
        BREW_INIT='eval "$(/usr/local/bin/brew shellenv)" 2>/dev/null || true'
    fi
    # Write a self-contained runner — avoids all AppleScript quoting hazards
    RUNNER=$(mktemp /tmp/simplerec_runner_XXXXXX.sh)
    cat > "$RUNNER" <<RUNNER_EOF
#!/usr/bin/env bash
$BREW_INIT
cd $(printf '%q' "$SCRIPT_DIR")
python3 simplerec.py
echo
printf 'Recording finished. Press ENTER to close...'
read -r _x
rm -f "$RUNNER"
RUNNER_EOF
    chmod +x "$RUNNER"
    osascript \
        -e 'tell application "iTerm2"' \
        -e '  activate' \
        -e '  set newWin to (create window with default profile)' \
        -e "  tell current session of newWin to write text \"bash $RUNNER\"" \
        -e 'end tell'
    exit 0  # Terminal closes this window on clean exit
fi

# ── Normal execution (Terminal fallback or already inside iTerm2) ─────────────
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
