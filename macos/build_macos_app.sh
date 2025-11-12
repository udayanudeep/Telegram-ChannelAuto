#!/usr/bin/env bash
# Build and package a macOS .app and .dmg for the Telegram uploader
# Adds: Material-themed DMG background, icon layout, embedded Python venv,
#       version stamping, and codesign/notarization placeholders.
set -euo pipefail

APP_NAME="TelegramUploader"
DISPLAY_NAME="Telegram Uploader"
APP_FILENAME="${APP_NAME}.app"
BUNDLE_ID="com.udayaanudeep.telegramuploader"
# Version stamping (override via env VARS: VERSION and BUILD)
VERSION="${VERSION:-1.0.0}"
BUILD_NUMBER="${BUILD:-1}"
SRC_ROOT="$(cd "$(dirname "$0")" && pwd)/.."  # repo root
OUT_DIR="$SRC_ROOT/dist"

SWIFT_SOURCE="$SRC_ROOT/macos/main.swift"
INFO_PLIST="$SRC_ROOT/macos/Info.plist"
PY_SCRIPT="$SRC_ROOT/telegram_uploader_gui_python.py"

mkdir -p "$OUT_DIR"
rm -rf "$OUT_DIR/$APP_FILENAME"

APP_DIR="$OUT_DIR/$APP_FILENAME/Contents"
MACOS_DIR="$APP_DIR/MacOS"
RESOURCES_DIR="$APP_DIR/Resources"

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

echo "Compiling Swift launcher..."
swiftc -o "$MACOS_DIR/$APP_NAME" "$SWIFT_SOURCE"

echo "Copying Info.plist..."
cp "$INFO_PLIST" "$APP_DIR/Info.plist"

# Version stamp Info.plist (quietly)
if /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$APP_DIR/Info.plist" >/dev/null 2>&1; then
	/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP_DIR/Info.plist"
else
	/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$APP_DIR/Info.plist" || true
fi
if /usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "$APP_DIR/Info.plist" >/dev/null 2>&1; then
	/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$APP_DIR/Info.plist"
else
	/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $BUILD_NUMBER" "$APP_DIR/Info.plist" || true
fi

echo "Bundling Python script into Resources..."
cp "$PY_SCRIPT" "$RESOURCES_DIR/"

echo "Setting executable permissions..."
chmod +x "$MACOS_DIR/$APP_NAME"

# Create embedded Python venv (optional but preferred for portability)
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
	"$PY_FOUND" -m venv "$RESOURCES_DIR/venv" || true
		if [ -x "$RESOURCES_DIR/venv/bin/python3" ]; then
			echo "Installing runtime deps into embedded venv..."
			"$RESOURCES_DIR/venv/bin/python3" -m pip install --upgrade pip >/dev/null 2>&1 || true
			"$RESOURCES_DIR/venv/bin/python3" -m pip install requests >/dev/null 2>&1 || true
	else
		echo "Warning: venv creation failed; app will fall back to system Python detection."
	fi
else
	echo "Warning: No local Python found to create venv; app will fall back to system Python detection."
fi

# DMG background generation (Material-inspired)
BG_DIR="$OUT_DIR/.dmg_assets"
mkdir -p "$BG_DIR"
BG_IMG="$BG_DIR/background.png"
cat > "$SRC_ROOT/macos/generate_dmg_background.py" << 'PY'
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import sys

W, H = 800, 480
# Material 3-ish colors
surface = (0xFF, 0xFB, 0xFE)
primary = (0x67, 0x50, 0xA4)
inverse = (0xD0, 0xBC, 0xFF)

img = Image.new('RGB', (W, H), surface)
draw = ImageDraw.Draw(img)

# Subtle diagonal gradient using translucent bands
for i in range(0, W, 8):
		alpha = int(30 * (1 - i / W))
		draw.line([(i, 0), (i + H, H)], fill=(primary[0], primary[1], primary[2]), width=12)
img = img.filter(ImageFilter.GaussianBlur(10))

# Header bar
draw.rectangle([(0, 0), (W, 72)], fill=primary)
title = "Telegram Uploader"
try:
		font = ImageFont.truetype("Helvetica.ttc", 28)
except Exception:
		font = ImageFont.load_default()
draw.text((24, 22), title, fill=(255, 255, 255), font=font)

img.save(sys.argv[1])
PY

echo "Generating DMG background..."
if "$PY_FOUND" -c "import PIL" >/dev/null 2>&1; then
	"$PY_FOUND" "$SRC_ROOT/macos/generate_dmg_background.py" "$BG_IMG" || true
elif [ -x "$RESOURCES_DIR/venv/bin/python3" ] && "$RESOURCES_DIR/venv/bin/python3" -c "import PIL" >/dev/null 2>&1; then
	"$RESOURCES_DIR/venv/bin/python3" "$SRC_ROOT/macos/generate_dmg_background.py" "$BG_IMG" || true
else
	echo "Pillow not available; attempting to install in embedded venv..."
	if [ -x "$RESOURCES_DIR/venv/bin/python3" ]; then
		"$RESOURCES_DIR/venv/bin/python3" -m pip install pillow >/dev/null 2>&1 || true
		"$RESOURCES_DIR/venv/bin/python3" "$SRC_ROOT/macos/generate_dmg_background.py" "$BG_IMG" || true
	fi
fi

echo "Creating .dmg in $OUT_DIR"
DMG_NAME="$OUT_DIR/${APP_NAME}.dmg"
rm -f "$DMG_NAME"

# Create a temporary staging folder for the dmg so the .app is at the root
STAGING="$OUT_DIR/staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "$OUT_DIR/$APP_FILENAME" "$STAGING/"
# Add Applications symlink for drag-to-Applications install UX
(cd "$STAGING" && ln -s /Applications Applications) || true

# Create a temporary read-write DMG to lay out icons and background
RW_DMG="$OUT_DIR/${APP_NAME}-rw.dmg"
rm -f "$RW_DMG"
hdiutil create -volname "$DISPLAY_NAME" -srcfolder "$STAGING" -ov -format UDRW "$RW_DMG" >/dev/null

# Attach and capture mount point
echo "Detaching any pre-existing mount at /Volumes/$DISPLAY_NAME (if present)"
hdiutil detach "/Volumes/$DISPLAY_NAME" -quiet || true

ATTACH_PLIST="$OUT_DIR/attach.plist"
hdiutil attach "$RW_DMG" -readwrite -noverify -noautoopen -plist > "$ATTACH_PLIST"
MOUNT_POINT=""
for i in 0 1 2 3 4 5 6 7 8 9; do
	mp=$(/usr/libexec/PlistBuddy -c "Print :system-entities:$i:mount-point" "$ATTACH_PLIST" 2>/dev/null || true)
	if [ -n "$mp" ]; then MOUNT_POINT="$mp"; fi
done
rm -f "$ATTACH_PLIST"
if [ -z "$MOUNT_POINT" ]; then
	echo "Failed to get mount point" >&2
	exit 1
fi
echo "Mounted at: $MOUNT_POINT"

# Copy background image into hidden .background folder
mkdir -p "$MOUNT_POINT/.background"
if [ -f "$BG_IMG" ]; then
	cp "$BG_IMG" "$MOUNT_POINT/.background/background.png"
fi

# AppleScript to set Finder view, background, window size, and icon positions
osascript <<OSA || true
tell application "Finder"
	tell disk "$DISPLAY_NAME"
		open
		set current view of container window to icon view
		set toolbar visible of container window to false
		set statusbar visible of container window to false
		set the bounds of container window to {100, 100, 900, 580}
		delay 0.3
		set viewOptions to the icon view options of container window
		set arrangement of viewOptions to not arranged
		set icon size of viewOptions to 120
		try
			set background picture of viewOptions to file ".background:background.png"
		end try
		-- Position the app and Applications link
		set appFile to item "$APP_FILENAME"
		set appsLink to item "Applications"
		set position of appFile to {180, 280}
		set position of appsLink to {620, 280}
		update without registering applications
		delay 0.5
		close
		delay 0.3
		open
		delay 0.3
	end tell
end tell
OSA

# Detach and convert to compressed DMG
hdiutil detach "$MOUNT_POINT" -quiet || true
hdiutil convert "$RW_DMG" -format UDZO -o "$DMG_NAME" -quiet
rm -f "$RW_DMG"

echo "Created: $DMG_NAME"
echo "Done. The app launcher will auto-detect or use embedded venv (Tk 8.6+ recommended)."

echo "Optional: code sign the app if you plan to distribute outside your machines."

# --- Codesign placeholders ---
# Set SIGN_IDENTITY to your Developer ID Application certificate name to enable signing
# Example: export SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
if [ -n "${SIGN_IDENTITY:-}" ]; then
	echo "Code signing .app with identity: $SIGN_IDENTITY"
	codesign --deep --force --options runtime --sign "$SIGN_IDENTITY" "$OUT_DIR/$APP_FILENAME" || true
	codesign --verify --verbose "$OUT_DIR/$APP_FILENAME" || true
fi

# --- Notarization placeholder (requires Xcode 13+ notarytool setup) ---
# Provide NOTARY_PROFILE (keychain profile) or NOTARY_APPLE_ID/NOTARY_TEAM_ID/NOTARY_PWD
# Example using notarytool profile:
#   xcrun notarytool submit "$DMG_NAME" --keychain-profile "$NOTARY_PROFILE" --wait
# After notarization, you can staple:
#   xcrun stapler staple "$DMG_NAME"
