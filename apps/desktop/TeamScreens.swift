import AppKit
import SwiftUI

// MARK: - Shared pieces

/// Plain-language status for a team, with a colour and symbol.
struct TeamStatus {
    let text: String; let color: Color; let icon: String
    init(status: String, stage: String) {
        switch status {
        case "running":
            text = stage == "plan" ? "Planning" : stage == "work" ? "Building" : "Reviewing"; color = .blue; icon = "circle.dotted"
        case "awaiting_admin": text = "Needs you"; color = .orange; icon = "hand.raised.fill"
        case "completed": text = "Done"; color = .green; icon = "checkmark.circle.fill"
        case "blocked": text = "Needs a fix"; color = .red; icon = "exclamationmark.triangle.fill"
        case "failed": text = "Stopped with an error"; color = .red; icon = "xmark.octagon.fill"
        case "stopping": text = "Stopping…"; color = .gray; icon = "stop.circle"
        default: text = "Stopped"; color = .gray; icon = "stop.circle.fill"
        }
    }
}

struct StatusPill: View {
    let status: TeamStatus
    var body: some View {
        Label(status.text, systemImage: status.icon).font(.bridge(.caption).bold()).foregroundStyle(status.color)
            .padding(.horizontal, 10).padding(.vertical, 5).background(status.color.opacity(0.12)).clipShape(Capsule())
    }
}

func agentName(_ agent: String) -> String { agent == "claude" ? "Claude" : "Codex" }

struct Card<Content: View>: View {
    var tint: Color = .primary
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 12) { content }
            .padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(tint == .primary ? Color.primary.opacity(0.03) : tint.opacity(0.08))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(tint == .primary ? Color.primary.opacity(0.10) : tint.opacity(0.35)))
            .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}

struct OptionCard: View {
    let icon: String; let title: String; let detail: String; let selected: Bool
    var enabled = true
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle").foregroundStyle(selected ? Color.accentColor : Color.secondary)
                Image(systemName: icon).foregroundStyle(.tint).frame(width: 22)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.bridge(.headline))
                    Text(detail).font(.bridge(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(selected ? Color.accentColor.opacity(0.09) : Color.primary.opacity(0.025))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(selected ? Color.accentColor : Color.primary.opacity(0.12), lineWidth: selected ? 1.5 : 1))
                .clipShape(RoundedRectangle(cornerRadius: 12)).contentShape(Rectangle())
        }.buttonStyle(.plain).disabled(!enabled).opacity(enabled ? 1 : 0.45)
    }
}

struct FormStep<Content: View>: View {
    let number: Int; let title: String; let subtitle: String
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 12) {
                Text("\(number)").font(.bridge(.headline)).foregroundStyle(.white).frame(width: 28, height: 28).background(Circle().fill(Color.accentColor))
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.bridge(.title3).bold())
                    Text(subtitle).font(.bridge(.callout)).foregroundStyle(.secondary)
                }
            }
            content.padding(.leading, 40)
        }
    }
}

// MARK: - Sidebar

struct TeamSidebar: View {
    @ObservedObject var model: TeamRoomModel
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Label("Your AI team", systemImage: "person.3.fill").font(.bridge(.title2).bold())
                Text("You’re the admin. You have the final say.").font(.bridge(.callout)).foregroundStyle(.secondary)
            }
            Button { model.showAssignment = true; model.draft = ""; model.refresh(discover: true) } label: {
                Label("New task", systemImage: "plus").frame(maxWidth: .infinity)
            }.buttonStyle(.borderedProminent).controlSize(.large).disabled(model.busy || model.hasPending)
            Text("TASKS").font(.bridge(.caption).bold()).foregroundStyle(.secondary).padding(.top, 6)
            ScrollView { VStack(spacing: 8) {
                ForEach(model.rooms) { room in
                    let status = TeamStatus(status: room.status, stage: room.stage)
                    let chosen = model.selected == room.id && !model.showAssignment
                    Button { model.selected = room.id; model.showAssignment = false; model.draft = ""; model.refresh() } label: {
                        VStack(alignment: .leading, spacing: 7) {
                            Text(room.objective).lineLimit(2).font(.bridge(.headline)).multilineTextAlignment(.leading)
                            HStack(spacing: 6) {
                                Image(systemName: status.icon).foregroundStyle(status.color)
                                Text(status.text).foregroundStyle(status.color)
                                Text("·").foregroundStyle(.secondary)
                                Text("\(agentName(room.leader.agent)) → \(agentName(room.follower.agent))").foregroundStyle(.secondary)
                            }.font(.bridge(.caption))
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                            .background(chosen ? Color.accentColor.opacity(0.13) : Color.primary.opacity(0.035)).clipShape(RoundedRectangle(cornerRadius: 12))
                            .overlay(RoundedRectangle(cornerRadius: 12).stroke(chosen ? Color.accentColor.opacity(0.5) : Color.clear))
                    }.buttonStyle(.plain)
                }
                if model.rooms.isEmpty {
                    Text("No tasks yet. Choose New task to give your team its first job.").font(.bridge(.callout)).foregroundStyle(.secondary).padding(.vertical, 8)
                }
            } }
            Button { model.refresh(discover: true) } label: { Label("Refresh agents and Macs", systemImage: "arrow.clockwise") }.buttonStyle(.borderless).disabled(model.busy)
        }.padding(20)
    }
}

// MARK: - New task

struct NewTeamTaskView: View {
    @ObservedObject var model: TeamRoomModel
    var navigate: (Int) -> Void
    private let examples = [("Weight entry screen", "Add a screen where people enter their weight, save it, and see their last 7 entries."),
                            ("Fix a bug", "Fix the error people see when they sign in from the settings page."),
                            ("Add tests", "Write tests for the checkout flow and fix anything they catch.")]

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text("New task").font(.bridge(.largeTitle).bold())
                    Text("Tell your team what you want. The leader plans and reviews, the worker writes the code, and you stay in charge.").foregroundStyle(.secondary)
                }
                Spacer()
                if model.room != nil { Button("Cancel") { model.showAssignment = false; model.draft = "" } }
            }.padding(.horizontal, 32).padding(.vertical, 24)
            Divider()
            ScrollView { VStack(alignment: .leading, spacing: 30) {
                if model.choices.count < 2 { GettingStarted(count: model.choices.count, navigate: navigate) }
                FormStep(number: 1, title: "Pick your team", subtitle: "Any agent on any connected Mac can lead or do the work.") {
                    HStack(alignment: .top, spacing: 12) {
                        RolePicker(title: "Leader", detail: "Plans the work and reviews it", icon: "crown.fill", choices: model.choices, selection: $model.leader,
                                   agent: model.leaderChoice?.member.agent ?? "codex", modelID: $model.leaderModel, effort: $model.leaderEffort)
                        Button { let lead = model.leader; model.leader = model.follower; model.follower = lead } label: { Image(systemName: "arrow.left.arrow.right") }
                            .help("Swap leader and worker").padding(.top, 44)
                        RolePicker(title: "Worker", detail: "Writes the code and runs checks", icon: "hammer.fill", choices: model.choices, selection: $model.follower,
                                   agent: model.followerChoice?.member.agent ?? "claude", modelID: $model.followerModel, effort: $model.followerEffort)
                    }
                    if !model.leader.isEmpty && model.leader == model.follower {
                        Label("Choose two different agents or Macs.", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    }
                }
                FormStep(number: 2, title: "Describe what you want", subtitle: "Write it like you’d explain it to a teammate. Say what “done” looks like.") {
                    ZStack(alignment: .topLeading) {
                        TextEditor(text: $model.draft).font(.bridge(.body)).frame(minHeight: 130).padding(8)
                        if model.draft.isEmpty {
                            Text("For example: Add a new screen that takes weight as input, saves it, and shows the last 7 entries.")
                                .foregroundStyle(.secondary).padding(.horizontal, 13).padding(.vertical, 16).allowsHitTesting(false)
                        }
                    }.background(Color.primary.opacity(0.02)).overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.primary.opacity(0.15))).clipShape(RoundedRectangle(cornerRadius: 12))
                    HStack {
                        Text("Try:").font(.bridge(.caption)).foregroundStyle(.secondary)
                        ForEach(examples, id: \.0) { example in
                            Button(example.0) { model.draft = example.1 }.buttonStyle(.bordered).controlSize(.small).help(example.1)
                        }
                        Spacer()
                        Text("\(model.draft.count)/3000").font(.bridge(.caption)).foregroundStyle(model.draft.count > 3000 ? Color.red : Color.secondary)
                    }
                }
                FormStep(number: 3, title: "How much should the team do on its own?", subtitle: "You can send them a note at any time, whichever you pick.") {
                    HStack(alignment: .top, spacing: 12) {
                        OptionCard(icon: "hand.raised", title: "Check with me", detail: "The leader shows you its plan before any code is written, and asks before each round of fixes.",
                                   selected: model.approval == "ask") { model.approval = "ask" }
                        OptionCard(icon: "bolt.fill", title: "Work on its own", detail: "The leader owns the task. When its review finds problems, it sends them back to the worker. It tells you when the task is finished.",
                                   selected: model.approval == "auto") { model.approval = "auto" }
                    }
                    if model.approval == "auto" {
                        Stepper(value: $model.maxFixes, in: 1...10) {
                            Text("Ask me if it still isn’t right after **\(model.maxFixes)** fix \(model.maxFixes == 1 ? "round" : "rounds")")
                        }.frame(maxWidth: 460, alignment: .leading)
                    }
                }
                FormStep(number: 4, title: "Where should the code go?", subtitle: "Nothing is ever merged into main for you. Merging is always your choice.") {
                    VStack(spacing: 8) {
                        ForEach(PlacementOption.all) { option in
                            OptionCard(icon: option.icon, title: option.title, detail: option.detail, selected: model.placement == option.id,
                                       enabled: model.placementAllowed(option.id)) { model.placement = option.id }
                        }
                    }
                    if !model.hasLocalMember && !model.choices.isEmpty {
                        Label("To put code in your project, the leader or the worker has to run on this Mac.", systemImage: "info.circle").font(.bridge(.callout)).foregroundStyle(.secondary)
                    }
                    if model.placement == "branch" || model.placement == "worktree" {
                        HStack(spacing: 6) {
                            Text("Branch name:").foregroundStyle(.secondary)
                            Text(model.branchPreview).font(.bridge(.callout, design: .monospaced))
                        }.font(.bridge(.callout))
                    }
                    Toggle(isOn: $model.pullRequest) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Also open a pull request on GitHub").font(.bridge(.headline))
                            Text("Uses your GitHub CLI (gh) login on this Mac. The leader’s reviews are posted on the pull request. You get a Merge button when the task is done.")
                                .font(.bridge(.callout)).foregroundStyle(.secondary)
                        }
                    }.toggleStyle(.checkbox).disabled(model.placement == "sandbox")
                }
            }.padding(32).frame(maxWidth: 900, alignment: .leading) }
            Divider()
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(summary).font(.bridge(.callout))
                    Text("Uses the selected agents’ own accounts.").font(.bridge(.caption)).foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.send() } label: { Label("Start task", systemImage: "play.fill") }
                    .buttonStyle(.borderedProminent).controlSize(.large).keyboardShortcut(.return, modifiers: .command)
                    .disabled(!model.ready || model.hasPending)
            }.padding(.horizontal, 32).padding(.vertical, 16)
        }
    }

    private var summary: String {
        guard let lead = model.leaderChoice, let work = model.followerChoice else { return "Pick a leader and a worker to start." }
        let mode = model.approval == "auto" ? "works on its own" : "checks with you"
        let place = PlacementOption.all.first(where: { $0.id == model.placement })?.short ?? ""
        return "\(agentName(lead.member.agent)) leads, \(agentName(work.member.agent)) builds, \(mode) · \(place)\(model.placement != "sandbox" && model.pullRequest ? " + pull request" : "")"
    }
}

struct RolePicker: View {
    let title: String; let detail: String; let icon: String
    let choices: [TeamChoice]
    @Binding var selection: String
    let agent: String
    @Binding var modelID: String
    @Binding var effort: String
    var body: some View {
        Card {
            HStack(spacing: 10) {
                Image(systemName: icon).font(.bridge(.title2)).foregroundStyle(.tint).frame(width: 30)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.bridge(.headline))
                    Text(detail).font(.bridge(.caption)).foregroundStyle(.secondary)
                }
            }
            Picker(title, selection: $selection) {
                Text("Choose an agent").tag("")
                ForEach(choices) { Text($0.label).tag($0.id) }
            }.labelsHidden()
            if let links = choices.first(where: { $0.id == selection })?.links, !links.isEmpty {
                Label("Also works with " + links.map { "\($0.name) (\($0.accessLabel))" }.joined(separator: ", "), systemImage: "link")
                    .font(.bridge(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            DisclosureGroup("Model and effort (optional)") {
                ModelSettingsFields(agent: agent, model: $modelID, effort: $effort).padding(.top, 6)
            }.font(.bridge(.callout))
        }
    }
}

struct GettingStarted: View {
    let count: Int
    var navigate: (Int) -> Void
    var body: some View {
        Card(tint: .orange) {
            Label("Before your first task", systemImage: "sparkles").font(.bridge(.headline))
            Text(count == 0 ? "AABridge didn’t find a coding project yet. A team needs two agents, which can be on the same Mac."
                 : "AABridge found one agent. Add a second one: the other agent on this Mac, or one on a connected Mac.")
            VStack(alignment: .leading, spacing: 8) {
                step(1, "Set up this Mac and sign in to Codex or Claude Code", "Settings", 3)
                step(2, "Add your Git project once for each agent", "Projects", 2)
                step(3, "Optional: connect a second Mac", "Devices", 1)
            }
        }
    }
    private func step(_ number: Int, _ text: String, _ button: String, _ section: Int) -> some View {
        HStack {
            Text("\(number).").bold()
            Text(text)
            Spacer()
            Button("Open \(button)") { navigate(section) }
        }
    }
}

// MARK: - Team chat

struct TeamChatView: View {
    @ObservedObject var model: TeamRoomModel
    let room: TeamRoom
    @State private var confirmMerge = false
    @State private var activityOpen = false

    private func memberName(_ member: TeamMember) -> String { agentName(member.agent) + " on " + model.deviceLabel(member.target) }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollViewReader { proxy in ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    TeamProgress(room: room)
                    if ["running", "stopping"].contains(room.status) { LiveActivityView(model: model) }
                    ForEach(room.messages ?? []) { entry in TeamMessageRow(entry: entry).id(entry.id) }
                    actionPanel.id("action")
                }.padding(24)
            }.onChange(of: room.messages?.last?.id) { id in if let id = id { withAnimation { proxy.scrollTo(id, anchor: .bottom) } } } }
            Divider()
            composer
        }.confirmationDialog("Merge this pull request on GitHub?", isPresented: $confirmMerge) {
            Button("Merge pull request") { model.control("team_merge") }
            Button("Cancel", role: .cancel) {}
        } message: { Text("This merges the team’s pull request\((room.publication?.linked?.contains { $0.pull_request != nil } ?? false) ? "s in every project" : "") into their base branch using your GitHub login.") }
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 10) {
                Text(room.objective).font(.bridge(.title2).bold()).lineLimit(2)
                HStack(spacing: 8) {
                    Label(memberName(room.leader) + " · Leader", systemImage: "crown.fill")
                    Image(systemName: "arrow.right").foregroundStyle(.secondary)
                    Label(memberName(room.follower) + " · Worker", systemImage: "hammer.fill")
                }.font(.bridge(.callout))
                HStack(spacing: 8) {
                    if let approval = room.approval {
                        chip(approval == "auto" ? "Works on its own" : "Checks with you", approval == "auto" ? "bolt.fill" : "hand.raised")
                    }
                    if let placement = room.placement, let option = PlacementOption.all.first(where: { $0.id == placement.mode }) {
                        chip(option.title + (placement.pull_request == true ? " + PR" : ""), option.icon)
                    }
                }
            }
            Spacer()
            StatusPill(status: TeamStatus(status: room.status, stage: room.stage))
        }.padding(.horizontal, 24).padding(.vertical, 18)
    }

    private func chip(_ text: String, _ icon: String) -> some View {
        Label(text, systemImage: icon).font(.bridge(.caption)).foregroundStyle(.secondary)
            .padding(.horizontal, 8).padding(.vertical, 4).background(Color.primary.opacity(0.05)).clipShape(Capsule())
    }

    @ViewBuilder private var actionPanel: some View {
        if room.status == "awaiting_admin", let gate = room.gate {
            Card(tint: .orange) {
                Label(gate.kind == "plan" ? "The plan is ready for your OK" : "Your call: the leader asked for fixes", systemImage: "hand.raised.fill").font(.bridge(.headline))
                Text(gate.reason)
                Text("You can type a note in the box below first. It’s sent along with the button you press.").font(.bridge(.caption)).foregroundStyle(.secondary)
                HStack {
                    if gate.kind == "plan" {
                        Button { model.decide("continue") } label: { Label("Approve plan and start building", systemImage: "checkmark") }.buttonStyle(.borderedProminent)
                        Button("Ask for a different plan") { model.decide("replan") }.disabled(model.trimmedDraft.isEmpty)
                            .help("Type what to change in the box below first.")
                    } else {
                        Button { model.decide("continue") } label: { Label("Let the worker fix it", systemImage: "wrench.and.screwdriver") }.buttonStyle(.borderedProminent)
                        Button("Change the plan") { model.decide("replan") }.disabled(model.trimmedDraft.isEmpty).help("Type what to change in the box below first.")
                        Button("Finish here") { model.decide("finish") }
                    }
                }.disabled(model.busy || model.hasPending)
            }
        } else if let failure = room.failure, ["blocked", "failed"].contains(room.status) {
            FailureCard(failure: failure, retry: room.status == "blocked" ? { model.control("team_retry") } : nil, disabled: model.busy || model.hasPending)
        } else if room.status == "blocked" {
            Card(tint: .red) {
                Label("The team is paused", systemImage: "exclamationmark.triangle.fill").font(.bridge(.headline))
                Text(room.error?.isEmpty == false ? room.error! : "A team member couldn’t be reached.").textSelection(.enabled)
                Button("Try again") { model.control("team_retry") }.buttonStyle(.borderedProminent).disabled(model.busy || model.hasPending)
            }
        } else if room.status == "completed" {
            DeliveryCard(model: model, room: room, confirmMerge: $confirmMerge)
        } else if ["failed", "cancelled"].contains(room.status), let result = room.result {
            Card {
                Label("Partial work is saved", systemImage: "tray.full").font(.bridge(.headline))
                Text(result.worktree).font(.bridge(.callout, design: .monospaced)).textSelection(.enabled)
                Text("Send a follow-up below to continue from where the team stopped.").foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private var composer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if model.terminal {
                DisclosureGroup("Models for the next message") {
                    HStack(alignment: .top, spacing: 18) {
                        VStack(alignment: .leading) { Text("Leader").font(.bridge(.headline)); ModelSettingsFields(agent: room.leader.agent, model: $model.leaderModel, effort: $model.leaderEffort) }
                        VStack(alignment: .leading) { Text("Worker").font(.bridge(.headline)); ModelSettingsFields(agent: room.follower.agent, model: $model.followerModel, effort: $model.followerEffort) }
                    }.padding(.top, 6)
                }.font(.bridge(.callout))
            }
            HStack(alignment: .bottom, spacing: 12) {
                TextField(placeholder, text: $model.draft, axis: .vertical).lineLimit(1...5).textFieldStyle(.roundedBorder).font(.bridge(.body))
                    .disabled(model.hasPending)
                if model.terminal {
                    Button("Send") { model.send() }.buttonStyle(.borderedProminent).keyboardShortcut(.return, modifiers: .command)
                        .disabled(!model.settingsValid || model.busy || model.trimmedDraft.isEmpty || model.draft.count > 3000 || model.hasPending)
                } else if !model.waiting {
                    Button { model.sendNote() } label: { Label("Send note", systemImage: "paperplane") }.buttonStyle(.borderedProminent)
                        .keyboardShortcut(.return, modifiers: .command)
                        .disabled(model.busy || model.trimmedDraft.isEmpty || model.draft.count > 3000 || model.hasPending || room.status == "stopping")
                    Button("Stop", role: .destructive) { model.control("team_cancel") }.disabled(model.busy || room.status == "stopping" || model.hasPending)
                } else {
                    Button("Stop", role: .destructive) { model.control("team_cancel") }.disabled(model.busy || model.hasPending)
                }
            }
            Text(hint).font(.bridge(.caption)).foregroundStyle(.secondary)
        }.padding(.horizontal, 24).padding(.vertical, 14)
    }

    private var placeholder: String {
        model.terminal ? "What should the team do next?" : model.waiting ? "Optional note to send with your choice…" : "Send a note to the team, e.g. “Use kilograms, not pounds”"
    }

    private var hint: String {
        if model.terminal { return "Your next message continues on the same branch, so earlier changes are kept. ⌘Return sends." }
        if model.waiting { return "The team is paused until you choose above." }
        let waiting = room.notes_waiting ?? 0
        if waiting > 0 { return "\(waiting) note\(waiting == 1 ? "" : "s") waiting. The team gets it when the current step finishes, then adjusts." }
        return "Notes reach the team when its current step finishes. ⌘Return sends."
    }
}

struct TeamProgress: View {
    let room: TeamRoom
    private let steps = [("plan", "Plan", "list.bullet.clipboard"), ("work", "Build", "hammer"), ("review", "Review", "checkmark.seal"), ("done", "Done", "flag.checkered")]
    private var current: Int { room.status == "completed" ? 4 : steps.firstIndex(where: { $0.0 == room.stage }) ?? 0 }
    private var halted: Bool { ["failed", "cancelled", "blocked"].contains(room.status) }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                let done = index < current
                let now = index == current && !halted
                let color: Color = done ? .green : now ? (room.status == "awaiting_admin" ? .orange : .accentColor) : .secondary
                HStack(spacing: 7) {
                    ZStack {
                        Circle().fill(done || now ? color.opacity(0.16) : Color.primary.opacity(0.05)).frame(width: 30, height: 30)
                        Image(systemName: done ? "checkmark" : step.2).font(.bridge(.callout).bold()).foregroundStyle(color)
                    }
                    Text(step.1).font(.bridge(.callout).weight(now ? .bold : .regular)).foregroundStyle(done || now ? Color.primary : Color.secondary)
                }
                if index < steps.count - 1 {
                    Rectangle().fill(done ? Color.green.opacity(0.5) : Color.primary.opacity(0.12)).frame(height: 2).frame(maxWidth: .infinity).padding(.horizontal, 8)
                }
            }
            if let fixes = room.fixes, fixes > 0 {
                Text("Fix round \(fixes) of \(room.max_fixes ?? fixes)").font(.bridge(.caption).bold()).foregroundStyle(.orange)
                    .padding(.horizontal, 8).padding(.vertical, 4).background(Color.orange.opacity(0.12)).clipShape(Capsule()).padding(.leading, 12)
            }
        }.padding(14).background(Color.primary.opacity(0.025)).clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct TeamMessageRow: View {
    let entry: TeamEntry
    @State private var expanded = false
    private var isAdmin: Bool { entry.speaker.hasPrefix("You") }
    private var isSystem: Bool { entry.speaker == "AABridge" }
    private var isLeader: Bool { entry.speaker.hasSuffix("leader") }
    private var title: String {
        if isAdmin { return entry.speaker.hasSuffix("note") ? "You · note to the team" : "You" }
        let agent = entry.speaker.components(separatedBy: " · ").first ?? entry.speaker
        return agentName(agent) + (isLeader ? " · Leader" : " · Worker")
    }
    /// The leader's machine-readable verdict line, shown as a badge instead.
    private var verdict: String? {
        guard isLeader, let line = entry.text.components(separatedBy: "\n").last(where: { $0.uppercased().contains("VERDICT:") }) else { return nil }
        return line.uppercased().contains("APPROVED") ? "approved" : line.uppercased().contains("CHANGES") ? "changes" : nil
    }
    private var body_: String {
        verdict == nil ? entry.text : entry.text.components(separatedBy: "\n").filter { !$0.uppercased().contains("VERDICT:") }.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }
    private var long: Bool { body_.count > 900 }

    var body: some View {
        if isSystem {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "info.circle").foregroundStyle(.secondary)
                Text(entry.text).font(.bridge(.callout)).foregroundStyle(.secondary).textSelection(.enabled)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 6)
        } else {
            HStack(alignment: .top, spacing: 10) {
                if isAdmin { Spacer(minLength: 90) }
                else {
                    Image(systemName: isLeader ? "crown.fill" : "hammer.fill").foregroundStyle(.white).frame(width: 30, height: 30)
                        .background(Circle().fill(isLeader ? Color.purple : Color.teal))
                }
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(title).font(.bridge(.headline)).foregroundStyle(isAdmin ? Color.accentColor : Color.primary)
                        Spacer()
                        Text(Date(timeIntervalSince1970: entry.time), style: .time).font(.bridge(.caption)).foregroundStyle(.secondary)
                    }
                    Text(body_).textSelection(.enabled).lineLimit(long && !expanded ? 14 : nil).frame(maxWidth: .infinity, alignment: .leading)
                    if let verdict = verdict {
                        Label(verdict == "approved" ? "Approved" : "Changes requested", systemImage: verdict == "approved" ? "checkmark.seal.fill" : "arrow.uturn.backward.circle.fill")
                            .font(.bridge(.caption).bold()).foregroundStyle(verdict == "approved" ? Color.green : Color.orange)
                            .padding(.horizontal, 8).padding(.vertical, 4).background((verdict == "approved" ? Color.green : Color.orange).opacity(0.12)).clipShape(Capsule())
                    }
                    if long {
                        Button(expanded ? "Show less" : "Show more") { expanded.toggle() }.buttonStyle(.borderless).font(.bridge(.callout))
                    }
                }.padding(16)
                    .background(isAdmin ? Color.accentColor.opacity(0.10) : Color.primary.opacity(0.035)).clipShape(RoundedRectangle(cornerRadius: 14))
                if !isAdmin { Spacer(minLength: 40) }
            }
        }
    }
}

struct DeliveryCard: View {
    @ObservedObject var model: TeamRoomModel
    let room: TeamRoom
    @Binding var confirmMerge: Bool

    /// Every repository the task changed: this project first, then linked ones.
    private var places: [(label: String, branch: String?, path: String?, pull: String?, error: String?)] {
        guard let publication = room.publication else { return [] }
        let main: [(label: String, branch: String?, path: String?, pull: String?, error: String?)] =
            publication.branch == nil && publication.pull_request == nil ? [] : [(publication.path.map { URL(fileURLWithPath: $0).lastPathComponent } ?? room.follower.project, publication.branch, publication.path, publication.pull_request, nil)]
        return main + (publication.linked ?? []).map { ($0.name, $0.branch, $0.path, $0.pull_request, $0.error) }
    }

    var body: some View {
        Card(tint: .green) {
            Label(room.verdict == "approved" ? "Done. The leader approved the work." : "Done", systemImage: "checkmark.seal.fill").font(.bridge(.title3).bold())
            let pulls = places.compactMap { $0.pull }
            if !places.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(places, id: \.label) { place in row(place) }
                }
            } else if let result = room.result {
                Text("The work is saved in AABridge, not in your project folder yet.").foregroundStyle(.secondary)
                row((room.follower.project, result.branch, result.worktree, nil, nil))
            }
            if !pulls.isEmpty, let publication = room.publication {
                switch publication.merge {
                case "merged": Label(pulls.count == 1 ? "Merged on GitHub" : "All \(pulls.count) pull requests merged on GitHub", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                case "requested": HStack { ProgressView().controlSize(.small); Text("Merging on GitHub…") }
                default:
                    if let error = publication.merge_error, !error.isEmpty { Text(error).foregroundStyle(.orange).textSelection(.enabled) }
                    Button { confirmMerge = true } label: { Label(pulls.count == 1 ? "Merge pull request" : "Merge all \(pulls.count) pull requests", systemImage: "arrow.triangle.merge") }
                        .buttonStyle(.borderedProminent).disabled(model.busy || model.hasPending)
                    Text("Nothing is merged until you press Merge.").font(.bridge(.caption)).foregroundStyle(.secondary)
                }
            }
            if let error = room.publication?.error, !error.isEmpty {
                Label("Couldn’t deliver it to your project: " + error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).textSelection(.enabled)
            }
        }
    }

    private func row(_ place: (label: String, branch: String?, path: String?, pull: String?, error: String?)) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(place.label).font(.bridge(.headline))
            if let url = place.pull, let link = URL(string: url) {
                HStack(spacing: 8) { Image(systemName: "arrow.triangle.pull"); Link(url, destination: link).lineLimit(1) }
            }
            if let branch = place.branch {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.triangle.branch")
                    Text(branch).font(.bridge(.callout, design: .monospaced)).textSelection(.enabled)
                    Button("Copy") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(branch, forType: .string) }.buttonStyle(.borderless)
                    if let path = place.path, room.follower.target == "local" || room.publication != nil {
                        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }.buttonStyle(.borderless)
                    }
                }
            }
            if let error = place.error { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).textSelection(.enabled) }
        }
    }
}

struct FailureCard: View {
    let failure: TeamFailure
    let retry: (() -> Void)?
    let disabled: Bool
    var body: some View {
        Card(tint: .red) {
            Label(failure.machine == "this Mac" ? "Needs a fix on this Mac" : "Needs a fix on \(failure.machine)", systemImage: "wrench.and.screwdriver.fill").font(.bridge(.headline))
            Text(failure.problem).textSelection(.enabled)
            VStack(alignment: .leading, spacing: 4) {
                Text("How to fix").font(.bridge(.caption).bold()).foregroundStyle(.secondary)
                Text(failure.fix).textSelection(.enabled)
            }
            if let retry = retry {
                HStack {
                    Button { retry() } label: { Label("Try again", systemImage: "arrow.clockwise") }.buttonStyle(.borderedProminent).disabled(disabled)
                    Text("Nothing is lost. The team continues from this step.").font(.bridge(.caption)).foregroundStyle(.secondary)
                }
            }
        }
    }
}
