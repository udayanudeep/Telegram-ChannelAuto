# Telegram Uploader (macOS app + DMG)

This repo contains a Tkinter GUI for uploading images/videos to a Telegram channel and a macOS packaging setup that produces a native `.app` and branded `.dmg`.

Highlights
- Material 3–inspired styling for the Tk GUI.
- Robust uploader with album batching, retries, and resume support.
- macOS launcher (Swift) that prefers an embedded Python venv and only runs with Tk 8.6+ to avoid old Tk crashes.
- Polished DMG with a custom background and drag-to-Applications layout.

## Build the macOS app and DMG

Requirements: Xcode command line tools (`swiftc`), `hdiutil`, and a modern Python (for the optional embedded venv).

From the repo root:

```bash
macos/build_macos_app.sh
```

The script creates:
- `dist/TelegramUploader.app` — the app bundle
- `dist/TelegramUploader.dmg` — compressed DMG with background and icon layout

Version stamping:
- Provide `VERSION` and/or `BUILD` env vars when building, e.g.
	- `VERSION=1.2.3 BUILD=45 macos/build_macos_app.sh`
- These set `CFBundleShortVersionString` and `CFBundleVersion` in the app’s `Info.plist`.

Embedded venv:
- If a local Python is found, the script creates `Contents/Resources/venv` and installs `requests`.
- At runtime, the Swift launcher prefers this venv. If it’s not available, it falls back to a modern system/Homebrew Python with Tk 8.6+.

## Runtime behavior and optional themes

To keep distribution clean and predictable, the app does not install extra Python packages at runtime.
- Optional theme packages are disabled by default inside the app bundle.
- If you want to enable runtime pip installs for development, set:
	- `ALLOW_RUNTIME_PIP=1`

Environment flags honored by the GUI:
- `DISABLE_TTKBOOTSTRAP=1` — disables ttkbootstrap import (set by the launcher in the bundled app)
- `DISABLE_TTKTHEMES=1` — disables ttkthemes auto-detection and any install attempt
- `ALLOW_RUNTIME_PIP=1` — allows best-effort `pip install` of missing packages (requests/ttkthemes) when developing

## Codesign and notarization (optional)

The build script includes placeholders. To sign the app (outside your machine), export your identity and run the script:

```bash
export SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
macos/build_macos_app.sh
```

After creating the DMG, notarize with `notarytool` (requires setup):

```bash
# Example (using a keychain profile)
xcrun notarytool submit dist/TelegramUploader.dmg --keychain-profile "MyNotaryProfile" --wait
xcrun stapler staple dist/TelegramUploader.dmg
```

## Troubleshooting

- If the GUI doesn’t open on macOS, you likely have an old Tk (8.5). The launcher will try other Pythons and prefers the embedded venv when present.
- If you see “missing requests,” rebuild to embed the venv (recommended) or manually install `requests` in your active Python.

## Development

Run the GUI directly during development:

```bash
python3 telegram_uploader_gui_python.py
```

Install deps into your venv:

```bash
python -m pip install requests pillow
```

Note: ttkbootstrap and ttkthemes are optional and disabled in the distributed app to avoid compatibility issues.