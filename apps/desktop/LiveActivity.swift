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
}
struct LiveActivityEntry: Decodable, Identifiable {
    let id: String; let cursor: Int; let role: String; let agent: String; let target: String
    let stage: String; let kind: String; let text: String; let state: String; let time: Double
}
struct LiveActivityPage: Decodable { let entries: [LiveActivityEntry]; let cursor: Int; let has_more: Bool }

struct LiveActivityView: View {
    @ObservedObject var model: TeamRoomModel
    @State private var follow = true
    @State private var expanded = true
    private var live: TeamLiveState? { model.room?.live }
    private var active: Bool { ["running", "stopping"].contains(model.room?.status ?? "") }
    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 10) {
                if let live = live {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let last = model.liveEntries.last?.time ?? live.activity?.time ?? live.checked_at
                        let age = max(0, Int(context.date.timeIntervalSince1970 - last))
                        HStack(alignment: .top) {
                            if active { ProgressView().controlSize(.small) }
                            VStack(alignment: .leading, spacing: 4) {
                                Text("\(live.role.capitalized) · \(live.agent == "claude" ? "Claude" : "Codex") · \(model.deviceLabel(live.target))")
                                    .font(.bridge(.headline))
                                Text(live.activity?.message ?? (live.status == "queued" ? "Waiting for the agent to become available" : live.status.capitalized))
                                if active { Text("Last agent update \(age)s ago · service checked \(max(0, Int(context.date.timeIntervalSince1970 - live.checked_at)))s ago").font(.bridge(.caption)).foregroundStyle(.secondary) }
                            }
                            Spacer()
                        }
                    }
                    if !live.supported {
                        Text("This participant needs the live-activity update. Finish its active tasks before updating that Mac.").foregroundStyle(.orange)
                    }
                    if let error = live.stream_error { Text(error).foregroundStyle(.orange) }
                }
                if !model.liveNotice.isEmpty { Text(model.liveNotice).foregroundStyle(.orange) }
                HStack {
                    Text("Actions and progress messages").font(.bridge(.caption)).foregroundStyle(.secondary)
                    Spacer()
                    Toggle("Follow live", isOn: $follow).toggleStyle(.switch).controlSize(.small)
                }
                ScrollViewReader { reader in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 10) {
                            ForEach(model.liveEntries) { entry in
                                HStack(alignment: .top, spacing: 9) {
                                    Image(systemName: entry.kind == "file" ? "doc.text" : entry.kind == "tool" ? "terminal" : entry.kind == "message" ? "bubble.left" : "circle.dotted")
                                        .foregroundStyle(entry.state == "failed" ? Color.orange : Color.accentColor)
                                    VStack(alignment: .leading, spacing: 3) {
                                        HStack {
                                            Text("\(entry.role.capitalized) · \(entry.agent == "claude" ? "Claude" : "Codex") · \(entry.stage == "plan" ? "Planning" : entry.stage == "work" ? "Building" : "Reviewing")")
                                            Spacer()
                                            Text(Date(timeIntervalSince1970: entry.time), style: .time)
                                        }.font(.bridge(.caption)).foregroundStyle(.secondary)
                                        Text(entry.text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                                    }
                                }.padding(9).background(Color.primary.opacity(0.025)).clipShape(RoundedRectangle(cornerRadius: 8)).id(entry.id)
                            }
                            if model.liveEntries.isEmpty { Text(active ? "Waiting for the agent’s first update…" : "Activity will appear here when a task starts.").foregroundStyle(.secondary).padding(12) }
                        }
                    }.frame(height: 190)
                        .onAppear { if follow, let id = model.liveEntries.last?.id { reader.scrollTo(id, anchor: .bottom) } }
                        .onChange(of: model.liveEntries.last?.cursor) { _ in if follow, let id = model.liveEntries.last?.id { reader.scrollTo(id, anchor: .bottom) } }
                }
            }.padding(.top, 12)
        } label: {
            Label("Live activity", systemImage: "waveform.path").font(.bridge(.headline))
        }.padding(16).background(Color.accentColor.opacity(0.045)).clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
