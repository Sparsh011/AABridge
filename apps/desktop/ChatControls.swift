import SwiftUI

struct PendingSendView: View {
    @ObservedObject var model: TaskBrowserModel
    var body: some View {
        if model.actionBusy {
            HStack { ProgressView().controlSize(.small); Text(model.actionNotice).font(.bridge(.callout)); Spacer() }.padding(10)
        } else if let pending = model.pendingAction {
            VStack(alignment: .leading, spacing: 8) {
                Text("Unconfirmed \(pending.action) for \(pending.target == "local" ? "this Mac" : pending.target)").font(.bridge(.headline))
                if let id = pending.taskID { Text(id).font(.bridge(.caption, design: .monospaced)).foregroundStyle(.secondary) }
                if model.visibleSend == nil, let text = model.pendingText { Text(text).font(.bridge(.callout)).lineLimit(5).textSelection(.enabled) }
                Text(model.actionNotice.isEmpty ? "A previous send is saved locally. Check the task list, then retry it or clear the pending send." : model.actionNotice).font(.bridge(.callout))
                HStack {
                    Button("Retry same send") { model.retryPending() }
                    Button("Clear pending send") { model.clearPending() }
                }
                Text("Clearing this record does not cancel a task already accepted by the receiver.").font(.bridge(.caption)).foregroundStyle(.secondary)
            }.padding(12).frame(maxWidth: .infinity, alignment: .leading).background(Color.orange.opacity(0.08)).clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }
}

struct TaskComposer: View {
    @ObservedObject var model: TaskBrowserModel
    private var draft: Binding<String> {
        Binding(get: { model.drafts[model.selection ?? ""] ?? "" }, set: { model.drafts[model.selection ?? ""] = $0 })
    }
    var body: some View {
        if !model.canControlSelectedTask {
            Label(model.composerHint, systemImage: "eye").font(.bridge(.callout)).foregroundStyle(.secondary).padding(16)
        } else {
        VStack(alignment: .leading, spacing: 9) {
            PendingSendView(model: model)
            HStack {
                Label("You → \(model.workerName)", systemImage: "person.crop.circle") .font(.bridge(.headline))
                Spacer()
                if let state = model.taskState, ["queued", "running", "waiting_question"].contains(state.status) {
                    Button("Cancel task", role: .destructive) { model.cancelTask() }.disabled(!model.canAct)
                }
            }
            TextEditor(text: draft).font(.bridge(.body)).frame(minHeight: 64, maxHeight: 110)
                .padding(6).background(Color.primary.opacity(0.025))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.primary.opacity(0.15)))
                .disabled(model.actionBusy || model.pendingAction != nil)
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.composerHint).font(.bridge(.caption)).foregroundStyle(.secondary)
                    Text("Sending an answer or revision can use your model subscription. ⌘Return sends.").font(.bridge(.caption2)).foregroundStyle(.secondary)
                }
                Spacer()
                Text("\(draft.wrappedValue.unicodeScalars.count)/4000").font(.bridge(.caption2)).foregroundStyle(draft.wrappedValue.unicodeScalars.count > 4000 ? Color.red : Color.secondary)
                Button(model.replyLabel) { model.sendReply() }.buttonStyle(.borderedProminent)
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled(!model.canReply || draft.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || draft.wrappedValue.unicodeScalars.count > 4000)
            }
        }.padding(16).background(Color.primary.opacity(0.02))
        }
    }
}

struct NewTaskSheet: View {
    @ObservedObject var model: TaskBrowserModel
    @State private var projectID = ""
    @State private var scopeText = ""
    @State private var chosenModel = ""
    @State private var chosenEffort = ""
    private var scope: [String] { scopeText.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } }
    private var project: BridgeProject? { model.projects.first(where: { $0.id == projectID }) }
    private func chooseDefault() {
        if !model.projects.contains(where: { $0.id == projectID }) { projectID = model.projects.first(where: \.available)?.id ?? model.projects.first?.id ?? "" }
        scopeText = project?.scope.joined(separator: "\n") ?? ""
    }
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 16) {
            Text("Start a task").font(.bridge(.title2).bold())
            Text("Send your instructions to an approved worker on \(model.target == "local" ? "this Mac" : model.target). The task and replies will appear in its conversation.").foregroundStyle(.secondary)
            if model.projects.isEmpty {
                Text("No approved projects are loaded. Refresh the receiver or finish Receiver setup first.").foregroundStyle(.orange)
            } else {
                Picker("Project and worker", selection: $projectID) {
                    ForEach(model.projects) { project in
                        Text("\(project.id) · \(model.agentName(project.worker))\(project.available ? "" : " · unavailable")").tag(project.id)
                    }
                }
            }
            if let project = project {
                Text("You → \(model.agentName(project.worker))").font(.bridge(.callout))
                if project.verification?.profile != "repository-v1" {
                Text("Task scope · one project-relative file per line").font(.bridge(.headline))
                TextEditor(text: $scopeText).font(.bridge(.callout, design: .monospaced)).frame(height: 80).padding(6)
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.primary.opacity(0.2)))
                    .disabled(!model.canAct)
                Text("Remove files to narrow this task. To approve additional or new files, use Projects & scope on the receiving Mac. Paths here refer to that Mac's registered repository.").font(.bridge(.caption)).foregroundStyle(.secondary)
                DisclosureGroup("Receiver-approved files") { Text(project.scope.joined(separator: "\n")).font(.bridge(.caption, design: .monospaced)).textSelection(.enabled) }
                Text(project.verification?.profile == "file-proposal-v1" ? "File proposal: changes are prepared in a separate task checkout for review. No application tests are run and the original folder is not modified." : "Math demo: this project must pass its fixed addition tests. For other work, create a file-proposal project in Projects & scope on the receiver.").font(.bridge(.callout)).foregroundStyle(.secondary)
                } else {
                    Text("Full coding · the agent can edit the project and run checks in its own task branch.").foregroundStyle(.secondary)
                    if project.capabilities?["model_settings"] == true {
                        ModelSettingsFields(agent: project.adapter, model: $chosenModel, effort: $chosenEffort)
                    } else {
                        Text("Model selection requires the new service on this Mac. Finish active tasks before updating.").font(.bridge(.caption)).foregroundStyle(.secondary)
                    }
                }
                if !project.available {
                    Text(["codex", "claude"].contains(project.adapter) ? "This project's execution gate is disabled on the receiver." : "The \(model.agentName(project.worker)) managed worker adapter is not implemented. Choose an enabled Codex or Claude project.").foregroundStyle(.orange).font(.bridge(.callout))
                }
            }
            Text("Task instructions").font(.bridge(.headline))
            TextEditor(text: $model.newObjective).font(.bridge(.body)).frame(minHeight: 170).padding(8)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.primary.opacity(0.2)))
                .disabled(model.actionBusy || model.pendingAction != nil)
            Text("Starting a task can use the selected worker's subscription. Viewing and background refreshes do not.").font(.bridge(.caption)).foregroundStyle(.secondary)
            if !model.error.isEmpty { Text(model.error).font(.bridge(.callout)).foregroundStyle(.orange).textSelection(.enabled) }
            PendingSendView(model: model)
            HStack {
                Text("\(model.newObjective.unicodeScalars.count)/4000").font(.bridge(.caption)).foregroundStyle(.secondary)
                Spacer()
                Button("Close") { model.showNewTask = false }.disabled(model.actionBusy)
                Button("Start task") { model.startTask(projectID: projectID, scope: scope, settings: AgentModelSettings(model: chosenModel, effort: chosenEffort)) }.buttonStyle(.borderedProminent)
                    .disabled(!AgentModelSettings.validModel(chosenModel) || !model.canAct || project?.available != true || scope.isEmpty || scope.count > 16 || Set(scope).count != scope.count || !Set(scope).isSubset(of: Set(project?.scope ?? [])) || model.newObjective.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.newObjective.unicodeScalars.count > 4000)
            }
        }.padding(24).frame(width: 620) }.frame(width: 660, height: 700)
            .onAppear { chooseDefault() }
            .onChange(of: model.projects.map(\.id)) { _ in chooseDefault() }
            .onChange(of: projectID) { _ in scopeText = project?.scope.joined(separator: "\n") ?? ""; chosenModel = ""; chosenEffort = "" }
    }
}

struct MessageBubble: View {
    @ObservedObject var model: TaskBrowserModel
    let message: BridgeMessage
    private var outgoing: Bool { message.role == "coordinator" }
    private var agentMessage: Bool { message.role == "worker" }
    private var structured: [String: Any]? {
        guard message.parts == 1, let data = message.text.data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }
    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            if outgoing { Spacer(minLength: 55) }
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top) {
                    Label(model.senderLabel(message), systemImage: outgoing ? (message.actor?.kind == "human" ? "person.crop.circle.fill" : "desktopcomputer") : agentMessage ? "sparkles" : "info.circle")
                        .font(.bridge(.headline))
                    if !model.recipientLabel(message).isEmpty { Text("→ \(model.recipientLabel(message))").font(.bridge(.callout)).foregroundStyle(.secondary) }
                    Spacer(minLength: 0)
                }
                HStack {
                    Text(Date(timeIntervalSince1970: message.time).formatted(date: .abbreviated, time: .shortened))
                    Text(message.kind.replacingOccurrences(of: "_", with: " "))
                    if let sender = message.sender_identity, outgoing { Text("via \(sender)") }
                    if message.parts > 1 { Text("Part \(message.part)/\(message.parts)") }
                }.font(.bridge(.caption2)).foregroundStyle(.secondary)
                if message.recovered { Text("Recovered · earlier history may be incomplete").font(.bridge(.caption)).foregroundStyle(.orange) }
                if message.kind == "proposal", let object = structured {
                    if let summary = object["summary"] as? String { Text(summary).textSelection(.enabled) }
                    if let issues = object["issues"] as? [String], !issues.isEmpty {
                        ForEach(Array(issues.enumerated()), id: \.offset) { _, issue in Label(issue, systemImage: "exclamationmark.bubble").font(.bridge(.callout)).foregroundStyle(.orange).textSelection(.enabled) }
                    }
                    if let edits = object["edits"] as? [[String: String]] {
                        ForEach(Array(edits.enumerated()), id: \.offset) { _, edit in
                            if let path = edit["path"], let content = edit["content"] {
                                Text(path).font(.bridge(.caption).bold()).foregroundStyle(.secondary)
                                Text(content).font(.bridge(.callout, design: .monospaced)).textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading).padding(10).background(Color.primary.opacity(0.035))
                            }
                        }
                    }
                } else if ["verified_result", "review_required_result"].contains(message.kind), let object = structured {
                    if message.kind == "review_required_result" { Label("Proposal ready · review required", systemImage: "doc.text.magnifyingglass").font(.bridge(.headline)).foregroundStyle(.orange) }
                    Text(object["summary"] as? String ?? "Result").textSelection(.enabled)
                } else { Text(message.text).textSelection(.enabled) }
            }.frame(maxWidth: 760, alignment: .leading).padding(16)
                .background(outgoing ? Color.accentColor.opacity(0.12) : agentMessage ? Color.primary.opacity(0.045) : Color.orange.opacity(0.055))
                .clipShape(RoundedRectangle(cornerRadius: 15))
            if !outgoing { Spacer(minLength: 55) }
        }.frame(maxWidth: .infinity, alignment: outgoing ? .trailing : .leading)
    }
}

struct PendingMessageBubble: View {
    @ObservedObject var model: TaskBrowserModel
    let pending: PendingChatAction
    var body: some View {
        HStack {
            Spacer(minLength: 55)
            VStack(alignment: .leading, spacing: 10) {
                Label("You → \(model.pendingRecipient(pending))", systemImage: "person.crop.circle.fill").font(.bridge(.headline))
                if let text = model.messageText(pending) { Text(text).textSelection(.enabled) }
                HStack(spacing: 8) {
                    if model.actionBusy { ProgressView().controlSize(.small) }
                    else { Image(systemName: model.pendingAction == nil ? "checkmark.circle" : "clock") }
                    Text(model.actionBusy ? "Sending… saved on this Mac" : model.pendingAction == nil ? "Accepted · loading saved conversation…" : "Delivery unconfirmed · saved for retry")
                        .font(.bridge(.caption)).foregroundStyle(.secondary)
                }
            }.padding(16).frame(maxWidth: 760, alignment: .leading)
                .background(Color.accentColor.opacity(0.12)).clipShape(RoundedRectangle(cornerRadius: 15))
        }.frame(maxWidth: .infinity, alignment: .trailing)
    }
}

struct WorkerActivityView: View {
    @ObservedObject var model: TaskBrowserModel
    var body: some View {
        if let state = model.taskState {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                HStack(alignment: .top, spacing: 10) {
                    if ["queued", "running", "cancelling"].contains(state.status) { ProgressView().controlSize(.small) }
                    else { Image(systemName: state.status == "failed" ? "exclamationmark.circle" : state.status == "waiting_question" ? "bubble.left" : "checkmark.circle").foregroundStyle(state.status == "failed" ? Color.orange : Color.secondary) }
                    VStack(alignment: .leading, spacing: 4) {
                        Text(title(state)).font(.bridge(.callout).weight(.semibold))
                        if state.status == "running", let activity = state.activity {
                            Text(activity.message).font(.bridge(.caption))
                            Text("Last agent update \(max(0, Int(context.date.timeIntervalSince1970 - activity.time)))s ago · status refreshes every 5s with Live on").font(.bridge(.caption2)).foregroundStyle(.secondary)
                        } else if state.status == "running" { Text("The receiver reports a running session. Detailed activity requires the latest receiver update.").font(.bridge(.caption)).foregroundStyle(.secondary) }
                        if let error = state.error, state.status == "failed" { Text(error).font(.bridge(.caption)).foregroundStyle(.orange).textSelection(.enabled) }
                    }
                    Spacer()
                }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.primary.opacity(0.035)).clipShape(RoundedRectangle(cornerRadius: 9))
            }
        }
    }
    private func title(_ state: TaskState) -> String {
        switch state.status {
        case "queued": return "Queued on \(model.target == "local" ? "this Mac" : model.target) · waiting for the worker"
        case "running": return "\(model.workerName) session active · \((state.activity?.phase ?? "working").replacingOccurrences(of: "_", with: " "))"
        case "waiting_question": return "\(model.workerName) is waiting for your answer"
        case "cancelling": return "Stopping the worker…"
        case "failed": return "Task failed · see the worker proposal and error below"
        case "completed": return "Task finished · review its result below"
        default: return "Task \(state.status.replacingOccurrences(of: "_", with: " "))"
        }
    }
}
