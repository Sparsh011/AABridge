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
    var name: String? = nil
    var coding: Bool { verification.profile == "repository-v1" }
    var shownName: String { name ?? ProjectKey.name(fromID: id) }
}

/// One project as you see it: a Git folder on this Mac, with every agent that may work on it.
/// Underneath, each agent keeps its own pinned entry, so grants and history are unchanged.
struct LocalProjectGroup: Identifiable {
    let key: String
    let name: String
    let repository: String
    var projects: [LocalProject]
    var id: String { key }
    var agents: [String] { projects.map(\.adapter).sorted() }
    func project(_ agent: String) -> LocalProject? { projects.first { $0.adapter == agent } }
    var peers: Set<String> { Set(projects.flatMap(\.peers)) }
    var links: [ProjectLink] { projects.first { !($0.links ?? []).isEmpty }?.links ?? [] }
}

final class ProjectSettingsModel: ObservableObject {
    static let agents = ["claude", "codex"]
    @Published var projects: [LocalProject] = []
    @Published var peers: [String] = []
    @Published var peerLabels: [String: String] = [:]
    /// The project being edited (its key), or empty for a new one.
    @Published var editing = ""
    @Published var name = ""
    @Published var repository = ""
    @Published var agents: Set<String> = Set(ProjectSettingsModel.agents)
    @Published var selectedPeers: Set<String> = []
    /// Related projects agents may also open while working on this one.
    @Published var links: [ProjectLink] = []
    @Published var agentErrors: [String: String] = [:]
    @Published var busy = false
    @Published var loaded = false
    @Published var notice = ""
    /// Called after a save, so the sidebar and the team's agent list update.
    var onSaved: (String) -> Void = { _ in }
    private let activity: TaskBrowserModel
    init(activity: TaskBrowserModel) { self.activity = activity }

    /// Coding projects on this Mac, one per folder. Older proposal-only projects stay in All agent tasks.
    var groups: [LocalProjectGroup] {
        var found: [String: LocalProjectGroup] = [:]
        for project in projects where project.coding {
            let key = ProjectKey.slug(project.shownName)
            if found[key] == nil { found[key] = LocalProjectGroup(key: key, name: project.shownName, repository: project.repository, projects: []) }
            found[key]!.projects.append(project)
        }
        return found.values.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }
    var group: LocalProjectGroup? { groups.first { $0.key == editing } }
    /// Agents already working in the project being edited. They stay; saving updates their settings.
    var existingAgents: Set<String> { Set(group?.agents ?? []) }
    var canSave: Bool { !busy && !repository.isEmpty && !agents.union(existingAgents).isEmpty }

    func startNew() {
        editing = ""; name = ""; repository = ""; agents = Set(Self.agents); selectedPeers = Set(peers); links = []; agentErrors = [:]; notice = ""
    }
    func edit(_ key: String) {
        guard let group = groups.first(where: { $0.key == key }) else { startNew(); return }
        editing = key; name = group.name; repository = group.repository; agents = Set(group.agents)
        selectedPeers = group.peers; links = group.links; agentErrors = [:]; notice = ""
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
        panel.title = "Choose the root folder of a Git project on this Mac"
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        repository = url.path
        if let existing = groups.first(where: { $0.repository == url.path }) {
            edit(existing.key)
            notice = "This folder is already the project “\(existing.name)”. Its settings are shown below."
            return
        }
        if name.isEmpty { name = url.lastPathComponent }
    }
    func refresh() { perform(nil) }
    func save() {
        guard canSave else { return }
        do {
            var body: [String: Any] = ["repository": repository, "agents": Self.agents.filter { agents.union(existingAgents).contains($0) },
                                       "peers": selectedPeers.sorted(), "links": links.map { ["repository": $0.repository ?? "", "access": $0.access] }]
            let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { body["name"] = trimmed }
            perform(try JSONSerialization.data(withJSONObject: body))
        } catch { notice = error.localizedDescription }
    }
    /// Adds one agent to an existing project, keeping its sharing and related projects.
    func addAgent(_ agent: String, to key: String) {
        edit(key)
        agents.insert(agent)
        save()
    }
    private func perform(_ body: Data?) {
        guard !busy else { return }
        busy = true
        let saving = body != nil
        let names = agents.union(existingAgents).map(agentName).sorted().joined(separator: " and ")
        notice = saving ? "Saving and checking \(names) on this Mac. No model is started…" : "Loading projects on this Mac…"
        let wanted = ProjectKey.slug(name.isEmpty ? URL(fileURLWithPath: repository).lastPathComponent : name)
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                struct Reply: Decodable { let projects: [LocalProject]; let peers: [String]; let peer_labels: [String: String]?; var agent_errors: [String: String]? = nil }
                let reply = try JSONDecoder().decode(Reply.self, from: self.activity.call(saving ? "project-save" : "projects-local", target: "local", input: body))
                DispatchQueue.main.async {
                    self.projects = reply.projects; self.peers = reply.peers; self.busy = false; self.loaded = true
                    self.peerLabels = reply.peer_labels ?? [:]
                    if saving {
                        let errors = reply.agent_errors ?? [:]
                        self.edit(self.groups.first(where: { $0.repository == self.repository })?.key ?? wanted)
                        self.agentErrors = errors
                        self.notice = errors.isEmpty ? "Saved. Start a session from the sidebar." : "Saved, but not every agent could be added. See below."
                        self.onSaved(self.editing)
                    } else {
                        if self.editing.isEmpty && self.repository.isEmpty { self.selectedPeers = Set(reply.peers) }
                        self.notice = ""
                    }
                }
            } catch {
                DispatchQueue.main.async { self.busy = false; self.notice = error.localizedDescription }
            }
        }
    }
}

/// Add a project, or change one: folder, agents on this Mac, sharing and related projects.
struct ProjectSettingsView: View {
    @ObservedObject var model: ProjectSettingsModel
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 22) {
            VStack(alignment: .leading, spacing: 6) {
                Text(model.editing.isEmpty ? "Add a project" : "\(model.name) settings").font(.bridge(.title).bold())
                Text(model.editing.isEmpty ? "A project is one Git folder. Codex and Claude can both work in it, on this Mac and on your paired Mac. Add it on each Mac once."
                     : "These settings belong to this Mac. Your paired Mac keeps its own copy of the project.").foregroundStyle(.secondary)
            }
            GroupBox { VStack(alignment: .leading, spacing: 18) {
                Text("1. Folder").font(.bridge(.headline))
                HStack {
                    Text(model.repository.isEmpty ? "No folder chosen" : model.repository).foregroundStyle(.secondary).textSelection(.enabled).lineLimit(1).truncationMode(.middle)
                    Spacer()
                    if model.editing.isEmpty { Button("Choose folder…") { model.chooseRepository() } }
                }
                TextField("Name", text: $model.name).textFieldStyle(.roundedBorder).frame(maxWidth: 360).disabled(!model.editing.isEmpty)
                Text("Use the root of a Git project with at least one commit. Agents work in separate copies, so your files stay as they are. Use the same name on your other Mac so the two are matched.")
                    .font(.bridge(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)

                Text("2. Agents on this Mac").font(.bridge(.headline))
                HStack(spacing: 12) {
                    ForEach(ProjectSettingsModel.agents, id: \.self) { agent in agentToggle(agent) }
                }
                Text("Each agent uses its own login on this Mac and can edit the project and run its commands. Saving checks that the agent is installed; no model is started.")
                    .font(.bridge(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)

                Text("3. Share with your other Mac").font(.bridge(.headline))
                Text("A shared Mac can start sessions that use this Mac’s agents here, and both Macs show those sessions under this project.").font(.bridge(.caption)).foregroundStyle(.secondary)
                if model.peers.isEmpty { Text("No Mac is paired yet. Pair one in Devices; you can still work with both agents on this Mac.").foregroundStyle(.secondary) }
                ForEach(model.peers, id: \.self) { peer in
                    Toggle(model.peerLabels[peer] ?? peer, isOn: Binding(get: { model.selectedPeers.contains(peer) }, set: { if $0 { model.selectedPeers.insert(peer) } else { model.selectedPeers.remove(peer) } }))
                }

                Text("4. Related projects (optional)").font(.bridge(.headline))
                Text("Does this project work with others, like a server with its Android and Mac apps? Link them so agents can look at them, and change them if you allow it, in the same session.")
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
            }.padding(18) }
            HStack {
                if model.busy { ProgressView().controlSize(.small) }
                Text(model.notice).foregroundStyle(.secondary).textSelection(.enabled)
                Spacer()
                Button(model.editing.isEmpty ? "Add project" : "Save") { model.save() }.buttonStyle(.borderedProminent).disabled(!model.canSave)
            }
        }.padding(32).frame(maxWidth: 950, alignment: .leading).disabled(model.busy) }
            .onAppear { if !model.loaded && !model.busy { model.refresh() } }
    }

    private func agentToggle(_ agent: String) -> some View {
        let existing = model.existingAgents.contains(agent)
        let chosen = existing || model.agents.contains(agent)
        let problem = model.agentErrors[agent]
        return Button { if !existing { if chosen { model.agents.remove(agent) } else { model.agents.insert(agent) } } } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: chosen ? "checkmark.square.fill" : "square").foregroundStyle(chosen ? Color.accentColor : Color.secondary)
                VStack(alignment: .leading, spacing: 3) {
                    Text(agentName(agent)).font(.bridge(.headline))
                    if let problem = problem {
                        Text(problem).font(.bridge(.caption)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                    } else if existing {
                        Text(model.group?.project(agent)?.execution_enabled == true ? "Ready" : "Added, but not checked yet. Save to check it again.").font(.bridge(.caption)).foregroundStyle(.secondary)
                    } else {
                        Text(chosen ? "Will be added" : "Not added").font(.bridge(.caption)).foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
            }.padding(12).frame(maxWidth: 300, alignment: .leading)
                .background(chosen ? Color.accentColor.opacity(0.08) : Color.primary.opacity(0.03))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(problem != nil ? Color.orange : chosen ? Color.accentColor.opacity(0.6) : Color.primary.opacity(0.12)))
                .clipShape(RoundedRectangle(cornerRadius: 10)).contentShape(Rectangle())
        }.buttonStyle(.plain).help(existing ? "\(agentName(agent)) already works in this project." : "")
    }
}
