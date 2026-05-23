#!/usr/bin/env bash
# simplerec — start (double-click in Finder to launch)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Re-launch inside iTerm2 when available (and not already running in it) ───
# $ITERM_SESSION_ID is set by iTerm2 in every shell it spawns.
# Check both /Applications and ~/Applications (common install locations).
_iterm_app=""
if   [[ -d "/Applications/iTerm.app" ]];       then _iterm_app="/Applications/iTerm.app"
elif [[ -d "$HOME/Applications/iTerm.app" ]];  then _iterm_app="$HOME/Applications/iTerm.app"
fi

if [[ -z "$ITERM_SESSION_ID" && -n "$_iterm_app" ]]; then
    # Choose the right Homebrew init line for this CPU architecture
    if [[ "$(uname -m)" == "arm64" ]]; then
        BREW_INIT='eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true'
    else
        BREW_INIT='eval "$(/usr/local/bin/brew shellenv)" 2>/dev/null || true'
    fi
    # Write a self-contained runner — avoids all AppleScript quoting hazards.
    # macOS mktemp requires X's at the END of the template (no suffix after X's).
    RUNNER=$(mktemp /tmp/simplerec_runner_XXXXXX)
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

    # Detect whether iTerm2 is already running BEFORE we activate it.
    # When iTerm2 is NOT running, `tell application to activate` launches it and
    # it opens its own default window automatically.  Sending Cmd+N at that point
    # would open a SECOND window — the extra empty window the user sees.
    # When iTerm2 IS already running, we DO need Cmd+N to get a fresh window.
    _iterm_already_open=false
    pgrep -x "iTerm2" > /dev/null 2>&1 && _iterm_already_open=true

    # Avoid ALL iTerm2-specific AppleScript (its dictionary causes -2741 errors).
    # Instead: activate the app by path, conditionally open a new window via
    # Cmd+N (only when iTerm2 was already running), then type the command using
    # System Events — no app dictionary needed.
    if osascript <<APPLESCRIPT
tell application "$_iterm_app"
    activate
end tell
-- Only open a new window when iTerm2 was already running.
-- If we just launched it, it already opened its default window; pressing Cmd+N
-- here would produce the unwanted second empty window.
if "$_iterm_already_open" is "true" then
    delay 0.8
    tell application "System Events"
        tell process "iTerm2"
            keystroke "n" using {command down}
        end tell
    end tell
    delay 1.2
else
    -- freshly launched — wait longer for the default window to become ready
    delay 2.5
end if
tell application "System Events"
    tell process "iTerm2"
        keystroke "bash $RUNNER"
        key code 36
    end tell
end tell
APPLESCRIPT
    then
        exit 0  # iTerm2 launched — Terminal closes this window on clean exit
    else
        # osascript failed — clean up and fall through to run in Terminal
        rm -f "$RUNNER"
    fi
fi

# ── Normal execution (Terminal.app or already inside iTerm2) ──────────────────
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
