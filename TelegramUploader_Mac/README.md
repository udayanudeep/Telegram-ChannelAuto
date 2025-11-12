# TelegramUploader_Mac

This folder contains a SwiftUI macOS front‑end for the Telegram Uploader.
It can either:

- Launch the original Python Tk GUI ("Open Tk GUI" button), or
- Run headless uploads directly from the SwiftUI app with live progress and logs.

Overview
- `main.swift` — SwiftUI app (glass-style UI) that can run headless uploads and launch the Tk GUI.
- `Info.plist` — App metadata.
- `build_mac_app.sh` — Build script that compiles the SwiftUI launcher, copies the Python
  script into the bundle, and creates a `.dmg` (requires Xcode command-line tools).

Bundled Python runtime

The build script now creates an embedded Python virtual environment under:

- Contents/Resources/venv

and installs the minimal deps (`requests`, `pillow`). The app prefers this
runtime automatically; it falls back to system Python if the venv is missing.

Icon
Universal Binary

The build script produces a universal (arm64 + x86_64) Swift launcher when both architectures compile successfully and `lipo` is available. If only one architecture can be built on your host, it falls back gracefully to a single-arch binary.

Dual Architecture Virtual Envs

When possible it creates `venv-arm64` and `venv-x86_64` under `Contents/Resources`, plus a compatibility symlink `venv`. At runtime the launcher picks the matching venv for the current architecture, falling back to the generic symlink or system Python.


An app icon is generated at build time and embedded as `TelegramUploader.icns`.
You can replace it by dropping your own .icns into Contents/Resources and
updating Info.plist if you change the filename.

How to build (on macOS)

1. Ensure you have Xcode command-line tools installed (for `swiftc`) and `hdiutil` (comes with macOS):

   xcode-select --install

2. From this directory run:

```bash
./build_mac_app.sh
```

3. The script will produce `TelegramUploader.app` in this folder (and `TelegramUploader.dmg` when `hdiutil` is present).
4. Open the app and either:
  - Click "Open Tk GUI" to use the full Python UI, or
  - Fill the fields (Folder, Token, Channel, options) and click "Start Upload" to run headless.

Notes
- The app resolves a likely `python3` from common locations (Homebrew, system). It runs the bundled
  Python script at `Contents/Resources/telegram_uploader_gui_python.py`.
  - For predictable behavior, create a virtual environment and ensure `requests` is installed.
  - If Tk is not available in the chosen Python, the "Open Tk GUI" action will warn you.
- If you plan to distribute via the App Store or outside your machine you'll need to sign and
  notarize the app. This scaffold does not perform code signing.

Troubleshooting

- If Tk GUI doesn’t open, ensure your Python build includes Tk 8.6+.
- If the embedded venv is missing, re-run the build script. The app will still try system Python.

If you'd like, I can:
- Integrate a bundled Python runtime and dependencies (py2app or brief packaging) for a self-contained .app.
- Add an icon and better Info.plist metadata.
