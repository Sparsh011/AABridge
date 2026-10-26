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
        picker.title = agent == "codex" ? "Choose ChatGPT.app, Codex.app, or the Codex executable" : "Choose the Claude desktop app (Claude.app) or the claude executable"
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
                   arguments: [folder.appendingPathComponent("Setup.command").path, "--worker", worker, "--coding"]
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
            detail = "Check prerequisites, then select Set up this Mac."
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
        headline = kind == "setup" ? "Preparing this Mac…" : "Checking \(kind)…"
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
                        self.headline = kind == "setup" && self.ready ? "This Mac is ready" : (kind == "status" ? "Bridge supervisor is responding" : "Isolation checks passed")
                        self.detail = kind == "setup" ? "Open Devices to connect your other Mac, then add a project. Either Mac can lead or receive work."
                            : (kind == "status" ? "See Details for the supervisor response. This does not verify a remote agent session."
                               : "\(self.workerName) was found and all six sandbox canaries passed. Account and SSH access have not been checked.")
                    } else {
                        self.headline = kind == "status" ? "The supervisor is not responding" : "\(kind.capitalized) needs attention"
                        if !self.detail.contains("INCOMPLETE:") {
                            self.detail = "The check exited with code \(code). Expand Details and copy the final diagnostic."
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
            Text(title).font(.bridge(.headline))
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
                Image(systemName: "desktopcomputer.and.arrow.down").font(.system(size: 43.2)).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 4) {
                    Text("AABridge").font(.bridge(.largeTitle).bold())
                    Text("Set up this Mac").foregroundStyle(.secondary)
                }
                Spacer()
                Text("DEVELOPMENT PREVIEW").font(.bridge(.caption)).foregroundStyle(.secondary)
            }
            Text("Install the bridge on this Mac. Agents are found automatically; choose a location below if needed. Add your real projects in Projects after setup.")
            GroupBox {
                VStack(alignment: .leading, spacing: 18) {
                    Picker("Worker agent", selection: $model.worker) {
                        ForEach(SetupModel.workers, id: \.0) { Text($0.1).tag($0.0) }
                    }.frame(maxWidth: 360)
                    Text(model.worker == "claude"
                         ? "Use your existing Claude login on this Mac."
                         : "Use your existing Codex login on this Mac.")
                        .font(.bridge(.callout)).foregroundStyle(.secondary)
                    if model.worker == "claude" {
                        DisclosureGroup("Advanced: subscription token for proposal mode") {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Claude subscription token").font(.bridge(.headline))
                            SecureField(model.claudeTokenStored ? "Stored on this Mac · paste a new token to replace it" : "Paste the sk-ant-oat01-… token from claude setup-token", text: $model.claudeToken)
                                .textFieldStyle(.roundedBorder)
                            Text("Full coding uses your normal Claude login. Small proposal mode uses a token from claude setup-token.")
                                .font(.bridge(.caption)).foregroundStyle(.secondary)
                        }
                        }
                    }
                    location("Codex · select ChatGPT.app, Codex.app, or its executable", agent: "codex", value: $model.codex)
                    location("Claude Code · optional executable", agent: "claude", value: $model.claude)
                    Text("You can add more Codex or Claude Code projects later in Projects & scope.")
                        .font(.bridge(.callout)).foregroundStyle(.secondary)
                }.padding(10)
            }.disabled(model.busy)
            HStack(alignment: .top, spacing: 12) {
                if model.busy { ProgressView().controlSize(.small) }
                else { Image(systemName: model.ready ? "checkmark.circle.fill" : "info.circle").foregroundStyle(model.ready ? .green : .secondary) }
                VStack(alignment: .leading, spacing: 6) {
                    Text(model.headline).font(.bridge(.headline))
                    Text(model.detail).font(.bridge(.callout)).foregroundStyle(.secondary).textSelection(.enabled)
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
            HStack {
                Button("Check prerequisites") { model.run(diagnose: true) }
                Button("Set up this Mac") { model.run(diagnose: false) }.buttonStyle(.borderedProminent)
                    .disabled(model.worker == "claude" && !model.claudeTokenStored && model.claudeToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Spacer()
                Button("Refresh status") { model.refreshStatus() }
            }.disabled(model.busy)
            Text("Set up this Mac installs the bundled bridge, prepares a disposable demo, checks \(model.workerName) without a model call, and adds restricted SSH access for that demo. Existing repositories, keys, and provider credentials are preserved.")
                .font(.bridge(.caption)).foregroundStyle(.secondary)
            Divider()
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Remote Login is the connection route").font(.bridge(.headline))
                    Text("On this receiving Mac: System Settings → General → Sharing → Remote Login, allowed for your account. The app location tells AABridge what to launch; Remote Login lets the other Mac reach it.")
                        .font(.bridge(.callout)).foregroundStyle(.secondary)
                }
                Spacer()
                Button("System Settings") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/System Settings.app"))
                }
            }
            DisclosureGroup("Details", isExpanded: $expanded) {
                ScrollView {
                    Text(model.logs.isEmpty ? "Check results and setup progress will appear here." : model.logs)
                        .font(.bridge(.caption, design: .monospaced)).textSelection(.enabled)
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
                .font(.bridge(.caption)).foregroundStyle(.secondary)
        }.padding(28).frame(minWidth: 820, idealWidth: 880, minHeight: 700)
    }
}

@main
struct AABridgeDesktop: App {
    @StateObject private var model: SetupModel

    init() {
        let payload = Bundle.main.resourceURL!.appendingPathComponent("SetupPayload")
        if let index = CommandLine.arguments.firstIndex(of: "--preview-live"), CommandLine.arguments.count > index + 1 {
            let activity = TaskBrowserModel(resources: Bundle.main.resourceURL!)
            let team = TeamRoomModel(activity: activity)
            let lead = TeamMember(target: "local", project: "app", agent: "codex", model_settings: AgentModelSettings(model: "gpt-6.1-sol", effort: "high"))
            let follower = TeamMember(target: "mini", project: "app", agent: "claude", model_settings: AgentModelSettings(model: "opus", effort: "medium"))
            team.choices = [TeamChoice(id: "local/app", label: "Codex · MacBook · My business", member: lead), TeamChoice(id: "mini/app", label: "Claude · Mac mini · My business", member: follower)]
            let now = Date().timeIntervalSince1970
            var room = TeamRoom(id: "team-preview", objective: "Build a simple booking page for my business", status: "running", stage: "work", leader: lead, follower: follower,
                                messages: [TeamEntry(id: "admin", speaker: "You · admin", text: "Build a booking page that works well on a phone.", time: now - 30),
                                           TeamEntry(id: "leader", speaker: "Codex · leader", text: "I’ve planned the booking flow. Claude will build the form and check validation; I’ll review the result.", time: now - 20)], error: nil, result: nil)
            room.live = TeamLiveState(role: "follower", agent: "claude", target: "mini", stage: "work", status: "running", checked_at: now, supported: true,
                                      activity: WorkerActivity(phase: "tool", message: "Running a project command", time: now - 2), stream_error: nil, model_settings: follower.model_settings)
            team.room = room; team.rooms = [room]; team.selected = room.id
            team.liveEntries = [LiveActivityEntry(id: "1", cursor: 1, role: "leader", agent: "codex", target: "local", stage: "plan", kind: "message", text: "Checking the existing booking flow before writing the plan.", state: "completed", time: now - 25),
                                LiveActivityEntry(id: "2", cursor: 2, role: "follower", agent: "claude", target: "mini", stage: "work", kind: "status", text: "Selected model: opus · effort: medium", state: "completed", time: now - 15),
                                LiveActivityEntry(id: "3", cursor: 3, role: "follower", agent: "claude", target: "mini", stage: "work", kind: "file", text: "Editing a file: src/BookingForm.tsx", state: "completed", time: now - 8),
                                LiveActivityEntry(id: "4", cursor: 4, role: "follower", agent: "claude", target: "mini", stage: "work", kind: "tool", text: "Running a project command", state: "running", time: now - 2)]
            exit(renderOwnView(TeamRoomView(model: team, active: false, preview: true), output: CommandLine.arguments[index + 1]) ? 0 : 1)
        }
        if let index = CommandLine.arguments.firstIndex(of: "--preview-team"), CommandLine.arguments.count > index + 1 {
            let activity = TaskBrowserModel(resources: Bundle.main.resourceURL!)
            let team = TeamRoomModel(activity: activity)
            team.showAssignment = true
            team.choices = [TeamChoice(id: "local/app", label: "Codex · MacBook · My business", member: TeamMember(target: "local", project: "app", agent: "codex")),
                            TeamChoice(id: "mini/app", label: "Claude · Mac mini · My business", member: TeamMember(target: "mini", project: "app", agent: "claude"))]
            team.leader = "local/app"; team.follower = "mini/app"
            team.leaderModel = "gpt-6.1-sol"; team.leaderEffort = "high"; team.followerModel = "opus"; team.followerEffort = "medium"
            team.draft = "Build a simple booking page for my business. Make it easy to use on a phone."
            exit(renderOwnView(TeamRoomView(model: team, active: false, preview: true), output: CommandLine.arguments[index + 1]) ? 0 : 1)
        }
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
