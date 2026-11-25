import AppKit
import SwiftUI

/// Everything known about one session: who works in it, how it runs, where its code went and what it cost.
/// Opened from the info button in the session header, the sidebar and the project page.
struct SessionInfoSheet: View {
    @Environment(\.dismiss) private var dismiss
    let room: TeamRoom
    /// The Mac that runs the session (`local` for this one).
    var host: String = "local"
    var deviceLabel: (String) -> String = { $0 == "local" ? "This Mac" : $0 }

    private var status: TeamStatus { TeamStatus(room: room) }

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "info.circle.fill").font(.system(size: 22)).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 4) {
                    Text(room.displayTitle).font(.bridge(.title3).weight(.semibold)).lineLimit(2).textSelection(.enabled)
                    Text("Session info").font(.bridge(.callout)).foregroundStyle(.secondary)
                }
                Spacer()
                StatusPill(status: status)
            }.padding(20)
            Hairline()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    section("Session") {
                        InfoRow(label: "ID", value: room.id, monospaced: true, copyable: true)
                        InfoRow(label: "Status", value: status.text + " · " + stageVerb(room.stage).lowercased() + " step")
                        InfoRow(label: "Run from", value: deviceLabel(host))
                        if let updated = room.updated_at, updated > 0 {
                            InfoRow(label: "Last update", value: Date(timeIntervalSince1970: updated).formatted(date: .abbreviated, time: .shortened))
                        }
                        if room.read_only == true { InfoRow(label: "Access", value: "Read only here. Reply, approve and stop it on \(deviceLabel(host)).") }
                    }
                    section("Team") {
                        member(room.leader, role: "leader")
                        Hairline()
                        member(room.follower, role: "follower")
                    }
                    section("How it runs") {
                        InfoRow(label: "Autonomy", value: room.approval == "auto" ? "Works on its own · asks after \(room.max_fixes ?? 3) fix rounds" : room.approval == "ask" ? "Checks with you" : "Single pass")
                        if let placement = room.placement {
                            InfoRow(label: "Code goes to", value: (PlacementOption.all.first { $0.id == placement.mode }?.title ?? placement.mode) + (placement.pull_request == true ? " + pull request" : ""))
                        }
                        if let fixes = room.fixes, fixes > 0 { InfoRow(label: "Fix rounds", value: "\(fixes) of \(room.max_fixes ?? fixes)") }
                        if let verdict = room.verdict { InfoRow(label: "Last verdict", value: verdict == "approved" ? "Approved" : "Changes requested") }
                        if let waiting = room.notes_waiting, waiting > 0 { InfoRow(label: "Notes waiting", value: "\(waiting)") }
                    }
                    if room.publication?.branch != nil || room.publication?.pull_request != nil || room.result != nil {
                        section("Code") {
                            if let branch = room.publication?.branch { InfoRow(label: "Branch", value: branch, monospaced: true, copyable: true) }
                            if let path = room.publication?.path { InfoRow(label: "Project folder", value: path, monospaced: true) }
                            if let url = room.publication?.pull_request, let link = URL(string: url) {
                                HStack(alignment: .firstTextBaseline, spacing: 12) {
                                    Text("Pull request").font(.bridge(.callout)).foregroundStyle(.secondary).frame(width: 120, alignment: .leading)
                                    Link(url, destination: link).font(.bridge(.callout)).lineLimit(1).truncationMode(.middle)
                                    Spacer(minLength: 0)
                                }.padding(.vertical, 7)
                            }
                            if let merge = room.publication?.merge { InfoRow(label: "Merge", value: merge == "merged" ? "Merged on GitHub" : merge == "requested" ? "Merging…" : merge) }
                            ForEach(room.publication?.linked ?? []) { link in
                                InfoRow(label: link.name, value: [link.branch, link.pull_request].compactMap { $0 }.joined(separator: " · "), monospaced: true)
                            }
                            if let result = room.result {
                                InfoRow(label: "Task branch", value: result.branch, monospaced: true, copyable: true)
                                if !result.worktree.isEmpty { InfoRow(label: "Task checkout", value: result.worktree, monospaced: true, copyable: true) }
                                InfoRow(label: "Base commit", value: String(result.base_commit.prefix(12)), monospaced: true)
                                InfoRow(label: "Result commit", value: String(result.result_commit.prefix(12)), monospaced: true)
                            }
                        }
                    }
                    if let usage = room.usage, !usage.isEmpty {
                        section("Usage") {
                            ForEach(["leader", "follower"], id: \.self) { role in
                                if let used = usage[role] {
                                    InfoRow(label: role == "leader" ? "Leader" : "Worker", value: "\(TeamUsage.short(used.total)) tokens · " + used.detail)
                                }
                            }
                        }
                    }
                    section("Request") {
                        Text(room.objective).font(.bridge(.callout)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 6)
                    }
                    if let error = room.error, !error.isEmpty {
                        section("Last error") { Text(error).font(.bridge(.callout)).foregroundStyle(.orange).textSelection(.enabled).padding(.vertical, 6) }
                    }
                }.padding(20)
            }
            Hairline()
            HStack {
                Button { copyToPasteboard(room.id) } label: { Label("Copy session ID", systemImage: "doc.on.doc") }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }.padding(16)
        }.frame(width: 620, height: 640).background(Theme.canvas)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(title)
            VStack(alignment: .leading, spacing: 0) { content() }
                .padding(.horizontal, 14).padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: Theme.corner, style: .continuous).fill(Theme.fill))
        }
    }

    private func member(_ member: TeamMember, role: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            AgentGlyph(agent: member.agent, role: role, size: 28)
            VStack(alignment: .leading, spacing: 3) {
                Text("\(agentName(member.agent)) · \(role == "leader" ? "Leader" : "Worker")").font(.bridge(.callout).weight(.semibold))
                Text("\(deviceLabel(member.target)) · \(member.project)").font(.bridge(.caption)).foregroundStyle(.secondary).textSelection(.enabled)
                let model = member.model_settings?.model ?? "", effort = member.model_settings?.effort ?? ""
                Text(model.isEmpty && effort.isEmpty ? "Agent default model" : [model.isEmpty ? "default model" : model, effort.isEmpty ? nil : effort + " effort"].compactMap { $0 }.joined(separator: " · "))
                    .font(.bridge(.caption, design: .monospaced)).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }.padding(.vertical, 10)
    }
}
