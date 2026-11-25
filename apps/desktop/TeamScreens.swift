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
    /// "Done" is reserved for work the leader approved.
    init(room: TeamRoom) {
        if room.unapproved { self.init(text: "Finished, not approved", color: .orange, icon: "exclamationmark.circle.fill") }
        else if room.onlyAnswered { self.init(text: "Answered", color: .green, icon: "checkmark.bubble.fill") }
        else { self.init(status: room.status, stage: room.stage) }
    }
    private init(text: String, color: Color, icon: String) { self.text = text; self.color = color; self.icon = icon }
}

struct StatusPill: View {
    let status: TeamStatus
    var body: some View {
        HStack(spacing: 6) {
            StatusDot(color: status.color, pulsing: status.icon == "circle.dotted", size: 6).frame(width: 10, height: 10)
            Text(status.text)
        }.font(.bridge(.caption).weight(.semibold)).foregroundStyle(status.color)
            .padding(.leading, 6).padding(.trailing, 10).padding(.vertical, 4)
            .background(Capsule().fill(status.color.opacity(0.11)))
            .overlay(Capsule().strokeBorder(status.color.opacity(0.18)))
            .accessibilityElement(children: .ignore).accessibilityLabel("Task status: " + status.text)
    }
}

func agentName(_ agent: String) -> String { agent == "claude" ? "Claude" : "Codex" }

/// A raised card. A tint marks one that needs attention (orange), failed (red) or finished (green).
struct Card<Content: View>: View {
    var tint: Color = .primary
    @ViewBuilder let content: Content
    private var neutral: Bool { tint == .primary }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) { content }
            .padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(
                ZStack {
                    RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).fill(Theme.raised)
                    if !neutral { RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).fill(tint.opacity(0.06)) }
                })
            .overlay(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).strokeBorder(neutral ? Theme.stroke : tint.opacity(0.28)))
            .shadow(color: .black.opacity(0.04), radius: 8, y: 2)
    }
}

struct OptionCard: View {
    let icon: String; let title: String; let detail: String; let selected: Bool
    var enabled = true
    let action: () -> Void
    @State private var hovering = false
    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: icon).font(.system(size: 14, weight: .medium)).foregroundStyle(selected ? Color.white : Color.accentColor)
                    .frame(width: 30, height: 30)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(selected ? Color.accentColor : Color.accentColor.opacity(0.1)))
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.bridge(.callout).weight(.semibold))
                    Text(detail).font(.bridge(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                Image(systemName: selected ? "checkmark.circle.fill" : "circle").font(.system(size: 15))
                    .foregroundStyle(selected ? Color.accentColor : Color.secondary.opacity(0.5))
            }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(selected ? Color.accentColor.opacity(0.07) : hovering ? Theme.hover : Theme.raised))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(selected ? Color.accentColor.opacity(0.7) : Theme.stroke, lineWidth: selected ? 1.5 : 1))
                .contentShape(Rectangle())
        }.buttonStyle(.plain).disabled(!enabled).opacity(enabled ? 1 : 0.45).onHover { hovering = $0 }
            .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

struct FormStep<Content: View>: View {
    let number: Int; let title: String; let subtitle: String
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("\(number)").font(.bridge(.caption).weight(.bold).monospacedDigit()).foregroundStyle(.secondary)
                    .frame(width: 20, height: 20).background(Circle().fill(Theme.fillStrong))
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.bridge(.headline))
                    Text(subtitle).font(.bridge(.caption)).foregroundStyle(.secondary)
                }
            }
            content
        }
    }
}

// MARK: - New task

struct NewTeamTaskView: View {
    @ObservedObject var model: TeamRoomModel
    var navigate: (Int) -> Void
    /// Agents this Mac could still add to the project, and how to add one.
    var missingAgents: [String] = []
    var addAgent: ((String) -> Void)? = nil
    var addingAgent = false
    var cancel: (() -> Void)? = nil
    private var projectName: String? { model.projectScope.isEmpty ? nil : model.visibleChoices.first?.projectName ?? model.projectScope }
    private let examples = [("Weight entry screen", "Add a screen where people enter their weight, save it, and see their last 7 entries."),
                            ("Fix a bug", "Fix the error people see when they sign in from the settings page."),
                            ("Add tests", "Write tests for the checkout flow and fix anything they catch.")]

    var body: some View {
        VStack(spacing: 0) {
            ScrollView { VStack(alignment: .leading, spacing: 30) {
                PageHeader(eyebrow: projectName.map { "New session · \($0)" } ?? "New session", title: "What should the team build?",
                           subtitle: "The leader plans and reviews, the worker writes the code, and you stay in charge.") {
                    if model.room != nil, let cancel = cancel { Button("Cancel") { model.showAssignment = false; model.draft = ""; cancel() }.keyboardShortcut(.cancelAction) }
                }
                if model.visibleChoices.count < 2 {
                    if model.projectScope.isEmpty { GettingStarted(count: model.choices.count, navigate: navigate) }
                    else { ProjectNeedsAgents(count: model.visibleChoices.count, missing: missingAgents, adding: addingAgent, add: addAgent, navigate: navigate) }
                }
                prompt
                FormStep(number: 1, title: "Pick your team", subtitle: model.projectScope.isEmpty ? "Any agent on any connected Mac can lead or do the work." : "Every agent that has this project, on this Mac or a paired one. Either can lead or do the work.") {
                    HStack(alignment: .top, spacing: 0) {
                        RolePicker(title: "Leader", detail: "Plans the work and reviews it", role: "leader", choices: model.visibleChoices, scoped: !model.projectScope.isEmpty, selection: $model.leader,
                                   agent: model.leaderChoice?.member.agent ?? "codex", modelID: $model.leaderModel, effort: $model.leaderEffort)
                        Button { let lead = model.leader; model.leader = model.follower; model.follower = lead } label: {
                            Image(systemName: "arrow.left.arrow.right").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                                .frame(width: 28, height: 28).background(Circle().fill(Theme.raised)).overlay(Circle().strokeBorder(Theme.stroke))
                        }.buttonStyle(.plain).help("Swap leader and worker").accessibilityLabel("Swap leader and worker").padding(.top, 30).padding(.horizontal, -6).zIndex(1)
                        RolePicker(title: "Worker", detail: "Writes the code and runs checks", role: "follower", choices: model.visibleChoices, scoped: !model.projectScope.isEmpty, selection: $model.follower,
                                   agent: model.followerChoice?.member.agent ?? "claude", modelID: $model.followerModel, effort: $model.followerEffort)
                    }
                    if !model.leader.isEmpty && model.leader == model.follower {
                        Label("Choose two different agents or Macs.", systemImage: "exclamationmark.triangle.fill").font(.bridge(.callout)).foregroundStyle(.orange)
                    }
                }
                FormStep(number: 2, title: "How much should the team do on its own?", subtitle: "You can send them a note at any time, whichever you pick.") {
                    HStack(alignment: .top, spacing: 10) {
                        OptionCard(icon: "hand.raised", title: "Check with me", detail: "The leader shows you its plan before any code is written, and asks before each round of fixes.",
                                   selected: model.approval == "ask") { model.approval = "ask" }
                        OptionCard(icon: "bolt.fill", title: "Work on its own", detail: "The leader owns the task. When its review finds problems, it sends them back to the worker. It tells you when the task is finished.",
                                   selected: model.approval == "auto") { model.approval = "auto" }
                    }
                    if model.approval == "auto" {
                        Stepper(value: $model.maxFixes, in: 1...10) {
                            Text("Ask me if it still isn’t right after **\(model.maxFixes)** fix \(model.maxFixes == 1 ? "round" : "rounds")").font(.bridge(.callout))
                        }.frame(maxWidth: 460, alignment: .leading)
                    }
                }
                FormStep(number: 3, title: "Where should the code go?", subtitle: "Nothing is ever merged into main for you. Merging is always your choice.") {
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
                        ForEach(PlacementOption.all) { option in
                            OptionCard(icon: option.icon, title: option.title, detail: option.detail, selected: model.placement == option.id,
                                       enabled: model.placementAllowed(option.id)) { model.placement = option.id }
                        }
                    }
                    if !model.hasLocalMember && !model.visibleChoices.isEmpty {
                        Label("To put code in your project, the leader or the worker has to run on this Mac.", systemImage: "info.circle").font(.bridge(.callout)).foregroundStyle(.secondary)
                    }
                    if model.placement == "branch" || model.placement == "worktree" {
                        HStack(spacing: 6) {
                            Image(systemName: "arrow.triangle.branch").foregroundStyle(.secondary)
                            Text(model.branchPreview).font(.bridge(.caption, design: .monospaced)).padding(.horizontal, 6).padding(.vertical, 2)
                                .background(RoundedRectangle(cornerRadius: 5).fill(Theme.fillStrong))
                            Text("The agents choose the rest during the task.").foregroundStyle(.secondary)
                        }.font(.bridge(.caption))
                    }
                    Toggle(isOn: $model.pullRequest) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Also open a pull request on GitHub").font(.bridge(.callout).weight(.semibold))
                            Text("Uses your GitHub CLI (gh) login on this Mac. The leader’s reviews are posted on the pull request. You get a Merge button when the task is done.")
                                .font(.bridge(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }.toggleStyle(.checkbox).disabled(model.placement == "sandbox")
                }
                HowItWorks()
            }.padding(.horizontal, 40).padding(.vertical, 32).frame(maxWidth: Theme.readable, alignment: .leading).frame(maxWidth: .infinity) }
            Hairline()
            HStack(spacing: 14) {
                if let lead = model.leaderChoice, let work = model.followerChoice {
                    HStack(spacing: -6) {
                        AgentGlyph(agent: lead.member.agent, role: "leader", size: 26).overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.canvas, lineWidth: 2))
                        AgentGlyph(agent: work.member.agent, role: "follower", size: 26).overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.canvas, lineWidth: 2))
                    }
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(summary).font(.bridge(.callout)).lineLimit(1).truncationMode(.tail)
                    Text("Uses the selected agents’ own accounts.").font(.bridge(.caption)).foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.send() } label: { Label("Start session", systemImage: "arrow.up.circle.fill").padding(.horizontal, 4) }
                    .buttonStyle(.borderedProminent).controlSize(.large).keyboardShortcut(.return, modifiers: .command)
                    .disabled(!model.ready || model.hasPending).help("Start session (⌘Return)")
            }.padding(.horizontal, 24).padding(.vertical, 14).background(.bar)
        }.background(Theme.canvas)
    }

    /// The request is the heart of the form, so it comes first and looks like a composer.
    private var prompt: some View {
        VStack(alignment: .leading, spacing: 10) {
            ZStack(alignment: .topLeading) {
                TextEditor(text: $model.draft).font(.bridge(.body)).scrollContentBackground(.hidden).frame(minHeight: 120).padding(10)
                    .accessibilityLabel("Describe what you want")
                if model.draft.isEmpty {
                    Text("Describe what you want, like you’d explain it to a teammate. Say what “done” looks like.")
                        .font(.bridge(.body)).foregroundStyle(.tertiary).padding(.horizontal, 15).padding(.vertical, 10).allowsHitTesting(false)
                }
            }
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.raised))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Theme.stroke))
            .shadow(color: .black.opacity(0.05), radius: 10, y: 3)
            HStack(spacing: 6) {
                ForEach(examples, id: \.0) { example in
                    Button { model.draft = example.1 } label: {
                        Label(example.0, systemImage: "sparkles").font(.bridge(.caption)).padding(.horizontal, 9).padding(.vertical, 4)
                            .background(Capsule().fill(Theme.fillStrong)).contentShape(Capsule())
                    }.buttonStyle(.plain).foregroundStyle(.secondary).help(example.1)
                }
                Spacer()
                Text("\(model.draft.count)/3000").font(.bridge(.caption).monospacedDigit()).foregroundStyle(model.draft.count > 3000 ? Color.red : Color.secondary)
            }
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
    let title: String; let detail: String
    /// `leader` or `follower`: sets the colour.
    let role: String
    let choices: [TeamChoice]
    /// Inside a project the picker names only the agent and the Mac.
    var scoped = false
    @Binding var selection: String
    let agent: String
    @Binding var modelID: String
    @Binding var effort: String
    var body: some View {
        Card {
            HStack(spacing: 10) {
                AgentGlyph(agent: agent, role: role, size: 30)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.bridge(.callout).weight(.semibold))
                    Text(detail).font(.bridge(.caption)).foregroundStyle(.secondary)
                }
            }
            Picker(title, selection: $selection) {
                Text("Choose an agent").tag("")
                ForEach(choices) { Text(scoped ? $0.agentAndMac : $0.label).tag($0.id) }
            }.labelsHidden()
            if let links = choices.first(where: { $0.id == selection })?.links, !links.isEmpty {
                Label("Also works with " + links.map { "\($0.name) (\($0.accessLabel))" }.joined(separator: ", "), systemImage: "link")
                    .font(.bridge(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            DisclosureGroup("Model and effort") {
                ModelSettingsFields(agent: agent, model: $modelID, effort: $effort).padding(.top, 6)
            }.font(.bridge(.caption))
        }
    }
}

/// The whole product in one row, for someone who has never run a team.
struct HowItWorks: View {
    @AppStorage("teamHowItWorksOpen") private var open = true
    private let steps = [("text.bubble", "You describe", "Say what you want, in plain words."),
                         ("list.bullet.clipboard", "Leader plans", "Reads the project. Writes instructions. Edits nothing."),
                         ("hammer", "Worker builds", "Writes the code in a private copy and runs checks."),
                         ("checkmark.seal", "Leader reviews", "Checks the real changes. Sends back real defects."),
                         ("arrow.triangle.merge", "You decide", "Get a branch or pull request. Only you merge.")]
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Button { withAnimation(.easeOut(duration: 0.15)) { open.toggle() } } label: {
                HStack {
                    Label("How a team works", systemImage: "questionmark.circle").font(.bridge(.callout).weight(.semibold))
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 10, weight: .bold)).rotationEffect(.degrees(open ? 90 : 0)).foregroundStyle(.secondary)
                }.contentShape(Rectangle())
            }.buttonStyle(.plain).accessibilityLabel(open ? "Hide how a team works" : "Show how a team works")
            if open {
                HStack(alignment: .top, spacing: 0) {
                    ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(spacing: 0) {
                                Image(systemName: step.0).font(.system(size: 12, weight: .medium)).foregroundStyle(.tint)
                                    .frame(width: 28, height: 28).background(Circle().fill(Color.accentColor.opacity(0.1)))
                                if index < steps.count - 1 { Rectangle().fill(Theme.stroke).frame(height: 1).frame(maxWidth: .infinity) }
                            }
                            Text(step.1).font(.bridge(.caption).weight(.semibold))
                            Text(step.2).font(.bridge(.caption2)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true).padding(.trailing, 10)
                        }.frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                }
                Text("The two agents talk to each other so you don’t relay messages between them. You can watch every action, send a note at any time, and stop the task.")
                    .font(.bridge(.caption)).foregroundStyle(.secondary)
            }
        }.padding(16).background(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).fill(Theme.fill))
    }
}

struct GettingStarted: View {
    let count: Int
    var navigate: (Int) -> Void
    var body: some View {
        Card(tint: .orange) {
            Label("Before your first task", systemImage: "sparkles").font(.bridge(.headline))
            Text(count == 0 ? "AABridge didn’t find a coding project yet. A team needs two agents, which can be on the same Mac."
                 : "AABridge found one agent. Add a second one: the other agent on this Mac, or one on a connected Mac.").font(.bridge(.callout))
            VStack(alignment: .leading, spacing: 8) {
                step(1, "Set up this Mac and sign in to Codex or Claude Code", "Settings", 3)
                step(2, "Add your Git project once for each agent", "Projects", 2)
                step(3, "Optional: connect a second Mac", "Devices", 1)
            }
        }
    }
    private func step(_ number: Int, _ text: String, _ button: String, _ section: Int) -> some View {
        HStack {
            Text("\(number)").font(.bridge(.caption).weight(.bold)).frame(width: 20, height: 20).background(Circle().fill(Theme.fillStrong))
            Text(text).font(.bridge(.callout))
            Spacer()
            Button("Open \(button)") { navigate(section) }
        }
    }
}

/// A project that has fewer than two agents can't make a team yet. Adding the other agent on this Mac is one click.
struct ProjectNeedsAgents: View {
    let count: Int
    let missing: [String]
    let adding: Bool
    let add: ((String) -> Void)?
    var navigate: (Int) -> Void
    var body: some View {
        Card(tint: .orange) {
            Label(count == 0 ? "No agent has this project yet" : "This project has one agent. A team needs two.", systemImage: "person.2.badge.plus").font(.bridge(.headline))
            Text("Add another agent on this Mac, or open the project on a paired Mac and add it there. The same folder can be worked on by Codex and Claude together.").font(.bridge(.callout))
            HStack(spacing: 10) {
                ForEach(missing, id: \.self) { agent in
                    Button { add?(agent) } label: { Label("Add \(agentName(agent)) on this Mac", systemImage: "plus") }.disabled(adding || add == nil)
                }
                if adding { ProgressView().controlSize(.small); Text("Checking the agent…").foregroundStyle(.secondary) }
                Spacer()
                Button("Pair a Mac") { navigate(1) }
            }
        }
    }
}

// MARK: - Team chat

struct TeamChatView: View {
    @ObservedObject var model: TeamRoomModel
    let room: TeamRoom
    private var readOnly: Bool { model.readOnly }
    @State private var confirmMerge = false
    @State private var showInfo = false
    @State private var showModels = false

    var body: some View {
        VStack(spacing: 0) {
            header
            if readOnly {
                Label("This session is run from \(model.deviceLabel(model.host)). You can follow it here; reply, approve and stop it on that Mac.", systemImage: "eye")
                    .font(.bridge(.caption)).foregroundStyle(.secondary).padding(.horizontal, 24).padding(.vertical, 7)
                    .frame(maxWidth: .infinity, alignment: .leading).background(Color.accentColor.opacity(0.06))
            }
            WorkLocationBar(model: model, room: room)
            Hairline()
            ScrollViewReader { proxy in ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    TeamProgress(room: room)
                    if model.hiddenMessages > 0 {
                        Button { model.loadEarlier() } label: {
                            Label(model.loadingEarlier ? "Loading…" : "Show earlier messages (\(model.hiddenMessages) more)", systemImage: "arrow.up.circle")
                                .font(.bridge(.caption))
                        }.buttonStyle(.borderless).disabled(model.loadingEarlier).frame(maxWidth: .infinity)
                    }
                    ForEach(model.earlier + (room.messages ?? [])) { entry in TeamMessageRow(entry: entry).id(entry.id) }
                    if room.running { WorkingRow(model: model, room: room).id("working") }
                    if !readOnly { actionPanel.id("action") }
                }.padding(.horizontal, 32).padding(.vertical, 24).frame(maxWidth: Theme.readable).frame(maxWidth: .infinity)
            }.onChange(of: room.messages?.last?.id) { id in if let id = id { withAnimation { proxy.scrollTo(room.running ? "working" : id, anchor: .bottom) } } }
                .onChange(of: room.status) { _ in withAnimation { proxy.scrollTo("action", anchor: .bottom) } } }
            if room.running || !model.liveEntries.isEmpty {
                LiveActivityView(model: model)
            }
            if !readOnly { composer }
        }.background(Theme.canvas)
            .confirmationDialog("Merge this pull request on GitHub?", isPresented: $confirmMerge) {
                Button("Merge pull request") { model.control("team_merge") }
                Button("Cancel", role: .cancel) {}
            } message: { Text("This merges the team’s pull request\((room.publication?.linked?.contains { $0.pull_request != nil } ?? false) ? "s in every project" : "") into their base branch using your GitHub login.") }
            .sheet(isPresented: $showInfo) { SessionInfoSheet(room: room, host: model.host, deviceLabel: model.deviceLabel) }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                Text(room.displayTitle).font(.bridge(.title3).weight(.semibold)).lineLimit(1).truncationMode(.tail).help(room.objective)
                HStack(spacing: 6) {
                    member(room.leader, role: "leader")
                    Image(systemName: "arrow.right").font(.system(size: 9, weight: .bold)).foregroundStyle(.tertiary)
                    member(room.follower, role: "follower")
                    if let approval = room.approval {
                        dot
                        Label(approval == "auto" ? "Works on its own" : "Checks with you", systemImage: approval == "auto" ? "bolt.fill" : "hand.raised")
                    }
                    if let placement = room.placement, let option = PlacementOption.all.first(where: { $0.id == placement.mode }) {
                        dot
                        Label(option.title + (placement.pull_request == true ? " + PR" : ""), systemImage: option.icon)
                    }
                    ForEach(["leader", "follower"], id: \.self) { role in
                        if let used = room.usage?[role], used.total > 0 {
                            dot
                            Text("\(role == "leader" ? "Leader" : "Worker") \(TeamUsage.short(used.total))")
                                .help(used.detail).accessibilityLabel("\(role == "leader" ? "Leader" : "Worker") used \(used.total) tokens. \(used.detail)")
                        }
                    }
                }.font(.bridge(.caption)).foregroundStyle(.secondary).lineLimit(1).labelStyle(CompactLabel())
            }
            Spacer(minLength: 12)
            StatusPill(status: TeamStatus(room: room))
            IconButton(symbol: "info.circle", help: "Session info", size: 28) { showInfo = true }
                .keyboardShortcut("i", modifiers: .command)
        }.padding(.horizontal, 24).padding(.vertical, 14)
    }

    private var dot: some View { Text("·").foregroundStyle(.tertiary) }

    private func member(_ member: TeamMember, role: String) -> some View {
        HStack(spacing: 5) {
            AgentGlyph(agent: member.agent, role: role, size: 16)
            Text(agentName(member.agent)).fontWeight(.medium).foregroundStyle(.primary)
            Text(model.deviceLabel(member.target))
        }.help("\(agentName(member.agent)) on \(model.deviceLabel(member.target)) · \(role == "leader" ? "Leader" : "Worker")")
            .accessibilityElement(children: .ignore).accessibilityLabel("\(agentName(member.agent)) on \(model.deviceLabel(member.target)), \(role == "leader" ? "leader" : "worker")")
    }

    @ViewBuilder private var actionPanel: some View {
        if room.status == "awaiting_admin", let gate = room.gate, gate.kind == "question" {
            Card(tint: .orange) {
                cardTitle("The \(room.stage == "work" ? "worker" : "leader") has a question for you", "questionmark.bubble.fill", .orange)
                Text(gate.reason).font(.bridge(.body)).textSelection(.enabled)
                Text("Type your answer in the box below. The \(room.stage == "work" ? "worker" : "leader") carries on from where it stopped; nothing is restarted.").font(.bridge(.caption)).foregroundStyle(.secondary)
                Button { model.decide("continue") } label: { Label("Send answer", systemImage: "paperplane.fill") }.buttonStyle(.borderedProminent)
                    .keyboardShortcut(.return, modifiers: .command).disabled(model.trimmedDraft.isEmpty || model.busy || model.hasPending)
            }
        } else if room.status == "awaiting_admin", let gate = room.gate {
            Card(tint: .orange) {
                cardTitle(gateTitle(gate), "hand.raised.fill", .orange)
                Text(gate.reason).font(.bridge(.body)).textSelection(.enabled)
                Text("You can type a note in the box below first. It’s sent along with the button you press.").font(.bridge(.caption)).foregroundStyle(.secondary)
                HStack {
                    if gate.kind == "plan" {
                        Button { model.decide("continue") } label: { Label("Approve plan and start building", systemImage: "checkmark") }.buttonStyle(.borderedProminent)
                        Button("Ask for a different plan") { model.decide("replan") }.disabled(model.trimmedDraft.isEmpty)
                            .help("Type what to change in the box below first.")
                    } else {
                        Button { model.decide("continue") } label: { Label("Let the worker fix it", systemImage: "wrench.and.screwdriver") }.buttonStyle(.borderedProminent)
                        Button("Change the plan") { model.decide("replan") }.disabled(model.trimmedDraft.isEmpty).help("Type what to change in the box below first.")
                        Button(["dispute", "leader"].contains(gate.cause ?? "") ? "Accept the work as it is" : "Finish here") { model.decide("finish") }
                            .help("Ends the task and keeps the work. The leader’s last review stays unapproved.")
                    }
                }.disabled(model.busy || model.hasPending)
            }
        } else if let failure = room.failure, ["blocked", "failed"].contains(room.status) {
            FailureCard(failure: failure, retry: room.status == "blocked" ? { model.control("team_retry") } : nil, disabled: model.busy || model.hasPending)
        } else if room.status == "blocked", room.error_code == "baseline_mismatch" {
            Card(tint: .orange) {
                cardTitle("The two project copies are on different commits", "arrow.triangle.branch", .orange)
                Text(room.error?.isEmpty == false ? room.error! : "The leader’s and the worker’s copies of the project differ.").textSelection(.enabled)
                Text("Match the two copies updates each one from its own remote (a fast-forward pull, only when nothing is uncommitted) and then continues. It never switches branches or discards work.")
                    .font(.bridge(.caption)).foregroundStyle(.secondary)
                if room.align == "requested" {
                    HStack { ProgressView().controlSize(.small); Text("Updating both copies…") }
                } else {
                    HStack {
                        Button { model.control("team_align") } label: { Label("Match the two copies", systemImage: "arrow.triangle.2.circlepath") }.buttonStyle(.borderedProminent)
                        Button("Try again without changes") { model.control("team_retry") }
                    }.disabled(model.busy || model.hasPending)
                }
            }
        } else if room.status == "blocked" {
            Card(tint: .red) {
                cardTitle("The team is paused", "exclamationmark.triangle.fill", .red)
                Text(room.error?.isEmpty == false ? room.error! : "A team member couldn’t be reached.").textSelection(.enabled)
                Button("Try again") { model.control("team_retry") }.buttonStyle(.borderedProminent).disabled(model.busy || model.hasPending)
            }
        } else if room.status == "completed" {
            DeliveryCard(model: model, room: room, confirmMerge: $confirmMerge)
        } else if ["failed", "cancelled"].contains(room.status), let result = room.result {
            Card {
                cardTitle("Partial work is saved", "tray.full", .secondary)
                Text(result.worktree).font(.bridge(.callout, design: .monospaced)).textSelection(.enabled)
                Text("Send a follow-up below to continue from where the team stopped.").font(.bridge(.callout)).foregroundStyle(.secondary)
            }
        }
    }

    private func cardTitle(_ text: String, _ icon: String, _ color: Color) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon).font(.system(size: 13, weight: .semibold)).foregroundStyle(color)
                .frame(width: 28, height: 28).background(Circle().fill(color.opacity(0.13)))
            Text(text).font(.bridge(.headline))
        }
    }

    private func gateTitle(_ gate: TeamGate) -> String {
        if gate.kind == "plan" { return "The plan is ready for your OK" }
        switch gate.cause ?? "" {
        case "dispute": return "Your call: the leader and the worker disagree"
        case "leader": return "Your call: the leader needs a decision from you"
        case "limit": return "Your call: still not approved after \(room.max_fixes ?? 3) fix rounds"
        case "unclear": return "Your call: the review has no clear verdict"
        default: return "Your call: the leader asked for fixes"
        }
    }

    /// Model choices, in a popover so the composer stays clean.
    @ViewBuilder private var modelsPopover: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(model.terminal ? "Models for the next message" : "Change an agent’s model").font(.bridge(.headline))
            ForEach(["leader", "follower"], id: \.self) { role in
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        AgentGlyph(agent: role == "leader" ? room.leader.agent : room.follower.agent, role: role, size: 20)
                        Text(role == "leader" ? "Leader" : "Worker").font(.bridge(.callout).weight(.semibold))
                    }
                    if !model.terminal && room.busy_role == role {
                        Text("Working right now. You can change its model when this step ends.").font(.bridge(.caption)).foregroundStyle(.secondary)
                    } else if role == "leader" {
                        ModelSettingsFields(agent: room.leader.agent, model: $model.leaderModel, effort: $model.leaderEffort)
                    } else {
                        ModelSettingsFields(agent: room.follower.agent, model: $model.followerModel, effort: $model.followerEffort)
                    }
                }
                if role == "leader" { Hairline() }
            }
            if !model.terminal {
                Hairline()
                HStack {
                    Text("Takes effect when that agent starts its next step. The step running now is not interrupted.").font(.bridge(.caption)).foregroundStyle(.secondary)
                    Spacer()
                    Button("Apply from the next step") { model.applyModels(); showModels = false }.buttonStyle(.borderedProminent)
                        .disabled(!model.settingsValid || model.busy || model.hasPending || room.status == "stopping")
                }
            }
        }.padding(18).frame(width: 560)
    }

    @ViewBuilder private var composer: some View {
        VStack(alignment: .leading, spacing: 6) {
            VStack(alignment: .leading, spacing: 8) {
                TextField(placeholder, text: $model.draft, axis: .vertical).lineLimit(1...8).textFieldStyle(.plain).font(.bridge(.body))
                    .padding(.horizontal, 4).padding(.top, 4)
                    .accessibilityLabel(model.terminal ? "Message to the team" : room.gate?.kind == "question" ? "Your answer" : model.waiting ? "Optional note with your decision" : "Note to the team")
                    .disabled(model.hasPending)
                HStack(spacing: 8) {
                    Button { showModels.toggle() } label: {
                        Label("Models", systemImage: "cpu").font(.bridge(.caption).weight(.medium)).padding(.horizontal, 8).padding(.vertical, 4)
                            .background(Capsule().fill(Theme.fillStrong)).contentShape(Capsule())
                    }.buttonStyle(.plain).foregroundStyle(.secondary)
                        .help(model.terminal ? "Models for the next message" : "Change an agent’s model")
                        .popover(isPresented: $showModels, arrowEdge: .top) { modelsPopover }
                    if !model.terminal, let waiting = room.notes_waiting, waiting > 0 {
                        Chip(text: "\(waiting) note\(waiting == 1 ? "" : "s") waiting", icon: "tray.full", tint: .accentColor)
                    }
                    Spacer()
                    if !model.terminal {
                        Button(role: .destructive) { model.control("team_cancel") } label: {
                            Label("Stop", systemImage: "stop.fill").font(.bridge(.caption).weight(.semibold)).padding(.horizontal, 9).padding(.vertical, 5)
                                .background(Capsule().fill(Color.red.opacity(0.1))).contentShape(Capsule())
                        }.buttonStyle(.plain).foregroundStyle(.red)
                            .disabled(model.busy || room.status == "stopping" || model.hasPending)
                            .keyboardShortcut(".", modifiers: .command).help("Stop the task (⌘.)")
                    }
                    if model.terminal {
                        sendButton(label: "Send", disabled: !model.settingsValid || model.busy || model.trimmedDraft.isEmpty || model.draft.count > 3000 || model.hasPending) { model.send() }
                    } else if !model.waiting {
                        sendButton(label: "Send note", disabled: model.busy || model.trimmedDraft.isEmpty || model.draft.count > 3000 || model.hasPending || room.status == "stopping") { model.sendNote() }
                    }
                }
            }.padding(12)
                .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Theme.raised))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(Theme.stroke))
                .shadow(color: .black.opacity(0.06), radius: 12, y: 4)
            Text(hint).font(.bridge(.caption2)).foregroundStyle(.tertiary).padding(.horizontal, 8)
        }.padding(.horizontal, 24).padding(.top, 10).padding(.bottom, 14).frame(maxWidth: Theme.readable + 48).frame(maxWidth: .infinity)
    }

    private func sendButton(label: String, disabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: "arrow.up").font(.system(size: 13, weight: .bold)).foregroundStyle(.white)
                .frame(width: 28, height: 28).background(Circle().fill(disabled ? Color.secondary.opacity(0.35) : Color.accentColor))
        }.buttonStyle(.plain).disabled(disabled).keyboardShortcut(.return, modifiers: .command)
            .help(label + " (⌘Return)").accessibilityLabel(label)
    }

    private var placeholder: String {
        model.terminal ? "Ask the team a question, or tell it what to change next" : room.gate?.kind == "question" ? "Type your answer…" : model.waiting ? "Optional note to send with your choice…" : "Send a note to the team, e.g. “Use kilograms, not pounds”"
    }

    private var hint: String {
        if model.terminal { return "Ask a question or request a change. A question is answered without touching code; a change continues on the same branch. ⌘Return sends." }
        if room.gate?.kind == "question" { return "The team is paused until you answer. ⌘Return sends your answer." }
        if model.waiting { return "The team is paused until you choose above." }
        let waiting = room.notes_waiting ?? 0
        if waiting > 0 { return "\(waiting) note\(waiting == 1 ? "" : "s") waiting. The team gets it when the current step finishes, then adjusts." }
        return "Notes reach the team when its current step finishes. ⌘Return sends."
    }
}

/// An icon and its title, a little closer than the system default.
struct CompactLabel: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 4) { configuration.icon.imageScale(.small); configuration.title }
    }
}

struct TeamProgress: View {
    let room: TeamRoom
    private let steps = [("plan", "Plan", "list.bullet.clipboard"), ("work", "Build", "hammer"), ("review", "Review", "checkmark.seal"), ("done", "Done", "flag.checkered")]
    /// A task finished without approval stops at Review; an answered question never left Plan.
    private var current: Int { room.unapproved ? 2 : room.onlyAnswered ? 1 : room.status == "completed" ? 4 : steps.firstIndex(where: { $0.0 == room.stage }) ?? 0 }
    private var halted: Bool { ["failed", "cancelled", "blocked"].contains(room.status) || room.onlyAnswered }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                let done = index < current
                let now = index == current && !halted
                let color: Color = done ? .green : now ? (room.status == "awaiting_admin" || room.unapproved ? .orange : .accentColor) : .secondary
                HStack(spacing: 6) {
                    ZStack {
                        Circle().fill(done || now ? color.opacity(0.14) : Theme.fill).frame(width: 22, height: 22)
                        if now && !room.unapproved && room.running { Circle().strokeBorder(color.opacity(0.5), lineWidth: 1).frame(width: 22, height: 22) }
                        Image(systemName: done ? "checkmark" : now && room.unapproved ? "exclamationmark" : step.2).font(.system(size: 10, weight: .bold)).foregroundStyle(color)
                    }
                    Text(now && room.unapproved ? "Not approved" : step.1).font(.bridge(.caption).weight(now ? .semibold : .regular))
                        .foregroundStyle(done || now ? Color.primary : Color.secondary).lineLimit(1).fixedSize()
                }
                if index < steps.count - 1 {
                    Capsule().fill(done ? Color.green.opacity(0.45) : Theme.stroke).frame(height: 1.5).frame(maxWidth: .infinity).padding(.horizontal, 10)
                }
            }
            if let fixes = room.fixes, fixes > 0 {
                Chip(text: "Fix round \(fixes) of \(room.max_fixes ?? fixes)", icon: "arrow.uturn.backward", tint: .orange).fixedSize().padding(.leading, 14)
            }
        }.padding(.horizontal, 14).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.fill))
            .accessibilityElement(children: .ignore).accessibilityLabel(spoken)
    }

    /// The whole strip as one sentence, since the colours and ticks carry its meaning.
    var spoken: String {
        let parts = steps.enumerated().map { index, step in
            index < current ? "\(step.1) done" : index == current && !halted ? (room.unapproved ? "review not approved" : room.status == "awaiting_admin" ? "\(step.1) waiting for you" : "\(step.1) in progress") : "\(step.1) not started"
        }
        return "Progress: " + parts.joined(separator: ", ") + ((room.fixes ?? 0) > 0 ? ". Fix round \(room.fixes!) of \(room.max_fixes ?? room.fixes!)" : "")
    }
}

struct TeamMessageRow: View {
    let entry: TeamEntry
    @State private var expanded = false
    private var isAdmin: Bool { entry.speaker.hasPrefix("You") }
    private var isSystem: Bool { entry.speaker == "AABridge" }
    private var isLeader: Bool { entry.speaker.hasSuffix("leader") }
    private var agent: String { (entry.speaker.components(separatedBy: " · ").first ?? entry.speaker).lowercased() }
    private var title: String {
        if isAdmin { return entry.speaker.hasSuffix("note") ? "You · note to the team" : "You" }
        return agentName(agent) + (isLeader ? " · Leader" : " · Worker")
    }
    /// The leader's machine-readable verdict line, shown as a badge instead.
    private var verdict: String? {
        guard isLeader, let line = entry.text.components(separatedBy: "\n").last(where: { $0.uppercased().contains("VERDICT:") }) else { return nil }
        let value = line.uppercased()
        return value.contains("APPROVED") ? "approved" : value.contains("CHANGES") ? "changes" : value.contains("NEEDS") ? "admin" : nil
    }
    private var answerOnly: Bool { isLeader && entry.text.uppercased().range(of: #"(?m)^\W*NEXT:\W*ANSWER[ _]ONLY\W*$"#, options: .regularExpression) != nil }
    /// Lines the agents write for AABridge, not for the reader.
    private static let markers = #"^\W*(TASK_TITLE|BRANCH_NAME|NEXT|VERDICT):.*$"#
    static func readable(_ text: String) -> String {
        text.components(separatedBy: "\n").filter { $0.range(of: markers, options: [.regularExpression, .caseInsensitive]) == nil }
            .joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }
    private var body_: String { Self.readable(entry.text) }
    private var long: Bool { body_.count > 900 }
    private var time: some View {
        Text(Date(timeIntervalSince1970: entry.time), style: .time).font(.bridge(.caption2)).foregroundStyle(.tertiary)
    }

    var body: some View {
        if isSystem {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "info.circle").font(.system(size: 11)).foregroundStyle(.tertiary).padding(.top, 2)
                Text(Self.linked(entry.text)).font(.bridge(.caption)).foregroundStyle(.secondary).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }.padding(.horizontal, 12).padding(.vertical, 7)
                .background(Capsule(style: .continuous).fill(Theme.fill))
                .frame(maxWidth: .infinity)
                .accessibilityElement(children: .combine).accessibilityLabel("AABridge notice: " + entry.text)
        } else if isAdmin {
            VStack(alignment: .trailing, spacing: 4) {
                if entry.speaker.hasSuffix("note") {
                    Label("Note to the team", systemImage: "note.text").font(.bridge(.caption2).weight(.medium)).foregroundStyle(.secondary)
                }
                MarkdownText(text: body_).padding(.horizontal, 14).padding(.vertical, 10)
                    .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Color.accentColor.opacity(0.1)))
                    .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Color.accentColor.opacity(0.12)))
                time
            }.frame(maxWidth: 560, alignment: .trailing).frame(maxWidth: .infinity, alignment: .trailing)
                .accessibilityElement(children: .combine).accessibilityLabel(title + ": " + body_)
        } else {
            HStack(alignment: .top, spacing: 12) {
                AgentGlyph(agent: agent, role: isLeader ? "leader" : "follower", size: 26)
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(agentName(agent)).font(.bridge(.callout).weight(.semibold))
                        RoleTag(leader: isLeader)
                        time
                    }.accessibilityElement(children: .ignore).accessibilityLabel(title).accessibilityAddTraits(.isHeader)
                    MarkdownText(text: body_).frame(maxWidth: .infinity, maxHeight: long && !expanded ? 320 : nil, alignment: .topLeading).clipped()
                        .mask(LinearGradient(stops: long && !expanded ? [.init(color: .black, location: 0.8), .init(color: .clear, location: 1)] : [.init(color: .black, location: 0), .init(color: .black, location: 1)],
                                             startPoint: .top, endPoint: .bottom))
                    if long {
                        Button(expanded ? "Show less" : "Show more") { withAnimation(.easeOut(duration: 0.2)) { expanded.toggle() } }.buttonStyle(.borderless).font(.bridge(.caption))
                    }
                    if verdict == "approved" { Chip(text: "Approved", icon: "checkmark.seal.fill", tint: .green) }
                    else if verdict == "changes" { Chip(text: "Changes requested", icon: "arrow.uturn.backward.circle.fill", tint: .orange) }
                    else if verdict == "admin" { Chip(text: "Needs your decision", icon: "hand.raised.fill", tint: .orange) }
                    else if answerOnly { Chip(text: "Answer only · no code changed", icon: "bubble.left.fill") }
                }
                Spacer(minLength: 24)
            }
        }
    }
}

extension TeamMessageRow {
    /// AABridge's own notices can carry a pull-request link; make https links clickable.
    static func linked(_ text: String) -> AttributedString {
        var result = AttributedString(text)
        guard let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue) else { return result }
        for match in detector.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let url = match.url, url.scheme == "https", let range = Range(match.range, in: text),
                  let lower = AttributedString.Index(range.lowerBound, within: result), let upper = AttributedString.Index(range.upperBound, within: result) else { continue }
            result[lower..<upper].link = url
        }
        return result
    }
}

/// The in-progress row at the end of the chat: who has the task right now.
struct WorkingRow: View {
    @ObservedObject var model: TeamRoomModel
    let room: TeamRoom
    private var worker: Bool { room.stage == "work" }
    private var member: TeamMember { worker ? room.follower : room.leader }
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            AgentGlyph(agent: member.agent, role: worker ? "follower" : "leader", size: 26)
            TimelineView(.periodic(from: .now, by: 1)) { context in
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 8) {
                        Text(room.status == "stopping" ? "Stopping \(agentName(member.agent))…" : "\(agentName(member.agent)) · \(worker ? "Worker" : "Leader") is \(stageVerb(room.stage).lowercased())")
                            .font(.bridge(.callout).weight(.semibold))
                        TypingDots(color: Theme.agent(member.agent), date: context.date)
                        if let started = room.live?.started_at ?? room.stage_started {
                            Text(elapsedText(Int(context.date.timeIntervalSince1970 - started))).font(.bridge(.caption).monospacedDigit()).foregroundStyle(.secondary)
                        }
                    }
                    Text(detail).font(.bridge(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 24)
        }.padding(14)
            .background(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).fill(Theme.agent(member.agent).opacity(0.05)))
            .overlay(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).strokeBorder(Theme.agent(member.agent).opacity(0.2)))
            .accessibilityElement(children: .ignore).accessibilityLabel("\(agentName(member.agent)), the \(worker ? "worker" : "leader"), is \(stageVerb(room.stage).lowercased()). \(detail)")
    }
    private var detail: String {
        let place = "on " + model.deviceLabel(member.target)
        switch room.stage {
        case "plan": return "Reading the project \(place) and writing instructions for the worker. No files are edited in this step."
        case "work": return (room.fixes ?? 0) > 0 ? "Fixing what the review found \(place), in its private copy of the project." : "Writing the code \(place), in its private copy of the project. Your folder is not touched while it works."
        default: return "Checking the worker’s actual changes \(place). It can run checks but cannot edit the code."
        }
    }
}

/// Three dots that light up in turn, driven by the surrounding TimelineView.
struct TypingDots: View {
    let color: Color
    let date: Date
    var body: some View {
        let step = Int(date.timeIntervalSince1970) % 3
        HStack(spacing: 3) {
            ForEach(0..<3) { index in Circle().fill(color.opacity(index == step ? 0.9 : 0.25)).frame(width: 4, height: 4) }
        }.animation(.easeInOut(duration: 0.3), value: step).accessibilityHidden(true)
    }
}

/// A branch name as a quiet monospaced token with a copy button.
struct BranchToken: View {
    let branch: String
    var live = false
    var body: some View {
        HStack(spacing: 4) {
            Text(branch).font(.bridge(.caption, design: .monospaced)).textSelection(.enabled).lineLimit(1).truncationMode(.middle)
            IconButton(symbol: "doc.on.doc", help: "Copy", size: 18) { copyToPasteboard(branch) }
        }.padding(.leading, 7).padding(.trailing, 2).padding(.vertical, 1)
            .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(live ? Color.green.opacity(0.1) : Theme.fillStrong))
    }
}

/// Where the code is right now. Shown for the whole task, not only at the end.
struct WorkLocationBar: View {
    @ObservedObject var model: TeamRoomModel
    let room: TeamRoom
    private var mode: String { room.placement?.mode ?? "sandbox" }
    private var building: Bool { room.running && room.stage == "work" }

    var body: some View {
        if room.onlyAnswered && room.result == nil { EmptyView() } else {
            VStack(alignment: .leading, spacing: 4) {
                if let publication = room.publication, let branch = publication.branch {
                    HStack(spacing: 8) {
                        Image(systemName: "arrow.triangle.branch").foregroundStyle(.tint)
                        BranchToken(branch: branch)
                        if let path = publication.path {
                            Text("in " + URL(fileURLWithPath: path).lastPathComponent).foregroundStyle(.secondary)
                            IconButton(symbol: "folder", help: "Show in Finder", size: 20) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
                        }
                        if let url = publication.pull_request, let link = URL(string: url) {
                            Text("·").foregroundStyle(.tertiary)
                            Image(systemName: "arrow.triangle.pull").foregroundStyle(.tint)
                            Link("Pull request" + (link.lastPathComponent.allSatisfy(\.isNumber) ? " #" + link.lastPathComponent : ""), destination: link)
                        }
                        Spacer(minLength: 0)
                    }
                    liveRow
                    note(publication.merge == "merged" ? "Merged on GitHub." : building ? "The worker is changing its private copy. This branch updates when the build step finishes."
                         : "This branch has the worker’s latest finished build. Check it out with: git switch \(branch)")
                    ForEach(publication.linked ?? []) { link in
                        if let other = link.branch { note("\(link.name): branch \(other)" + (link.pull_request.map { " · " + $0 } ?? "")) }
                    }
                } else if mode != "sandbox" {
                    row("arrow.triangle.branch", room.running || room.status == "awaiting_admin" ? "No branch yet" : "No branch was created")
                    liveRow
                    note(mode == "current" ? "The commits are added to your checked-out branch when the first build step finishes. Until then the worker uses a private copy."
                         : room.preview?.branch != nil ? "AABridge creates the real \(room.follower.agent)/… branch in your project when this build step finishes."
                         : "AABridge creates \(room.follower.agent)/… in your project when the first build step finishes. Until then the worker uses a private copy, so nothing appears in your folder.")
                } else if let result = room.result {
                    HStack(spacing: 8) {
                        Image(systemName: "shippingbox").foregroundStyle(.tint)
                        Text("Kept in AABridge").fontWeight(.semibold)
                        BranchToken(branch: result.branch)
                        if room.follower.target == "local" && !result.worktree.isEmpty {
                            IconButton(symbol: "folder", help: "Show in Finder", size: 20) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: result.worktree)]) }
                        }
                        Spacer(minLength: 0)
                    }
                    note(room.follower.target == "local" && !result.worktree.isEmpty ? "Your project folder is unchanged. The work is a Git checkout at \(result.worktree)"
                         : "Your project folder is unchanged. The work is saved on \(model.deviceLabel(room.follower.target)).")
                } else {
                    row("shippingbox", "Result stays in AABridge")
                    note("Your project folder is not touched. A saved copy appears here when the first build step finishes.")
                }
                if let error = room.publication?.error, !error.isEmpty {
                    Label("Couldn’t deliver the latest build: " + error, systemImage: "exclamationmark.triangle.fill").font(.bridge(.caption)).foregroundStyle(.orange).textSelection(.enabled)
                }
            }.font(.bridge(.caption)).padding(.horizontal, 24).padding(.vertical, 9).frame(maxWidth: .infinity, alignment: .leading).background(Theme.fill)
        }
    }
    /// The unfinished build, refreshed every 20 seconds while a worker on this Mac is building.
    @ViewBuilder private var liveRow: some View {
        if building, let preview = room.preview {
            if let branch = preview.branch {
                HStack(spacing: 8) {
                    StatusDot(color: .green, pulsing: true, size: 6).frame(width: 14, height: 14)
                    Text("Live").fontWeight(.semibold).foregroundStyle(.green)
                    BranchToken(branch: branch, live: true)
                    Text("\(preview.files ?? 0) file\(preview.files == 1 ? "" : "s") changed so far").foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                }
                note("Work in progress, refreshed every 20 seconds. See it with: git diff main...\(branch)   It is removed when the build step finishes and the real branch is updated.")
            } else if let error = preview.error {
                note("Live view of the build is paused: " + error)
            }
        }
    }
    private func row(_ icon: String, _ text: String) -> some View {
        HStack(spacing: 8) { Image(systemName: icon).foregroundStyle(.secondary); Text(text).fontWeight(.semibold) }
    }
    private func note(_ text: String) -> some View {
        Text(text).font(.bridge(.caption2)).foregroundStyle(.secondary).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
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
        Card(tint: room.unapproved ? .orange : .green) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: room.unapproved ? "exclamationmark" : room.answered == true ? "bubble.left.fill" : "checkmark")
                    .font(.system(size: 15, weight: .bold)).foregroundStyle(.white)
                    .frame(width: 34, height: 34).background(Circle().fill(room.unapproved ? Color.orange : Color.green))
                VStack(alignment: .leading, spacing: 4) {
                    if room.unapproved {
                        Text("Finished without the leader’s approval").font(.bridge(.title3).weight(.semibold))
                        Text("The work is saved, but the leader’s last review did not approve it. Read that review before you merge, or send a message to keep working on it.")
                            .font(.bridge(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    } else if room.answered == true {
                        Text(room.verdict == "approved" ? "Answered. The approved work is unchanged." : "Answered. No code was changed.").font(.bridge(.title3).weight(.semibold))
                    } else {
                        Text(room.verdict == "approved" ? "Done. The leader approved the work." : "Done").font(.bridge(.title3).weight(.semibold))
                    }
                }
            }
            let pulls = places.compactMap { $0.pull }
            if !places.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(places.enumerated()), id: \.element.label) { index, place in
                        row(place)
                        if index < places.count - 1 { Hairline() }
                    }
                }.padding(.horizontal, 12).background(RoundedRectangle(cornerRadius: Theme.corner, style: .continuous).fill(Theme.fill))
            } else if let result = room.result {
                Text("The work is saved in AABridge, not in your project folder yet.").font(.bridge(.callout)).foregroundStyle(.secondary)
                row((room.follower.project, result.branch, result.worktree, nil, nil)).padding(.horizontal, 12)
                    .background(RoundedRectangle(cornerRadius: Theme.corner, style: .continuous).fill(Theme.fill))
            }
            if !pulls.isEmpty, let publication = room.publication {
                switch publication.merge {
                case "merged": Label(pulls.count == 1 ? "Merged on GitHub" : "All \(pulls.count) pull requests merged on GitHub", systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.bridge(.callout).weight(.semibold))
                case "requested": HStack { ProgressView().controlSize(.small); Text("Merging on GitHub…") }
                default:
                    if let error = publication.merge_error, !error.isEmpty { Text(error).font(.bridge(.callout)).foregroundStyle(.orange).textSelection(.enabled) }
                    HStack(spacing: 12) {
                        Button { confirmMerge = true } label: { Label(pulls.count == 1 ? "Merge pull request" : "Merge all \(pulls.count) pull requests", systemImage: "arrow.triangle.merge") }
                            .buttonStyle(.borderedProminent).controlSize(.large).disabled(model.busy || model.hasPending)
                        Text("Nothing is merged until you press Merge.").font(.bridge(.caption)).foregroundStyle(.secondary)
                    }
                }
            }
            if let error = room.publication?.error, !error.isEmpty {
                Label("Couldn’t deliver it to your project: " + error, systemImage: "exclamationmark.triangle.fill").font(.bridge(.callout)).foregroundStyle(.orange).textSelection(.enabled)
            }
        }
    }

    private func row(_ place: (label: String, branch: String?, path: String?, pull: String?, error: String?)) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "folder").font(.system(size: 11)).foregroundStyle(.secondary)
                Text(place.label).font(.bridge(.callout).weight(.semibold))
                Spacer(minLength: 0)
                if let path = place.path, place.branch != nil, room.follower.target == "local" || room.publication != nil {
                    IconButton(symbol: "folder", help: "Show in Finder", size: 22) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
                }
            }
            if let url = place.pull, let link = URL(string: url) {
                HStack(spacing: 6) { Image(systemName: "arrow.triangle.pull").foregroundStyle(.tint); Link(url, destination: link).lineLimit(1).truncationMode(.middle) }.font(.bridge(.caption))
            }
            if let branch = place.branch {
                HStack(spacing: 6) { Image(systemName: "arrow.triangle.branch").foregroundStyle(.secondary); BranchToken(branch: branch) }.font(.bridge(.caption))
            }
            if let error = place.error { Label(error, systemImage: "exclamationmark.triangle.fill").font(.bridge(.caption)).foregroundStyle(.orange).textSelection(.enabled) }
        }.padding(.vertical, 10)
    }
}

struct FailureCard: View {
    let failure: TeamFailure
    let retry: (() -> Void)?
    let disabled: Bool
    var body: some View {
        Card(tint: .red) {
            HStack(spacing: 10) {
                Image(systemName: "wrench.and.screwdriver.fill").font(.system(size: 13, weight: .semibold)).foregroundStyle(.red)
                    .frame(width: 28, height: 28).background(Circle().fill(Color.red.opacity(0.13)))
                Text(failure.machine == "this Mac" ? "Needs a fix on this Mac" : "Needs a fix on \(failure.machine)").font(.bridge(.headline))
            }
            Text(failure.problem).textSelection(.enabled)
            VStack(alignment: .leading, spacing: 4) {
                SectionLabel("How to fix")
                Text(failure.fix).textSelection(.enabled)
            }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: Theme.corner, style: .continuous).fill(Theme.fill))
            if let retry = retry {
                HStack {
                    Button { retry() } label: { Label("Try again", systemImage: "arrow.clockwise") }.buttonStyle(.borderedProminent).disabled(disabled)
                    Text("Nothing is lost. The team continues from this step.").font(.bridge(.caption)).foregroundStyle(.secondary)
                }
            }
        }
    }
}
