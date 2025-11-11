import Foundation

// Swift launcher for the Telegram uploader Python GUI
// This launcher assumes a `telegram_uploader_gui_python.py` file is present
// inside the app bundle's Resources directory.

let bundle = Bundle.main
guard let resourcePath = bundle.resourcePath else {
    // Fallback: try current directory
    print("⚠️ Could not find bundle resource path")
    exit(1)
}

let scriptPath = (resourcePath as NSString).appendingPathComponent("telegram_uploader_gui_python.py")

// Use /usr/bin/env python3 so the user's python environment is used
let launcher = Process()
launcher.executableURL = URL(fileURLWithPath: "/usr/bin/env")
launcher.arguments = ["python3", scriptPath]

// Inherit existing environment so virtualenvs and PATH work
launcher.environment = ProcessInfo.processInfo.environment

do {
    try launcher.run()
    launcher.waitUntilExit()
    exit(Int32(launcher.terminationStatus))
} catch {
    print("Failed to launch Python script: \(error)")
    exit(1)
}
