# TelegramUploader_Mac

This folder contains a minimal macOS app scaffold that launches the Python
GUI script `telegram_uploader_gui_python.py` bundled in the app Resources.

Overview
- `main.swift` — Swift launcher that executes the bundled Python script using `python3`.
- `Info.plist` — App metadata.
- `build_mac_app.sh` — Build script that compiles the Swift launcher, copies the Python
  script into the bundle, and creates a `.dmg` (requires macOS command-line tools).

How to build (on macOS)

1. Ensure you have Xcode command-line tools installed (for `swiftc`) and `hdiutil` (comes with macOS):

   xcode-select --install

2. From this directory run:

```bash
./build_mac_app.sh
```

3. The script will produce `TelegramUploader.app` in this folder (and `TelegramUploader.dmg` when `hdiutil` is present).

Notes
- The launcher uses the system `python3` (`/usr/bin/env python3`) to run the script located at
  `Contents/Resources/telegram_uploader_gui_python.py`. For predictable behavior, create a virtual
  environment and install `requests` and other dependencies, or modify the launcher to point to
  a bundled interpreter.
- If you plan to distribute via the App Store or outside your machine you'll need to sign and
  notarize the app. This scaffold does not perform code signing.

If you'd like, I can:
- Integrate a bundled Python runtime and dependencies (py2app or brief packaging). This is more work but yields a self-contained .app.
- Add an icon and better Info.plist metadata.
