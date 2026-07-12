#!/bin/bash
# DLSS Updater - Flathub Build Script
# Builds the Flathub-style flatpak (from-source CPython 3.14t + offline pip
# wheels + trimmed flet client) locally, mirroring what Flathub's builders
# will do. Run in WSL2 or native Linux from the repo root.
#
# This is the DEV pipeline for the Flathub submission (issue #234). It differs
# from build_flatpak.sh (the GitHub-releases bundle) in every way that matters:
#   - app ID io.github.recol.dlss_updater (Flathub-verifiable underscore form)
#   - no PyInstaller: the app runs from site-packages on a source-built
#     free-threaded CPython
#   - flet's Flutter client is built from source, trimmed to core (no
#     extensions, no prebuilt blobs, no libmpv/libsecret linkage)
# Submission deltas (handled at PR time, see flathub/README.md):
#   - client build moves inside the manifest (flatpak-flutter offline sources)
#   - app source pins the release commit instead of the local checkout

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

FLUTTER_VERSION="3.44.6"
APP_ID="io.github.recol.dlss_updater"

VERSION=$(grep -oP '__version__\s*=\s*"\K[^"]+' dlss_updater/version.py)
FLET_VERSION=$(grep -A1 '^name = "flet"$' uv.lock | grep -oP 'version = "\K[^"]+' | sort -u)
if [ "$(echo "$FLET_VERSION" | wc -l)" -ne 1 ]; then
    echo -e "${RED}Ambiguous flet pin in uv.lock (found: $(echo $FLET_VERSION | tr '\n' ' ')) - unify the pin first${NC}"
    exit 1
fi
echo -e "${GREEN}Building DLSS Updater v${VERSION} (Flathub-style, flet ${FLET_VERSION})${NC}"

# =============================================================================
# Step 1: Check and install system dependencies
# =============================================================================
echo -e "\n${YELLOW}[1/7] Checking system dependencies...${NC}"

NEEDED_TOOLS=(flatpak flatpak-builder clang cmake ninja pkg-config curl git unzip python3)
MISSING=()
for cmd in "${NEEDED_TOOLS[@]}"; do
    command -v "$cmd" &> /dev/null || MISSING+=("$cmd")
done
dpkg -s libgtk-3-dev &> /dev/null 2>&1 || MISSING+=("libgtk-3-dev")

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Missing: ${MISSING[*]}"
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y flatpak flatpak-builder clang cmake ninja-build \
            pkg-config libgtk-3-dev curl git unzip xz-utils python3
    else
        echo -e "${RED}Install the missing tools manually and re-run.${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}All build dependencies already installed${NC}"
fi

# =============================================================================
# Step 2: Flathub repository and SDK
# =============================================================================
echo -e "\n${YELLOW}[2/7] Setting up Flathub repository and SDK...${NC}"

flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
for ref in org.freedesktop.Sdk org.freedesktop.Platform; do
    if ! flatpak list --user | grep -q "${ref}//25.08"; then
        flatpak install --user -y flathub "${ref}//25.08"
    fi
done
echo -e "${GREEN}SDK 25.08 ready${NC}"

# =============================================================================
# Step 3: Flutter SDK (pinned, cached in flathub/.flutter-sdk)
# =============================================================================
echo -e "\n${YELLOW}[3/7] Flutter SDK ${FLUTTER_VERSION}...${NC}"

FLUTTER_DIR="flathub/.flutter-sdk"
if [ ! -x "$FLUTTER_DIR/flutter/bin/flutter" ] || \
   ! "$FLUTTER_DIR/flutter/bin/flutter" --version 2>/dev/null | grep -q "$FLUTTER_VERSION"; then
    rm -rf "$FLUTTER_DIR" && mkdir -p "$FLUTTER_DIR"
    curl -sL -o "$FLUTTER_DIR/flutter.tar.xz" \
        "https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_${FLUTTER_VERSION}-stable.tar.xz"
    tar xf "$FLUTTER_DIR/flutter.tar.xz" -C "$FLUTTER_DIR" && rm "$FLUTTER_DIR/flutter.tar.xz"
fi
export PATH="$PWD/$FLUTTER_DIR/flutter/bin:$PATH"
flutter config --no-analytics --enable-linux-desktop > /dev/null 2>&1
echo -e "${GREEN}$(flutter --version | head -1)${NC}"

# =============================================================================
# Step 4: Build the trimmed flet client from source
# =============================================================================
echo -e "\n${YELLOW}[4/7] Building flet ${FLET_VERSION} client (trimmed, from source)...${NC}"

FLET_SRC="flathub/flet-src"
if [ ! -d "$FLET_SRC" ] || ! git -C "$FLET_SRC" describe --tags 2>/dev/null | grep -q "v${FLET_VERSION}"; then
    rm -rf "$FLET_SRC"
    git clone --depth 1 --branch "v${FLET_VERSION}" https://github.com/flet-dev/flet.git "$FLET_SRC"
fi
git -C "$FLET_SRC" checkout -- client/pubspec.yaml client/lib/main.dart

python3 flathub/trim_flet_client.py "$FLET_SRC/client"

(
    cd "$FLET_SRC/client"
    flutter pub get > /dev/null
    rm -rf build/linux .dart_tool/flutter_build
    flutter build linux --release
)

BUNDLE="$FLET_SRC/client/build/linux/x64/release/bundle"
[ -f "$BUNDLE/flet" ] || { echo -e "${RED}client build failed${NC}"; exit 1; }
# Hard-fail if the trimmed client still links libs the Platform doesn't ship
if ldd "$BUNDLE/flet" | grep -qiE "libmpv|libsecret"; then
    echo -e "${RED}client links libmpv/libsecret - trim failed${NC}"; exit 1
fi
rm -rf flathub/client-build && mkdir -p flathub/client-build
cp -r "$BUNDLE" flathub/client-build/bundle
echo -e "${GREEN}client built: $(du -sh flathub/client-build/bundle | cut -f1)${NC}"

# =============================================================================
# Step 5: Generate offline pip sources from uv.lock
# =============================================================================
echo -e "\n${YELLOW}[5/7] Generating offline pip sources (cp314t)...${NC}"

if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
mkdir -p flathub/generated
uv export --frozen --no-emit-project --no-dev --no-hashes -o flathub/generated/requirements.txt > /dev/null
python3 flathub/gen_pip_sources.py flathub/generated/requirements.txt flathub/generated/python-deps.json

# =============================================================================
# Step 6: flatpak-builder
# =============================================================================
echo -e "\n${YELLOW}[6/7] Building flatpak...${NC}"

cd flathub
rm -rf build-dir repo
# --build-only skips the finish phase: this WSL/flatpak-builder combo lacks
# appstream-compose (same workaround as build_flatpak.sh). Flathub's own
# infra runs the full pipeline from the manifest's finish-args.
flatpak-builder --user --force-clean --build-only --install-deps-from=flathub \
    build-dir dev.yml

# Keep this list in sync with finish-args in the manifest.
flatpak build-finish build-dir \
    --socket=wayland --socket=fallback-x11 --device=dri --share=ipc --share=network \
    --filesystem=home:rw --filesystem=/mnt:rw --filesystem=/media:rw --filesystem=/run/media:rw \
    --filesystem=~/.local/share/dlss-updater:create \
    --env=LD_LIBRARY_PATH=/app/lib64:/app/lib --env=PYTHON_GIL=0 \
    --command=dlss-updater

flatpak build-export repo build-dir

# =============================================================================
# Step 7: Bundle
# =============================================================================
echo -e "\n${YELLOW}[7/7] Creating bundle...${NC}"

FLATPAK_NAME="DLSS_Updater-flathub-${VERSION}.flatpak"
flatpak build-bundle repo "${FLATPAK_NAME}" "$APP_ID"

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}Flathub-style build complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "Bundle: ${YELLOW}flathub/${FLATPAK_NAME}${NC} ($(du -h "${FLATPAK_NAME}" | cut -f1))"
echo -e "\nInstall and test:"
echo -e "  ${YELLOW}flatpak install --user flathub/${FLATPAK_NAME}${NC}"
echo -e "  ${YELLOW}flatpak run ${APP_ID}${NC}"
echo -e "Uninstall:"
echo -e "  ${YELLOW}flatpak uninstall --user ${APP_ID}${NC}"
