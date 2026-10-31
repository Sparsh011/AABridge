import SwiftUI
import Combine
import Foundation

struct EditTaskTitleSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var title: String
    let save: (String) -> Void
    init(title: String, save: @escaping (String) -> Void) {
        _title = State(initialValue: title)
        self.save = save
    }
    private var cleaned: String { title.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var valid: Bool { !cleaned.isEmpty && title.unicodeScalars.count <= 80 && !title.contains(where: { $0.isNewline || $0 == "\t" }) }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Edit title").font(.bridge(.title2).bold())
            TextField("Task title", text: $title).textFieldStyle(.roundedBorder).onSubmit { if valid { save(cleaned); dismiss() } }
            Text("\(title.unicodeScalars.count)/80 · Changes the label in your own history.").font(.bridge(.caption)).foregroundStyle(valid ? Color.secondary : Color.orange)
            HStack { Spacer(); Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Save") { save(cleaned); dismiss() }.disabled(!valid).keyboardShortcut(.defaultAction) }
        }.padding(24).frame(width: 480)
    }
}

struct TaskInfoSheet: View {
    @Environment(\.dismiss) private var dismiss
    let task: BridgeTask
    let state: TaskState?
    let messages: [BridgeMessage]
    let events: String
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack { Label("Task info", systemImage: "info.circle").font(.bridge(.title2).bold()); Spacer(); Button("Done") { dismiss() }.keyboardShortcut(.cancelAction) }
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text(task.displayTitle).font(.bridge(.headline))
                    info("Task ID", task.id)
                    info("Session", state == nil ? "Loading details…" : state?.session_id ?? "Not started")
                    if let thread = state?.thread_id { info("Agent thread", thread) }
                    info("Project", task.project_id)
                    info("Status", task.status)
                    info("Revision", String(task.revision))
                    if let scope = state?.scope { info("Scope", scope.joined(separator: ", ")) }
                    DisclosureGroup("Full instructions") { Text(state?.objective ?? task.objective).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                    if !messages.isEmpty {
                        DisclosureGroup("Saved context and verification records") {
                            ForEach(messages.filter { $0.role == "context" || ["verified_result", "review_required_result"].contains($0.kind) }) { message in
                                Text(message.kind).font(.bridge(.caption).bold())
                                Text(message.text).font(.bridge(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                    if !events.isEmpty {
                        DisclosureGroup("Task events") { Text(events).font(.bridge(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
        }.padding(24).frame(width: 650, height: 580)
    }
    private func info(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.bridge(.caption)).foregroundStyle(.secondary)
            Text(value).font(.bridge(.callout, design: .monospaced)).textSelection(.enabled)
        }
    }
}

struct TaskWorkFile: Decodable, Identifiable {
    let path: String
    let kind: String
    let preview: String
    var id: String { path }
}
struct TaskWorkRepository: Decodable, Identifiable {
    let name: String
    let path: String
    let branch: String
    let head: String
    let saved_commit_count: Int
    let changed_files: Int
    let files: [TaskWorkFile]
    var id: String { path }
}
struct TaskTeamUsage: Decodable {
    let title: String
    let status: String
    let active: Bool
    let stages: [String]
}
struct TaskWorkReview: Decodable {
    let task_id: String
    let status: String
    let snapshot: String
    let bytes: Int
    let changed_files: Int
    let repositories: [TaskWorkRepository]
    let team_usage: [TaskTeamUsage]
    let blockers: [String]
    let can_delete_work: Bool
    let work_deleted: Bool
    let cleanup_pending: Bool
    let remote_team_possible: Bool
    let running: Bool
    let can_stop: Bool
    let kept: [String]
}

final class TaskDeletionModel: ObservableObject {
    let browser: TaskBrowserModel
    let task: BridgeTask
    @Published var review: TaskWorkReview?
    @Published var loading = false
    @Published var busy = false
    @Published var deleteWork = false
    @Published var acknowledgedLoss = false
    @Published var finished = false
    @Published var error = ""
    init(browser: TaskBrowserModel, task: BridgeTask) { self.browser = browser; self.task = task }
    func load() {
        guard !loading && !busy else { return }
        loading = true
        error = ""
        let target = browser.target
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let data = try self.browser.call("task_work", target: target, task: self.task.id)
                let review = try JSONDecoder().decode(TaskWorkReview.self, from: data)
                DispatchQueue.main.async {
                    if self.review?.snapshot != review.snapshot { self.acknowledgedLoss = false }
                    self.review = review; self.loading = false
                }
            } catch { DispatchQueue.main.async { self.error = error.localizedDescription; self.loading = false; self.acknowledgedLoss = false } }
        }
    }
    func stop() { perform("cancel", body: ["task_id": task.id], removing: false) }
    func remove() {
        guard !deleteWork || (acknowledgedLoss && error.isEmpty && (review?.can_delete_work == true || review?.cleanup_pending == true)) else { return }
        var body: [String: Any] = ["task_id": task.id]
        if deleteWork, let review = review { body["delete_work"] = true; body["work_snapshot"] = review.snapshot }
        perform("task_forget", body: body, removing: true)
    }
    private func perform(_ action: String, body: [String: Any], removing: Bool) {
        guard !busy && !loading && browser.canAct else { return }
        guard let payload = try? JSONSerialization.data(withJSONObject: ["key": "history-" + UUID().uuidString.lowercased(), "body": body]) else { return }
        busy = true; browser.actionBusy = true; error = ""
        let target = browser.target
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let data = try self.browser.call(action, target: target, input: payload)
                let result = (try JSONSerialization.jsonObject(with: data)) as? [String: Any]
                DispatchQueue.main.async {
                    self.busy = false; self.browser.actionBusy = false
                    self.browser.refresh(silent: true)
                    if removing {
                        if result?["cleanup_pending"] as? Bool == true {
                            self.error = "The history entry was removed, but some files could not be cleaned up. Refresh this review and retry cleanup."
                            self.load()
                        } else { self.finished = true }
                    } else { self.load() }
                }
            } catch {
                DispatchQueue.main.async { self.busy = false; self.browser.actionBusy = false; self.error = error.localizedDescription; self.acknowledgedLoss = false }
            }
        }
    }
}

struct TaskDeletionSheet: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var model: TaskDeletionModel
    @State private var selectedFile: String?
    private let timer = Timer.publish(every: 2, on: .main, in: .common).autoconnect()
    init(browser: TaskBrowserModel, task: BridgeTask) { _model = StateObject(wrappedValue: TaskDeletionModel(browser: browser, task: task)) }
    init(model: TaskDeletionModel, selectedFile: String? = nil) { _model = StateObject(wrappedValue: model); _selectedFile = State(initialValue: selectedFile) }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label("Review before deleting", systemImage: "trash").font(.bridge(.title2).bold())
                Spacer()
                Button { model.load() } label: { Label("Refresh", systemImage: "arrow.clockwise") }.disabled(model.loading || model.busy)
            }
            Text(model.task.displayTitle).font(.bridge(.headline))
            if model.loading { ProgressView("Inspecting saved work…").controlSize(.small) }
            if !model.error.isEmpty { Text(model.error).foregroundStyle(.orange).textSelection(.enabled) }
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let review = model.review {
                        HStack {
                            Label("\(review.changed_files) changed \(review.changed_files == 1 ? "file" : "files")", systemImage: "doc.on.doc")
                            Spacer()
                            Text(review.cleanup_pending ? "Cleanup pending" : ByteCountFormatter.string(fromByteCount: Int64(review.bytes), countStyle: .file) + " in isolated copies")
                        }.font(.bridge(.callout)).foregroundStyle(.secondary)
                        if review.cleanup_pending {
                            Label("The task copies are retired. Some files still need disk cleanup; retry cleanup below.", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                        }
                        if review.running {
                            HStack {
                                Text("This work is still running or closing. Stop it to review the exact files before deleting work.")
                                Spacer()
                                if review.can_stop { Button("Stop task") { model.stop() }.disabled(model.busy) }
                            }.padding(12).background(Color.orange.opacity(0.09)).clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                        ForEach(review.repositories) { repository in
                            VStack(alignment: .leading, spacing: 8) {
                                Text(repository.name).font(.bridge(.headline))
                                Text("\(repository.saved_commit_count) saved commits · \(repository.changed_files) changed \(repository.changed_files == 1 ? "file" : "files")").font(.bridge(.caption)).foregroundStyle(.secondary)
                                if repository.files.isEmpty { Text("No file changes in the current checkout.").foregroundStyle(.secondary) }
                                ForEach(repository.files) { file in
                                    Button { selectedFile = selectedFile == repository.id + file.id ? nil : repository.id + file.id } label: {
                                        HStack { Image(systemName: "doc.text"); Text(file.path).lineLimit(2); Spacer(); Text(file.kind).font(.bridge(.caption)).foregroundStyle(.secondary); Image(systemName: "chevron.down").font(.bridge(.caption)) }
                                    }.buttonStyle(.plain).padding(.vertical, 5)
                                    if selectedFile == repository.id + file.id {
                                        Text(file.preview.isEmpty ? "No text preview available. The listed file is included when deleting this task copy." : file.preview)
                                            .font(.bridge(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                                            .padding(10).background(Color.primary.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius: 8))
                                    }
                                }
                            }.padding(14).background(Color.primary.opacity(0.025)).clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                        if !review.team_usage.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Used by these team chats").font(.bridge(.headline))
                                ForEach(Array(review.team_usage.enumerated()), id: \.offset) { _, team in
                                    Text("\(team.title) · \(team.status.replacingOccurrences(of: "_", with: " "))")
                                }
                                Text("These chats use this saved result to continue work or deliver it. Deleting the copies can make those actions unavailable.").font(.bridge(.caption)).foregroundStyle(.secondary)
                            }
                        }
                        if review.remote_team_possible { Text("A team on another Mac may use this result. Deleting its task copies prevents that Mac from fetching or resuming them.").font(.bridge(.callout)).foregroundStyle(.orange) }
                        ForEach(review.blockers, id: \.self) { Text($0).font(.bridge(.callout)).foregroundStyle(.orange) }
                        Text("Original project folders and any published branches or pull requests are kept. Text previews may be shortened and show the current checkout. Deleting work removes the entire isolated copies, including all saved branches, Git history and build files.")
                            .font(.bridge(.callout)).foregroundStyle(.secondary)
                    } else if !model.loading {
                        Text("Saved work could not be inspected. You can keep it and remove only the history entry; deleting work requires a successful review.").foregroundStyle(.secondary)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider()
            Picker("What should happen to saved work?", selection: $model.deleteWork) {
                Text("Keep saved work").tag(false)
                Text("Delete saved work").tag(true)
            }.pickerStyle(.segmented).disabled(model.busy || model.loading)
            if model.deleteWork {
                Toggle("I understand these task copies will be deleted and may no longer be resumable or available for delivery.", isOn: $model.acknowledgedLoss).font(.bridge(.callout))
            } else { Text("Only the entry is removed from your history view. Saved work and conversations remain.").font(.bridge(.caption)).foregroundStyle(.secondary) }
            HStack {
                Button("Cancel") { dismiss() }.disabled(model.busy).keyboardShortcut(.cancelAction)
                Spacer()
                if model.busy { ProgressView().controlSize(.small) }
                Button(model.deleteWork ? (model.review?.cleanup_pending == true ? "Retry cleanup" : "Delete work and remove history") : "Keep work and remove history", role: model.deleteWork ? .destructive : nil) { model.remove() }
                    .disabled(model.busy || model.loading || (model.deleteWork && (!model.acknowledgedLoss || !model.error.isEmpty || (model.review?.can_delete_work != true && model.review?.cleanup_pending != true))))
            }
        }.padding(24).frame(width: 820, height: 720)
            .onAppear { if model.review == nil { model.load() } }
            .onChange(of: model.finished) { done in if done { dismiss() } }
            .onReceive(timer) { _ in if model.review?.running == true { model.load() } }
    }
}
