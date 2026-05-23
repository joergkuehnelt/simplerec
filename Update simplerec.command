#!/usr/bin/env bash
# =============================================================================
#  simplerec — Updater
#  Downloads the latest version from GitHub and re-runs the installer.
#  Just double-click in Finder.
# =============================================================================

set -euo pipefail

BOLD="\033[1m"; RESET="\033[0m"
GREEN="\033[32m"; YELLOW="\033[33m"; CYAN="\033[36m"; RED="\033[31m"

info()    { echo -e "${CYAN}${BOLD}[•]${RESET} $*"; }
success() { echo -e "${GREEN}${BOLD}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}${BOLD}[!]${RESET} $*"; }
error()   { echo -e "${RED}${BOLD}[✗]${RESET} $*"; echo; read -r -p "Press ENTER to close …"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL="https://github.com/joergkuehnelt/simplerec/archive/refs/heads/main.zip"
TMP_ZIP="/tmp/simplerec_update_$$.zip"
TMP_DIR="/tmp/simplerec_update_$$"

clear
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║         simplerec  —  Updater                ║"
echo "  ║   Downloads latest version from GitHub       ║"
echo "  ║   then re-runs the installer                 ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${RESET}"

# ── 1. Download ──────────────────────────────────────────────────────────────
info "Downloading latest simplerec from GitHub …"
# Note: the ZIP is downloaded over HTTPS from GitHub. No additional checksum
# verification is performed. If you require supply-chain integrity, verify the
# SHA-256 of the downloaded file against a known-good value before proceeding.
if ! curl -fsSL "$REPO_URL" -o "$TMP_ZIP"; then
    error "Download failed. Please check your internet connection."
fi
success "Download complete."

# ── 2. Extract ───────────────────────────────────────────────────────────────
info "Extracting …"
mkdir -p "$TMP_DIR"
if ! unzip -q "$TMP_ZIP" -d "$TMP_DIR"; then
    error "Could not unzip the downloaded file."
fi
EXTRACTED="$TMP_DIR/simplerec-main"
if [[ ! -d "$EXTRACTED" ]]; then
    error "Unexpected archive structure — expected folder 'simplerec-main' inside ZIP."
fi
success "Extracted."

# ── 3. Clean old app files, then copy fresh from download ────────────────────
info "Removing old app files from: $SCRIPT_DIR …"
# Delete known app file types so files renamed or removed in the new release
# don't linger. User data (recordings, photos, playlists) lives in a separate
# output folder and is not touched.
find "$SCRIPT_DIR" -maxdepth 1 \( -name "*.py" -o -name "*.command" -o -name "*.md" \) -delete
success "Old files removed."

info "Installing fresh files into: $SCRIPT_DIR …"
if command -v rsync &>/dev/null; then
    rsync -a --exclude='.git' "$EXTRACTED/" "$SCRIPT_DIR/"
else
    cp -R "$EXTRACTED/." "$SCRIPT_DIR/"
fi
success "Files installed."

# ── 4. Make .command files executable ────────────────────────────────────────
chmod +x "$SCRIPT_DIR/"*.command 2>/dev/null || true

# ── 5. Clean up temp files ────────────────────────────────────────────────────
rm -rf "$TMP_ZIP" "$TMP_DIR"

# ── 6. Launch installer ───────────────────────────────────────────────────────
echo
echo -e "  ${GREEN}${BOLD}Update complete — starting installer …${RESET}"
echo
sleep 1

INSTALLER="$SCRIPT_DIR/Install simplerec.command"
if [[ ! -f "$INSTALLER" ]]; then
    error "Could not find 'Install simplerec.command' after update."
fi

# Run the installer in the same terminal window (exec keeps the TTY intact)
exec bash "$INSTALLER"
