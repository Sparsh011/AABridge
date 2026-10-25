import AppKit
import SwiftUI

struct LocalProject: Decodable, Identifiable {
    let id: String
    let repository: String
    let scope: [String]
    let verification: ProjectVerification
    let execution_enabled: Bool
    let peers: [String]
}

final class ProjectSettingsModel: ObservableObject {
    @Published var projects: [LocalProject] = []
    @Published var peers: [String] = []
    @Published var selected = ""
    @Published var projectID = ""
    @Published var repository = ""
    @Published var scopeText = ""
    @Published var selectedPeers: Set<String> = []
    @Published var busy = false
    @Published var notice = ""
    private let activity: TaskBrowserModel
    init(activity: TaskBrowserModel) { self.activity = activity }
    var scope: [String] { scopeText.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } }
    var canSave: Bool { !busy && !projectID.isEmpty && !repository.isEmpty && !scope.isEmpty && scope.count <= 16 && scope.count == Set(scope).count }
    func chooseProject() {
        guard let project = projects.first(where: { $0.id == selected }) else {
            projectID = ""; repository = ""; scopeText = ""; selectedPeers = []; return
        }
        projectID = project.id; repository = project.repository; scopeText = project.scope.joined(separator: "\n"); selectedPeers = Set(project.peers)
    }
    func chooseRepository() {
        let panel = NSOpenPanel()
        panel.title = "Choose a committed Git repository on this receiving Mac"
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { repository = url.path }
    }
    func chooseFiles() {
        guard !repository.isEmpty else { notice = "Choose the repository first."; return }
        let root = URL(fileURLWithPath: repository).standardizedFileURL
        let panel = NSOpenPanel()
        panel.title = "Choose existing files to allow"
        panel.directoryURL = root; panel.canChooseDirectories = false; panel.canChooseFiles = true; panel.allowsMultipleSelection = true
        if panel.runModal() == .OK {
            var paths = scope
            for url in panel.urls {
                let path = url.standardizedFileURL.path
                guard path.hasPrefix(root.path + "/") else { notice = "Choose files inside the selected repository."; return }
                let relative = String(path.dropFirst(root.path.count + 1))
                if !paths.contains(relative) { paths.append(relative) }
            }
            scopeText = paths.joined(separator: "\n")
        }
    }
    func refresh() { perform("projects-local", body: nil) }
    func save() {
        guard canSave else { return }
        do {
            let data = try JSONSerialization.data(withJSONObject: ["id": projectID, "repository": repository, "scope": scope, "peers": selectedPeers.sorted()])
            perform("project-save", body: data)
        } catch { notice = error.localizedDescription }
    }
    private func perform(_ action: String, body: Data?) {
        guard !busy else { return }
        busy = true
        notice = action == "project-save" ? "Saving scope and checking the Codex account and isolation. No model inference is started…" : "Loading projects on this Mac…"
        let wanted = projectID
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                struct Reply: Decodable { let projects: [LocalProject]; let peers: [String] }
                let reply = try JSONDecoder().decode(Reply.self, from: self.activity.call(action, target: "local", input: body))
                DispatchQueue.main.async {
                    self.projects = reply.projects; self.peers = reply.peers; self.busy = false
                    if action == "project-save" { self.selected = wanted; self.notice = "Project saved and enabled. On the sending Mac, refresh the receiver and choose this project in New task." }
                    else { self.notice = "Project permissions below belong to this Mac." }
                }
            } catch {
                DispatchQueue.main.async { self.busy = false; self.notice = error.localizedDescription }
            }
        }
    }
}

struct ProjectSettingsView: View {
    @ObservedObject var model: ProjectSettingsModel
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 18) {
            Text("Projects & scope · this Mac").font(.title.bold())
            Text("Configure this on the receiving Mac, where the project files live. Remote callers can narrow an approved scope; they cannot grant themselves access to other files.").foregroundStyle(.secondary)
            HStack {
                Picker("Edit project", selection: $model.selected) {
                    Text("Create a new file-proposal project").tag("")
                    ForEach(model.projects.filter { $0.verification.profile == "file-proposal-v1" }) { project in Text(project.id + (project.execution_enabled ? "" : " · disabled")).tag(project.id) }
                }.onChange(of: model.selected) { _ in model.chooseProject() }
                Button("Refresh") { model.refresh() }
            }
            TextField("Project ID, for example backend", text: $model.projectID).disabled(!model.selected.isEmpty)
            HStack { TextField("Repository folder on this Mac", text: $model.repository); Button("Choose folder…") { model.chooseRepository() } }
            Text("Use a Git repository with at least one commit. Tasks start from its current committed HEAD, without uncommitted changes. The original project folder is not modified.").font(.caption).foregroundStyle(.secondary)
            HStack { Text("Allowed files · one relative path per line").font(.headline); Spacer(); Button("Add existing files…") { model.chooseFiles() } }
            TextEditor(text: $model.scopeText).font(.system(.body, design: .monospaced)).frame(height: 150).padding(8)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.primary.opacity(0.2)))
            Text("Example: src/server.py or hello.txt. You may type a new file path that does not exist yet. Allow 1–16 files; no folders, wildcards, absolute paths, hidden files, symlinks or submodules. Each file is limited to 16 KiB, with 64 KiB total per proposal.").font(.caption).foregroundStyle(.secondary)
            Text("Allow enrolled senders").font(.headline)
            if model.peers.isEmpty { Text("No remote senders are enrolled. Local tasks remain available; finish pairing to add a remote sender.").font(.callout).foregroundStyle(.secondary) }
            ForEach(model.peers, id: \.self) { peer in
                Toggle(peer, isOn: Binding(get: { model.selectedPeers.contains(peer) }, set: { if $0 { model.selectedPeers.insert(peer) } else { model.selectedPeers.remove(peer) } }))
            }
            Text("File proposals require your review. AABridge checks the file boundaries and Git diff, but does not execute your app, tests, build scripts or shell commands. Changes, including new files, are saved in a separate task checkout and are not automatically copied into this folder, pushed or merged.").font(.callout)
            HStack {
                if model.busy { ProgressView().controlSize(.small) }
                Text(model.notice).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                Spacer()
                Button("Save scope & enable") { model.save() }.buttonStyle(.borderedProminent).disabled(!model.canSave)
            }
            Text("Saving checks the existing Codex login and isolation without a model turn. If that preflight fails, the saved project stays disabled until you fix the prerequisite and retry. Finish/cancel active tasks before changing an existing project's policy. The math demo remains a separate test project.").font(.caption).foregroundStyle(.secondary)
        }.padding(28).frame(maxWidth: 1000, alignment: .leading).disabled(model.busy) }
            .onAppear { if model.projects.isEmpty && !model.busy { model.refresh() } }
    }
}
