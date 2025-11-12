import Foundation

// Swift launcher for the Telegram uploader Python GUI
// This launcher assumes a `telegram_uploader_gui_python.py` file is present
// inside the app bundle's Resources directory.

struct PythonCandidate {
    let kind: String   // e.g., "brew", "python.org", "env", "system"
    let exec: String   // either absolute path to python, or "/usr/bin/env"
    let args: [String] // arguments to run the interpreter (e.g., ["python3"] for env)
}

func runProcess(exec: String, args: [String], env: [String: String]) -> (status: Int32, stdout: String, stderr: String) {
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: exec)
    proc.arguments = args
    proc.environment = env

    let outPipe = Pipe()
    let errPipe = Pipe()
    proc.standardOutput = outPipe
    proc.standardError = errPipe

    do {
        try proc.run()
    } catch {
        return (status: -1, stdout: "", stderr: "run error: \(error)")
    }

    proc.waitUntilExit()
    let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
    let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
    let outStr = String(data: outData, encoding: .utf8) ?? ""
    let errStr = String(data: errData, encoding: .utf8) ?? ""
    return (status: proc.terminationStatus, stdout: outStr, stderr: errStr)
}

func detectWorkingPython(env baseEnv: [String: String]) -> PythonCandidate? {
    // Prefer Python builds that ship with modern Tk (>= 8.6). Try Homebrew and python.org first.
    var candidates: [PythonCandidate] = []

    // 1) Embedded venv inside the app bundle (Resources/venv/bin/python3)
    if let resPath = Bundle.main.resourcePath {
        let embedded = (resPath as NSString).appendingPathComponent("venv/bin/python3")
        if FileManager.default.fileExists(atPath: embedded) {
            candidates.append(PythonCandidate(kind: "embedded-venv", exec: embedded, args: []))
        }
    }

    // 2) System candidates
    candidates += [
        PythonCandidate(kind: "brew", exec: "/opt/homebrew/bin/python3", args: []),
        PythonCandidate(kind: "brew-intel", exec: "/usr/local/bin/python3", args: []),
        PythonCandidate(kind: "python.org-312", exec: "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3", args: []),
        PythonCandidate(kind: "python.org-311", exec: "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3", args: []),
        // Try PATH-resolved python3
        PythonCandidate(kind: "env", exec: "/usr/bin/env", args: ["python3"]),
        // Last resort: system python3 (often CommandLineTools) — likely to crash with old Tk 8.5
        PythonCandidate(kind: "system", exec: "/usr/bin/python3", args: [])
    ]

    for cand in candidates {
        // Build a small probe that ensures tkinter can be imported and TkVersion >= 8.6
        let code = [
            "import sys",
            "try:",
            "    import tkinter as tk",
            "    v = getattr(tk, 'TkVersion', 0.0)",
            "    print('TK_OK', v)",
            "    sys.exit(0)",
            "except Exception as e:",
            "    print('TK_FAIL', e)",
            "    sys.exit(1)",
        ].joined(separator: "\n")

        var env = baseEnv
        // Reduce noisy deprecation spam; has no functional effect if Tk is fine
        env["TK_SILENCE_DEPRECATION"] = "1"
        // Some environments need Framework builds to find GUI backends
        env["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

        var args = cand.args
        args += ["-c", code]

        let res = runProcess(exec: cand.exec, args: args, env: env)
        if res.status == 0, res.stdout.contains("TK_OK") {
            // Additional guard: ensure version >= 8.6
            if let line = res.stdout.split(separator: "\n").first(where: { $0.contains("TK_OK") }),
               let verStr = line.split(separator: " ").last,
               let ver = Double(verStr) {
                if ver >= 8.6 {
                    return cand
                }
            }
            // If unknown parsing, still accept a passing probe
            return cand
        }
        // If probe crashed (SIGABRT), terminationStatus may be non-zero; try next candidate.
    }
    return nil
}

let bundle = Bundle.main
guard let resourcePath = bundle.resourcePath else {
    // Fallback: try current directory
    fputs("\u{26A0}\u{FE0F} Could not find bundle resource path\n", stderr)
    exit(1)
}

let scriptPath = (resourcePath as NSString).appendingPathComponent("telegram_uploader_gui_python.py")
let fm = FileManager.default
guard fm.fileExists(atPath: scriptPath) else {
    fputs("\u{26A0}\u{FE0F} Python script missing in Resources: telegram_uploader_gui_python.py\n", stderr)
    exit(1)
}

// Start with current environment and add a few safe defaults
var env = ProcessInfo.processInfo.environment
env["PYTHONUNBUFFERED"] = "1"
env["TK_SILENCE_DEPRECATION"] = "1"
// Disable ttkbootstrap inside the bundled app to avoid incompatibility issues in some Python/Tk combos
env["DISABLE_TTKBOOTSTRAP"] = "1"

// Detect a working Python with Tk 8.6+
guard let python = detectWorkingPython(env: env) else {
    let msg = "No suitable Python 3 with Tk 8.6+ found.\n" +
              "Install Python from python.org or Homebrew (which bundles modern Tk),\n" +
              "then re-launch the app."
    fputs(msg + "\n", stderr)
    exit(2)
}

// Launch the actual app script
let launcher = Process()
launcher.environment = env

if python.kind == "env" {
    launcher.executableURL = URL(fileURLWithPath: python.exec)
    launcher.arguments = python.args + [scriptPath]
} else if python.args.isEmpty {
    launcher.executableURL = URL(fileURLWithPath: python.exec)
    launcher.arguments = [scriptPath]
} else {
    launcher.executableURL = URL(fileURLWithPath: python.exec)
    launcher.arguments = python.args + [scriptPath]
}

do {
    try launcher.run()
    launcher.waitUntilExit()
    exit(launcher.terminationStatus)
} catch {
    fputs("Failed to launch Python script: \(error)\n", stderr)
    exit(1)
}
