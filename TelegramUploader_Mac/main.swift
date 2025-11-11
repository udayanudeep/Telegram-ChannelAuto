// SwiftUI "Apple Glass" style front-end for Telegram Uploader
// Provides a translucent (vibrant) macOS interface that wraps the Python script.
// The original minimal launcher has been replaced with a UI so you can manage
// uploads without the Tk window if desired. You can still run the Python GUI.

import SwiftUI
import AppKit

// MARK: - Glass Background using NSVisualEffectView
struct GlassBackground: NSViewRepresentable {
    let material: NSVisualEffectView.Material
    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = material
        v.blendingMode = .behindWindow
        v.state = .active
        return v
    }
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {}
}

// MARK: - Upload Controller
final class UploadController: ObservableObject {
    @Published var folderPath: String = ""
    @Published var token: String = ""
    @Published var channel: String = ""
    @Published var captionLink: String = ""
    @Published var includeLink: Bool = true
    @Published var logLines: [String] = []
    @Published var isRunning: Bool = false
    @Published var progress: Double = 0.0
    @Published var total: Double = 1.0
    @Published var etaText: String = "" // computed from python output
    @Published var savedTokens: [String] = []
    @Published var savedChannels: [String] = []
    @Published var status: UploadStatus = .idle
    // Advanced options for headless mode
    @Published var asDocument: Bool = false
    @Published var noAlbum: Bool = false
    @Published var delay: Double = 1.0
    @Published var jitter: Double = 0.4
    @Published var resume: Bool = false
    @Published var moveAfter: Bool = false
    @Published var workers: Int = 3
    @Published var skipValidate: Bool = false
    @Published var useCustomCaption: Bool = false
    @Published var customCaption: String = ""

    private let tokensStoreURL: URL = {
        let p = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".telegram_uploader_tokens.json")
        return p
    }()

    private var process: Process?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?

    init() {
        loadTokenStore()
    }

    func appendLog(_ line: String) {
        DispatchQueue.main.async {
            self.logLines.append(line)
        }
    }

    enum UploadStatus { case idle, running, stopping, done, failed }

    struct AnyEvent: Decodable { let type: String }
    struct ProgressEvent: Decodable {
        let type: String
        let sent: Int
        let total: Int
        let eta_seconds: Double?
        let timestamp: Double?
    }
    struct DoneEvent: Decodable {
        let type: String
        let success: Bool
        let timestamp: Double?
    }

    func loadTokenStore() {
        do {
            let data = try Data(contentsOf: tokensStoreURL)
            let obj = try JSONSerialization.jsonObject(with: data, options: []) as? [String: Any]
            let tokens = (obj?["tokens"] as? [String] ?? []).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            let channels = (obj?["channels"] as? [String] ?? []).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            self.savedTokens = Array(NSOrderedSet(array: tokens)) as? [String] ?? []
            self.savedChannels = Array(NSOrderedSet(array: channels)) as? [String] ?? []
        } catch {
            // no store yet; ignore
        }
    }

    func saveCurrentTokenChannel() {
        let t = token.trimmingCharacters(in: .whitespacesAndNewlines)
        let c = channel.trimmingCharacters(in: .whitespacesAndNewlines)
        if !t.isEmpty && !savedTokens.contains(t) {
            savedTokens.insert(t, at: 0)
        }
        if !c.isEmpty && !savedChannels.contains(c) {
            savedChannels.insert(c, at: 0)
        }
        let payload: [String: Any] = ["tokens": Array(savedTokens.prefix(50)), "channels": Array(savedChannels.prefix(50))]
        do {
            let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])
            try data.write(to: tokensStoreURL)
            appendLog("💾 Saved token/channel store")
        } catch {
            appendLog("⚠️ Failed saving tokens: \(error)")
        }
    }

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Select"
        if panel.runModal() == .OK, let url = panel.url {
            folderPath = url.path
        }
    }

    func launchPythonGUI() {
        // Launch original Tkinter GUI (embedded script) for full feature set.
        // Preflight: ensure selected Python has Tk available to avoid silent failure.
        let python = resolvePythonExecutable()
        if !ensureTkAvailable(python: python) {
            appendLog("❌ Tkinter not available in selected Python. Install Python with Tk (e.g., from python.org), or use headless mode.")
        }
        runPython(withArgs: [], preferredPython: python)
    }

    func startUploadHeadless() {
        // Headless invocation of python script with CLI flags.
        // Expand ~ if user picked a home-relative folder; if empty, attempt failing fast.
        let expandedFolder: String = {
            let trimmed = folderPath.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.hasPrefix("~") { return NSString(string: trimmed).expandingTildeInPath }
            return trimmed
        }()
        var args = ["--folder", expandedFolder, "--token", token, "--channel", channel]
        let link = captionLink.trimmingCharacters(in: .whitespacesAndNewlines)
        if includeLink && !link.isEmpty {
            args += ["--include-link", "--link", link]
        }
        if asDocument { args += ["--as-document"] }
        if noAlbum { args += ["--no-album"] }
        if resume { args += ["--resume"] }
        if moveAfter { args += ["--move-after"] }
        if skipValidate { args += ["--skip-validate"] }
        if useCustomCaption {
            args += ["--use-custom-caption"]
            let cc = customCaption.trimmingCharacters(in: .whitespacesAndNewlines)
            if !cc.isEmpty { args += ["--custom-caption", cc] }
        }
        // numeric flags
        args += ["--delay", String(format: "%.3f", delay)]
        args += ["--jitter", String(format: "%.3f", jitter)]
        args += ["--workers", String(max(1, min(10, workers)))]
        let python = resolvePythonExecutable()
        runPython(withArgs: args, preferredPython: python)
    }

    private func runPython(withArgs: [String], preferredPython: String? = nil) {
        guard !isRunning else { return }
        logLines.removeAll()
        progress = 0
        total = 1
        etaText = ""
        let resourcePath = Bundle.main.resourcePath ?? FileManager.default.currentDirectoryPath
        let scriptPath = URL(fileURLWithPath: resourcePath).appendingPathComponent("telegram_uploader_gui_python.py").path
        let task = Process()
        // Resolve a likely Python interpreter (Homebrew, local, then system) unless a preferred one was provided.
        let python = preferredPython ?? resolvePythonExecutable()
        task.executableURL = URL(fileURLWithPath: python)
        task.arguments = [scriptPath] + withArgs
        let outPipe = Pipe(); let errPipe = Pipe()
        self.stdoutPipe = outPipe; self.stderrPipe = errPipe
        task.standardOutput = outPipe
        task.standardError = errPipe

        outPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else { return }
            // Split by newlines to process multiple events per read
            let lines = chunk.split(whereSeparator: \.isNewline).map(String.init)
            for line in lines {
                self.handleStdoutLine(line)
            }
        }
        errPipe.fileHandleForReading.readabilityHandler = { handle in
            if let line = String(data: handle.availableData, encoding: .utf8), !line.isEmpty {
                self.appendLog("ERR: " + line.trimmingCharacters(in: .whitespacesAndNewlines))
            }
        }

        do {
            try task.run()
            isRunning = true
            process = task
            DispatchQueue.main.async { self.status = .running }
            appendLog("🚀 Launched Python uploader")
            DispatchQueue.global().async {
                task.waitUntilExit()
                DispatchQueue.main.async {
                    self.isRunning = false
                    self.appendLog("✅ Python exited status \(task.terminationStatus)")
                    if self.status == .running { self.status = (task.terminationStatus == 0) ? .done : .failed }
                    // Cleanup for relaunch
                    self.stdoutPipe?.fileHandleForReading.readabilityHandler = nil
                    self.stderrPipe?.fileHandleForReading.readabilityHandler = nil
                    self.stdoutPipe = nil
                    self.stderrPipe = nil
                    self.process = nil
                }
            }
        } catch {
            appendLog("❌ Launch failed: \(error)")
            DispatchQueue.main.async { self.status = .failed }
        }
    }

    func stop() {
        guard let p = process, isRunning else { return }
        p.terminate()
        appendLog("🛑 Termination requested")
        DispatchQueue.main.async { self.status = .stopping }
    }

    private func parseProgress(from line: String) {
        // Attempt to detect progress lines like: Sent X/Y (Z%) — ETA: 1m 2s
        let pattern = #"Sent (\d+)/(\d+) \(([^%]+)%\) — ETA: ([^\n]+)"#
        guard let r = try? NSRegularExpression(pattern: pattern, options: []) else { return }
        if let m = r.firstMatch(in: line, options: [], range: NSRange(location: 0, length: line.utf16.count)) {
            func group(_ i: Int) -> String {
                let range = m.range(at: i)
                if let swiftRange = Range(range, in: line) { return String(line[swiftRange]) }
                return ""
            }
            let sentStr = group(1)
            let totalStr = group(2)
            let etaStr = group(4)
            if let sentVal = Double(sentStr), let totalVal = Double(totalStr) {
                DispatchQueue.main.async {
                    self.progress = sentVal
                    self.total = max(totalVal, 1)
                    self.etaText = etaStr
                }
            }
        }
    }

    private func handleStdoutLine(_ line: String) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if trimmed.first == "{" { // try JSON event
            if let data = trimmed.data(using: .utf8) {
                do {
                    let any = try JSONDecoder().decode(AnyEvent.self, from: data)
                    switch any.type {
                    case "progress":
                        let ev = try JSONDecoder().decode(ProgressEvent.self, from: data)
                        DispatchQueue.main.async {
                            self.progress = Double(ev.sent)
                            self.total = Double(max(ev.total, 1))
                            if let eta = ev.eta_seconds, eta > 0 { self.etaText = self.secondsToReadable(eta) }
                        }
                    case "done":
                        let ev = try JSONDecoder().decode(DoneEvent.self, from: data)
                        DispatchQueue.main.async { self.status = ev.success ? .done : .failed }
                    default:
                        break
                    }
                    return
                } catch {
                    // fall through to legacy parsing/log
                }
            }
        }
        // Fallback: legacy progress regex + raw log append
        self.parseProgress(from: trimmed)
        self.appendLog(trimmed)
    }

    private func secondsToReadable(_ s: Double) -> String {
        let totalSeconds = Int(max(0, s))
        let hours = totalSeconds / 3600
        let minutes = (totalSeconds % 3600) / 60
        let seconds = totalSeconds % 60
        if hours > 0 { return "\(hours)h \(minutes)m \(seconds)s" }
        if minutes > 0 { return "\(minutes)m \(seconds)s" }
        return "\(seconds)s"
    }

    // MARK: - Python/Tk resolution helpers
    func resolvePythonExecutable() -> String {
        // Preference order: Homebrew arm64, Homebrew x86, /usr/local, system, then env (handled by caller if needed)
        let candidates = [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3"
        ]
        for p in candidates {
            if FileManager.default.isExecutableFile(atPath: p) { return p }
        }
        // Fallback to env if nothing else found
        return "/usr/bin/env/python3"
    }

    func ensureTkAvailable(python: String) -> Bool {
        // Best-effort check that tkinter can be imported.
        let check = Process()
        check.executableURL = URL(fileURLWithPath: python)
        check.arguments = ["-c", "import tkinter"]
        do {
            try check.run()
            check.waitUntilExit()
            return check.terminationStatus == 0
        } catch {
            return false
        }
    }
}

// MARK: - Content View
struct ContentView: View {
    @StateObject private var vm = UploadController()
    @State private var selectedToken: String = ""
    @State private var selectedChannel: String = ""
    @Environment(\.colorScheme) var colorScheme
    @State private var lastLogCount: Int = 0

    var body: some View {
        ZStack {
            GlassBackground(material: bgMaterial)
                .ignoresSafeArea()
            VStack(spacing: 18) {
                header
                formSection
                progressSection
                logSection
                actionButtons
            }
            .padding(24)
            .frame(minWidth: 740, minHeight: 520)
        }
    }

    var bgMaterial: NSVisualEffectView.Material {
        colorScheme == .dark ? .hudWindow : .underWindowBackground
    }

    var header: some View {
        HStack {
            VStack(alignment: .leading) {
                Text("Telegram Uploader")
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .shadow(radius: 2)
                Text("Glass UI front-end")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            statusPill
            
            Button(action: vm.launchPythonGUI) {
                Label("Open Tk GUI", systemImage: "rectangle.on.rectangle")
            }.buttonStyle(.borderedProminent)
        }
    }

    var statusPill: some View {
        let meta = statusMeta
        return Text(meta.text)
            .font(.caption.bold())
            .padding(.vertical, 6)
            .padding(.horizontal, 10)
            .background(
                Capsule().fill(meta.color.opacity(0.9))
            )
            .foregroundColor(.white)
            .shadow(color: Color.black.opacity(0.15), radius: 1, x: 0, y: 1)
    }

    var statusMeta: (text: String, color: Color) {
        switch vm.status {
        case .idle: return ("Idle", .gray)
        case .running: return ("Uploading…", .blue)
        case .stopping: return ("Stopping…", .orange)
        case .done: return ("Done", .green)
        case .failed: return ("Failed", .red)
        }
    }

    var formSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                TextField("Folder", text: $vm.folderPath)
                    .textFieldStyle(.roundedBorder)
                Button("Browse") { vm.chooseFolder() }
            }
            HStack(spacing: 12) {
                SecureField("Bot Token", text: $vm.token)
                    .textFieldStyle(.roundedBorder)
                Menu {
                    ForEach(vm.savedTokens, id: \.self) { t in
                        Button(t) { vm.token = t }
                    }
                } label: { Label("Tokens", systemImage: "key.fill") }
                Button("Save") { vm.saveCurrentTokenChannel() }
            }
            HStack(spacing: 12) {
                TextField("Channel ID", text: $vm.channel)
                    .textFieldStyle(.roundedBorder)
                Menu {
                    ForEach(vm.savedChannels, id: \.self) { c in
                        Button(c) { vm.channel = c }
                    }
                } label: { Label("Channels", systemImage: "number") }
                Button("Save") { vm.saveCurrentTokenChannel() }
            }
            HStack(spacing: 12) {
                Toggle("Include link in caption", isOn: $vm.includeLink)
                TextField("Caption Link (optional)", text: $vm.captionLink)
                    .textFieldStyle(.roundedBorder)
            }
            DisclosureGroup("Advanced") {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Toggle("Send as document", isOn: $vm.asDocument)
                        Toggle("No album (individual)", isOn: $vm.noAlbum)
                        Toggle("Resume", isOn: $vm.resume)
                        Toggle("Move after upload", isOn: $vm.moveAfter)
                        Toggle("Skip token validate", isOn: $vm.skipValidate)
                    }
                    HStack {
                        Stepper(value: $vm.workers, in: 1...10) { Text("Workers: \(vm.workers)") }
                        Spacer()
                        HStack {
                            Text("Delay")
                            TextField("1.0", value: $vm.delay, format: .number)
                                .frame(width: 60)
                            Text("Jitter")
                            TextField("0.4", value: $vm.jitter, format: .number)
                                .frame(width: 60)
                        }
                    }
                    HStack {
                        Toggle("Use custom caption", isOn: $vm.useCustomCaption)
                        TextField("Custom caption", text: $vm.customCaption)
                            .textFieldStyle(.roundedBorder)
                    }
                }.padding(.top, 6)
            }
        }
    }

    var progressSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            ProgressView(value: vm.progress, total: vm.total)
                .progressViewStyle(.linear)
                .tint(.accentColor)
                .overlay(alignment: .center) {
                    Text(progressLabel)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            Text("ETA: \(vm.etaText.isEmpty ? "–" : vm.etaText)")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    var progressLabel: String {
        let sent = Int(vm.progress)
        let total = Int(vm.total)
        guard total > 0 else { return "" }
        let pct = Double(sent) / Double(total) * 100.0
        return String(format: "%d / %d (%.1f%%)", sent, total, pct)
    }

    var logSection: some View {
        GroupBox(label: Label("Log", systemImage: "terminal")) {
            ScrollViewReader { proxy in
                ZStack(alignment: .topLeading) {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 4) {
                            ForEach(Array(vm.logLines.enumerated()), id: \.offset) { idx, line in
                                Text(line)
                                    .font(.system(size: 11, weight: .regular, design: .monospaced))
                                    .foregroundColor(line.contains("ERR") ? .red : .primary)
                                    .id(idx)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .frame(minHeight: 200)
                    // TimelineView drives smooth auto-scroll when new lines arrive
                    TimelineView(.animation) { _ in
                        Color.clear.onAppear { lastLogCount = vm.logLines.count }
                            .task {
                                // Diff detection: if count advanced, animate scroll
                                if vm.logLines.count != lastLogCount && vm.logLines.count > 0 {
                                    lastLogCount = vm.logLines.count
                                    withAnimation(.easeOut(duration: 0.25)) {
                                        proxy.scrollTo(vm.logLines.count - 1, anchor: .bottom)
                                    }
                                }
                            }
                    }
                }
            }
        }
        .groupBoxStyle(.automatic)
    }

    var actionButtons: some View {
        HStack {
            Button(action: vm.startUploadHeadless) {
                Label(vm.isRunning ? "Uploading…" : "Start Upload", systemImage: vm.isRunning ? "cloud.fill" : "cloud")
            }
            .disabled(vm.isRunning || vm.folderPath.isEmpty || vm.token.isEmpty || vm.channel.isEmpty)
            .buttonStyle(.borderedProminent)

            Button(action: vm.stop) {
                Label("Stop", systemImage: "stop.fill")
            }.disabled(!vm.isRunning)
            Spacer()
            Button(action: { vm.logLines.removeAll() }) {
                Label("Clear Log", systemImage: "trash")
            }
        }
    }
}

// MARK: - App Entry
@main
struct TelegramUploaderGlassApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 760, minHeight: 560)
        }
        .windowStyle(.automatic)
    }
}
