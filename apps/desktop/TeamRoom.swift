import AppKit
import Combine
import SwiftUI
import UserNotifications

struct TeamMember: Codable { let target: String; let project: String; let agent: String; var model_settings: AgentModelSettings? = nil }
struct TeamEntry: Decodable, Identifiable {
    let id: String; let speaker: String; let text: String; let time: Double
}
struct TeamResult: Decodable { let branch: String; let worktree: String; let result_commit: String; let base_commit: String }
struct TeamPlacement: Codable { var mode: String; var pull_request: Bool? = nil }
/// A build in progress, mirrored to a branch AABridge owns in the admin's project.
struct TeamPreview: Decodable { var branch: String? = nil; var files: Int? = nil; var time: Double? = nil; var error: String? = nil }
/// Tokens one role reported over the whole task. `cost` is the CLI's own estimate, when it gives one.
struct TeamUsage: Decodable {
    let input: Int; let output: Int; let cached: Int; var cost: Double? = nil; var priced: Bool? = nil
    var total: Int { input + output }
    /// "842", "12.3k", "1.2M"
    static func short(_ value: Int) -> String {
        value < 1000 ? "\(value)" : value < 1_000_000 ? String(format: "%.1fk", Double(value) / 1000) : String(format: "%.1fM", Double(value) / 1_000_000)
    }
    var detail: String {
        "\(input) in, \(output) out, \(cached) read from cache" + (priced == true && cost != nil ? String(format: ". The agent’s own cost estimate: $%.2f (not a bill; subscription plans are not charged per token)", cost!) : "")
    }
}
struct TeamGate: Decodable { let kind: String; let reason: String; var cause: String? = nil }
/// Why a stage stopped, and the one thing to do about it.
struct TeamFailure: Decodable { let problem: String; let fix: String; let machine: String; let project: String; let retry: Bool }
struct TeamPublication: Decodable {
    var mode: String? = nil; var branch: String? = nil; var path: String? = nil; var pull_request: String? = nil
    var merge: String? = nil; var merge_error: String? = nil; var error: String? = nil
    var linked: [LinkedPublication]? = nil
}
/// Where a linked project's changes went: its own branch and pull request.
struct LinkedPublication: Decodable, Identifiable {
    let name: String; var branch: String? = nil; var path: String? = nil; var pull_request: String? = nil; var error: String? = nil
    var id: String { name }
}
/// A related project a task can also open. Peers see only the name and access.
struct ProjectLink: Codable, Hashable, Identifiable {
    var name: String; var repository: String? = nil; var access: String
    var id: String { repository ?? name }
    var accessLabel: String { access == "edit" ? "can edit" : "read only" }
}
struct TeamRoom: Decodable, Identifiable {
    let id: String; let objective: String; let status: String; let stage: String
    let leader: TeamMember; let follower: TeamMember
    let messages: [TeamEntry]?; let error: String?; let result: TeamResult?
    var live: TeamLiveState? = nil
    var approval: String? = nil; var placement: TeamPlacement? = nil; var gate: TeamGate? = nil
    var publication: TeamPublication? = nil; var verdict: String? = nil
    var fixes: Int? = nil; var max_fixes: Int? = nil; var notes_waiting: Int? = nil
    var failure: TeamFailure? = nil
    var preview: TeamPreview? = nil
    var usage: [String: TeamUsage]? = nil
    /// Why the task is paused, the role in the middle of a step, and a commit match in progress.
    var error_code: String? = nil; var busy_role: String? = nil; var align: String? = nil
    var title: String? = nil; var answered: Bool? = nil; var earlier_messages: Int? = nil; var stage_started: Double? = nil
    /// The task keeps its first name; a follow-up message does not rename it.
    var displayTitle: String { title?.isEmpty == false ? title! : objective }
    var running: Bool { ["running", "stopping"].contains(status) }
    /// Finished, but the leader's last review did not approve the work.
    var unapproved: Bool { status == "completed" && approval != nil && verdict != "approved" && !(answered == true && verdict == nil) }
    /// The leader answered a question and no code was written in this task.
    var onlyAnswered: Bool { status == "completed" && answered == true && verdict == nil }
}
struct TeamChoice: Identifiable {
    let id: String; let label: String; let member: TeamMember
    var links: [ProjectLink] = []
}

/// Where the finished code goes, in the order the new-task form offers it.
struct PlacementOption: Identifiable {
    let id: String; let title: String; let detail: String; let icon: String
    var short: String { ["sandbox": "result kept in AABridge", "branch": "new branch", "worktree": "new branch in its own folder", "current": "your current branch"][id] ?? id }
    static let all = [
        PlacementOption(id: "sandbox", title: "Keep it in AABridge", detail: "Safest. Your project folder isn’t touched. You review the result first.", icon: "shippingbox"),
        PlacementOption(id: "branch", title: "New branch in my project", detail: "Adds a branch to your project. The branch you’re on and main stay as they are.", icon: "arrow.triangle.branch"),
        PlacementOption(id: "worktree", title: "New branch in a separate folder", detail: "The same new branch, checked out in its own folder next to your project (a Git worktree), so you can open and run it separately.", icon: "folder.badge.plus"),
        PlacementOption(id: "current", title: "My current branch", detail: "Adds the commits to the feature branch you have checked out. AABridge refuses to do this on main.", icon: "arrow.down.to.line"),
    ]
}

final class TeamRoomModel: ObservableObject {
    @Published var rooms: [TeamRoom] = []
    @Published var room: TeamRoom?
    @Published var selected = ""
    @Published var choices: [TeamChoice] = []
    @Published var leader = ""
    @Published var follower = ""
    @Published var draft = ""
    @Published var leaderModel = ""
    @Published var leaderEffort = ""
    @Published var followerModel = ""
    @Published var followerEffort = ""
    @Published var approval: String { didSet { preferences.set(approval, forKey: "teamApproval") } }
    @Published var placement: String { didSet { preferences.set(placement, forKey: "teamPlacement") } }
    @Published var pullRequest: Bool { didSet { preferences.set(pullRequest, forKey: "teamPullRequest") } }
    @Published var maxFixes: Int { didSet { preferences.set(maxFixes, forKey: "teamMaxFixes") } }
    @Published var liveEntries: [LiveActivityEntry] = []
    @Published var liveNotice = ""
    /// Older messages the admin paged back to, for the selected task only.
    @Published var earlier: [TeamEntry] = []
    @Published var loadingEarlier = false
    private var earlierRoom = ""
    /// Last status seen per task, so a change can raise a notification once.
    private var seenStatus: [String: String] = [:]
    private var askedToNotify = false
    private var liveCursor = 0
    private var liveRoom = ""
    /// A send is in flight. Polling uses `refreshing`, so buttons don't flicker.
    @Published var busy = false
    private var refreshing = false
    /// A rediscovery that arrived mid-refresh, kept so a reconnect isn't missed.
    var rediscover = false
    @Published var notice = ""
    @Published var showAssignment = false
    @Published var connectionCard = ""
    @Published var connectionHost = ""
    @Published var pastedCard = ""
    private let activity: TaskBrowserModel
    private let preferences: UserDefaults
    init(activity: TaskBrowserModel, preferences: UserDefaults = .standard) {
        self.activity = activity
        self.preferences = preferences
        approval = preferences.string(forKey: "teamApproval") ?? "ask"
        placement = preferences.string(forKey: "teamPlacement") ?? "sandbox"
        pullRequest = preferences.bool(forKey: "teamPullRequest")
        maxFixes = preferences.object(forKey: "teamMaxFixes") as? Int ?? 3
    }
    var settingsValid: Bool { AgentModelSettings.validModel(leaderModel) && AgentModelSettings.validModel(followerModel) }
    var trimmedDraft: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }
    var ready: Bool { settingsValid && !busy && !leader.isEmpty && !follower.isEmpty && leader != follower && !trimmedDraft.isEmpty && draft.count <= 3000 && placementAllowed(placement) }
    var terminal: Bool { room == nil || ["completed", "failed", "cancelled"].contains(room!.status) }
    var waiting: Bool { room?.status == "awaiting_admin" }
    var working: Bool { ["running", "stopping", "blocked"].contains(room?.status ?? "") }
    var leaderChoice: TeamChoice? { choices.first(where: { $0.id == leader }) }
    var followerChoice: TeamChoice? { choices.first(where: { $0.id == follower }) }
    /// The result can only reach a project folder on this Mac.
    var hasLocalMember: Bool { leaderChoice?.member.target == "local" || followerChoice?.member.target == "local" }
    func placementAllowed(_ mode: String) -> Bool { mode == "sandbox" || hasLocalMember }
    /// The agents name the branch during the task, so only its prefix is known here.
    var branchPreview: String { (followerChoice?.member.agent ?? "claude") + "/…" }
    private func envelope(_ body: [String: Any], key: String? = nil) throws -> Data {
        try JSONSerialization.data(withJSONObject: ["key": key ?? "team-ui-" + UUID().uuidString, "body": body])
    }
    func refresh(discover requested: Bool = false) {
        guard !refreshing && !busy else { rediscover = rediscover || requested; return }
        let discover = requested || rediscover
        rediscover = false
        refreshing = true
        let selection = selected
        let feedRoom = liveRoom
        let feedCursor = liveCursor
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                var found: [TeamChoice]? = nil
                var issues: [String] = []
                if discover {
                    struct Targets: Decodable { let targets: [ActivityTarget] }
                    struct Projects: Decodable { let projects: [BridgeProject] }
                    let targets = try JSONDecoder().decode(Targets.self, from: self.activity.call("targets", target: "local")).targets
                    var options: [TeamChoice] = []
                    for target in targets {
                        do {
                            let projects = try JSONDecoder().decode(Projects.self, from: self.activity.call("projects", target: target.id)).projects
                            for project in projects where project.available && project.verification?.profile == "repository-v1" {
                                let agent = project.adapter == "claude" ? "Claude" : "Codex"
                                options.append(TeamChoice(id: target.id + "/" + project.id, label: "\(agent) · \(target.label) · \(project.id)",
                                    member: TeamMember(target: target.id, project: project.id, agent: project.adapter), links: project.links ?? []))
                            }
                        } catch { issues.append("\(target.label): \(error.localizedDescription)") }
                    }
                    found = options
                }
                struct Reply: Decodable { let teams: [TeamRoom] }
                let rooms = try JSONDecoder().decode(Reply.self, from: self.activity.call("teams", target: "local")).teams
                let wanted = rooms.contains(where: { $0.id == selection }) ? selection : rooms.first?.id ?? ""
                var room: TeamRoom? = nil
                var feed: [LiveActivityEntry] = []
                var nextCursor = wanted == feedRoom ? feedCursor : 0
                var feedNotice = ""
                if !wanted.isEmpty {
                    room = try JSONDecoder().decode(TeamRoom.self, from: self.activity.call("team_status", target: "local", input: self.envelope(["team_id": wanted])))
                }
                if !wanted.isEmpty {
                    do {
                        for _ in 0..<4 {
                            let page = try JSONDecoder().decode(LiveActivityPage.self, from: self.activity.call("team_activity", target: "local", input: self.envelope(["team_id": wanted, "cursor": nextCursor])))
                            feed.append(contentsOf: page.entries)
                            nextCursor = page.cursor
                            if !page.has_more { break }
                        }
                    } catch {
                        feedNotice = "Live updates need the new bridge service on the admin Mac. Finish active work before updating; the task continues independently."
                    }
                }
                DispatchQueue.main.async {
                    let changed = self.liveRoom != wanted
                    if changed { self.liveEntries = []; self.liveRoom = wanted }
                    if self.earlierRoom != wanted { self.earlier = []; self.earlierRoom = wanted }
                    self.announce(rooms)
                    var merged = Dictionary(uniqueKeysWithValues: self.liveEntries.map { ($0.id, $0) })
                    for entry in feed { merged[entry.id] = entry }
                    self.liveEntries = Array(merged.values.sorted { $0.cursor < $1.cursor }.suffix(250))
                    self.liveCursor = nextCursor; self.liveNotice = feedNotice
                    if changed, let room = room {
                        self.leaderModel = room.leader.model_settings?.model ?? ""; self.leaderEffort = room.leader.model_settings?.effort ?? ""
                        self.followerModel = room.follower.model_settings?.model ?? ""; self.followerEffort = room.follower.model_settings?.effort ?? ""
                    }
                    self.rooms = rooms; self.room = room; self.selected = wanted; self.refreshing = false
                    if let found = found {
                        self.choices = found
                        if !found.contains(where: { $0.id == self.leader }) { self.leader = found.first?.id ?? "" }
                        if !found.contains(where: { $0.id == self.follower }) { self.follower = found.first(where: { $0.id != self.leader })?.id ?? "" }
                        self.notice = issues.joined(separator: "\n")
                    }
                }
            } catch { DispatchQueue.main.async { self.refreshing = false; self.notice = error.localizedDescription } }
        }
    }
    /// Messages not yet on screen: the server's count minus the ones paged in.
    var hiddenMessages: Int { max(0, (room?.earlier_messages ?? 0) - earlier.count) }
    func loadEarlier() {
        guard let room = room, hiddenMessages > 0, !loadingEarlier else { return }
        loadingEarlier = true
        let before = hiddenMessages, id = room.id
        DispatchQueue.global(qos: .userInitiated).async {
            let page = try? JSONDecoder().decode(TeamRoom.self, from: self.activity.call("team_status", target: "local", input: self.envelope(["team_id": id, "before": before])))
            DispatchQueue.main.async {
                self.loadingEarlier = false
                guard self.earlierRoom == id else { return }
                if let messages = page?.messages, !messages.isEmpty { self.earlier = messages + self.earlier }
                else { self.notice = "Couldn’t load earlier messages. Update the bridge service on this Mac in Settings → Set up this Mac, then try again." }
            }
        }
    }
    /// A macOS notification when a task stops needing only the agents, while AABridge is in the background.
    private func announce(_ rooms: [TeamRoom]) {
        guard Bundle.main.bundleIdentifier != nil else { return }
        let first = seenStatus.isEmpty
        for room in rooms {
            let before = seenStatus[room.id]
            seenStatus[room.id] = room.status
            guard !first, let before = before, before != room.status, !NSApp.isActive else { continue }
            let text: String
            switch room.status {
            case "awaiting_admin": text = "The team is waiting for your decision."
            case "blocked": text = "The team is paused and needs a fix."
            case "failed": text = "The task stopped with an error."
            case "completed": text = room.unapproved ? "Finished, but the leader did not approve the work." : room.answered == true ? "The leader answered your question." : "Done. The leader approved the work."
            default: continue
            }
            let content = UNMutableNotificationContent()
            content.title = room.displayTitle; content.body = text; content.sound = .default
            UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: room.id + "-" + room.status, content: content, trigger: nil))
        }
        if !askedToNotify, rooms.contains(where: { ["running", "stopping"].contains($0.status) }) {
            askedToNotify = true
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
    }
    func deviceLabel(_ target: String) -> String {
        if target == "local" { return "This Mac" }
        let label = choices.first(where: { $0.member.target == target })?.label.components(separatedBy: " · ")
        return label?.count ?? 0 > 1 ? label![1] : target
    }
    private func settings(_ model: String, _ effort: String) -> [String: String] {
        AgentModelSettings(model: model.trimmingCharacters(in: .whitespacesAndNewlines), effort: effort).dictionary
    }
    func send() {
        guard !busy, settingsValid else { return }
        if room != nil && !showAssignment {
            guard terminal, let room = room else { return }
            mutate("team_reply", body: ["team_id": room.id, "text": draft, "leader_settings": settings(leaderModel, leaderEffort), "follower_settings": settings(followerModel, followerEffort)])
            return
        }
        guard ready, let lead = leaderChoice, let follow = followerChoice else { return }
        func member(_ value: TeamMember, model: String, effort: String) -> [String: Any] {
            var body: [String: Any] = ["target": value.target, "project": value.project, "agent": value.agent]
            let selected = settings(model, effort)
            if !selected.isEmpty { body["model_settings"] = selected }
            return body
        }
        mutate("team_start", body: ["objective": draft, "approval": approval, "max_fixes": maxFixes,
                                    "placement": ["mode": placement, "pull_request": placement != "sandbox" && pullRequest],
                                    "leader": member(lead.member, model: leaderModel, effort: leaderEffort), "follower": member(follow.member, model: followerModel, effort: followerEffort)])
    }
    /// A note for the agents while they work; it reaches the next agent turn.
    func sendNote() {
        guard let room = room, !trimmedDraft.isEmpty, draft.count <= 3000 else { return }
        mutate("team_guide", body: ["team_id": room.id, "text": draft])
    }
    func decide(_ decision: String) {
        guard let room = room else { return }
        var body: [String: Any] = ["team_id": room.id, "decision": decision]
        if !trimmedDraft.isEmpty { body["text"] = draft }
        mutate("team_decide", body: body)
    }
    func control(_ action: String) {
        guard let room = room else { return }
        mutate(action, body: ["team_id": room.id])
    }
    /// Change the model of whichever agent is not in the middle of a step. It applies from that agent's next step.
    func applyModels() {
        guard let room = room, settingsValid else { return }
        var body: [String: Any] = ["team_id": room.id]
        if room.busy_role != "leader" { body["leader_settings"] = settings(leaderModel, leaderEffort) }
        if room.busy_role != "follower" { body["follower_settings"] = settings(followerModel, followerEffort) }
        keepDraft = true
        mutate("team_models", body: body)
    }
    /// A note being typed survives an action that is not about the note.
    private var keepDraft = false
    private func mutate(_ action: String, body: [String: Any]) {
        guard !busy, !hasPending else { return }
        do {
            let payload = try envelope(body)
            // Durable send keys survive a dropped acknowledgement or app restart.
            preferences.set(payload, forKey: "teamPendingPayload")
            preferences.set(action, forKey: "teamPendingAction")
            retry()
        } catch { notice = error.localizedDescription }
    }
    var hasPending: Bool { preferences.data(forKey: "teamPendingPayload") != nil }
    func retry() {
        guard !busy, let payload = preferences.data(forKey: "teamPendingPayload"), let action = preferences.string(forKey: "teamPendingAction") else { return }
        busy = true
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let room = try JSONDecoder().decode(TeamRoom.self, from: self.activity.call(action, target: "local", input: payload))
                DispatchQueue.main.async {
                    self.preferences.removeObject(forKey: "teamPendingPayload"); self.preferences.removeObject(forKey: "teamPendingAction")
                    self.room = room; self.selected = room.id; self.showAssignment = false
                    if !self.keepDraft { self.draft = "" }
                    self.keepDraft = false
                    self.busy = false; self.notice = ""; self.refresh()
                }
            } catch { DispatchQueue.main.async { self.busy = false; self.notice = error.localizedDescription + " Your send is saved; Retry will use the same key." } }
        }
    }
    func createCard() {
        guard !busy else { return }; busy = true
        let host = connectionHost.trimmingCharacters(in: .whitespacesAndNewlines)
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let options = try JSONSerialization.data(withJSONObject: ["host": host])
                let data = try self.activity.call("connection-card", target: "local", input: options)
                let card = String(decoding: data, as: UTF8.self)
                DispatchQueue.main.async { self.connectionCard = card; self.busy = false; self.notice = "Copy this card to your other Mac. Cards contain public connection details, never private keys." }
            } catch { DispatchQueue.main.async { self.busy = false; self.notice = error.localizedDescription } }
        }
    }
    func connect() {
        guard !busy, let data = pastedCard.data(using: .utf8) else { return }; busy = true
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                _ = try self.activity.call("connect", target: "local", input: data)
                DispatchQueue.main.async { self.busy = false; self.notice = "Connection saved. Exchange both cards, then share a project in Projects. The other Mac must be reachable when you start a task."; self.pastedCard = "" }
            } catch { DispatchQueue.main.async { self.busy = false; self.notice = error.localizedDescription } }
        }
    }
}

struct TeamRoomView: View {
    @ObservedObject var model: TeamRoomModel
    let active: Bool
    var preview = false
    /// Switches the main window to another section (Projects, Devices, Settings).
    var navigate: (Int) -> Void = { _ in }
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        HSplitView {
            TeamSidebar(model: model).frame(minWidth: 250, idealWidth: 280, maxWidth: 330)
            VStack(spacing: 0) {
                if model.showAssignment || model.room == nil {
                    NewTeamTaskView(model: model, navigate: navigate)
                } else if let room = model.room {
                    TeamChatView(model: model, room: room)
                }
                if !model.notice.isEmpty || model.hasPending {
                    HStack(alignment: .top) {
                        Image(systemName: "exclamationmark.bubble").foregroundStyle(.orange)
                        Text(model.notice.isEmpty ? "A message didn’t reach the team yet. It’s saved; retry to send it with the same key." : model.notice).textSelection(.enabled)
                        Spacer()
                        if model.hasPending { Button("Retry saved send") { model.retry() }.disabled(model.busy) }
                    }.padding(14).background(Color.orange.opacity(0.08))
                }
            }.frame(minWidth: 680)
        }.onAppear { if !preview { model.refresh(discover: true) } }
            .onReceive(timer) { _ in if active && !model.showAssignment { model.refresh() } }
    }
}

struct DevicesView: View {
    @ObservedObject var model: TeamRoomModel
    @ObservedObject var devices: DeviceStatusModel
    @StateObject private var nearby = NearbyMacs()
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 22) {
            Text("Connect your Macs").font(.bridge(.title).bold())
            Text("Optional. Skip this if both agents run on this Mac. To use a second Mac, exchange a connection card once in each direction. Either Mac can then lead or do the work.").foregroundStyle(.secondary)
            if devices.report != nil && devices.devices.isEmpty {
                Card(tint: .accentColor) {
                    Label("No Macs paired, and you may not need one", systemImage: "laptopcomputer.and.arrow.down").font(.bridge(.headline))
                    Text("Codex and Claude can both run on this Mac: add the project twice in Projects, once for each agent, and start a task. Pair a second Mac only if you want one agent to work there.")
                    Text("To pair: open AABridge on the other Mac, create its card there, paste it in step 2 below, then do the same in the other direction. It takes about a minute and is done once.").foregroundStyle(.secondary)
                }
            }
            NearbyMacsView(nearby: nearby, paired: devices.devices)
            if !devices.devices.isEmpty {
                GroupBox { VStack(alignment: .leading, spacing: 12) {
                    Text("Paired Macs").font(.bridge(.headline))
                    Text("Pairing is saved. When AABridge or either Mac restarts, they reconnect by themselves on the same network, so you don’t need to exchange cards again.").foregroundStyle(.secondary)
                    DeviceList(model: devices)
                }.padding(16).frame(maxWidth: .infinity, alignment: .leading) }
            }
            GroupBox { VStack(alignment: .leading, spacing: 14) {
                Text("1. Send this Mac’s card").font(.bridge(.headline))
                Text("Set up AABridge on this Mac using the app’s Settings, then create its card. AABridge accepts an encrypted connection directly.")
                TextField("LAN or VPN address (optional; defaults to this Mac’s local hostname)", text: $model.connectionHost).textFieldStyle(.roundedBorder)
                Button("Create connection card") { model.createCard() }.disabled(model.busy)
                if !model.connectionCard.isEmpty {
                    Text(model.connectionCard).font(.bridge(.caption, design: .monospaced)).lineLimit(4).textSelection(.enabled)
                    Button("Copy card") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(model.connectionCard, forType: .string) }
                }
            }.padding(16).frame(maxWidth: .infinity, alignment: .leading) }
            GroupBox { VStack(alignment: .leading, spacing: 14) {
                Text("2. Connect the other Mac").font(.bridge(.headline))
                Text("Paste the card copied directly from your other Mac. Connecting trusts that Mac’s app certificate. Your agent logins stay on their own Macs.")
                TextEditor(text: $model.pastedCard).font(.bridge(.callout, design: .monospaced)).frame(height: 120).padding(8).overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.15)))
                Button("Connect this Mac") { model.connect() }.buttonStyle(.borderedProminent).disabled(model.busy || model.pastedCard.isEmpty)
            }.padding(16).frame(maxWidth: .infinity, alignment: .leading) }
            Text("Repeat on the other Mac. Then add the same Git project on both Macs in Projects, choose each agent, and allow the paired Mac. Use the same committed version of the project on both.").foregroundStyle(.secondary)
            Text("Keep both Macs reachable on your LAN or VPN. If macOS asks, allow AABridge’s local network connection. Pairing does not create a connection across separate networks.").foregroundStyle(.secondary)
            if !model.notice.isEmpty { Text(model.notice).foregroundStyle(.orange).textSelection(.enabled) }
        }.padding(32).frame(maxWidth: 900, alignment: .leading) }
    }
}
