#!/usr/bin/env bash
# =============================================================================
#  simplerec — Installer
#  Doppelklick im Finder genügt. macOS öffnet diese Datei automatisch.
#  Supports: Intel (x86_64) and Apple Silicon (arm64)
# =============================================================================

set -euo pipefail

BOLD="\033[1m"; RESET="\033[0m"
GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"; RED="\033[31m"

info()    { echo -e "${CYAN}${BOLD}[•]${RESET} $*"; }
success() { echo -e "${GREEN}${BOLD}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}${BOLD}[!]${RESET} $*"; }
error()   { echo -e "${RED}${BOLD}[✗]${RESET} $*"; echo; read -r -p "Press ENTER to close …"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

clear
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║         simplerec  —  Installer              ║"
echo "  ║   macOS CLI Audio Recorder  (M4A / Stereo)   ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${RESET}"
echo "  What this installer does:"
echo "    1. Installs Homebrew  (macOS package manager)"
echo "    2. Installs Python 3"
echo "    3. Installs Python packages:"
echo "         sounddevice · soundfile · numpy · shazamio · psutil · asciichartpy"
echo "    4. Installs imagesnap  (DJ webcam photos: first after 5 min, then every 15 min — optional)"
echo "    5. Creates  [Start simplerec.command]"
echo "       → just double-click that file to record"
echo
echo -e "  ${YELLOW}Your password may be asked once for Homebrew.${RESET}"
echo
echo -e "  ${YELLOW}If macOS shows 'could not be verified' / Gatekeeper warning:${RESET}"
echo    "  Option A (System Settings): double-click → OK → System Settings"
echo    "            → Privacy & Security → scroll down → 'Open Anyway'"
echo    "  Option B (right-click):     right-click file → Open → Open"
echo    "  Option C (Terminal):        xattr -cr \"$(dirname \"$0\")\""
echo
read -r -p "  Press ENTER to start, or close this window to abort …"
echo

# ── Architecture ──────────────────────────────────────────────────────────────
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    info "Apple Silicon (arm64) detected"
else
    info "Intel (x86_64) detected"
fi

# ── Homebrew ──────────────────────────────────────────────────────────────────
echo
info "Checking Homebrew …"
if command -v brew &>/dev/null; then
    success "Homebrew is already installed."
else
    warn "Homebrew not found — installing now (this takes a few minutes) …"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ "$ARCH" == "arm64" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    success "Homebrew installed."
fi

# Ensure brew is on PATH for the rest of this script
if [[ "$ARCH" == "arm64" ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
else
    eval "$(/usr/local/bin/brew shellenv)" 2>/dev/null || true
fi

# ── Python 3 ─────────────────────────────────────────────────────────────────
echo
info "Checking Python 3 …"
if command -v python3 &>/dev/null; then
    success "Python 3 found: $(python3 --version)"
else
    warn "Installing Python 3 via Homebrew …"
    brew install python3
    success "Python 3 installed: $(python3 --version)"
fi
PY=$(command -v python3)

# ── ffmpeg is NOT needed — simplerec uses macOS built-in afconvert ────────────

# ── imagesnap (optional — for webcam snapshots every 15 min) ─────────────────
echo
info "Checking imagesnap …"
if command -v imagesnap &>/dev/null; then
    success "imagesnap already installed."
else
    warn "Installing imagesnap via Homebrew (optional – for webcam snapshots) …"
    brew install imagesnap
    success "imagesnap installed."
fi

# ── Python packages ───────────────────────────────────────────────────────────
echo
info "Installing Python packages …"
echo "  · sounddevice  — captures audio from your microphone"
echo "  · soundfile    — reads/writes audio files"
echo "  · numpy        — numerical processing for the audio stream"
echo "  · shazamio     — song recognition via Shazam (optional)"
echo "  · psutil       — CPU/RAM display in the UI (optional)"
echo
"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install sounddevice soundfile numpy shazamio psutil asciichartpy --upgrade --quiet
success "Python packages installed."

# ── Verify required Python scripts ──────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/simplerec.py" ]]; then
    error "simplerec.py not found in $SCRIPT_DIR — please run this installer from the project folder."
fi

# ── Create [Start simplerec.command] ─────────────────────────────────────────
echo
info "Creating start file …"

START="$SCRIPT_DIR/Start simplerec.command"

cat > "$START" << 'STARTSCRIPT'
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
    # Use single-command form: avoids "window" being parsed as a class name.
    if osascript <<APPLESCRIPT
tell application "iTerm2"
    activate
    create window with default profile command "bash $RUNNER"
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
STARTSCRIPT

chmod +x "$START"
success "Start file created."


# ── Remove quarantine flags ───────────────────────────────────────────────────
xattr -d com.apple.quarantine "$START" 2>/dev/null || true
xattr -d com.apple.quarantine "$0"     2>/dev/null || true

# ── Done ──────────────────────────────────────────────────────────────────────
echo
echo -e "${GREEN}${BOLD}  ════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}   Installation complete!${RESET}"
echo -e "${GREEN}${BOLD}  ════════════════════════════════════════════${RESET}"
echo
echo "  → Double-click  [Start simplerec.command]  to record."
echo
echo "  Features:"
echo "    · Stereo M4A recording  ·  Auto-gain  ·  Shazam song recognition"
echo "    · Webcam DJ photos: first after 5 min, then every 15 min  ·  Auto-update [U]"
echo "    · Each segment saved in its own YYYYMMDD-HHMM subfolder"
echo
echo "  If macOS blocks the file the first time:"
echo "    System Settings → Privacy & Security → Open Anyway"
echo
read -r -p "Press ENTER to close …"
