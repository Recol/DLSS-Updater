#!/bin/bash
# DLSS Updater - Flatpak Build Script
# Run this script in WSL2 or native Linux to build the Flatpak package

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get version from version.py
VERSION=$(grep -oP '__version__\s*=\s*"\K[^"]+' dlss_updater/version.py)
echo -e "${GREEN}Building DLSS Updater v${VERSION} Flatpak${NC}"

# Application ID for the GitHub-release bundle channel. The Flathub build uses a
# DIFFERENT id (io.github.recol.dlss_updater, underscore) and Flathub's own
# infrastructure - it never touches the repository below.
APP_ID="io.github.recol.dlss-updater"

# Origin remote baked into the bundle. `flatpak build-bundle --repo-url` makes
# installing the bundle configure this remote automatically, which is what gives
# `flatpak update` (and GNOME Software / KDE Discover background updates) a
# newer version to find. Without it an installed bundle has no origin at all and
# can never be updated in place - the user has to fetch each release by hand.
# Published from this repo by publish_flatpak_repo.sh; see
# https://github.com/Recol/dlss-updater-flatpak
FLATPAK_REPO_URL="${FLATPAK_REPO_URL:-https://recol.github.io/dlss-updater-flatpak/}"

# Where clients get the runtime the app depends on.
FLATPAK_RUNTIME_REPO="${FLATPAK_RUNTIME_REPO:-https://dl.flathub.org/repo/flathub.flatpakrepo}"

# GPG key id used to sign the repo, and embedded in the bundle so clients verify
# every update. Falls back to .flatpak_gpg_key (untracked, written when the key
# was generated) so a build can't silently produce an unsigned repo just because
# an env var wasn't exported - publishing unsigned over a signed channel breaks
# `flatpak update` for everyone who already verified against the key.
FLATPAK_GPG_KEY="${FLATPAK_GPG_KEY:-}"
if [ -z "$FLATPAK_GPG_KEY" ] && [ -f .flatpak_gpg_key ]; then
    FLATPAK_GPG_KEY=$(tr -d '[:space:]' < .flatpak_gpg_key)
fi
if [ -n "$FLATPAK_GPG_KEY" ]; then
    if ! gpg --list-secret-keys "$FLATPAK_GPG_KEY" >/dev/null 2>&1; then
        echo -e "${RED}Signing key ${FLATPAK_GPG_KEY} is configured but not in this keyring${NC}"
        echo -e "Import it, or unset FLATPAK_GPG_KEY / remove .flatpak_gpg_key to build unsigned."
        exit 1
    fi
    echo -e "${GREEN}Signing with GPG key ${FLATPAK_GPG_KEY}${NC}"
else
    echo -e "${YELLOW}No signing key - repo will be unsigned (clients get gpg-verify=false)${NC}"
fi

# =============================================================================
# Step 1: Check and install system dependencies
# =============================================================================
echo -e "\n${YELLOW}[1/6] Checking system dependencies...${NC}"

install_deps() {
    if command -v apt-get &> /dev/null; then
        echo "Detected Debian/Ubuntu - installing with apt"
        sudo apt-get update
        sudo apt-get install -y \
            flatpak \
            flatpak-builder \
            patchelf \
            execstack
    elif command -v dnf &> /dev/null; then
        echo "Detected Fedora/RHEL - installing with dnf"
        sudo dnf install -y \
            flatpak \
            flatpak-builder \
            patchelf \
            execstack
    elif command -v pacman &> /dev/null; then
        echo "Detected Arch Linux - installing with pacman"
        sudo pacman -S --noconfirm \
            flatpak \
            flatpak-builder \
            patchelf
        echo -e "${YELLOW}Note: execstack is in AUR on Arch — install manually if missing (yay -S execstack)${NC}"
    else
        echo -e "${RED}Could not detect package manager. Please install dependencies manually.${NC}"
        exit 1
    fi
}

# Check for required tools
MISSING_DEPS=false
for cmd in flatpak flatpak-builder; do
    if ! command -v $cmd &> /dev/null; then
        MISSING_DEPS=true
        break
    fi
done

if [ "$MISSING_DEPS" = true ]; then
    echo "Installing build dependencies..."
    install_deps
else
    echo -e "${GREEN}All build dependencies already installed${NC}"
fi

# =============================================================================
# Step 2: Setup Flathub repository and SDK
# =============================================================================
echo -e "\n${YELLOW}[2/6] Setting up Flathub repository and SDK...${NC}"

flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo

# Check if SDK is installed
if ! flatpak list --user | grep -q "org.freedesktop.Sdk//25.08"; then
    echo "Installing Freedesktop SDK 25.08..."
    flatpak install --user -y flathub org.freedesktop.Sdk//25.08
else
    echo -e "${GREEN}Freedesktop SDK 25.08 already installed${NC}"
fi

if ! flatpak list --user | grep -q "org.freedesktop.Platform//25.08"; then
    echo "Installing Freedesktop Platform 25.08..."
    flatpak install --user -y flathub org.freedesktop.Platform//25.08
else
    echo -e "${GREEN}Freedesktop Platform 25.08 already installed${NC}"
fi

# =============================================================================
# Step 3: Check for uv and install Python dependencies
# =============================================================================
echo -e "\n${YELLOW}[3/6] Installing Python dependencies with uv...${NC}"

if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# Use Python 3.14.3 free-threaded for Linux builds (matches Windows / .python-version)
if ! uv python list | grep -q "3.14.3+freethreaded"; then
    echo "Installing Python 3.14.3 (free-threaded)..."
    uv python install 3.14.3+freethreaded
fi

uv python pin 3.14.3+freethreaded
uv sync --extra build

# =============================================================================
# Step 3.5: Clear PT_GNU_STACK on bundled libpython (Issue #217)
# =============================================================================
# python-build-standalone 3.14.3+freethreaded ships libpython3.14t.so.1.0 with
# PT_GNU_STACK marked executable (RWE). Modern Linux kernels (Steam Deck SteamOS,
# CachyOS, recent Arch/Fedora) enforce W^X and reject dlopen() on shared objects
# requesting an executable stack, breaking the PyInstaller-bundled application.
# Clearing the flag here means PyInstaller copies the corrected .so into the
# onefile bundle. 3.14.2+freethreaded did not have this flag set; the regression
# is in the upstream 3.14.3 build.
echo -e "\n${YELLOW}[3.5/6] Clearing PT_GNU_STACK on bundled .so files (Issue #217)...${NC}"

if ! command -v execstack &> /dev/null; then
    echo -e "${RED}Error: execstack not installed. Install with: sudo apt-get install -y execstack${NC}"
    exit 1
fi

PY_BIN="$(uv python find 3.14.3+freethreaded)"
# Inside a uv project, `uv python find` returns the .venv symlink. Resolve to the
# real interpreter in ~/.local/share/uv/python/... so we patch the source-of-truth
# libpython that PyInstaller will actually bundle.
PY_BIN_REAL="$(readlink -f "$PY_BIN")"
PY_ROOT="$(dirname "$(dirname "$PY_BIN_REAL")")"
echo "Python install root: $PY_ROOT"

# Clear executable stack on all .so files in the Python install.
# execstack returns non-zero on files without a GNU_STACK header; suppress with || true.
find "$PY_ROOT" -name '*.so*' -type f -print0 | xargs -0 -r execstack -c 2>/dev/null || true

# Hard-verify libpython is clean - fail the build rather than ship a broken binary.
LIBPY="$PY_ROOT/lib/libpython3.14t.so.1.0"
if [ ! -f "$LIBPY" ]; then
    echo -e "${RED}Error: libpython not found at $LIBPY${NC}"
    exit 1
fi
if readelf -lW "$LIBPY" | grep -E '^\s*GNU_STACK' | grep -q 'RWE'; then
    echo -e "${RED}Error: libpython still has executable stack after execstack -c${NC}"
    readelf -lW "$LIBPY" | grep GNU_STACK
    exit 1
fi
echo -e "${GREEN}libpython GNU_STACK verified clean (RW, not RWE)${NC}"

# =============================================================================
# Step 4: Build with PyInstaller
# =============================================================================
echo -e "\n${YELLOW}[4/6] Building Linux application with PyInstaller...${NC}"

# Clean previous builds
rm -rf build/pyinstaller dist/DLSS_Updater .flatpak-builder/ build-dir/ repo/

# Run PyInstaller with the Linux spec file
# PYTHON_GIL=0 suppresses msgpack GIL warning during analysis phase
PYTHON_GIL=0 uv run pyinstaller DLSS_Updater_Linux.spec --distpath dist --workpath build/pyinstaller

# Verify the binary was created
if [ ! -f "dist/DLSS_Updater" ]; then
    echo -e "${RED}Error: PyInstaller build failed - dist/DLSS_Updater not found${NC}"
    exit 1
fi

echo -e "${GREEN}Flet build successful${NC}"
ls -la dist/

# =============================================================================
# Step 5: Build Flatpak
# =============================================================================
echo -e "\n${YELLOW}[5/6] Building Flatpak package...${NC}"

# Use --build-only to skip the finish phase (which requires appstream-compose)
flatpak-builder --user --force-clean --build-only build-dir io.github.recol.dlss-updater.yml

# Manually apply finish-args (permissions)
flatpak build-finish build-dir \
    --socket=wayland \
    --socket=fallback-x11 \
    --device=dri \
    --share=ipc \
    --share=network \
    --filesystem=home:rw \
    --filesystem=/mnt:rw \
    --filesystem=/media:rw \
    --filesystem=/run/media:rw \
    --filesystem=xdg-config/DLSS-Updater:rw \
    --filesystem=xdg-cache/DLSS-Updater:rw \
    --filesystem=~/.local/share/dlss-updater:create \
    --filesystem=~/.flet:create \
    --talk-name=org.freedesktop.portal.FileChooser \
    --command=dlss-updater

# =============================================================================
# Generate the AppStream catalogue
# =============================================================================
# `flatpak build-export` publishes /files/share/app-info into the repo's
# appstream branch, and that is what software centres (GNOME Software, KDE
# Discover) read to show the app's name, description and per-release notes from
# io.github.recol.dlss-updater.appdata.xml. Without it the export logs
# "No appstream data ... /files/share/app-info" and the published channel
# carries no metadata at all - `flatpak update` still works, but the app looks
# blank in a software centre.
#
# flatpak-builder's finish phase would normally generate this, but it invokes the
# legacy `appstream-compose` binary that AppStream 1.x removed - which is exactly
# why --build-only is used above. appstreamcli's `compose` subcommand is the
# current equivalent (verified against AppStream 1.0.6 in the 25.08 SDK).
#
# Argument notes, each learned the hard way:
#   --origin=$APP_ID   appstreamcli names the output file after the origin, and
#                      build-update-repo looks for exactly
#                      files/share/app-info/xmls/<APP-ID>.xml.gz. With any other
#                      origin the file is generated but silently ignored. (This
#                      is the job the legacy tool's --basename flag did.)
#   --prefix=/         prefix is relative to the SOURCE root, so /app would make
#                      it look in /app/app/share and find nothing.
#   --icons-dir .../flatpak  literally "flatpak", regardless of origin - that is
#                      the icon directory flatpak itself reads.
#   no --media-dir     it demands --media-baseurl, which only applies when media
#                      is served separately from the repo.
#   no --no-partial-urls  likewise requires a base URL; partial (shared-prefix)
#                      URLs are what flatpak expects.
if flatpak build build-dir sh -c 'command -v appstreamcli' >/dev/null 2>&1; then
    echo -e "\n${YELLOW}Generating AppStream catalogue...${NC}"
    flatpak build build-dir appstreamcli compose \
        --origin="$APP_ID" \
        --prefix=/ \
        --result-root=/app \
        --data-dir=/app/share/app-info/xmls \
        --icons-dir=/app/share/app-info/icons/flatpak \
        --no-net \
        /app
    if [ -f "build-dir/files/share/app-info/xmls/${APP_ID}.xml.gz" ]; then
        echo -e "${GREEN}AppStream catalogue generated${NC}"
    else
        echo -e "${RED}appstreamcli reported success but produced no catalogue${NC}"
        echo -e "${RED}(expected build-dir/files/share/app-info/xmls/${APP_ID}.xml.gz)${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}appstreamcli not in the SDK - repo will have no appstream branch${NC}"
    echo -e "${YELLOW}(updates still work; software centres will show no metadata)${NC}"
fi

# Export to local repo
GPG_ARGS=()
if [ -n "$FLATPAK_GPG_KEY" ]; then
    GPG_ARGS=(--gpg-sign="$FLATPAK_GPG_KEY")
fi
flatpak build-export "${GPG_ARGS[@]}" repo build-dir

# Regenerate the repo summary so clients can see the new commit, prune old
# history, and generate static deltas so an update downloads only what changed
# rather than the whole ~36MB bundle again.
flatpak build-update-repo \
    "${GPG_ARGS[@]}" \
    --generate-static-deltas \
    --prune \
    --prune-depth=3 \
    repo

# =============================================================================
# Step 6: Create distributable bundle
# =============================================================================
echo -e "\n${YELLOW}[6/6] Creating Flatpak bundle...${NC}"

BUNDLE_ARGS=(--repo-url="$FLATPAK_REPO_URL" --runtime-repo="$FLATPAK_RUNTIME_REPO")
if [ -n "$FLATPAK_GPG_KEY" ]; then
    # Export the public key so the configured remote can verify updates.
    gpg --export "$FLATPAK_GPG_KEY" > repo-key.gpg
    BUNDLE_ARGS+=(--gpg-keys=repo-key.gpg)
fi

FLATPAK_NAME="DLSS_Updater-${VERSION}.flatpak"
flatpak build-bundle "${BUNDLE_ARGS[@]}" repo "${FLATPAK_NAME}" "$APP_ID"

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}Build complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Flatpak bundle: ${YELLOW}${FLATPAK_NAME}${NC}"
echo -e "Size: $(du -h "${FLATPAK_NAME}" | cut -f1)"
echo -e "\nTo install and test locally:"
echo -e "  ${YELLOW}flatpak install --user ${FLATPAK_NAME}${NC}"
echo -e "  ${YELLOW}flatpak run io.github.recol.dlss-updater${NC}"
echo -e "\nTo uninstall:"
echo -e "  ${YELLOW}flatpak uninstall --user io.github.recol.dlss-updater${NC}"
