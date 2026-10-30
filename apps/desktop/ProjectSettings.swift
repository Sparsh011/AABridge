import AppKit
import SwiftUI

struct LocalProject: Decodable, Identifiable {
    let id: String
    let repository: String
    let adapter: String
    let scope: [String]
    let verification: ProjectVerification
    let execution_enabled: Bool
    let peers: [String]
    var links: [ProjectLink]? = nil
}

final class ProjectSettingsModel: ObservableObject {
    @Published var projects: [LocalProject] = []
    @Published var peers: [String] = []
    @Published var peerLabels: [String: String] = [:]
    @Published var selected = ""
    @Published var projectID = ""
    @Published var repository = ""
    @Published var scopeText = ""
    @Published var selectedPeers: Set<String> = []
    @Published var agent = "codex"
    @Published var coding = true
    @Published var claudeToken = ""
    /// Related projects agents may also open while working on this one.
    @Published var links: [ProjectLink] = []
    @Published var claudeTokenStored = false
    @Published var busy = false
    @Published var notice = ""
    private let activity: TaskBrowserModel
    init(activity: TaskBrowserModel) { self.activity = activity }
    var scope: [String] { scopeText.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } }
    var canSave: Bool { !busy && !projectID.isEmpty && !repository.isEmpty && (coding || (!scope.isEmpty && scope.count <= 16 && scope.count == Set(scope).count))
        && (coding || agent != "claude" || claudeTokenStored || !claudeToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
    var agentName: String { agent == "claude" ? "Claude Code" : "Codex" }
    func chooseProject() {
        guard let project = projects.first(where: { $0.id == selected }) else {
            projectID = ""; repository = ""; scopeText = ""; selectedPeers = []; agent = "codex"; coding = true; selectedPeers = Set(peers); links = []; return
        }
        projectID = project.id; repository = project.repository; scopeText = project.scope.joined(separator: "\n"); selectedPeers = Set(project.peers); agent = project.adapter; coding = project.verification.profile == "repository-v1"
        links = project.links ?? []
    }
    /// Folder name as AABridge will name the link (matches the Python side).
    static func linkName(_ folder: URL) -> String {
        let slug = folder.lastPathComponent.lowercased().map { $0.isASCII && ($0.isLetter || $0.isNumber) ? String($0) : "-" }.joined()
        let parts = slug.split(separator: "-").joined(separator: "-")
        return parts.isEmpty ? "project" : String(parts.prefix(40))
    }
    func addLink() {
        let panel = NSOpenPanel()
        panel.title = "Choose a related Git project on this Mac"
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = true
        guard panel.runModal() == .OK else { return }
        for url in panel.urls {
            let path = url.standardizedFileURL.path
            guard path != repository, !links.contains(where: { $0.repository == path }) else { notice = "\(url.lastPathComponent) is already part of this project."; continue }
            guard links.count < 8 else { notice = "You can link up to 8 related projects."; break }
            links.append(ProjectLink(name: Self.linkName(url), repository: path, access: "read"))
        }
    }
    func chooseRepository() {
        let panel = NSOpenPanel()
        panel.title = "Choose a committed Git repository on this receiving Mac"
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            repository = url.path
            // A second agent's project for the same folder usually needs the same links.
            if links.isEmpty, let sibling = projects.first(where: { $0.repository == url.path && !($0.links ?? []).isEmpty }) {
                links = sibling.links ?? []
                notice = "Copied related projects from \(sibling.id). Change them below if this agent needs different access."
            }
            if projectID.isEmpty {
                let slug = url.lastPathComponent.lowercased().map { $0.isASCII && ($0.isLetter || $0.isNumber) ? $0 : Character("-") }
                projectID = "project-" + String(slug.prefix(45)) + "-" + agent
            }
        }
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
            var body: [String: Any] = ["id": projectID, "repository": repository, "scope": coding ? ["*"] : scope, "peers": selectedPeers.sorted(), "agent": agent, "coding": coding]
            if coding { body["links"] = links.map { ["repository": $0.repository ?? "", "access": $0.access] } }
            let token = claudeToken.trimmingCharacters(in: .whitespacesAndNewlines)
            if agent == "claude" && !token.isEmpty { body["claude_token"] = token }
            let data = try JSONSerialization.data(withJSONObject: body)
            perform("project-save", body: data)
        } catch { notice = error.localizedDescription }
    }
    private func perform(_ action: String, body: Data?) {
        guard !busy else { return }
        busy = true
        notice = action == "project-save" ? "Saving your project and checking \(agentName)…" : "Loading projects on this Mac…"
        let wanted = projectID
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                struct Reply: Decodable { let projects: [LocalProject]; let peers: [String]; let claude_token_stored: Bool; let peer_labels: [String: String]? }
                let reply = try JSONDecoder().decode(Reply.self, from: self.activity.call(action, target: "local", input: body))
                DispatchQueue.main.async {
                    self.projects = reply.projects; self.peers = reply.peers; self.busy = false
                    if self.selected.isEmpty { self.selectedPeers = Set(reply.peers) }
                    self.peerLabels = reply.peer_labels ?? [:]
                    self.claudeTokenStored = reply.claude_token_stored; self.claudeToken = ""
                    if action == "project-save" { self.selected = wanted; self.notice = "Project ready. Open Team chat and refresh your team to select it." }
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
    @State private var advanced = false
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 22) {
            Text("Add your project").font(.bridge(.title).bold())
            Text("Choose the folder this Mac will work on and the agent it will use. Add the same project on your other Mac, using the same committed version.").foregroundStyle(.secondary)
            HStack {
                Picker("Project", selection: $model.selected) {
                    Text("Add a new project").tag("")
                    ForEach(model.projects) { Text($0.id + ($0.adapter == "claude" ? " · Claude" : " · Codex")).tag($0.id) }
                }.onChange(of: model.selected) { _ in model.chooseProject() }
                Button("Refresh") { model.refresh() }
            }
            GroupBox { VStack(alignment: .leading, spacing: 18) {
                Text("1. Choose a folder").font(.bridge(.headline))
                HStack { Text(model.repository.isEmpty ? "No project selected" : model.repository).foregroundStyle(.secondary).textSelection(.enabled); Spacer(); Button("Choose folder…") { model.chooseRepository() } }
                Text("Use a Git project with at least one commit. Your team works in separate copies, so your current files stay safe.").font(.bridge(.caption)).foregroundStyle(.secondary)
                Text("2. Choose this Mac’s agent").font(.bridge(.headline))
                Picker("Agent", selection: $model.agent) { Text("Codex").tag("codex"); Text("Claude").tag("claude") }.pickerStyle(.segmented)
                Text("Full coding lets the agent edit the project and run commands with its normal CLI permissions and login. Sign in to the selected agent on this Mac first.").font(.bridge(.callout)).foregroundStyle(.secondary)
                Text("3. Share with your other Mac").font(.bridge(.headline))
                if model.peers.isEmpty { Text("Connect the other Mac in Devices first. You can still save a project for local work.").foregroundStyle(.secondary) }
                ForEach(model.peers, id: \.self) { peer in
                    Toggle(model.peerLabels[peer] ?? peer, isOn: Binding(get: { model.selectedPeers.contains(peer) }, set: { if $0 { model.selectedPeers.insert(peer) } else { model.selectedPeers.remove(peer) } }))
                }
                if model.coding {
                    Text("4. Related projects (optional)").font(.bridge(.headline))
                    Text("Does this project work with others, like a server with its Android and Mac apps? Link them so agents can look at them, and change them if you allow it, in the same task. Agents work on their own copies; your folders aren’t touched until you choose where results go.")
                        .font(.bridge(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    ForEach($model.links) { $link in
                        HStack(spacing: 12) {
                            Image(systemName: "folder").foregroundStyle(.tint)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(link.name).font(.bridge(.headline))
                                Text(link.repository ?? "").font(.bridge(.caption)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                            }
                            Spacer()
                            Picker("Access", selection: $link.access) { Text("Can read").tag("read"); Text("Can edit").tag("edit") }
                                .pickerStyle(.segmented).labelsHidden().frame(width: 190)
                            Button { model.links.removeAll { $0.id == link.id } } label: { Image(systemName: "minus.circle") }
                                .buttonStyle(.borderless).help("Remove this related project")
                        }.padding(10).background(Color.primary.opacity(0.03)).clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    Button { model.addLink() } label: { Label("Add related project…", systemImage: "plus") }
                    if !model.links.isEmpty {
                        Text("Can read: agents may open it for context. Can edit: agents may change it, and the changes come back on the same branch name in that project, with its own pull request if you ask for one.")
                            .font(.bridge(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }.padding(18) }
            DisclosureGroup("Advanced settings", isExpanded: $advanced) {
                VStack(alignment: .leading, spacing: 14) {
                    TextField("Project name", text: $model.projectID).disabled(!model.selected.isEmpty)
                    Toggle("Full project coding", isOn: $model.coding)
                    if !model.coding {
                        Text("Small file proposals · agent tools and test commands are disabled.").foregroundStyle(.secondary)
                        HStack { Text("Allowed files"); Spacer(); Button("Choose files…") { model.chooseFiles() } }
                        TextEditor(text: $model.scopeText).font(.bridge(.body, design: .monospaced)).frame(height: 130)
                    }
                    if model.agent == "claude" {
                        SecureField("Optional Claude subscription token", text: $model.claudeToken)
                        Text("Full coding uses your normal Claude login. The small-proposal sandbox requires a token from claude setup-token.").font(.bridge(.caption)).foregroundStyle(.secondary)
                    }
                }.padding(.top, 12)
            }
            HStack {
                if model.busy { ProgressView().controlSize(.small) }
                Text(model.notice).foregroundStyle(.secondary).textSelection(.enabled)
                Spacer()
                Button("Save project") { model.save() }.buttonStyle(.borderedProminent).disabled(!model.canSave)
            }
        }.padding(32).frame(maxWidth: 950, alignment: .leading).disabled(model.busy) }
            .onAppear { if model.projects.isEmpty && !model.busy { model.refresh() } }
    }
}
