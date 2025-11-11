#!/usr/bin/env bash
# Build and package a macOS .app and .dmg for the Telegram uploader
set -euo pipefail

APP_NAME="TelegramUploader"
DISPLAY_NAME="Telegram Uploader"
BUNDLE_ID="com.udayaanudeep.telegramuploader"
SRC_ROOT="$(cd "$(dirname "$0")" && pwd)/.."  # repo root
OUT_DIR="$SRC_ROOT/dist"

SWIFT_SOURCE="$SRC_ROOT/macos/main.swift"
INFO_PLIST="$SRC_ROOT/macos/Info.plist"
PY_SCRIPT="$SRC_ROOT/telegram_uploader_gui_python.py"

mkdir -p "$OUT_DIR"
rm -rf "$OUT_DIR/$APP_NAME.app"

APP_DIR="$OUT_DIR/$APP_NAME.app/Contents"
MACOS_DIR="$APP_DIR/MacOS"
RESOURCES_DIR="$APP_DIR/Resources"

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

echo "Compiling Swift launcher..."
swiftc -o "$MACOS_DIR/$APP_NAME" "$SWIFT_SOURCE"

echo "Copying Info.plist..."
cp "$INFO_PLIST" "$APP_DIR/Info.plist"

echo "Bundling Python script into Resources..."
cp "$PY_SCRIPT" "$RESOURCES_DIR/"

echo "Setting executable permissions..."
chmod +x "$MACOS_DIR/$APP_NAME"

echo "Creating .dmg in $OUT_DIR"
DMG_NAME="$OUT_DIR/${APP_NAME}.dmg"
rm -f "$DMG_NAME"

# Create a temporary staging folder for the dmg so the .app is at the root
STAGING="$OUT_DIR/staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "$OUT_DIR/$APP_NAME.app" "$STAGING/"

# Create dmg (compressed)
hdiutil create -volname "$DISPLAY_NAME" -srcfolder "$STAGING" -ov -format UDZO "$DMG_NAME"

echo "Created: $DMG_NAME"
echo "Done. Note: The generated app runs the system 'python3' — ensure Python 3 is installed on target machines."

echo "Optional: code sign the app if you plan to distribute outside your machines."
