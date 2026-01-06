#!/bin/bash
# AppImage build script for DLSS Updater
# Run this from the project root directory in a Linux environment (native or WSL2)

set -e

APP_NAME="DLSS_Updater"
APP_VERSION="3.4.0"
FLET_VERSION="0.28.3"

echo "=== Building DLSS Updater AppImage v${APP_VERSION} ==="

# Check if we're on Linux
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    echo "Error: This script must be run on Linux (or WSL2)"
    exit 1
fi

# Download Flet client for bundling (not included in Python package)
echo "Step 0: Downloading Flet client for bundling..."
FLET_CLIENT_DIR="flet_client"
if [ ! -f "${FLET_CLIENT_DIR}/flet/flet" ]; then
    mkdir -p "${FLET_CLIENT_DIR}"
    wget -q "https://github.com/flet-dev/flet/releases/download/v${FLET_VERSION}/flet-linux-amd64.tar.gz" -O flet-linux.tar.gz
    tar -xzf flet-linux.tar.gz -C "${FLET_CLIENT_DIR}/"
    rm flet-linux.tar.gz
    echo "  Downloaded Flet client v${FLET_VERSION}"
else
    echo "  Flet client already downloaded"
fi

# Build PyInstaller binary (spec file bundles Flet desktop client)
echo "Step 1: Building PyInstaller binary..."
uv run pyinstaller DLSS_Updater_Linux.spec

# Clean up any previous AppDir
rm -rf ${APP_NAME}.AppDir

# Create AppDir structure
echo "Step 2: Creating AppDir structure..."
mkdir -p ${APP_NAME}.AppDir/usr/bin
mkdir -p ${APP_NAME}.AppDir/usr/lib
mkdir -p ${APP_NAME}.AppDir/usr/share/metainfo

# Copy PyInstaller binary
cp dist/DLSS_Updater ${APP_NAME}.AppDir/usr/bin/

# Create AppRun script
echo "Step 3: Creating AppRun entry point..."
cat > ${APP_NAME}.AppDir/AppRun << 'EOF'
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH}"
exec "${HERE}/usr/bin/DLSS_Updater" "$@"
EOF
chmod +x ${APP_NAME}.AppDir/AppRun

# Create .desktop file
echo "Step 4: Creating desktop entry..."
cat > ${APP_NAME}.AppDir/io.github.recol.dlss-updater.desktop << EOF
[Desktop Entry]
Type=Application
Name=DLSS Updater
Exec=DLSS_Updater
Icon=io.github.recol.dlss-updater
Categories=Game;Utility;
Comment=Update DLSS/XeSS/FSR DLLs for games
EOF

# Copy icon
echo "Step 5: Copying icon..."
if [ -f appimagex_png.png ]; then
    cp appimagex_png.png ${APP_NAME}.AppDir/io.github.recol.dlss-updater.png
else
    echo "Warning: appimagex_png.png not found, creating placeholder icon"
    # Create a simple 1x1 transparent PNG as placeholder
    echo -n -e '\x89PNG\r\n\x1a\n' > ${APP_NAME}.AppDir/io.github.recol.dlss-updater.png
fi

# Copy AppStream metadata
echo "Step 6: Copying AppStream metadata..."
if [ -f io.github.recol.dlss-updater.metainfo.xml ]; then
    cp io.github.recol.dlss-updater.metainfo.xml ${APP_NAME}.AppDir/usr/share/metainfo/
    echo "  AppStream metadata installed"
else
    echo "Warning: io.github.recol.dlss-updater.metainfo.xml not found, skipping"
fi

# Bundle libmpv
echo "Step 7: Bundling libmpv..."
LIBMPV_FOUND=false
if [ -f /usr/lib/x86_64-linux-gnu/libmpv.so.2 ]; then
    cp /usr/lib/x86_64-linux-gnu/libmpv.so.2 ${APP_NAME}.AppDir/usr/lib/libmpv.so.1
    LIBMPV_FOUND=true
    echo "  Found libmpv at /usr/lib/x86_64-linux-gnu/libmpv.so.2"
elif [ -f /usr/lib/libmpv.so.2 ]; then
    cp /usr/lib/libmpv.so.2 ${APP_NAME}.AppDir/usr/lib/libmpv.so.1
    LIBMPV_FOUND=true
    echo "  Found libmpv at /usr/lib/libmpv.so.2"
elif [ -f /usr/lib64/libmpv.so.2 ]; then
    cp /usr/lib64/libmpv.so.2 ${APP_NAME}.AppDir/usr/lib/libmpv.so.1
    LIBMPV_FOUND=true
    echo "  Found libmpv at /usr/lib64/libmpv.so.2"
fi

if [ "$LIBMPV_FOUND" = false ]; then
    echo "Warning: libmpv not found. Install it with:"
    echo "  Ubuntu/Debian: sudo apt install libmpv2"
    echo "  Arch: sudo pacman -S mpv"
    echo "  Fedora: sudo dnf install mpv-libs"
fi

# Download appimagetool if not present
echo "Step 8: Getting appimagetool..."
if [ ! -f appimagetool-x86_64.AppImage ]; then
    echo "  Downloading appimagetool..."
    wget -q https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x appimagetool-x86_64.AppImage
else
    echo "  appimagetool already present"
fi

# Build AppImage
echo "Step 9: Building AppImage..."
ARCH=x86_64 ./appimagetool-x86_64.AppImage ${APP_NAME}.AppDir dist/${APP_NAME}_Linux-${APP_VERSION}-x86_64.AppImage

echo ""
echo "=== Build Complete ==="
echo "Created: dist/${APP_NAME}_Linux-${APP_VERSION}-x86_64.AppImage"
echo ""
echo "To run: ./dist/${APP_NAME}_Linux-${APP_VERSION}-x86_64.AppImage"
echo "Note: Most games work without sudo. Use sudo only for system-installed Wine/Proton."