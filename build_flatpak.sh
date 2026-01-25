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
            patchelf
    elif command -v dnf &> /dev/null; then
        echo "Detected Fedora/RHEL - installing with dnf"
        sudo dnf install -y \
            flatpak \
            flatpak-builder \
            patchelf
    elif command -v pacman &> /dev/null; then
        echo "Detected Arch Linux - installing with pacman"
        sudo pacman -S --noconfirm \
            flatpak \
            flatpak-builder \
            patchelf
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
if ! flatpak list --user | grep -q "org.freedesktop.Sdk//24.08"; then
    echo "Installing Freedesktop SDK 24.08..."
    flatpak install --user -y flathub org.freedesktop.Sdk//24.08
else
    echo -e "${GREEN}Freedesktop SDK 24.08 already installed${NC}"
fi

if ! flatpak list --user | grep -q "org.freedesktop.Platform//24.08"; then
    echo "Installing Freedesktop Platform 24.08..."
    flatpak install --user -y flathub org.freedesktop.Platform//24.08
else
    echo -e "${GREEN}Freedesktop Platform 24.08 already installed${NC}"
fi

# =============================================================================
# Step 3: Check for uv and install Python
# =============================================================================
echo -e "\n${YELLOW}[3/6] Setting up Python environment...${NC}"

if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# Install Python 3.12 for flet build (serious-python only supports 3.12.6)
# See: https://github.com/flet-dev/serious-python/issues/173
# Note: Project requires Python >=3.14 but flet build needs 3.12 internally
if ! uv python list | grep -q "cpython-3.12"; then
    echo "Installing Python 3.12 for flet build..."
    uv python install 3.12
fi

echo -e "${GREEN}Python 3.12 available for flet build${NC}"

# =============================================================================
# Step 4: Build with Flet
# =============================================================================
echo -e "\n${YELLOW}[4/6] Building Linux application with Flet...${NC}"

# Clean previous builds
rm -rf build/linux build/flutter dist/

# Run flet build linux with Python 3.12
# Note: flet build uses serious-python which only supports Python 3.12.6
# We use uvx to run flet-cli in an isolated Python 3.12 environment
uvx --python 3.12 --from flet-cli flet build linux \
    --project "DLSS_Updater" \
    --product "DLSS Updater" \
    --org "io.github.recol" \
    --description "Update DLSS/XeSS/FSR DLLs for games" \
    --build-version "${VERSION}"

# Move build output to expected location for Flatpak
mkdir -p dist
cp -r build/linux/* dist/

# Verify the binary was created
if [ ! -f "dist/dlss_updater" ]; then
    echo -e "${RED}Error: Flet build failed - dist/dlss_updater not found${NC}"
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
    --filesystem=home:ro \
    --filesystem=/mnt:ro \
    --filesystem=/media:ro \
    --filesystem=/run/media:ro \
    --filesystem=xdg-config/DLSS-Updater:rw \
    --filesystem=xdg-cache/DLSS-Updater:rw \
    --filesystem=~/.local/share/dlss-updater:create \
    --filesystem=~/.flet:create \
    --talk-name=org.freedesktop.portal.FileChooser \
    --command=dlss-updater

# Export to local repo
flatpak build-export repo build-dir

# =============================================================================
# Step 6: Create distributable bundle
# =============================================================================
echo -e "\n${YELLOW}[6/6] Creating Flatpak bundle...${NC}"

FLATPAK_NAME="DLSS_Updater-${VERSION}.flatpak"
flatpak build-bundle repo "${FLATPAK_NAME}" io.github.recol.dlss-updater

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
