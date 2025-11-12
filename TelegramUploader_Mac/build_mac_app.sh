#!/usr/bin/env bash
set -euo pipefail

# Build script for TelegramUploader macOS app
# Usage: run this inside the TelegramUploader_Mac folder on macOS with Xcode command-line tools installed.

APP_NAME="TelegramUploader"
APP_DIR="${APP_NAME}.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

echo "Building ${APP_NAME}.app..."

rm -rf "$APP_DIR" "${APP_NAME}.dmg"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

echo "Compiling SwiftUI Glass UI (universal arm64+x86_64)…"
BUILD_DIR="${OUT_DIR:-$(pwd)}/.build"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Build per-arch binaries
ARM_BIN="$BUILD_DIR/${APP_NAME}-arm64"
X86_BIN="$BUILD_DIR/${APP_NAME}-x86_64"

set +e
swiftc -parse-as-library main.swift \
  -o "$ARM_BIN" \
  -framework SwiftUI -framework AppKit -framework Cocoa \
  -target arm64-apple-macos12.0
ARM_STATUS=$?

swiftc -parse-as-library main.swift \
  -o "$X86_BIN" \
  -framework SwiftUI -framework AppKit -framework Cocoa \
  -target x86_64-apple-macos12.0
X86_STATUS=$?
set -e

if [ $ARM_STATUS -ne 0 ] && [ $X86_STATUS -ne 0 ]; then
  echo "SwiftUI build failed for both architectures." >&2
  exit 1
fi

if [ $ARM_STATUS -eq 0 ] && [ $X86_STATUS -eq 0 ] && command -v lipo >/dev/null 2>&1; then
  echo "Creating universal binary with lipo…"
  lipo -create -output "$MACOS_DIR/$APP_NAME" "$ARM_BIN" "$X86_BIN"
elif [ $ARM_STATUS -eq 0 ]; then
  echo "Using arm64 binary only (lipo or x86_64 build unavailable)"
  cp "$ARM_BIN" "$MACOS_DIR/$APP_NAME"
else
  echo "Using x86_64 binary only (arm64 build unavailable)"
  cp "$X86_BIN" "$MACOS_DIR/$APP_NAME"
fi

echo "Copying Info.plist..."
if [ -f "Info.plist" ]; then
  mkdir -p "$CONTENTS_DIR"
  cp "Info.plist" "$CONTENTS_DIR/Info.plist"
else
  echo "Warning: Info.plist not found in $(pwd). The app may miss metadata." >&2
fi

echo "Copying Python script into Resources..."
# Expect the main python file to be one level up (repo root). Adjust as needed.
if [ -f "../telegram_uploader_gui_python.py" ]; then
  cp "../telegram_uploader_gui_python.py" "$RESOURCES_DIR/"
else
  echo "Warning: ../telegram_uploader_gui_python.py not found. Please copy your Python script into $RESOURCES_DIR manually." >&2
fi

echo "Adding README to Resources..."
cat > "$RESOURCES_DIR/README.txt" <<'TXT'
This app bundles the Telegram Uploader Python script.

Run the app. The launcher calls the system's `python3` to execute the bundled
script at Resources/telegram_uploader_gui_python.py. For best results, install
and use a virtual environment and ensure python3 has the required packages (requests).

If you need the app to use a contained Python runtime, consider packaging with
py2app or creating a custom runtime in Resources and adjusting the launcher.
TXT

echo "Setting executable permissions..."
chmod +x "$MACOS_DIR/$APP_NAME"

# Create embedded Python venv for a self-contained runtime
echo "Preparing embedded Python venv..."
PY_CANDIDATES=(
  "/opt/homebrew/bin/python3"
  "/usr/local/bin/python3"
  "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
  "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
  "/usr/bin/python3"
)
PY_FOUND=""
for p in "${PY_CANDIDATES[@]}"; do
  if [ -x "$p" ]; then PY_FOUND="$p"; break; fi
done

if [ -n "$PY_FOUND" ]; then
  echo "Using Python: $PY_FOUND"
  # Create dual-arch venvs when possible (host-dependent)
  "$PY_FOUND" -m venv "$RESOURCES_DIR/venv-arm64" || true
  "$PY_FOUND" -m venv "$RESOURCES_DIR/venv-x86_64" || true
  # Default venv symlink to the host one if not both exist
  if [ -d "$RESOURCES_DIR/venv-arm64" ] && [ ! -d "$RESOURCES_DIR/venv" ]; then
    ln -s "venv-arm64" "$RESOURCES_DIR/venv" || true
  elif [ -d "$RESOURCES_DIR/venv-x86_64" ] && [ ! -d "$RESOURCES_DIR/venv" ]; then
    ln -s "venv-x86_64" "$RESOURCES_DIR/venv" || true
  fi
  # Install deps into any existing venvs we created
  for VENV in venv venv-arm64 venv-x86_64; do
    if [ -x "$RESOURCES_DIR/$VENV/bin/python3" ]; then
    echo "Installing runtime deps into embedded venv..."
    "$RESOURCES_DIR/$VENV/bin/python3" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$RESOURCES_DIR/$VENV/bin/python3" -m pip install requests pillow >/dev/null 2>&1 || true
    fi
  done
else
  echo "Warning: No local Python found to create venv; app will fall back to system Python detection."
fi

# Generate an .icns app icon (simple, Material-ish)
ICONSET_DIR="${OUT_DIR:-$(pwd)}/icon.iconset"
ICNS_FILE="$RESOURCES_DIR/TelegramUploader.icns"
BASE_ICON_PNG="${OUT_DIR:-$(pwd)}/base_icon.png"
echo "Generating app icon..."

cat > "${OUT_DIR:-$(pwd)}/icon_generator.py" << 'PY'
from PIL import Image, ImageDraw, ImageFilter
import sys

size=1024
img = Image.new('RGBA', (size, size), (0,0,0,0))
draw = ImageDraw.Draw(img)

# Background gradient-ish using two circles
draw.ellipse((0, 0, size, size), fill=(103,80,164,255))  # Material primary
overlay = Image.new('RGBA', (size, size), (208,188,255,200)) # inversePrimary
overlay = overlay.filter(ImageFilter.GaussianBlur(180))
img.alpha_composite(overlay)

# Paper plane (simplified) in white
plane = Image.new('RGBA', (size, size), (0,0,0,0))
p = ImageDraw.Draw(plane)
tri = [
  (size*0.20, size*0.55),
  (size*0.85, size*0.35),
  (size*0.55, size*0.80)
]
p.polygon(tri, fill=(255,255,255,255))
img.alpha_composite(plane)

img.save(sys.argv[1])
PY

if [ -x "$RESOURCES_DIR/venv/bin/python3" ]; then
  "$RESOURCES_DIR/venv/bin/python3" "${OUT_DIR:-$(pwd)}/icon_generator.py" "$BASE_ICON_PNG" || true
elif [ -n "$PY_FOUND" ]; then
  "$PY_FOUND" "${OUT_DIR:-$(pwd)}/icon_generator.py" "$BASE_ICON_PNG" || true
fi

mkdir -p "$ICONSET_DIR"
if [ -f "$BASE_ICON_PNG" ]; then
  for sz in 16 32 64 128 256 512; do
    sips -z $sz $sz "$BASE_ICON_PNG" --out "$ICONSET_DIR/icon_${sz}x${sz}.png" >/dev/null 2>&1 || true
    dbl=$((sz*2))
    sips -z $dbl $dbl "$BASE_ICON_PNG" --out "$ICONSET_DIR/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1 || true
  done
  iconutil -c icns "$ICONSET_DIR" -o "$ICNS_FILE" >/dev/null 2>&1 || true
else
  echo "Icon base not generated; skipping .icns creation." >&2
fi

echo "Signing (skipped). If you want to distribute outside your machine, sign and notarize the app."

echo "Creating compressed DMG..."
if command -v hdiutil >/dev/null 2>&1; then
  hdiutil create -volname "$APP_NAME" -srcfolder "$APP_DIR" -ov -format UDZO "${APP_NAME}.dmg"
  echo "Created ${APP_NAME}.dmg"
else
  echo "hdiutil not found; skipping dmg creation. The app bundle is at: $APP_DIR"
fi

echo "Done. App bundle location: $(pwd)/$APP_DIR"
