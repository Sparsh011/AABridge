import SwiftUI

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
