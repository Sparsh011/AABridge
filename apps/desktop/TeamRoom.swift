import AppKit
import Combine
import SwiftUI

struct TeamMember: Codable { let target: String; let project: String; let agent: String; var model_settings: AgentModelSettings? = nil }
struct TeamEntry: Decodable, Identifiable {
    let id: String; let speaker: String; let text: String; let time: Double
}
struct TeamResult: Decodable { let branch: String; let worktree: String; let result_commit: String; let base_commit: String }
struct TeamRoom: Decodable, Identifiable {
    let id: String; let objective: String; let status: String; let stage: String
    let leader: TeamMember; let follower: TeamMember
    let messages: [TeamEntry]?; let error: String?; let result: TeamResult?
    var live: TeamLiveState? = nil
}
struct TeamChoice: Identifiable {
    let id: String; let label: String; let member: TeamMember
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
    @Published var liveEntries: [LiveActivityEntry] = []
    @Published var liveNotice = ""
    private var liveCursor = 0
    private var liveRoom = ""
    @Published var busy = false
    @Published var notice = ""
    @Published var showAssignment = false
    @Published var connectionCard = ""
    @Published var pastedCard = ""
    private let activity: TaskBrowserModel
    private let preferences = UserDefaults.standard
    init(activity: TaskBrowserModel) { self.activity = activity }
    var settingsValid: Bool { AgentModelSettings.validModel(leaderModel) && AgentModelSettings.validModel(followerModel) }
    var ready: Bool { settingsValid && !busy && !leader.isEmpty && !follower.isEmpty && leader != follower && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && draft.count <= 3000 }
    var terminal: Bool { room == nil || ["completed", "failed", "cancelled"].contains(room!.status) }
    private func envelope(_ body: [String: Any], key: String? = nil) throws -> Data {
        try JSONSerialization.data(withJSONObject: ["key": key ?? "team-ui-" + UUID().uuidString, "body": body])
    }
    func refresh(discover: Bool = false) {
        guard !busy else { return }
        busy = true
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
                                    member: TeamMember(target: target.id, project: project.id, agent: project.adapter)))
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
                    var merged = Dictionary(uniqueKeysWithValues: self.liveEntries.map { ($0.id, $0) })
                    for entry in feed { merged[entry.id] = entry }
                    self.liveEntries = Array(merged.values.sorted { $0.cursor < $1.cursor }.suffix(250))
                    self.liveCursor = nextCursor; self.liveNotice = feedNotice
                    if changed, let room = room {
                        self.leaderModel = room.leader.model_settings?.model ?? ""; self.leaderEffort = room.leader.model_settings?.effort ?? ""
                        self.followerModel = room.follower.model_settings?.model ?? ""; self.followerEffort = room.follower.model_settings?.effort ?? ""
                    }
                    self.rooms = rooms; self.room = room; self.selected = wanted; self.busy = false
                    if let found = found {
                        self.choices = found
                        if !found.contains(where: { $0.id == self.leader }) { self.leader = found.first?.id ?? "" }
                        if !found.contains(where: { $0.id == self.follower }) { self.follower = found.first(where: { $0.id != self.leader })?.id ?? "" }
                        self.notice = issues.joined(separator: "\n")
                    }
                }
            } catch { DispatchQueue.main.async { self.busy = false; self.notice = error.localizedDescription } }
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
        guard ready, let lead = choices.first(where: { $0.id == leader }), let follow = choices.first(where: { $0.id == follower }) else { return }
        func member(_ value: TeamMember, model: String, effort: String) -> [String: Any] {
            var body: [String: Any] = ["target": value.target, "project": value.project, "agent": value.agent]
            let selected = settings(model, effort)
            if !selected.isEmpty { body["model_settings"] = selected }
            return body
        }
        mutate("team_start", body: ["objective": draft, "leader": member(lead.member, model: leaderModel, effort: leaderEffort), "follower": member(follow.member, model: followerModel, effort: followerEffort)])
    }
    func control(_ action: String) {
        guard let room = room else { return }
        mutate(action, body: ["team_id": room.id])
    }
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
                    self.room = room; self.selected = room.id; self.draft = ""; self.showAssignment = false
                    self.busy = false; self.notice = ""; self.refresh()
                }
            } catch { DispatchQueue.main.async { self.busy = false; self.notice = error.localizedDescription + " Your send is saved; Retry will use the same key." } }
        }
    }
    func createCard() {
        guard !busy else { return }; busy = true
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let data = try self.activity.call("connection-card", target: "local")
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
                DispatchQueue.main.async { self.busy = false; self.notice = "Mac connected. Do the same on the other Mac, then share a project in Projects."; self.pastedCard = "" }
            } catch { DispatchQueue.main.async { self.busy = false; self.notice = error.localizedDescription } }
        }
    }
}

struct TeamRoomView: View {
    @ObservedObject var model: TeamRoomModel
    let active: Bool
    var preview = false
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()
    private func name(_ member: TeamMember) -> String {
        if let choice = model.choices.first(where: { $0.member.target == member.target && $0.member.project == member.project }) { return choice.label }
        return (member.agent == "claude" ? "Claude" : "Codex") + " · " + (member.target == "local" ? "This Mac" : member.target) }
    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 18) {
                Label("Your team", systemImage: "person.3.fill").font(.bridge(.title2).bold())
                Text("You’re the admin.").foregroundStyle(.secondary)
                Button { model.showAssignment = true; model.draft = ""; model.refresh(discover: true) } label: { Label("New task", systemImage: "square.and.pencil").frame(maxWidth: .infinity) }.buttonStyle(.borderedProminent).disabled(model.busy || model.hasPending)
                ScrollView { VStack(spacing: 8) {
                    ForEach(model.rooms) { room in
                        Button { model.selected = room.id; model.showAssignment = false; model.refresh() } label: {
                            VStack(alignment: .leading, spacing: 7) {
                                Text(room.objective).lineLimit(3).font(.bridge(.headline))
                                Text(room.status.capitalized).font(.bridge(.caption)).foregroundStyle(.secondary)
                            }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                                .background(model.selected == room.id ? Color.accentColor.opacity(0.12) : Color.primary.opacity(0.035)).clipShape(RoundedRectangle(cornerRadius: 12))
                        }.buttonStyle(.plain)
                    }
                } }
                Button("Refresh team & devices") { model.refresh(discover: true) }.disabled(model.busy)
            }.padding(22).frame(minWidth: 240, idealWidth: 270, maxWidth: 320)
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(model.showAssignment || model.room == nil ? "What shall we build?" : "Team chat").font(.bridge(.title).bold())
                        Text("Choose who leads. Tell your team what you need.").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text("YOU · ADMIN").font(.bridge(.caption).bold()).padding(10).background(Color.accentColor.opacity(0.10)).clipShape(Capsule())
                }.padding(24)
                Divider()
                if model.showAssignment || model.room == nil {
                    ScrollView { VStack(alignment: .leading, spacing: 22) {
                        Text("1. Choose your team").font(.bridge(.headline))
                        Picker("Leader · sender", selection: $model.leader) { Text("Choose a Mac and agent").tag(""); ForEach(model.choices) { Text($0.label).tag($0.id) } }
                        ModelSettingsFields(agent: model.choices.first(where: { $0.id == model.leader })?.member.agent ?? "codex", model: $model.leaderModel, effort: $model.leaderEffort)
                        HStack { Spacer(); Button { let lead = model.leader; model.leader = model.follower; model.follower = lead } label: { Label("Swap roles", systemImage: "arrow.up.arrow.down") }; Spacer() }
                        Picker("Worker · receiver", selection: $model.follower) { Text("Choose a Mac and agent").tag(""); ForEach(model.choices) { Text($0.label).tag($0.id) } }
                        ModelSettingsFields(agent: model.choices.first(where: { $0.id == model.follower })?.member.agent ?? "claude", model: $model.followerModel, effort: $model.followerEffort)
                        Text("The leader plans and reviews. The worker writes the code. Both report here. You have the final say.").foregroundStyle(.secondary)
                        if model.choices.count < 2 { Label("Add both Macs in Devices, then add a full coding project on each Mac in Projects.", systemImage: "info.circle").foregroundStyle(.orange) }
                        Text("2. Describe the result you want").font(.bridge(.headline))
                        Text("For example: Build a simple booking page for my business. Make it work well on a phone.").foregroundStyle(.secondary)
                        TextEditor(text: $model.draft).frame(minHeight: 160).padding(10).overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.primary.opacity(0.15)))
                    }.padding(28).frame(maxWidth: 850) }
                    Divider()
                    HStack {
                        Text("Uses the selected agents’ accounts.").font(.bridge(.caption)).foregroundStyle(.secondary)
                        Spacer()
                        Button("Start working") { model.send() }.buttonStyle(.borderedProminent).disabled(!model.ready || model.hasPending)
                    }.padding(20)
                } else if let room = model.room {
                    HStack {
                        Label(name(room.leader) + " · Leader", systemImage: "crown")
                        Image(systemName: "arrow.right").foregroundStyle(.secondary)
                        Label(name(room.follower) + " · Worker", systemImage: "hammer")
                        Spacer()
                        Text(room.status == "running" ? (room.stage == "plan" ? "Planning…" : room.stage == "work" ? "Building…" : "Reviewing…") : room.status.capitalized).foregroundStyle(.secondary)
                    }.font(.bridge(.callout)).padding(18)
                    LiveActivityView(model: model).padding(.horizontal, 20).padding(.bottom, 8)
                    ScrollViewReader { proxy in ScrollView {
                        LazyVStack(alignment: .leading, spacing: 18) {
                            ForEach(room.messages ?? []) { message in
                                VStack(alignment: .leading, spacing: 9) {
                                    Text(message.speaker).font(.bridge(.headline)).foregroundStyle(message.speaker.contains("admin") ? Color.accentColor : Color.primary)
                                    Text(message.text).textSelection(.enabled)
                                }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
                                    .background(message.speaker.contains("admin") ? Color.accentColor.opacity(0.09) : Color.primary.opacity(0.035)).clipShape(RoundedRectangle(cornerRadius: 14)).id(message.id)
                            }
                            if let result = room.result {
                                VStack(alignment: .leading, spacing: 8) {
                                    Label("Work saved for your review", systemImage: "checkmark.circle").font(.bridge(.headline))
                                    Text("On \(name(room.follower)): \(result.worktree)").textSelection(.enabled)
                                    Text(result.branch).font(.bridge(.caption, design: .monospaced)).textSelection(.enabled)
                                    Text("Review the branch before publishing it to GitHub.").foregroundStyle(.secondary)
                                }.padding(18)
                            }
                        }.padding(24)
                    }.onChange(of: room.messages?.last?.id) { id in if let id = id { proxy.scrollTo(id, anchor: .bottom) } } }
                    if let error = room.error, !error.isEmpty {
                        HStack { Text(error).foregroundStyle(.orange).textSelection(.enabled); Spacer(); if room.status == "blocked" { Button("Retry") { model.control("team_retry") }.disabled(model.busy) } }.padding(18)
                    }
                    Divider()
                    if model.terminal {
                        DisclosureGroup("Models for the next message") {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Leader").font(.bridge(.headline))
                                ModelSettingsFields(agent: room.leader.agent, model: $model.leaderModel, effort: $model.leaderEffort)
                                Text("Follower").font(.bridge(.headline))
                                ModelSettingsFields(agent: room.follower.agent, model: $model.followerModel, effort: $model.followerEffort)
                            }
                        }.padding(.horizontal, 20).padding(.top, 8)
                    }
                    HStack(spacing: 14) {
                        if model.terminal {
                            TextField("Tell the team what to change next…", text: $model.draft).textFieldStyle(.roundedBorder)
                            Button("Send") { model.send() }.buttonStyle(.borderedProminent).disabled(!model.settingsValid || model.busy || model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.draft.count > 3000 || model.hasPending)
                        } else {
                            Text("Your team is working. You can stop it at any time.").foregroundStyle(.secondary)
                            Spacer()
                            Button("Stop task", role: .destructive) { model.control("team_cancel") }.disabled(model.busy || room.status == "stopping" || model.hasPending)
                        }
                    }.padding(20)
                }
                if !model.notice.isEmpty || model.hasPending {
                    HStack { Text(model.notice).foregroundStyle(.orange).textSelection(.enabled); Spacer(); if model.hasPending { Button("Retry saved send") { model.retry() }.disabled(model.busy) } }.padding(16)
                }
            }.frame(minWidth: 650)
        }.onAppear { if !preview { model.refresh(discover: true) } }
            .onReceive(timer) { _ in if active && !model.showAssignment { model.refresh() } }
    }
}

struct DevicesView: View {
    @ObservedObject var model: TeamRoomModel
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 22) {
            Text("Connect your Macs").font(.bridge(.title).bold())
            Text("Each Mac can lead or receive work. Exchange a connection card once in each direction.").foregroundStyle(.secondary)
            GroupBox { VStack(alignment: .leading, spacing: 14) {
                Text("1. Send this Mac’s card").font(.bridge(.headline))
                Text("Turn on Remote Login in macOS System Settings → General → Sharing. Install AABridge on this Mac using Settings, then create its card.")
                Button("Create connection card") { model.createCard() }.disabled(model.busy)
                if !model.connectionCard.isEmpty {
                    Text(model.connectionCard).font(.bridge(.caption, design: .monospaced)).lineLimit(4).textSelection(.enabled)
                    Button("Copy card") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(model.connectionCard, forType: .string) }
                }
            }.padding(16).frame(maxWidth: .infinity, alignment: .leading) }
            GroupBox { VStack(alignment: .leading, spacing: 14) {
                Text("2. Connect the other Mac").font(.bridge(.headline))
                Text("Paste the card copied directly from your other Mac. Connecting trusts that Mac’s SSH identity and adds its restricted bridge key. Your agent logins stay on their own Macs.")
                TextEditor(text: $model.pastedCard).font(.bridge(.callout, design: .monospaced)).frame(height: 120).padding(8).overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.15)))
                Button("Connect this Mac") { model.connect() }.buttonStyle(.borderedProminent).disabled(model.busy || model.pastedCard.isEmpty)
            }.padding(16).frame(maxWidth: .infinity, alignment: .leading) }
            Text("Repeat on the other Mac. Then add the same Git project on both Macs in Projects, choose each agent, and allow the paired Mac. Use the same committed version of the project on both.").foregroundStyle(.secondary)
            if !model.notice.isEmpty { Text(model.notice).foregroundStyle(.orange).textSelection(.enabled) }
        }.padding(32).frame(maxWidth: 900, alignment: .leading) }
    }
}
