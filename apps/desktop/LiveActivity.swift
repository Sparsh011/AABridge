import SwiftUI

struct AgentModelSettings: Codable {
    var model: String?
    var effort: String?
    static func validModel(_ raw: String) -> Bool {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty || value.range(of: #"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,159}$"#, options: .regularExpression) != nil
    }
    var dictionary: [String: String] {
        var result: [String: String] = [:]
        if let model = model?.trimmingCharacters(in: .whitespacesAndNewlines), !model.isEmpty { result["model"] = model }
        if let effort = effort, !effort.isEmpty { result["effort"] = effort }
        return result
    }
}

struct ModelSettingsFields: View {
    let agent: String
    @Binding var model: String
    @Binding var effort: String
    @State private var custom = false
    private var models: [(String, String)] {
        agent == "claude" ? [("Agent default", ""), ("Opus", "opus"), ("Sonnet", "sonnet"), ("Haiku", "haiku")]
        : [("Agent default", ""), ("GPT-6.1 Sol", "gpt-6.1-sol"), ("GPT-6 Astra", "gpt-6-astra"), ("GPT-6 Sol", "gpt-6-sol"), ("GPT-6 Luna", "gpt-6-luna")]
    }
    private var efforts: [String] { agent == "claude" ? ["low", "medium", "high", "xhigh", "max", "ultracode"] : ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"] }
    private var modelChoice: Binding<String> {
        Binding(get: { custom || !models.contains(where: { $0.1 == model }) ? "__custom" : model }, set: { value in
            custom = value == "__custom"
            if !custom { model = value }
        })
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Picker("Model", selection: modelChoice) {
                    ForEach(models, id: \.1) { Text($0.0).tag($0.1) }
                    Text("Custom model ID…").tag("__custom")
                }
                Picker("Effort", selection: $effort) {
                    Text("Agent default").tag("")
                    ForEach(efforts, id: \.self) { Text($0.capitalized).tag($0) }
                }.frame(maxWidth: 230)
            }
            if custom || !models.contains(where: { $0.1 == model }) {
                TextField("Exact model ID supported by your agent", text: $model).textFieldStyle(.roundedBorder)
            }
            if !AgentModelSettings.validModel(model) {
                Text("Use a model ID without spaces or command options.").font(.bridge(.caption)).foregroundStyle(.red)
            }
            Text("Availability and supported effort levels depend on the agent version and your account. Agent default keeps that Mac’s saved settings.")
                .font(.bridge(.caption)).foregroundStyle(.secondary)
        }.onChange(of: agent) { _ in model = ""; effort = ""; custom = false }
    }
}

struct TeamLiveState: Decodable {
    let role: String; let agent: String; let target: String; let stage: String
    let status: String; let checked_at: Double; let supported: Bool
    let activity: WorkerActivity?; let stream_error: String?; let model_settings: AgentModelSettings?
    var started_at: Double? = nil
}
struct LiveActivityEntry: Decodable, Identifiable {
    let id: String; let cursor: Int; let role: String; let agent: String; let target: String
    let stage: String; let kind: String; let text: String; let state: String; let time: Double
}
struct LiveActivityPage: Decodable { let entries: [LiveActivityEntry]; let cursor: Int; let has_more: Bool }

func stageVerb(_ stage: String) -> String { stage == "plan" ? "Planning" : stage == "work" ? "Building" : "Reviewing" }

/// "42s", "3m 05s", "1h 12m": how long an agent has been on its current step.
func elapsedText(_ seconds: Int) -> String {
    let value = max(0, seconds)
    if value < 60 { return "\(value)s" }
    if value < 3600 { return "\(value / 60)m \(String(format: "%02d", value % 60))s" }
    return "\(value / 3600)h \(String(format: "%02d", value % 3600 / 60))m"
}

/// Pinned above the composer, so who is working and what it is doing stays on
/// screen however far the chat is scrolled. The log stays readable afterwards.
struct LiveActivityView: View {
    @ObservedObject var model: TeamRoomModel
    @AppStorage("teamActivityOpen") private var openWhileWorking = true
    @State private var openLog = false
    @State private var follow = true
    private var live: TeamLiveState? { model.room?.live }
    private var active: Bool { ["running", "stopping"].contains(model.room?.status ?? "") }
    private var expanded: Bool { active ? openWhileWorking : openLog }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { if active { openWhileWorking.toggle() } else { openLog.toggle() } } label: { summary }.buttonStyle(.plain)
                .keyboardShortcut("l", modifiers: .command).help("Show or hide the activity log (⌘L)")
                .accessibilityLabel(active ? "Live activity. " + (live.map { "\(agentName($0.agent)) is working: \(current($0))" } ?? "Starting the agent") : "Activity log, \(model.liveEntries.count) actions")
                .accessibilityHint(expanded ? "Hides the list of actions" : "Shows the list of actions")
            if expanded {
                if let live = live, active {
                    if !live.supported { Text("This Mac needs the live-activity update to show each action. Finish its active tasks before updating it.").font(.bridge(.caption)).foregroundStyle(.orange).padding(.top, 6) }
                    if let error = live.stream_error { Text(error).font(.bridge(.caption)).foregroundStyle(.orange).padding(.top, 6) }
                }
                if !model.liveNotice.isEmpty { Text(model.liveNotice).font(.bridge(.caption)).foregroundStyle(.orange).padding(.top, 6) }
                feed.padding(.top, 10)
            }
        }.padding(.horizontal, 24).padding(.vertical, 10).background(active ? Color.accentColor.opacity(0.06) : Color.primary.opacity(0.025))
    }

    private var summary: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let now = context.date.timeIntervalSince1970
            HStack(spacing: 10) {
                if active { ProgressView().controlSize(.small) } else { Image(systemName: "list.bullet.rectangle").foregroundStyle(.secondary) }
                if active, let live = live {
                    Text("\(agentName(live.agent)) · \(live.role == "follower" ? "Worker" : "Leader") · \(model.deviceLabel(live.target))").font(.bridge(.callout).bold())
                    Text(current(live)).font(.bridge(.callout)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.tail)
                    Spacer(minLength: 8)
                    let last = model.liveEntries.last?.time ?? live.activity?.time ?? live.checked_at
                    let quiet = Int(now - last)
                    if let started = live.started_at { Text(stageVerb(live.stage) + " for " + elapsedText(Int(now - started))).font(.bridge(.caption)).foregroundStyle(.secondary) }
                    Text(quiet < 5 ? "live" : "quiet for " + elapsedText(quiet)).font(.bridge(.caption).bold()).foregroundStyle(quiet > 120 ? Color.orange : Color.green)
                        .help("Time since the agent last reported an action. Long quiet periods are normal while a model is thinking or a command runs.")
                } else if active {
                    Text("Starting the agent…").font(.bridge(.callout))
                    Spacer(minLength: 8)
                } else {
                    Text("Activity log").font(.bridge(.callout).bold())
                    Text("\(model.liveEntries.count) action\(model.liveEntries.count == 1 ? "" : "s") from this task").font(.bridge(.callout)).foregroundStyle(.secondary)
                    Spacer(minLength: 8)
                }
                Image(systemName: expanded ? "chevron.down" : "chevron.up").font(.bridge(.caption)).foregroundStyle(.secondary)
            }.contentShape(Rectangle())
        }
    }

    /// The newest thing the agent reported, in one line.
    private func current(_ live: TeamLiveState) -> String {
        if let entry = model.liveEntries.last(where: { $0.kind != "message" && $0.stage == live.stage && $0.role == live.role }) { return entry.text }
        return live.activity?.message ?? (live.status == "queued" ? "Waiting for the agent to become available" : stageVerb(live.stage) + "…")
    }

    private var feed: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Each action the agents report: commands, file edits and progress notes. Private reasoning is never shown.").font(.bridge(.caption)).foregroundStyle(.secondary)
                Spacer()
                if active { Toggle("Follow live", isOn: $follow).toggleStyle(.switch).controlSize(.small) }
            }
            ScrollViewReader { reader in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 6) {
                        ForEach(model.liveEntries) { entry in
                            HStack(alignment: .top, spacing: 9) {
                                Image(systemName: entry.kind == "file" ? "doc.text" : entry.kind == "tool" ? "terminal" : entry.kind == "message" ? "bubble.left" : "circle.dotted")
                                    .foregroundStyle(entry.state == "failed" ? Color.orange : entry.role == "follower" ? Color.teal : Color.purple).frame(width: 18)
                                VStack(alignment: .leading, spacing: 2) {
                                    Group {
                                        if entry.kind == "message" { MarkdownText(text: entry.text) } else { Text(entry.text).textSelection(.enabled) }
                                    }.font(.bridge(.callout)).frame(maxWidth: .infinity, alignment: .leading)
                                    HStack(spacing: 4) {
                                        Text("\(agentName(entry.agent)) · \(entry.role == "follower" ? "Worker" : "Leader") · \(stageVerb(entry.stage))")
                                        Text("·")
                                        Text(Date(timeIntervalSince1970: entry.time), style: .time)
                                    }.font(.bridge(.caption)).foregroundStyle(.secondary)
                                }
                            }.padding(.vertical, 5).padding(.horizontal, 8).background(Color.primary.opacity(0.03)).clipShape(RoundedRectangle(cornerRadius: 8)).id(entry.id)
                        }
                        if model.liveEntries.isEmpty {
                            Text(active ? "Waiting for the agent’s first action. A model can think for a minute or more before it reports one." : "This task has no recorded actions.")
                                .font(.bridge(.callout)).foregroundStyle(.secondary).padding(8)
                        }
                    }
                }.frame(height: 170)
                    .onAppear { if let id = model.liveEntries.last?.id { reader.scrollTo(id, anchor: .bottom) } }
                    .onChange(of: model.liveEntries.last?.cursor) { _ in if follow || !active, let id = model.liveEntries.last?.id { reader.scrollTo(id, anchor: .bottom) } }
            }
        }
    }
}
