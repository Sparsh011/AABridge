import ServiceManagement
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
        func selection(_ agent: String) -> String {
            let saved = preferences.string(forKey: agent + "Path") ?? ""
            if !saved.isEmpty && FileManager.default.fileExists(atPath: (saved as NSString).expandingTildeInPath) { return saved }
            return seed[agent] as? String ?? saved
        }
        codex = selection("codex")
        claude = selection("claude")
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
            let sendToken = !diagnose && !token.isEmpty
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
                               : "An agent was found and all six sandbox canaries passed. Details lists each agent. Account and SSH access have not been checked.")
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
            HStack(spacing: 16) {
                Image(systemName: "point.3.connected.trianglepath.dotted").font(.system(size: 24, weight: .medium)).foregroundStyle(.white)
                    .frame(width: 52, height: 52)
                    .background(RoundedRectangle(cornerRadius: 13, style: .continuous).fill(LinearGradient(colors: [Theme.leader, Theme.worker], startPoint: .topLeading, endPoint: .bottomTrailing)))
                VStack(alignment: .leading, spacing: 3) {
                    Text("AABridge").font(.bridge(.title).weight(.semibold))
                    Text("Set up this Mac").font(.bridge(.callout)).foregroundStyle(.secondary)
                }
                Spacer()
                Chip(text: "Development preview")
            }
            Text("Install the bridge on this Mac. Agents are found automatically; choose a location below if needed. Add your real projects in Projects after setup.").font(.bridge(.callout)).foregroundStyle(.secondary)
            Panel {
                VStack(alignment: .leading, spacing: 18) {
                    Text("AABridge uses every agent it finds on this Mac: Codex, Claude Code, or both, each with its own login. Choose which ones work on a project when you add it.")
                        .font(.bridge(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Group {
                        DisclosureGroup("Advanced: Claude subscription token for small-proposal mode") {
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
                }
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
                // Full coding uses each agent's normal login; the proposal-mode token is optional.
                Button("Set up this Mac") { model.run(diagnose: false) }.buttonStyle(.borderedProminent)
                Spacer()
                Button("Refresh status") { model.refreshStatus() }
            }.disabled(model.busy)
            Text("Set up this Mac installs the bundled bridge. Pair your Macs in Devices, then add your projects from the sidebar. Existing repositories, keys, and provider credentials are preserved.")
                .font(.bridge(.caption)).foregroundStyle(.secondary)
            Divider()
            LoginItemToggle()
            Divider()
            VStack(alignment: .leading, spacing: 5) {
                Text("Connect directly in Devices").font(.bridge(.headline))
                Text("Exchange connection cards to use AABridge’s encrypted app connection. Keep both Macs reachable on the same network or a VPN.")
                    .font(.bridge(.callout)).foregroundStyle(.secondary)
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
            Text("Use All agent tasks in the sidebar to browse saved messages, supplied context and task events. Viewing does not start a model turn.")
                .font(.bridge(.caption)).foregroundStyle(.secondary)
        }.padding(.horizontal, 40).padding(.vertical, 32).frame(minWidth: 640, idealWidth: 860, minHeight: 640)
    }
}

/// Opens AABridge when you log in, so paired Macs reconnect after a restart without anyone opening the app.
struct LoginItemToggle: View {
    @State private var enabled = SMAppService.mainApp.status == .enabled
    @State private var problem = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Toggle("Open AABridge when I log in", isOn: Binding(get: { enabled }, set: { wanted in
                do {
                    if wanted { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
                    problem = ""
                } catch {
                    problem = "macOS didn’t accept the change: \(error.localizedDescription) Move AABridge to the Applications folder and try again, or add it yourself in System Settings → General → Login Items."
                }
                enabled = SMAppService.mainApp.status == .enabled
            }))
            Text("Keeps this Mac’s bridge running after a restart, so your other Macs can reach it and running teams can continue.")
                .font(.bridge(.caption)).foregroundStyle(.secondary)
            if !problem.isEmpty { Text(problem).font(.bridge(.caption)).foregroundStyle(.orange).textSelection(.enabled) }
        }
    }
}

@main
struct AABridgeDesktop: App {
    @StateObject private var model: SetupModel

    init() {
        if let index = CommandLine.arguments.firstIndex(of: "--self-test") {
            exit(runSelfTest(fixture: CommandLine.arguments.count > index + 1 ? CommandLine.arguments[index + 1] : nil) ? 0 : 1)
        }
        let payload = Bundle.main.resourceURL!.appendingPathComponent("SetupPayload")
        for (flag, panel) in [("--preview-task-info", "info"), ("--preview-task-edit", "edit")] {
            if let index = CommandLine.arguments.firstIndex(of: flag), CommandLine.arguments.count > index + 1 {
                exit(verifySharedHistoryView(resources: Bundle.main.resourceURL!, output: CommandLine.arguments[index + 1], panel: panel) ? 0 : 1)
            }
        }
        if let index = CommandLine.arguments.firstIndex(of: "--verify-shared-history"), CommandLine.arguments.count > index + 1 {
            exit(verifySharedHistoryView(resources: Bundle.main.resourceURL!, output: CommandLine.arguments[index + 1]) ? 0 : 1)
        }
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
            room.approval = "auto"; room.placement = TeamPlacement(mode: "branch", pull_request: true); room.fixes = 1; room.max_fixes = 3; room.notes_waiting = 1
            room.preview = TeamPreview(branch: "aabridge-live/3f9c2a", files: 4, time: now - 6)
            team.room = room; team.rooms = [room]; team.selected = room.id
            team.liveEntries = [LiveActivityEntry(id: "1", cursor: 1, role: "leader", agent: "codex", target: "local", stage: "plan", kind: "message", text: "Checking the existing booking flow before writing the plan.", state: "completed", time: now - 25),
                                LiveActivityEntry(id: "2", cursor: 2, role: "follower", agent: "claude", target: "mini", stage: "work", kind: "status", text: "Selected model: opus · effort: medium", state: "completed", time: now - 15),
                                LiveActivityEntry(id: "3", cursor: 3, role: "follower", agent: "claude", target: "mini", stage: "work", kind: "file", text: "Editing a file: src/BookingForm.tsx", state: "completed", time: now - 8),
                                LiveActivityEntry(id: "4", cursor: 4, role: "follower", agent: "claude", target: "mini", stage: "work", kind: "tool", text: "Running a project command", state: "running", time: now - 2)]
            exit(renderOwnView(TeamRoomView(model: team, active: false, preview: true), output: CommandLine.arguments[index + 1]) ? 0 : 1)
        }
        if let index = CommandLine.arguments.firstIndex(of: "--preview-projects"), CommandLine.arguments.count > index + 1 {
            let projects = ProjectSettingsModel(activity: TaskBrowserModel(resources: Bundle.main.resourceURL!))
            let links = [ProjectLink(name: "sparshur-android", repository: "/Users/you/Sparshur-Android", access: "edit"),
                         ProjectLink(name: "sparshur-mac", repository: "/Users/you/Sparshur-Mac", access: "read")]
            projects.projects = [LocalProject(id: "sparshur-service-claude", repository: "/Users/you/Sparshur-Service", adapter: "claude", scope: ["*"],
                                              verification: ProjectVerification(profile: "repository-v1"), execution_enabled: true, peers: [], links: links)]
            projects.peers = ["mbp"]; projects.peerLabels = ["mbp": "Sparsh’s MBP"]; projects.loaded = true
            projects.edit(projects.groups[0].key); projects.agentErrors = ["codex": "Codex isn’t installed on this Mac, or AABridge can’t find it. Choose its location in Settings."]
            exit(renderOwnView(ProjectSettingsView(model: projects), output: CommandLine.arguments[index + 1]) ? 0 : 1)
        }
        if let index = CommandLine.arguments.firstIndex(of: "--preview-done"), CommandLine.arguments.count > index + 2 {
            // Renders a finished autonomous task (--preview-done <png> done) or one waiting for the admin (--preview-done <png> gate).
            let activity = TaskBrowserModel(resources: Bundle.main.resourceURL!)
            let team = TeamRoomModel(activity: activity)
            let lead = TeamMember(target: "local", project: "app", agent: "codex"), follower = TeamMember(target: "mini", project: "app", agent: "claude")
            team.choices = [TeamChoice(id: "local/app", label: "Codex · MacBook · Fitness", member: lead), TeamChoice(id: "mini/app", label: "Claude · Mac mini · Fitness", member: follower)]
            let now = Date().timeIntervalSince1970
            let gate = CommandLine.arguments[index + 2] == "gate"
            var room = TeamRoom(id: "team-done", objective: "Add a new screen that takes weight as input", status: gate ? "awaiting_admin" : "completed", stage: gate ? "plan" : "review", leader: lead, follower: follower,
                                messages: [TeamEntry(id: "1", speaker: "You · admin", text: "Add a new screen that takes weight as input.", time: now - 900),
                                           TeamEntry(id: "2", speaker: "AABridge", text: "The leader owns this task. It plans, the worker builds, and the leader reviews the real changes.", time: now - 890),
                                           TeamEntry(id: "3", speaker: "codex · leader", text: "Plan: add WeightEntryScreen with a numeric field, validate 1–500 kg, save to the local store, and show the last 7 entries. Tests for validation and persistence.", time: now - 800)]
                                    + (gate ? [] : [TeamEntry(id: "4", speaker: "claude · follower", text: "Added WeightEntryScreen, validation and storage. 12 tests pass.", time: now - 500),
                                                    TeamEntry(id: "5", speaker: "codex · leader", text: "1. Negative weights are accepted by the save path.\nVERDICT: CHANGES_REQUESTED", time: now - 400),
                                                    TeamEntry(id: "6", speaker: "AABridge", text: "The leader asked for fixes (round 1 of 3). The worker is on it.", time: now - 399),
                                                    TeamEntry(id: "7", speaker: "claude · follower", text: "Rejected negative and zero weights in the save path and added two tests.", time: now - 200),
                                                    TeamEntry(id: "8", speaker: "codex · leader", text: "The fix is correct and covered by tests.\nVERDICT: APPROVED", time: now - 60)]),
                                error: nil, result: TeamResult(branch: "claude/task-1", worktree: "/tmp/task", result_commit: "a", base_commit: "b"))
            room.approval = "auto"; room.placement = TeamPlacement(mode: "branch", pull_request: true); room.verdict = gate ? nil : "approved"; room.fixes = gate ? 0 : 1; room.max_fixes = 3
            room.gate = gate ? TeamGate(kind: "plan", reason: "The leader’s plan is ready. Approve it to start building, or ask for changes.") : nil
            room.publication = gate ? nil : TeamPublication(mode: "branch", branch: "claude/weight-input-screen-3f9c2a", path: "/Users/you/Fitness", pull_request: "https://github.com/you/fitness/pull/42")
            if CommandLine.arguments.contains("--linked") {
                let branch = "claude/sync-weight-api-3f9c2a"
                room.publication = TeamPublication(mode: "branch", branch: branch, path: "/Users/you/Sparshur-Service", pull_request: "https://github.com/you/sparshur-service/pull/12",
                                                   linked: [LinkedPublication(name: "sparshur-android", branch: branch, path: "/Users/you/Sparshur-Android", pull_request: "https://github.com/you/sparshur-android/pull/31"),
                                                            LinkedPublication(name: "sparshur-mac", branch: branch, path: "/Users/you/Sparshur-Mac", pull_request: "https://github.com/you/sparshur-mac/pull/8")])
            }
            if CommandLine.arguments[index + 2] == "fail" {
                room = TeamRoom(id: "team-fail", objective: room.objective, status: "blocked", stage: "plan", leader: TeamMember(target: "mbp", project: "project-sparshur-service-codex", agent: "codex"), follower: follower,
                                messages: Array(room.messages!.prefix(2)), error: nil, result: nil)
                room.approval = "auto"; room.placement = TeamPlacement(mode: "branch", pull_request: true)
                room.failure = TeamFailure(problem: "Codex was updated on Sparsh’s MBP, so AABridge stopped it until the project is saved again.",
                                           fix: "On Sparsh’s MBP, open AABridge → Projects, select “project-sparshur-service-codex” and press Save. Then press Try again here.",
                                           machine: "Sparsh’s MBP", project: "project-sparshur-service-codex", retry: true)
            }
            if CommandLine.arguments[index + 2] == "question" {
                // The worker stopped to ask the admin something; older messages are paged out.
                room = TeamRoom(id: "team-question", objective: room.objective, status: "awaiting_admin", stage: "work", leader: lead, follower: follower,
                                messages: Array(room.messages!.prefix(3)) + [TeamEntry(id: "q", speaker: "claude · follower", text: "Should weights be stored in kilograms or pounds?", time: now - 30)], error: nil, result: nil)
                room.approval = "auto"; room.placement = TeamPlacement(mode: "branch", pull_request: true); room.earlier_messages = 12
                room.gate = TeamGate(kind: "question", reason: "Should weights be stored in kilograms or pounds?", cause: "question")
            }
            if CommandLine.arguments[index + 2] == "unapproved" { room.verdict = "changes" }
            if CommandLine.arguments[index + 2] == "mismatch" {
                room = TeamRoom(id: "team-mismatch", objective: room.objective, status: "blocked", stage: "plan", leader: lead, follower: follower, messages: Array(room.messages!.prefix(2)),
                                error: "The two project copies are on different Git commits: this Mac has 168fd34, Mac mini has 2646a64. Check out the same commit on both Macs, save the project in Projects on each, then press Try again.", result: nil)
                room.approval = "auto"; room.placement = TeamPlacement(mode: "branch", pull_request: true); room.error_code = "baseline_mismatch"
            }
            team.room = room; team.rooms = [room]; team.selected = room.id
            if CommandLine.arguments.contains("--info") {
                room.usage = ["leader": TeamUsage(input: 48_200, output: 6_100, cached: 31_000), "follower": TeamUsage(input: 120_400, output: 22_900, cached: 88_000)]
                room.updated_at = now - 60
                exit(renderOwnView(SessionInfoSheet(room: room, deviceLabel: team.deviceLabel).frame(width: 1200, height: 820), output: CommandLine.arguments[index + 1]) ? 0 : 1)
            }
            if CommandLine.arguments.contains("--card-only") {
                let panel = VStack(spacing: 16) { DeliveryCard(model: team, room: room, confirmMerge: .constant(false)); TeamMessageRow(entry: room.messages!.last!) }.padding(30).frame(width: 1200, height: 820, alignment: .top)
                exit(renderOwnView(panel, output: CommandLine.arguments[index + 1]) ? 0 : 1)
            }
            exit(renderOwnView(TeamRoomView(model: team, active: false, preview: true), output: CommandLine.arguments[index + 1]) ? 0 : 1)
        }
        if let index = CommandLine.arguments.firstIndex(of: "--preview-workspace"), CommandLine.arguments.count > index + 2 {
            // The project sidebar with sessions from both Macs: --preview-workspace <png> project|new|remote|single
            exit(renderOwnView(workspacePreview(resources: Bundle.main.resourceURL!, screen: CommandLine.arguments[index + 2]), output: CommandLine.arguments[index + 1]) ? 0 : 1)
        }
        if let index = CommandLine.arguments.firstIndex(of: "--preview-devices"), CommandLine.arguments.count > index + 1 {
            let activity = TaskBrowserModel(resources: Bundle.main.resourceURL!)
            let devices = DeviceStatusModel(activity: activity)
            devices.report = DeviceReport(service: true, listening: true, service_error: nil, devices: [])
            exit(renderOwnView(DevicesView(model: TeamRoomModel(activity: activity), devices: devices), output: CommandLine.arguments[index + 1]) ? 0 : 1)
        }
        if let index = CommandLine.arguments.firstIndex(of: "--preview-connections"), CommandLine.arguments.count > index + 1 {
            let devices = DeviceStatusModel(activity: TaskBrowserModel(resources: Bundle.main.resourceURL!))
            devices.report = DeviceReport(service: true, listening: true, service_error: nil,
                                          devices: [PairedDevice(id: "mbp", label: "Sparsh’s MBP", host: "Sparsh-MBP.local", online: true),
                                                    PairedDevice(id: "mini", label: "Mac mini", host: "Mac-mini.local", online: false)])
            if CommandLine.arguments.contains("--outdated") {
                devices.report = DeviceReport(service: true, listening: true, service_error: nil,
                                              devices: [PairedDevice(id: "mbp", label: "Sparsh’s MBP", host: "Sparsh-MBP.local", online: true, release: "older"),
                                                        PairedDevice(id: "mini", label: "Mac mini", host: "Mac-mini.local", online: true, release: "0.3.0.dev6")],
                                              app_release: "0.3.0.dev6", service_release: "0.3.0.dev5")
            }
            let sample = "## Plan\n\nAdd **WeightEntryScreen** with `validate()` and a [guide](https://example.com). Unsafe [link](file:///etc/hosts) stays plain.\n\n1. Save to the store\n2. Show entries\n   - last *seven*\n\n- [x] Tests\n- [ ] Docs\n\n> Keep main untouched.\n\n```swift\nlet weight = 72.5\n```\n\n| File | Change |\n|---|---|\n| `App.swift` | **new** |\n\n---\nVERDICT line stays text."
            let panel = VStack(alignment: .leading, spacing: 18) {
                DeviceToolbarStatus(model: devices) {}
                DeviceList(model: devices).frame(width: 420)
                Divider()
                MarkdownText(text: sample).frame(width: 760, alignment: .leading)
            }.padding(30).frame(width: 1200, height: 820, alignment: .topLeading)
            exit(renderOwnView(panel, output: CommandLine.arguments[index + 1]) ? 0 : 1)
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
            .defaultSize(width: 1200, height: 820)
            .windowToolbarStyle(.unified(showsTitle: false))
            .commands { CommandGroup(replacing: .newItem) {} }
    }
}

/// Fixture data for the project sidebar; nothing is read from or sent to a bridge.
func workspacePreview(resources: URL, screen: String) -> some View {
    let domain = "local.aabridge.workspace-preview." + UUID().uuidString
    let preferences = UserDefaults(suiteName: domain)!
    defer { preferences.removePersistentDomain(forName: domain) }
    let activity = TaskBrowserModel(resources: resources)
    let projects = ProjectSettingsModel(activity: activity)
    let team = TeamRoomModel(activity: activity, preferences: preferences)
    let devices = DeviceStatusModel(activity: activity)
    devices.report = DeviceReport(service: true, listening: true, service_error: nil, devices: [PairedDevice(id: "mbp", label: "Sparsh’s MBP", host: "Sparsh-MBP.local", online: true)])
    let verify = ProjectVerification(profile: "repository-v1")
    projects.projects = [LocalProject(id: "project-sparshur-service-claude", repository: "/Volumes/Work/Sparshur-Service", adapter: "claude", scope: ["*"], verification: verify, execution_enabled: true, peers: ["mbp"]),
                         LocalProject(id: "project-aabridge-claude", repository: "/Volumes/Work/AABridge", adapter: "claude", scope: ["*"], verification: verify, execution_enabled: true, peers: [], name: "AABridge"),
                         LocalProject(id: "project-aabridge-codex", repository: "/Volumes/Work/AABridge", adapter: "codex", scope: ["*"], verification: verify, execution_enabled: true, peers: [], name: "AABridge")]
    projects.peers = ["mbp"]; projects.peerLabels = ["mbp": "Sparsh’s MBP"]; projects.loaded = true
    team.deviceLabels = ["local": "This Mac", "mbp": "Sparsh’s MBP"]
    let mineClaude = TeamMember(target: "local", project: "project-sparshur-service-claude", agent: "claude")
    let theirCodex = TeamMember(target: "mbp", project: "project-sparshur-service-codex", agent: "codex")
    team.choices = [TeamChoice(id: "local/project-sparshur-service-claude", label: "Claude · This Mac · sparshur-service", member: mineClaude, projectName: "sparshur-service"),
                    TeamChoice(id: "mbp/project-sparshur-service-codex", label: "Codex · Sparsh’s MBP · sparshur-service", member: theirCodex, projectName: "sparshur-service"),
                    TeamChoice(id: "local/project-aabridge-claude", label: "Claude · This Mac · AABridge", member: TeamMember(target: "local", project: "project-aabridge-claude", agent: "claude"), projectName: "AABridge"),
                    TeamChoice(id: "local/project-aabridge-codex", label: "Codex · This Mac · AABridge", member: TeamMember(target: "local", project: "project-aabridge-codex", agent: "codex"), projectName: "AABridge")]
    let now = Date().timeIntervalSince1970
    func room(_ id: String, _ title: String, _ status: String, _ stage: String, _ lead: TeamMember, _ work: TeamMember, _ age: Double) -> TeamRoom {
        var room = TeamRoom(id: id, objective: title, status: status, stage: stage, leader: lead, follower: work,
                            messages: [TeamEntry(id: id + "-1", speaker: "You · admin", text: title, time: now - age - 60),
                                       TeamEntry(id: id + "-2", speaker: lead.agent + " · leader", text: "Plan: read the relay handlers, add the retry with backoff, and cover it with a test.", time: now - age)],
                            error: nil, result: nil)
        room.title = title; room.updated_at = now - age; room.approval = "auto"; room.placement = TeamPlacement(mode: "branch", pull_request: true)
        return room
    }
    team.rooms = [room("team-a", "Retry failed relay deliveries", "running", "work", mineClaude, theirCodex, 30),
                  room("team-b", "Add weight entry endpoint", "awaiting_admin", "plan", mineClaude, theirCodex, 400),
                  room("team-c", "Markdown preview in chats", "completed", "review", TeamMember(target: "local", project: "project-aabridge-codex", agent: "codex"), TeamMember(target: "local", project: "project-aabridge-claude", agent: "claude"), 9000)]
    team.rooms[2].verdict = "approved"
    team.room = team.rooms[0]; team.selected = "team-a"
    let workspace = WorkspaceModel(team: team, projects: projects, activity: activity, preferences: preferences)
    // A session the MacBook runs, in which this Mac's Claude is the worker.
    var remote = room("team-r", "Fix sign-in from settings page", "running", "work", TeamMember(target: "mbp", project: "project-sparshur-service-codex", agent: "codex"), mineClaude, 120)
    remote.host = "mbp"; remote.read_only = true
    workspace.remoteRooms = ["mbp": [remote]]
    switch screen {
    case "new":
        workspace.selection = .newSession("sparshur-service"); team.projectScope = "sparshur-service"; team.showAssignment = true; team.pickPair()
        team.draft = "Retry failed relay deliveries with backoff, and show the retry count in the admin page."
    case "remote":
        let viewer = TeamRoomModel(activity: activity, preferences: preferences, host: "mbp")
        viewer.deviceLabels = team.deviceLabels; viewer.room = remote; viewer.rooms = [remote]; viewer.selected = remote.id
        workspace.remote = viewer; workspace.selection = .session(host: "mbp", id: remote.id)
    case "single":
        workspace.selection = .newSession("aabridge"); team.projectScope = "aabridge"; team.showAssignment = true
        projects.projects.removeLast(); team.choices.removeLast(); team.pickPair()
    default:
        workspace.selection = .project("sparshur-service")
    }
    let detail: AnyView
    switch workspace.selection {
    case .newSession(let key):
        detail = AnyView(TeamRoomView(model: team, active: false, preview: true, missingAgents: workspace.missingAgents(key), addAgent: { _ in }))
    case .session: detail = AnyView(TeamRoomView(model: workspace.remote!, active: false, preview: true))
    default: detail = AnyView(ProjectHomeView(workspace: workspace, key: "sparshur-service"))
    }
    return HStack(spacing: 0) {
        WorkspaceSidebar(workspace: workspace, devices: devices).frame(width: 264).background(Theme.sidebar)
        Hairline(vertical: true)
        detail.frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.canvas)
    }.frame(width: 1200, height: 820)
}
