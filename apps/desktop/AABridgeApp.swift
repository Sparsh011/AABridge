import AppKit
import Foundation
import SwiftUI

// A local operator interface. Selected paths never become shell source or peer
// protocol fields. The Python bridge remains the owner of permissions and tasks.
final class SetupModel: ObservableObject {
    @Published var codex: String
    @Published var claude: String
    // Worker agents with managed adapters, in display order.
    static let workers = [("codex", "Codex"), ("claude", "Claude Code")]
    @Published var worker: String
    @Published var claudeToken = ""
    @Published var busy = false
    @Published var headline = "Choose your agent locations, then check this Mac."
    @Published var detail = "No model calls are made during setup or diagnostics."
    @Published var logs = ""
    @Published var ready = false
    private let payload: URL
    private let preferences: UserDefaults
    private var staging: URL?

    init(payload: URL, preferences: UserDefaults = .standard) {
        self.payload = payload
        self.preferences = preferences
        let seed = (try? Data(contentsOf: payload.appendingPathComponent("runtime-paths.json")))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        codex = preferences.string(forKey: "codexPath") ?? seed["codex"] as? String ?? ""
        claude = preferences.string(forKey: "claudePath") ?? seed["claude"] as? String ?? ""
        let saved = preferences.string(forKey: "receiverWorker") ?? "codex"
        worker = SetupModel.workers.contains { $0.0 == saved } ? saved : "codex"
    }

    var workerName: String { SetupModel.workers.first { $0.0 == worker }?.1 ?? "Codex" }
    var claudeTokenStored: Bool {
        FileManager.default.fileExists(atPath: FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/AABridge/claude-oauth-token").path)
    }

    func choose(_ agent: String) {
        let picker = NSOpenPanel()
        picker.title = agent == "codex" ? "Choose ChatGPT.app, Codex.app, or the Codex executable" : "Choose the Claude Code executable"
        picker.message = "You can choose a location on this Mac or a connected external drive. Press ⌘⇧G to enter a path."
        picker.canChooseFiles = true
        picker.canChooseDirectories = true
        picker.allowsMultipleSelection = false
        picker.treatsFilePackagesAsDirectories = false
        picker.showsHiddenFiles = true
        if picker.runModal() == .OK, let url = picker.url {
            if agent == "codex" { codex = url.path } else { claude = url.path }
        }
    }

    private func prepare() throws -> URL {
        // Each launch gets a private working copy; signed bundle resources are
        // never edited, nor are existing installation/configuration files here.
        if staging == nil {
            let target = FileManager.default.temporaryDirectory.appendingPathComponent("aabridge-desktop-" + UUID().uuidString)
            try FileManager.default.createDirectory(at: target, withIntermediateDirectories: false,
                                                   attributes: [.posixPermissions: 0o700])
            for name in try FileManager.default.contentsOfDirectory(atPath: payload.path) {
                try FileManager.default.copyItem(at: payload.appendingPathComponent(name), to: target.appendingPathComponent(name))
            }
            staging = target
        }
        let target = staging!
        let selected: [String: Any] = ["version": 1,
                                      "codex": codex.isEmpty ? NSNull() : codex as Any,
                                      "claude": claude.isEmpty ? NSNull() : claude as Any]
        let data = try JSONSerialization.data(withJSONObject: selected, options: [.prettyPrinted, .sortedKeys])
        let file = target.appendingPathComponent("runtime-paths.json")
        try data.write(to: file, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        preferences.set(codex, forKey: "codexPath")
        preferences.set(claude, forKey: "claudePath")
        preferences.set(worker, forKey: "receiverWorker")
        return target
    }

    func run(diagnose: Bool) {
        guard !busy else { return }
        do {
            let folder = try prepare()
            // The token travels only over the installer's stdin, never argv or files.
            let token = claudeToken.trimmingCharacters(in: .whitespacesAndNewlines)
            let sendToken = !diagnose && worker == "claude" && !token.isEmpty
            launch(executable: URL(fileURLWithPath: "/bin/zsh"),
                   arguments: [folder.appendingPathComponent("Setup.command").path, "--worker", worker]
                       + (diagnose ? ["--diagnose"] : []) + (sendToken ? ["--claude-token-stdin"] : []),
                   directory: folder, kind: diagnose ? "diagnostics" : "setup",
                   input: sendToken ? Data((token + "\n").utf8) : nil)
            if sendToken { claudeToken = "" }
        } catch {
            headline = "Could not prepare setup."
            detail = error.localizedDescription
        }
    }

    func refreshStatus() {
        guard !busy else { return }
        let python = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/AABridgeInstall/venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            headline = "The bridge has not been installed on this Mac."
            detail = "Check prerequisites, then select Install receiver."
            return
        }
        launch(executable: python, arguments: ["-m", "aabridge_cli", "ping"], directory: payload, kind: "status")
    }

    private func append(_ line: String) {
        // Keep operator logs bounded. No provider transcripts or credentials are
        // requested by these commands; the installer emits bounded diagnostics.
        let clean = line.filter { !$0.isASCII || $0.asciiValue! >= 32 || $0 == "\t" }
        logs += String(clean.prefix(2000)) + "\n"
        if logs.count > 60000 { logs = String(logs.suffix(60000)) }
        if line.hasPrefix("STEP: ") { headline = String(line.dropFirst(6)) }
        if line.contains("SETUP INCOMPLETE:") || line.hasPrefix("DIAGNOSTICS INCOMPLETE:") {
            detail = String(line.prefix(1200))
        }
        if line == "AABRIDGE MINI READY" { ready = true }
    }

    private func launch(executable: URL, arguments: [String], directory: URL, kind: String, input: Data? = nil) {
        busy = true
        ready = false
        logs = ""
        headline = kind == "setup" ? "Preparing this Mac as a receiver…" : "Checking \(kind)…"
        detail = "Keep selected drives connected. No model inference is started. You can expand Details below."
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = executable
            process.arguments = arguments
            process.currentDirectoryURL = directory
            var environment = ProcessInfo.processInfo.environment
            environment["PATH"] = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin").path + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            environment["PYTHONUNBUFFERED"] = "1"
            process.environment = environment
            let stdin = input.map { _ in Pipe() }
            process.standardInput = stdin ?? FileHandle.nullDevice
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            do {
                try process.run()
                pipe.fileHandleForWriting.closeFile()
                if let stdin, let input {
                    stdin.fileHandleForWriting.write(input)
                    stdin.fileHandleForWriting.closeFile()
                }
                var pending = Data()
                while true {
                    let chunk = pipe.fileHandleForReading.availableData
                    if chunk.isEmpty { break }
                    pending.append(chunk)
                    while let end = pending.firstIndex(of: 10) {
                        let line = String(decoding: pending.prefix(upTo: end), as: UTF8.self)
                        pending.removeSubrange(...end)
                        DispatchQueue.main.async { self.append(line) }
                    }
                    if pending.count > 8192 {
                        let line = String(decoding: pending.prefix(2000), as: UTF8.self)
                        pending.removeAll(keepingCapacity: true)
                        DispatchQueue.main.async { self.append(line) }
                    }
                }
                if !pending.isEmpty {
                    let line = String(decoding: pending, as: UTF8.self)
                    DispatchQueue.main.async { self.append(line) }
                }
                process.waitUntilExit()
                let code = process.terminationStatus
                DispatchQueue.main.async {
                    self.busy = false
                    if code == 0 {
                        self.headline = kind == "setup" && self.ready ? "Receiver ready on this Mac" : (kind == "status" ? "Bridge supervisor is responding" : "Isolation checks passed")
                        self.detail = kind == "setup" ? "Return to your MacBook chat for the first two-device test. Remote Login must remain on while receiving SSH connections."
                            : (kind == "status" ? "See Details for the supervisor response. This does not verify a remote agent session."
                               : "\(self.workerName) was found and all six sandbox canaries passed. Account and SSH access have not been checked.")
                    } else {
                        self.headline = kind == "status" ? "The supervisor is not responding" : "\(kind.capitalized) needs attention"
                        if !self.detail.contains("INCOMPLETE:") {
                            self.detail = "The check exited with code \(code). Expand Details and share the final diagnostic with your MacBook chat."
                        }
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    self.busy = false
                    self.headline = "Could not start \(kind)"
                    self.detail = error.localizedDescription
                }
            }
        }
    }
}

struct SetupView: View {
    @ObservedObject var model: SetupModel
    @State private var expanded = false

    private func location(_ title: String, agent: String, value: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.headline)
            HStack {
                TextField("Automatic discovery", text: value).textFieldStyle(.roundedBorder)
                Button("Choose…") { model.choose(agent) }
                Button("Auto") { value.wrappedValue = "" }
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 14) {
                Image(systemName: "desktopcomputer.and.arrow.down").font(.system(size: 36)).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 4) {
                    Text("AABridge").font(.largeTitle.bold())
                    Text("Receiver setup · this Mac").foregroundStyle(.secondary)
                }
                Spacer()
                Text("DEVELOPMENT PREVIEW").font(.caption).foregroundStyle(.secondary)
            }
            Text("Choose the worker agent for this receiver, and where each agent lives on this Mac. ChatGPT can stay on your SSD; Claude Code can live on the internal drive.")
            GroupBox {
                VStack(alignment: .leading, spacing: 18) {
                    Picker("Worker agent", selection: $model.worker) {
                        ForEach(SetupModel.workers, id: \.0) { Text($0.1).tag($0.0) }
                    }.frame(maxWidth: 360)
                    Text(model.worker == "claude"
                         ? "Install receiver prepares a separate claude-demo project that runs on Claude Code with your Claude subscription."
                         : "Install receiver prepares the demo project that runs on Codex with your ChatGPT login.")
                        .font(.callout).foregroundStyle(.secondary)
                    if model.worker == "claude" {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Claude subscription token").font(.headline)
                            SecureField(model.claudeTokenStored ? "Stored on this Mac · paste a new token to replace it" : "Paste the sk-ant-oat01-… token from claude setup-token", text: $model.claudeToken)
                                .textFieldStyle(.roundedBorder)
                            Text("In Terminal on this Mac, run claude setup-token and sign in. Claude Code keeps its normal login in the Keychain, which the worker sandbox cannot open, so the worker uses this inference-only token instead. It is stored privately on this Mac and is never sent to the other Mac. API keys are refused.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    location("Codex · select ChatGPT.app, Codex.app, or its executable", agent: "codex", value: $model.codex)
                    location("Claude Code · optional executable", agent: "claude", value: $model.claude)
                    Text("You can add more Codex or Claude Code projects later in Projects & scope.")
                        .font(.callout).foregroundStyle(.secondary)
                }.padding(10)
            }.disabled(model.busy)
            HStack(alignment: .top, spacing: 12) {
                if model.busy { ProgressView().controlSize(.small) }
                else { Image(systemName: model.ready ? "checkmark.circle.fill" : "info.circle").foregroundStyle(model.ready ? .green : .secondary) }
                VStack(alignment: .leading, spacing: 6) {
                    Text(model.headline).font(.headline)
                    Text(model.detail).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
            HStack {
                Button("Check prerequisites") { model.run(diagnose: true) }
                Button("Install receiver") { model.run(diagnose: false) }.buttonStyle(.borderedProminent)
                    .disabled(model.worker == "claude" && !model.claudeTokenStored && model.claudeToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Spacer()
                Button("Refresh status") { model.refreshStatus() }
            }.disabled(model.busy)
            Text("Install receiver installs the bundled bridge, prepares a disposable demo, checks \(model.workerName) without a model call, and adds restricted SSH access for that demo. Existing repositories, keys, and provider credentials are preserved.")
                .font(.caption).foregroundStyle(.secondary)
            Divider()
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Remote Login is the connection route").font(.headline)
                    Text("On this receiving Mac: System Settings → General → Sharing → Remote Login, allowed for your account. The app location tells AABridge what to launch; Remote Login lets the other Mac reach it.")
                        .font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button("System Settings") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/System Settings.app"))
                }
            }
            DisclosureGroup("Details", isExpanded: $expanded) {
                ScrollView {
                    Text(model.logs.isEmpty ? "Check results and setup progress will appear here." : model.logs)
                        .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(10)
                }.frame(minHeight: 100, maxHeight: 180).background(Color.primary.opacity(0.04))
                HStack {
                    Spacer()
                    Button("Copy details") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(model.logs, forType: .string)
                    }
                }
            }
            Text("Use Tasks & conversations to browse saved messages, supplied context and task events. Viewing does not start a model turn.")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(28).frame(minWidth: 820, idealWidth: 880, minHeight: 700)
    }
}

@main
struct AABridgeDesktop: App {
    @StateObject private var model: SetupModel

    init() {
        let payload = Bundle.main.resourceURL!.appendingPathComponent("SetupPayload")
        if let index = CommandLine.arguments.firstIndex(of: "--verify-chat"), CommandLine.arguments.count > index + 2 {
            exit(verifyChatControls(resources: Bundle.main.resourceURL!, stateDirectory: CommandLine.arguments[index + 1], output: CommandLine.arguments[index + 2]) ? 0 : 1)
        }
        if let index = CommandLine.arguments.firstIndex(of: "--verify-activity"), CommandLine.arguments.count > index + 2 {
            let passed = verifyActivity(resources: Bundle.main.resourceURL!, stateDirectory: CommandLine.arguments[index + 1], output: CommandLine.arguments[index + 2])
            exit(passed ? 0 : 1)
        }
        if CommandLine.arguments.contains("--smoke-test") {
            let required = ["Setup.command", "setup_worker.py", "manifest.json", "coordinator.pub", "runtime-paths.json"]
            let okay = required.allSatisfy { FileManager.default.fileExists(atPath: payload.appendingPathComponent($0).path) }
            print(okay ? "Desktop resources verified; no installation or model call started." : "Desktop payload is incomplete.")
            exit(okay ? 0 : 1)
        }
        if let index = CommandLine.arguments.firstIndex(of: "--verify-gui"), CommandLine.arguments.count > index + 1 {
            // Render our own view, not a desktop screenshot. Exercise the same
            // background process/streaming path as the diagnostics button.
            let domain = "local.aabridge.verification." + UUID().uuidString
            let preferences = UserDefaults(suiteName: domain)!
            let check = SetupModel(payload: payload, preferences: preferences)
            NSApplication.shared.appearance = NSAppearance(named: .aqua)
            let view = NSHostingView(rootView: SetupView(model: check).environment(\.colorScheme, .light))
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 880, height: 800),
                                  styleMask: [.titled], backing: .buffered, defer: false)
            window.contentView = view
            view.appearance = NSAppearance(named: .aqua)
            view.frame = NSRect(x: 0, y: 0, width: 880, height: 800)
            RunLoop.main.run(until: Date().addingTimeInterval(0.2))
            view.layoutSubtreeIfNeeded()
            if let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                view.cacheDisplay(in: view.bounds, to: bitmap)
                try? bitmap.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: CommandLine.arguments[index + 1]))
            }
            check.run(diagnose: true)
            let deadline = Date().addingTimeInterval(60)
            while check.busy && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
            preferences.removePersistentDomain(forName: domain)
            print(check.logs)
            let okay = !check.busy && check.logs.contains("\"checks\":") &&
                (check.logs.contains("DIAGNOSTICS PASSED:") || check.logs.contains("DIAGNOSTICS INCOMPLETE:"))
            print(okay ? "GUI diagnostic process and streaming verified; no installation or model call started." : "GUI diagnostic verification failed.")
            exit(okay ? 0 : 1)
        }
        _model = StateObject(wrappedValue: SetupModel(payload: payload))
        NSApplication.shared.setActivationPolicy(.regular)
    }

    var body: some Scene {
        Window("AABridge", id: "setup") { DesktopView(setup: model, resources: Bundle.main.resourceURL!) }
            .defaultSize(width: 1160, height: 820)
            .commands { CommandGroup(replacing: .newItem) {} }
    }
}
