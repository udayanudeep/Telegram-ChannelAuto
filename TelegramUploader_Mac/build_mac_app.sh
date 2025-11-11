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

echo "Compiling SwiftUI glass launcher (target macOS 15)..."
ARCH=$(uname -m)
TARGET="${ARCH}-apple-macos15.0"
# Build as library module to avoid @main + top-level code conflict and target macOS 14 APIs
if ! swiftc -parse-as-library main.swift \
  -o "$MACOS_DIR/$APP_NAME" \
  -framework SwiftUI -framework AppKit -framework Cocoa \
  -target "$TARGET"; then
  echo "SwiftUI build failed; retrying without explicit target (host default)" >&2
  swiftc -parse-as-library main.swift -o "$MACOS_DIR/$APP_NAME" -framework SwiftUI -framework AppKit -framework Cocoa || {
    echo "SwiftUI build failed; please ensure Xcode CLTs and SwiftUI are available." >&2
    exit 1
  }
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

echo "Signing (skipped). If you want to distribute outside your machine, sign and notarize the app."

echo "Creating compressed DMG..."
if command -v hdiutil >/dev/null 2>&1; then
  hdiutil create -volname "$APP_NAME" -srcfolder "$APP_DIR" -ov -format UDZO "${APP_NAME}.dmg"
  echo "Created ${APP_NAME}.dmg"
else
  echo "hdiutil not found; skipping dmg creation. The app bundle is at: $APP_DIR"
fi

echo "Done. App bundle location: $(pwd)/$APP_DIR"
