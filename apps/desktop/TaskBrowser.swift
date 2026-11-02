import AppKit
import Combine
import Foundation
import SwiftUI
import Darwin

struct MessageActor: Codable {
    let kind: String
    let name: String
}

struct ProjectLimits: Codable {
    let runtime_seconds: Int
    let max_turns: Int
    let max_revisions: Int
}

struct BridgeProject: Decodable, Identifiable {
    let id: String
    let worker: String
    let adapter: String
    let base_commit: String
    let scope: [String]
    let limits: ProjectLimits
    let execution_enabled: Bool
    let verification: ProjectVerification?
    var capabilities: [String: Bool]? = nil
    var links: [ProjectLink]? = nil
    var available: Bool { execution_enabled && ["codex", "claude"].contains(adapter) }
}

struct ProjectVerification: Decodable { let profile: String }
struct WorkerActivity: Decodable { let phase: String; let message: String; let time: Double }

struct TaskQuestion: Decodable { let id: String; let text: String }
struct TaskPermissions: Decodable { let can_control: Bool }
struct TaskState: Decodable {
    let id: String
    let status: String
    let recipient: String
    let sender: String
    let revision: Int
    let limits: ProjectLimits
    let question: TaskQuestion?
    let session_id: String?
    let activity: WorkerActivity?
    let error: String?
    let scope: [String]?
    var permissions: TaskPermissions? = nil
    var thread_id: String? = nil
    var objective: String? = nil
    var work_deleted: Bool? = nil
}

struct PendingChatAction: Codable {
    let action: String
    let target: String
    let taskID: String?
    let payload: Data
    var createdAt: Double? = nil
}

struct ActivityTarget: Decodable, Identifiable {
    let id: String
    let label: String
}

struct BridgeTask: Decodable, Identifiable {
    let id: String
    let project_id: String
    let status: String
    let objective: String
    let revision: Int
    let created_at: Double
    let updated_at: Double
    let recipient: String
    let sender: String
    var permissions: TaskPermissions? = nil
    var title: String? = nil
    var mode: String? = nil
    var displayTitle: String {
        if let title = title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return String(title.prefix(80)) }
        let original = objective.components(separatedBy: "Admin task:").last ?? objective
        if !objective.contains("Admin task:") && original.hasPrefix("You are the follower.") { return "Implementation task" }
        let line = original.components(separatedBy: .newlines).first(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }) ?? "Untitled task"
        let label = line.split(whereSeparator: { $0.isWhitespace }).prefix(8).joined(separator: " ")
        return String(label.prefix(80))
    }
    var stageLabel: String? {
        switch mode { case "plan": return "Plan"; case "work": return "Implementation"; case "review": return "Review"; default: return nil }
    }
}

struct BridgeMessage: Decodable, Identifiable {
    let cursor: Int
    let role: String
    let kind: String
    let text: String
    let part: Int
    let parts: Int
    let time: Double
    let recovered: Bool
    let actor: MessageActor?
    let recipient: MessageActor?
    let sender_identity: String?
    var id: Int { cursor }
    var speaker: String {
        switch role { case "coordinator": return "Coordinator → worker"
        case "worker": return "Worker → coordinator"
        case "context": return "Supplied context"
        default: return "AABridge" }
    }
}

final class TaskBrowserModel: ObservableObject {
    @Published var targets: [ActivityTarget] = []
    @Published var target = "local"
    @Published var tasks: [BridgeTask] = []
    @Published var selection: String?
    @Published var messages: [BridgeMessage] = []
    @Published var events = ""
    @Published var session = ""
    @Published var error = ""
    @Published var loading = false
    @Published var loadingDetail = false
    @Published var lastUpdated: Date?
    @Published var projects: [BridgeProject] = []
    @Published var taskState: TaskState?
    @Published var historyNotice = ""
    @Published var historyManagement = false
    @Published var actionBusy = false
    @Published var actionNotice = ""
    @Published var pendingAction: PendingChatAction?
    @Published var drafts: [String: String] = [:]
    @Published var newObjective = ""
    @Published var showNewTask = false
    @Published var showListLoader = false
    @Published var showDetailLoader = false
    @Published var acknowledgedEcho: PendingChatAction?
    private var acknowledgedTaskID: String?
    private var outboxUnsafe = false
    private let resources: URL
    private var generation = 0
    private var detailGeneration = 0
    private var preferredSelection: String?
    private var preferences: UserDefaults
    private let stateDirectory: String?

    init(resources: URL, preferences: UserDefaults = .standard, stateDirectory: String? = nil) {
        self.resources = resources
        self.preferences = preferences
        self.stateDirectory = stateDirectory
        do {
            self.pendingAction = try readOutbox()
            if let pending = pendingAction,
               let envelope = try JSONSerialization.jsonObject(with: pending.payload) as? [String: Any],
               let body = envelope["body"] as? [String: Any] {
                if pending.action == "submit" { self.newObjective = body["objective"] as? String ?? "" }
                else if let id = pending.taskID { self.drafts[id] = body["text"] as? String ?? "" }
            }
        }
        catch { self.error = error.localizedDescription; outboxUnsafe = true }
    }

    func call(_ action: String, target: String, task: String? = nil, input: Data? = nil) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        // -f skips the user's zsh startup files. Every UI refresh runs this, and
        // a slow or self-appending ~/.zshenv made each call take seconds.
        var args = ["-f", resources.appendingPathComponent("Activity.command").path, "--action", action]
        if let stateDirectory = stateDirectory { args += ["--state-dir", stateDirectory] }
        if target != "local" { args += ["--remote", target] }
        if target != "local" && ["tasks", "conversation", "events", "status", "task_work"].contains(action) { args.append("--shared") }
        if let task = task { args += ["--task-id", task] }
        if ["tasks", "conversation", "events"].contains(action) { args.append("--all") }
        process.arguments = args
        process.currentDirectoryURL = resources
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        process.environment = env
        let inputPipe = input == nil ? nil : Pipe()
        if let inputPipe = inputPipe { process.standardInput = inputPipe }
        else { process.standardInput = FileHandle.nullDevice }
        process.standardError = FileHandle.nullDevice
        let pipe = Pipe()
        process.standardOutput = pipe
        try process.run()
        if let input = input, let inputPipe = inputPipe {
            try inputPipe.fileHandleForWriting.write(contentsOf: input)
            try inputPipe.fileHandleForWriting.close()
        }
        pipe.fileHandleForWriting.closeFile()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard data.count <= 32 * 1024 * 1024 else { throw failure("The history response exceeds the viewer's size limit.") }
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let error = object["error"] as? [String: Any], let message = error["message"] as? String {
            throw failure(message)
        }
        guard process.terminationStatus == 0 else { throw failure("Could not read bridge activity. Check Python 3.11 and the selected receiver.") }
        return data
    }

    private func failure(_ message: String) -> NSError {
        NSError(domain: "AABridgeActivity", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    func discover() {
        guard targets.isEmpty else { return }
        loading = true
        showListLoader = true
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                struct Reply: Decodable { let targets: [ActivityTarget] }
                let reply = try JSONDecoder().decode(Reply.self, from: self.call("targets", target: "local"))
                DispatchQueue.main.async {
                    self.targets = reply.targets
                    let saved = self.pendingAction?.target ?? self.preferences.string(forKey: "activityTarget")
                    self.target = reply.targets.contains(where: { $0.id == saved }) ? saved! : (reply.targets.contains(where: { $0.id == "mini" }) ? "mini" : "local")
                    self.loading = false
                    self.showListLoader = false
                    self.refresh(silent: false)
                }
            } catch {
                DispatchQueue.main.async { self.loading = false; self.showListLoader = false; self.error = error.localizedDescription }
            }
        }
    }

    func changeTarget() {
        generation += 1
        detailGeneration += 1
        loading = false
        loadingDetail = false
        selection = nil
        tasks = []
        messages = []
        events = ""
        session = ""
        projects = []
        taskState = nil
        historyNotice = ""
        historyManagement = false
        lastUpdated = nil
        preferences.set(target, forKey: "activityTarget")
        refresh(silent: false)
    }

    func refresh(silent: Bool = false) {
        guard !loading && !actionBusy else { return }
        loading = true
        showListLoader = !silent && tasks.isEmpty
        generation += 1
        let token = generation, selectedTarget = target
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                struct Reply: Decodable { let tasks: [BridgeTask]; var history_notice: String? = nil; var capabilities: [String: Bool]? = nil }
                let reply = try JSONDecoder().decode(Reply.self, from: self.call("tasks", target: selectedTarget))
                struct ProjectReply: Decodable { let projects: [BridgeProject] }
                let projects = try JSONDecoder().decode(ProjectReply.self, from: self.call("projects", target: selectedTarget)).projects
                DispatchQueue.main.async {
                    guard token == self.generation else { return }
                    self.tasks = reply.tasks
                    self.historyNotice = reply.history_notice ?? ""
                    self.historyManagement = reply.capabilities?["history_management"] == true
                    self.projects = projects
                    self.loading = false
                    self.showListLoader = false
                    if self.pendingAction == nil && !self.outboxUnsafe { self.error = "" }
                    self.lastUpdated = Date()
                    if let preferred = self.preferredSelection, reply.tasks.contains(where: { $0.id == preferred }) {
                        self.preferredSelection = nil
                        if self.selection == preferred { self.loadDetail(silent: silent) }
                        else { self.selection = preferred }
                    } else if self.pendingNewTask {
                        self.selection = nil
                    } else if self.selection == nil || !reply.tasks.contains(where: { $0.id == self.selection }) {
                        self.selection = reply.tasks.first?.id
                    } else {
                        self.loadDetail(silent: silent)
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    guard token == self.generation else { return }
                    self.loading = false
                    self.showListLoader = false
                    self.error = error.localizedDescription
                }
            }
        }
    }

    func loadDetail(silent: Bool = false) {
        guard let selected = selection else { return }
        detailGeneration += 1
        let token = detailGeneration, selectedTarget = target
        loadingDetail = true
        showDetailLoader = !silent && messages.isEmpty
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                struct Reply: Decodable { let messages: [BridgeMessage] }
                let reply = try JSONDecoder().decode(Reply.self, from: self.call("conversation", target: selectedTarget, task: selected))
                let eventData = try self.call("events", target: selectedTarget, task: selected)
                let statusData = try self.call("status", target: selectedTarget, task: selected)
                let status = try JSONDecoder().decode(TaskState.self, from: statusData)
                let session = status.session_id ?? "Not started"
                let eventObject = try JSONSerialization.jsonObject(with: eventData)
                let pretty = try JSONSerialization.data(withJSONObject: eventObject, options: [.prettyPrinted, .sortedKeys])
                let eventText = String(decoding: pretty, as: UTF8.self)
                DispatchQueue.main.async {
                    guard token == self.detailGeneration && selected == self.selection else { return }
                    self.messages = reply.messages
                    self.events = eventText
                    self.session = session
                    self.taskState = status
                    self.acknowledgedEcho = nil
                    self.acknowledgedTaskID = nil
                    self.loadingDetail = false
                    self.showDetailLoader = false
                }
            } catch {
                DispatchQueue.main.async {
                    guard token == self.detailGeneration else { return }
                    self.loadingDetail = false
                    self.showDetailLoader = false
                    self.error = error.localizedDescription
                }
            }
        }
    }

    func selectTask() {
        messages = []
        events = ""
        session = ""
        taskState = nil
        loadDetail(silent: false)
    }

    func updateHistory(_ task: BridgeTask, title: String? = nil) {
        guard canAct, historyManagement else { return }
        let selectedTarget = target
        let action = title == nil ? "task_forget" : "task_rename"
        var body: [String: Any] = ["task_id": task.id]
        if let title = title { body["title"] = title }
        guard let payload = try? JSONSerialization.data(withJSONObject: ["key": "history-" + UUID().uuidString.lowercased(), "body": body]) else { return }
        actionBusy = true
        generation += 1
        detailGeneration += 1
        loading = false
        loadingDetail = false
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                _ = try self.call(action, target: selectedTarget, input: payload)
                DispatchQueue.main.async {
                    self.actionBusy = false
                    if title == nil {
                        self.tasks.removeAll(where: { $0.id == task.id })
                        if self.selection == task.id {
                            self.selection = nil
                            self.messages = []; self.events = ""; self.session = ""; self.taskState = nil
                        }
                    }
                    self.refresh(silent: true)
                }
            } catch {
                DispatchQueue.main.async { self.actionBusy = false; self.error = error.localizedDescription }
            }
        }
    }

    func export() {
        guard let task = tasks.first(where: { $0.id == selection }) else { return }
        let picker = NSSavePanel()
        let filename = task.displayTitle.components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }.joined(separator: "-")
        picker.nameFieldStringValue = String(filename.prefix(80)) + "-conversation.md"
        guard picker.runModal() == .OK, let url = picker.url else { return }
        var text = "# \(task.displayTitle)\n\nTask: \(task.id)\nReceiver: \(target)\nProject: \(task.project_id)\nStatus: \(task.status)\nSession: \(session)\n\n"
        text += "This export includes supplied context and accepted bridge messages. Older recovered entries are labelled. It is not a copy of internal model reasoning.\n\n"
        for message in messages {
            let recipient = recipientLabel(message)
            text += "## \(senderLabel(message))\(recipient.isEmpty ? "" : " → " + recipient) · \(message.kind)\(message.recovered ? " · RECOVERED" : "")\n\n"
            text += "\(Date(timeIntervalSince1970: message.time)) · part \(message.part)/\(message.parts)\n\n\(message.text)\n\n"
        }
        do { try text.write(to: url, atomically: true, encoding: .utf8) }
        catch { self.error = error.localizedDescription }
    }
}

extension TaskBrowserModel {
    var selectedTask: BridgeTask? { tasks.first(where: { $0.id == selection }) }
    var workerName: String { agentName(taskState?.recipient ?? selectedTask?.recipient ?? "agent") }
    var canAct: Bool { !actionBusy && pendingAction == nil && !outboxUnsafe }
    var canControlSelectedTask: Bool { taskState?.permissions?.can_control ?? selectedTask?.permissions?.can_control ?? true }
    var canReply: Bool {
        guard canAct, canControlSelectedTask, let state = taskState, state.id == selection else { return false }
        return state.work_deleted != true && (state.status == "waiting_question" || (state.status == "completed" && state.revision <= state.limits.max_revisions))
    }
    var replyLabel: String {
        if taskState?.status == "waiting_question" { return "Send answer" }
        return taskState?.status == "completed" ? "Request revision" : "Send"
    }
    var pendingText: String? {
        messageText(pendingAction)
    }
    func messageText(_ pending: PendingChatAction?) -> String? {
        guard let pending = pending,
              let envelope = (try? JSONSerialization.jsonObject(with: pending.payload)) as? [String: Any],
              let body = envelope["body"] as? [String: Any] else { return nil }
        return body["objective"] as? String ?? body["text"] as? String
    }
    func pendingRecipient(_ pending: PendingChatAction) -> String {
        if let envelope = (try? JSONSerialization.jsonObject(with: pending.payload)) as? [String: Any],
           let body = envelope["body"] as? [String: Any], let recipient = body["recipient"] as? String { return agentName(recipient) }
        return workerName
    }
    var visibleSend: PendingChatAction? {
        if let pending = pendingAction, pending.target == target, pending.action != "cancel",
           (pending.taskID == selection || (pending.action == "submit" && selection == nil)) { return pending }
        if let echo = acknowledgedEcho, echo.target == target, acknowledgedTaskID == selection { return echo }
        return nil
    }
    var pendingNewTask: Bool { pendingAction?.action == "submit" && pendingAction?.target == target && selection == nil }
    var composerHint: String {
        if !canControlSelectedTask { return "This task was created on the selected Mac. Open AABridge there to continue it; its shared history is read-only here." }
        if taskState?.work_deleted == true { return "Saved work was deleted. Start a new task to continue." }
        switch taskState?.status {
        case "waiting_question": return "Reply to \(workerName)'s question…"
        case "completed": return canReply ? "Ask \(workerName) for a revision…" : "Revision limit reached. Start a new task to continue."
        case "running", "queued", "cancelling": return "\(workerName) is working. Your draft stays here until it can receive a reply."
        default: return "Start a new task to send a message to an approved worker."
        }
    }
    func agentName(_ value: String) -> String {
        if value == "codex" { return "Codex" }
        if value == "claude" { return "Claude" }
        if value == "gemini" { return "Gemini" }
        return value
    }
    func actorLabel(_ actor: MessageActor) -> String {
        if actor.kind == "human" { return actor.name }
        if actor.kind == "coordinator" { return "Coordinator (\(actor.name))" }
        return agentName(actor.name)
    }
    func senderLabel(_ message: BridgeMessage) -> String {
        if let actor = message.actor {
            return actorLabel(actor) + (message.role == "worker" ? " · \(target == "local" ? "this Mac" : target)" : "")
        }
        if message.role == "worker" { return workerName + " · \(target == "local" ? "this Mac" : target)" }
        if message.role == "coordinator" { return "Coordinator (\(taskState?.sender ?? selectedTask?.sender ?? "unknown"))" }
        return "AABridge"
    }
    func recipientLabel(_ message: BridgeMessage) -> String {
        if let recipient = message.recipient { return actorLabel(recipient) }
        if message.role == "coordinator" { return workerName }
        if message.role == "worker" { return "Coordinator" }
        return ""
    }

    private var outboxURL: URL {
        let folder: URL
        if let stateDirectory = stateDirectory {
            folder = URL(fileURLWithPath: stateDirectory).deletingLastPathComponent().appendingPathComponent("desktop-outbox")
        } else {
            folder = FileManager.default.homeDirectoryForCurrentUser.resolvingSymlinksInPath().appendingPathComponent("Library/Application Support/AABridgeDesktop")
        }
        return folder.appendingPathComponent("pending-action.json")
    }
    private func checkPrivate(_ url: URL, directory: Bool) throws {
        let values = try FileManager.default.attributesOfItem(atPath: url.path)
        let type = values[.type] as? FileAttributeType
        let owner = (values[.ownerAccountID] as? NSNumber)?.uint32Value
        let mode = (values[.posixPermissions] as? NSNumber)?.intValue ?? 0o777
        let links = (values[.referenceCount] as? NSNumber)?.intValue ?? 0
        guard type == (directory ? .typeDirectory : .typeRegular), owner == getuid(), mode & 0o077 == 0,
              directory || links == 1 else { throw failure("The local pending-action file is not private. Review it locally before sending another task.") }
    }
    private func readOutbox() throws -> PendingChatAction? {
        guard FileManager.default.fileExists(atPath: outboxURL.path) else { return nil }
        try checkPrivate(outboxURL.deletingLastPathComponent(), directory: true)
        try checkPrivate(outboxURL, directory: false)
        let attributes = try FileManager.default.attributesOfItem(atPath: outboxURL.path)
        guard ((attributes[.size] as? NSNumber)?.intValue ?? Int.max) <= 262144 else { throw failure("The local pending action is oversized.") }
        return try JSONDecoder().decode(PendingChatAction.self, from: Data(contentsOf: outboxURL))
    }
    private func saveOutbox(_ action: PendingChatAction) throws {
        let folder = outboxURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try checkPrivate(folder, directory: true)
        if FileManager.default.fileExists(atPath: outboxURL.path) { try checkPrivate(outboxURL, directory: false) }
        try JSONEncoder().encode(action).write(to: outboxURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: outboxURL.path)
    }
    func clearPending() {
        do {
            if FileManager.default.fileExists(atPath: outboxURL.path) {
                try checkPrivate(outboxURL.deletingLastPathComponent(), directory: true)
                try checkPrivate(outboxURL, directory: false)
                try FileManager.default.removeItem(at: outboxURL)
            }
            pendingAction = nil
            actionNotice = "Pending send cleared locally. This does not cancel a task already accepted by the receiver."
            error = ""
        } catch { self.error = error.localizedDescription }
    }
    private func stage(_ action: String, body: [String: Any], taskID: String?) {
        guard canAct, action == "submit" || canControlSelectedTask else { return }
        do {
            let payload = try JSONSerialization.data(withJSONObject: ["key": "gui-" + UUID().uuidString.lowercased(), "body": body], options: [.sortedKeys])
            let pending = PendingChatAction(action: action, target: target, taskID: taskID, payload: payload, createdAt: Date().timeIntervalSince1970)
            try saveOutbox(pending)
            pendingAction = pending
            if action == "submit" { showNewTask = false; selection = nil }
            retryPending()
        } catch { self.error = error.localizedDescription }
    }
    func startTask(projectID: String, scope: [String]? = nil, settings: AgentModelSettings? = nil) {
        guard let project = projects.first(where: { $0.id == projectID }), project.available else {
            error = "Choose an enabled Codex or Claude project. Gemini managed workers are not implemented yet."
            return
        }
        guard !newObjective.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && newObjective.unicodeScalars.count <= 4000 else {
            error = "Enter task instructions of 1–4000 characters."
            return
        }
        guard AgentModelSettings.validModel(settings?.model ?? "") else { error = "Use a model ID without spaces or command options."; return }
        let selectedScope = scope ?? project.scope
        guard !selectedScope.isEmpty, selectedScope.count <= 16, Set(selectedScope).count == selectedScope.count,
              Set(selectedScope).isSubset(of: Set(project.scope)) else {
            error = "Choose 1–16 files within the receiver-approved scope. Add other files using Projects & scope on that Mac."
            return
        }
        var body: [String: Any] = ["project_id": project.id, "recipient": project.worker, "base_commit": project.base_commit,
            "objective": newObjective, "scope": selectedScope, "acceptance": [project.verification?.profile == "file-proposal-v1" ? "Return a scoped file proposal for human review; no application tests are run." : "Pass the locally configured verification profile."],
            "context": [], "limits": ["runtime_seconds": project.limits.runtime_seconds, "max_turns": project.limits.max_turns, "max_revisions": project.limits.max_revisions],
            "permission_profile": project.verification?.profile == "repository-v1" ? "coding-v1" : "proposal-v1"]
        if let selected = settings?.dictionary, !selected.isEmpty { body["model_settings"] = selected }
        stage("submit", body: body, taskID: nil)
    }
    func sendReply() {
        guard canReply, let state = taskState, let text = drafts[state.id], !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, text.unicodeScalars.count <= 4000 else { return }
        var body: [String: Any] = ["task_id": state.id, "revision": state.revision, "text": text]
        let action: String
        if state.status == "waiting_question", let question = state.question {
            body["question_id"] = question.id
            action = "answer"
        } else if state.status == "completed" { action = "revise" }
        else { return }
        stage(action, body: body, taskID: state.id)
    }
    func cancelTask() {
        guard canAct, canControlSelectedTask, let state = taskState, ["queued", "running", "waiting_question"].contains(state.status) else { return }
        stage("cancel", body: ["task_id": state.id], taskID: state.id)
    }
    func retryPending() {
        guard !actionBusy, let pending = pendingAction else { return }
        actionBusy = true
        generation += 1
        detailGeneration += 1
        loading = false
        loadingDetail = false
        showListLoader = false
        showDetailLoader = false
        error = ""
        actionNotice = "Sending \(pending.action) to \(pending.target == "local" ? "this Mac" : pending.target)…"
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let data = try self.call(pending.action, target: pending.target, input: pending.payload)
                let state = try JSONDecoder().decode(TaskState.self, from: data)
                DispatchQueue.main.async {
                    self.actionBusy = false
                    self.clearPending()
                    guard self.pendingAction == nil else { return }
                    self.acknowledgedEcho = pending.action == "cancel" ? nil : pending
                    self.acknowledgedTaskID = state.id
                    self.actionNotice = pending.action == "cancel" ? "Cancellation requested. Partial work is preserved." : "Message accepted by the receiver."
                    if pending.action == "submit" {
                        self.newObjective = ""
                        self.showNewTask = false
                    } else if let id = pending.taskID { self.drafts[id] = "" }
                    if self.target != pending.target { self.target = pending.target }
                    self.preferredSelection = state.id
                    self.selection = state.id
                    self.refresh(silent: true)
                }
            } catch {
                DispatchQueue.main.async {
                    self.actionBusy = false
                    self.error = error.localizedDescription
                    self.actionNotice = "The send is retained locally. Retry uses the same request key so an accepted task is not duplicated."
                }
            }
        }
    }
}

struct TaskBrowserView: View {
    @ObservedObject var model: TaskBrowserModel
    @State private var query = ""
    @State private var statusFilter = "All"
    @State private var autoRefresh = true
    @State private var editingTask: BridgeTask?
    @State private var infoTask: BridgeTask?
    @State private var deletingTask: BridgeTask?
    let active: Bool
    private let timer = Timer.publish(every: 5, on: .main, in: .common).autoconnect()

    private var filtered: [BridgeTask] {
        model.tasks.filter { task in
            (statusFilter == "All" || task.status == statusFilter) &&
            (query.isEmpty || (task.displayTitle + task.objective + task.project_id).localizedCaseInsensitiveContains(query))
        }
    }

    private func transcript(context: Bool) -> some View {
        ScrollViewReader { reader in ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                ForEach(model.messages.filter { ($0.role == "context") == context }) { message in
                    if !context { MessageBubble(model: model, message: message).id(message.cursor) }
                    else {
                    VStack(alignment: .leading, spacing: 9) {
                        HStack {
                            Label(model.senderLabel(message), systemImage: "info.circle")
                                .font(.bridge(.headline))
                            Spacer()
                            Text(message.kind.replacingOccurrences(of: "_", with: " ")).font(.bridge(.caption)).foregroundStyle(.secondary)
                        }
                        HStack {
                            Text(Date(timeIntervalSince1970: message.time).formatted(date: .abbreviated, time: .shortened))
                            if message.recovered { Text("Recovered · original history incomplete").foregroundStyle(.orange) }
                            if message.parts > 1 { Text("Part \(message.part)/\(message.parts)") }
                        }.font(.bridge(.caption)).foregroundStyle(.secondary)
                        Text(message.text).font(.bridge(context || message.kind == "proposal" || message.kind == "verified_result" ? .callout : .body,
                                                        design: context || message.kind == "proposal" || message.kind == "verified_result" ? .monospaced : .default))
                            .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }.padding(16).background(Color.primary.opacity(0.035)).clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                }
                if !context, let pending = model.visibleSend { PendingMessageBubble(model: model, pending: pending).id("pending-send") }
                if model.messages.isEmpty && !model.loadingDetail { Text("No messages have been loaded for this task.").foregroundStyle(.secondary).padding() }
            }.padding(18)
        }.onChange(of: model.messages.last?.cursor) { _ in
            if !context, let last = model.messages.last(where: { $0.role != "context" }) { reader.scrollTo(last.cursor, anchor: .bottom) }
        }.onChange(of: model.visibleSend?.payload) { _ in
            if !context && model.visibleSend != nil { reader.scrollTo("pending-send", anchor: .bottom) }
        } }
    }

    @ViewBuilder private func taskActions(_ task: BridgeTask) -> some View {
        Button { editingTask = task } label: { Label("Edit title", systemImage: "pencil") }
            .disabled(!model.canAct || !model.historyManagement)
        Button(role: .destructive) { deletingTask = task } label: { Label("Delete from history", systemImage: "trash") }
            .disabled(!model.canAct || !model.historyManagement)
        Divider()
        Button { model.selection = task.id; if model.taskState?.id != task.id { model.loadDetail() }; infoTask = task } label: { Label("Info", systemImage: "info.circle") }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Tasks & conversations").font(.bridge(.title2).bold())
                Spacer()
                Button { model.showNewTask = true } label: { Label("New task", systemImage: "square.and.pencil") }.disabled(!model.canAct)
                Picker("Receiver", selection: $model.target) {
                    ForEach(model.targets) { Text($0.label).tag($0.id) }
                }.frame(width: 280).disabled(model.actionBusy).onChange(of: model.target) { _ in model.changeTarget() }
                Toggle("Live", isOn: $autoRefresh).toggleStyle(.switch).controlSize(.small)
                Button { model.refresh() } label: { Image(systemName: "arrow.clockwise") }.help("Refresh without starting a model turn")
                    .disabled(model.showListLoader || model.actionBusy)
            }.padding(20)
            if !model.historyNotice.isEmpty {
                Text(model.historyNotice).font(.bridge(.callout)).foregroundStyle(.secondary).textSelection(.enabled).padding(14)
            }
            if !model.error.isEmpty {
                HStack(alignment: .top) {
                    Image(systemName: "exclamationmark.circle")
                    Text(model.error).textSelection(.enabled)
                    Spacer()
                }.font(.bridge(.callout)).foregroundStyle(.orange).padding(14).background(Color.orange.opacity(0.08))
            }
            if model.pendingAction != nil && model.selection == nil && model.visibleSend == nil { PendingSendView(model: model).padding(.horizontal, 16) }
            HSplitView {
                VStack(alignment: .leading, spacing: 10) {
                    TextField("Search tasks or projects", text: $query).textFieldStyle(.roundedBorder)
                    Picker("Status", selection: $statusFilter) {
                        ForEach(["All", "queued", "running", "waiting_question", "completed", "failed", "cancelling", "cancelled", "interrupted"], id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ")).tag($0) }
                    }
                    ScrollView {
                        LazyVStack(spacing: 8) {
                            ForEach(filtered) { task in
                                Button { model.selection = task.id } label: {
                                    VStack(alignment: .leading, spacing: 8) {
                                        Text(task.displayTitle).lineLimit(2).font(.bridge(.headline)).foregroundStyle(.primary)
                                        if task.permissions?.can_control == false { Label("Shared task · work controls read-only", systemImage: "eye").font(.bridge(.caption2)).foregroundStyle(.secondary) }
                                        if let stage = task.stageLabel { Text(stage).font(.bridge(.caption2)).foregroundStyle(.secondary) }
                                        HStack { Text(task.project_id); Spacer(); Text(task.status.replacingOccurrences(of: "_", with: " ")) }
                                            .font(.bridge(.caption)).foregroundStyle(.secondary)
                                        Text(Date(timeIntervalSince1970: task.updated_at), style: .date).font(.bridge(.caption2)).foregroundStyle(.secondary)
                                    }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                                        .background(model.selection == task.id ? Color.accentColor.opacity(0.12) : Color.primary.opacity(0.025))
                                        .clipShape(RoundedRectangle(cornerRadius: 10))
                                }.buttonStyle(.plain).contextMenu { taskActions(task) }
                            }
                        }
                    }.onChange(of: model.selection) { _ in model.selectTask() }
                    if model.showListLoader { ProgressView("Loading tasks…").controlSize(.small) }
                    Text("\(model.tasks.count) tasks · viewing does not use model inference").font(.bridge(.caption)).foregroundStyle(.secondary)
                }.padding(14).frame(minWidth: 260, idealWidth: 310, maxWidth: 380)
                VStack(alignment: .leading, spacing: 12) {
                    if model.selectedTask == nil, let pending = model.visibleSend {
                        Text("New task · \(model.target == "local" ? "this Mac" : model.target)").font(.bridge(.title3).bold()).padding(20)
                        ScrollView { PendingMessageBubble(model: model, pending: pending).padding(20) }
                        PendingSendView(model: model).padding(16)
                    } else if let task = model.tasks.first(where: { $0.id == model.selection }) {
                        VStack(alignment: .leading, spacing: 7) {
                            HStack(alignment: .top) {
                                Text(task.displayTitle).font(.bridge(.title3).bold()).lineLimit(2).textSelection(.enabled)
                                Spacer()
                                Menu { taskActions(task) } label: { Image(systemName: "ellipsis.circle") }
                                    .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Task options")
                            }
                            HStack { Text("\(model.workerName) · \(task.project_id) · \(task.status) · revision \(task.revision)"); Spacer(); Button("Export…") { model.export() }.disabled(model.actionBusy) }
                            if let scope = model.taskState?.scope { Text("Scope: " + scope.joined(separator: ", ")).font(.bridge(.caption)).foregroundStyle(.secondary).textSelection(.enabled) }
                            WorkerActivityView(model: model)
                            if model.showDetailLoader { ProgressView("Loading saved messages…").controlSize(.small) }
                        }.padding([.horizontal, .top], 20)
                        transcript(context: false)
                        TaskComposer(model: model)
                    } else {
                        Spacer()
                        VStack(spacing: 12) {
                            Image(systemName: "bubble.left.and.bubble.right").font(.system(size: 45.6)).foregroundStyle(.secondary)
                            Text(model.tasks.isEmpty ? "No tasks loaded" : "Select a task").font(.bridge(.title2))
                            Text("Choose an enrolled receiver to read its saved messages and progress.").foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity)
                        Spacer()
                    }
                }.frame(minWidth: 560, maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            HStack {
                Text("History stays on the receiving Mac. Paired connections use its existing project grants.")
                Spacer()
                if let date = model.lastUpdated { Text("Updated \(date.formatted(date: .omitted, time: .standard))") }
            }.font(.bridge(.caption)).foregroundStyle(.secondary).padding(12)
        }.onAppear { model.discover() }
            .onReceive(timer) { _ in if active && autoRefresh && !model.loading && !model.loadingDetail && !model.actionBusy { model.refresh(silent: true) } }
            .sheet(isPresented: $model.showNewTask) { NewTaskSheet(model: model) }
            .sheet(item: $editingTask) { task in
                EditTaskTitleSheet(title: task.displayTitle) { title in model.updateHistory(task, title: title) }
            }
            .sheet(item: $infoTask) { task in
                TaskInfoSheet(task: task, state: model.taskState?.id == task.id ? model.taskState : nil,
                              messages: model.selection == task.id ? model.messages : [], events: model.selection == task.id ? model.events : "")
            }
            .sheet(item: $deletingTask) { task in TaskDeletionSheet(browser: model, task: task) }
    }
}

struct DesktopView: View {
    @ObservedObject var setup: SetupModel
    @StateObject private var activity: TaskBrowserModel
    @StateObject private var projects: ProjectSettingsModel
    @StateObject private var team: TeamRoomModel
    @State private var section = 0

    init(setup: SetupModel, resources: URL) {
        self.setup = setup
        let browser = TaskBrowserModel(resources: resources)
        _activity = StateObject(wrappedValue: browser)
        _projects = StateObject(wrappedValue: ProjectSettingsModel(activity: browser))
        _team = StateObject(wrappedValue: TeamRoomModel(activity: browser))
    }

    var body: some View {
        TabView(selection: $section) {
            TeamRoomView(model: team, active: section == 0, navigate: { section = $0 }).tabItem { Label("Team", systemImage: "person.3") }.tag(0)
            ProjectSettingsView(model: projects).tabItem { Label("Projects", systemImage: "folder") }.tag(2)
            DevicesView(model: team).tabItem { Label("Devices", systemImage: "laptopcomputer.and.arrow.down") }.tag(1)
            TaskBrowserView(model: activity, active: section == 4).tabItem { Label("Task history", systemImage: "clock") }.tag(4)
            ScrollView { SetupView(model: setup) }.tabItem { Label("Settings", systemImage: "gearshape") }.tag(3)
        }.font(.bridge(.body)).padding(8).frame(minWidth: 1100, minHeight: 780)
    }
}

func verifyActivity(resources: URL, stateDirectory: String, output: String) -> Bool {
    let domain = "local.aabridge.activity-verification." + UUID().uuidString
    let preferences = UserDefaults(suiteName: domain)!
    preferences.set("local", forKey: "activityTarget")
    defer { preferences.removePersistentDomain(forName: domain) }
    NSApplication.shared.appearance = NSAppearance(named: .aqua)
    let model = TaskBrowserModel(resources: resources, preferences: preferences, stateDirectory: stateDirectory)
    model.discover()
    let deadline = Date().addingTimeInterval(45)
    while Date() < deadline {
        RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        if !model.error.isEmpty { print(model.error); return false }
        if model.selection != nil && !model.loading && !model.loadingDetail {
            if model.messages.isEmpty { model.loadDetail() } else { break }
        }
    }
    guard !model.tasks.isEmpty && !model.messages.isEmpty && !model.events.isEmpty else { return false }
    guard renderTaskBrowser(model: model, output: output) else { return false }
    print("Activity viewer verified through bundled backend and local IPC: \(model.tasks.count) tasks, \(model.messages.count) saved message parts. No model inference started.")
    return true
}

func verifySharedHistoryView(resources: URL, output: String, panel: String? = nil) -> Bool {
    let domain = "local.aabridge.shared-history-verification." + UUID().uuidString
    let preferences = UserDefaults(suiteName: domain)!
    defer { preferences.removePersistentDomain(forName: domain) }
    let fixtureState = FileManager.default.temporaryDirectory.appendingPathComponent("aabridge-shared-view-" + UUID().uuidString).appendingPathComponent("state").path
    let model = TaskBrowserModel(resources: resources, preferences: preferences, stateDirectory: fixtureState)
    let now = Date().timeIntervalSince1970
    model.targets = [ActivityTarget(id: "fixture-mini", label: "Fixture Mac mini")]
    model.target = "fixture-mini"
    model.tasks = [BridgeTask(id: "task-shared", project_id: "booking", status: "waiting_question", objective: "You are the follower. The human admin appointed codex on node-fixture as your leader. Implement the leader instructions below. Admin task: Align Sparshur clients and relay",
                             revision: 1, created_at: now - 60, updated_at: now, recipient: "claude", sender: "local", permissions: TaskPermissions(can_control: false), title: "Align Sparshur clients and relay", mode: "work")]
    model.historyManagement = true
    model.selection = "task-shared"
    model.taskState = TaskState(id: "task-shared", status: "waiting_question", recipient: "claude", sender: "local", revision: 1,
                               limits: ProjectLimits(runtime_seconds: 60, max_turns: 6, max_revisions: 3),
                               question: TaskQuestion(id: "question-fixture", text: "Which time zone should bookings use?"), session_id: nil,
                               activity: nil, error: nil, scope: ["*"], permissions: TaskPermissions(can_control: false))
    model.messages = [BridgeMessage(cursor: 1, role: "worker", kind: "question", text: "Which time zone should bookings use?", part: 1, parts: 1,
                                   time: now, recovered: false, actor: MessageActor(kind: "agent", name: "Claude"), recipient: nil, sender_identity: "local")]
    model.drafts["task-shared"] = "This reply must not be sent from the viewing Mac."
    model.sendReply(); model.cancelTask()
    guard !model.canControlSelectedTask && !model.canReply && model.pendingAction == nil else { return false }
    if panel == "info" {
        return renderOwnView(TaskInfoSheet(task: model.tasks[0], state: model.taskState, messages: model.messages, events: "{\"task_id\":\"task-shared\"}"), output: output)
    }
    if panel == "edit" { return renderOwnView(EditTaskTitleSheet(title: model.tasks[0].displayTitle) { _ in }, output: output) }
    guard renderTaskBrowser(model: model, output: output) else { return false }
    print("Shared history renders read-only; answer and cancel did not create an outbox send. No model inference started.")
    return true
}

func renderTaskBrowser(model: TaskBrowserModel, output: String) -> Bool {
    renderOwnView(TaskBrowserView(model: model, active: false), output: output)
}

func renderOwnView<Content: View>(_ content: Content, output: String) -> Bool {
    let view = NSHostingView(rootView: content.font(.bridge(.body)).environment(\.colorScheme, .light))
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 820), styleMask: [.titled], backing: .buffered, defer: false)
    window.contentView = view
    view.frame = NSRect(x: 0, y: 0, width: 1200, height: 820)
    RunLoop.main.run(until: Date().addingTimeInterval(0.2))
    view.layoutSubtreeIfNeeded()
    guard let original = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return false }
    view.cacheDisplay(in: view.bounds, to: original)
    guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: original.pixelsWide, pixelsHigh: original.pixelsHigh,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return false }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let bounds = NSRect(x: 0, y: 0, width: original.pixelsWide, height: original.pixelsHigh)
    NSColor.white.setFill()
    bounds.fill()
    let image = NSImage(size: bounds.size)
    image.addRepresentation(original)
    image.draw(in: bounds)
    NSGraphicsContext.restoreGraphicsState()
    do {
        try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
    } catch { return false }
    return true
}

func verifyChatControls(resources: URL, stateDirectory: String, output: String) -> Bool {
    let domain = "local.aabridge.chat-verification." + UUID().uuidString
    let preferences = UserDefaults(suiteName: domain)!
    preferences.set("local", forKey: "activityTarget")
    defer { preferences.removePersistentDomain(forName: domain) }
    NSApplication.shared.appearance = NSAppearance(named: .aqua)
    let model = TaskBrowserModel(resources: resources, preferences: preferences, stateDirectory: stateDirectory)
    func wait(_ condition: () -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(35)
        while Date() < deadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
            if condition() { return true }
        }
        print("Chat verification timed out: \(model.error) · \(model.actionNotice)")
        return false
    }
    model.discover()
    guard wait({ !model.loading && !model.tasks.isEmpty }),
          let questionTask = model.tasks.first(where: { $0.status == "waiting_question" }),
          let completedTask = model.tasks.first(where: { $0.status == "completed" }),
          let runningTask = model.tasks.first(where: { $0.status == "running" }) else { return false }
    let projectSettings = ProjectSettingsModel(activity: model)
    projectSettings.refresh()
    guard wait({ !projectSettings.busy && projectSettings.projects.contains(where: { $0.id == "files" }) }) else { return false }
    projectSettings.selected = "files"; projectSettings.chooseProject()
    guard projectSettings.scope.contains("notes/hello.txt") && projectSettings.canSave else { return false }
    guard renderOwnView(ProjectSettingsView(model: projectSettings), output: output + ".scope.png") else { return false }
    model.selection = runningTask.id; model.selectTask()
    guard wait({ !model.loadingDetail && model.taskState?.activity?.phase == "waiting_for_output" }) else { return false }
    guard renderTaskBrowser(model: model, output: output + ".active.png") else { return false }
    model.selection = questionTask.id
    model.selectTask()
    guard wait({ !model.loadingDetail && model.taskState?.status == "waiting_question" }) else { return false }
    model.drafts[questionTask.id] = "Unsent draft must survive polling"
    model.refresh(silent: true)
    guard !model.showListLoader && !model.showDetailLoader,
          wait({ !model.loading && !model.loadingDetail }),
          model.drafts[questionTask.id] == "Unsent draft must survive polling" else { return false }
    model.drafts[questionTask.id] = "Yes, return a + b. Sent through the native human composer."
    model.sendReply()
    guard model.actionBusy && model.visibleSend != nil else { return false }
    guard wait({ !model.actionBusy && model.pendingAction == nil && !model.loading && !model.loadingDetail && model.taskState?.status == "queued" }) else { return false }
    model.cancelTask()
    guard wait({ !model.actionBusy && model.pendingAction == nil && !model.loading && !model.loadingDetail && model.taskState?.status == "cancelled" }) else { return false }
    model.selection = completedTask.id
    model.selectTask()
    guard wait({ !model.loadingDetail && model.taskState?.status == "completed" }) else { return false }
    model.drafts[completedTask.id] = "Add a concise docstring. Sent through the native revision composer."
    model.sendReply()
    guard wait({ !model.actionBusy && model.pendingAction == nil && !model.loading && !model.loadingDetail && model.taskState?.revision == 2 }) else { return false }
    model.newObjective = "Native chat-control verification task; no model worker is scheduled."
    model.startTask(projectID: "files", scope: ["not-approved.txt"])
    guard model.pendingAction == nil && !model.error.isEmpty else { return false }
    model.startTask(projectID: "files", scope: ["notes/hello.txt"])
    guard model.actionBusy && model.pendingNewTask && model.visibleSend != nil && !model.showNewTask else { return false }
    guard renderTaskBrowser(model: model, output: output + ".pending.png") else { return false }
    guard wait({ !model.actionBusy && model.pendingAction != nil && !model.error.isEmpty }), let pending = model.pendingAction else { return false }
    // The fixture deliberately commits the first submit then drops its reply.
    // A new model must recover and retry the exact same durable request.
    let restored = TaskBrowserModel(resources: resources, preferences: preferences, stateDirectory: stateDirectory)
    guard restored.pendingAction?.payload == pending.payload && restored.newObjective == model.newObjective else { return false }
    restored.retryPending()
    guard wait({ !restored.actionBusy && restored.pendingAction == nil && !restored.loading }) else { return false }
    model.clearPending()
    model.selection = questionTask.id
    model.selectTask()
    guard wait({ !model.loadingDetail && model.taskState?.id == questionTask.id }) else { return false }
    model.updateHistory(questionTask, title: "Fix addition")
    guard wait({ !model.actionBusy && !model.loading && !model.loadingDetail && model.tasks.first(where: { $0.id == questionTask.id })?.displayTitle == "Fix addition" }) else { return false }
    let deletion = TaskDeletionModel(browser: model, task: questionTask)
    deletion.load()
    guard wait({ !deletion.loading && deletion.review != nil }),
          deletion.review?.can_delete_work == true,
          let repository = deletion.review?.repositories.first,
          let file = repository.files.first(where: { $0.path == "pending.txt" }) else { return false }
    guard renderOwnView(TaskDeletionSheet(model: deletion, selectedFile: repository.id + file.id), output: output + ".delete.png") else { return false }
    deletion.deleteWork = true; deletion.acknowledgedLoss = true; deletion.remove()
    guard wait({ deletion.finished && !model.actionBusy && !model.loading && !model.loadingDetail && !model.tasks.contains(where: { $0.id == questionTask.id }) }) else { return false }
    guard renderTaskBrowser(model: model, output: output) else { return false }
    print("Native chat controls verified over IPC: local project settings, scope subset, immediate pending bubble, active status, answer, revision, cancel, new task, durable retry after lost acknowledgement, and silent refresh preserving drafts. No model inference started.")
    return true
}
