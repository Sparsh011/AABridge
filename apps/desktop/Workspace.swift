import AppKit
import Combine
import SwiftUI

/// What the main window shows. Projects hold sessions; everything else is a tool.
enum SidebarItem: Hashable {
    case project(String)
    case newSession(String)
    /// A session held on `host` (`local`, or the paired Mac that runs it).
    case session(host: String, id: String)
    case addProject, devices, history, settings
}

/// A session in the sidebar, wherever it is run from.
struct SessionRow: Identifiable {
    let room: TeamRoom
    let host: String
    let project: String
    var id: String { host + "/" + room.id }
    var item: SidebarItem { .session(host: host, id: room.id) }
}

/// One project in the sidebar: the same folder on every Mac, with every agent that has it.
struct ProjectEntry: Identifiable {
    let key: String
    let name: String
    let local: LocalProjectGroup?
    let agents: [TeamChoice]
    var sessions: [SessionRow]
    var id: String { key }
}

/// Puts projects, their agents and their sessions from every Mac into one list.
/// The admin Mac runs a session; a Mac that only works in it sees it too, read-only.
final class WorkspaceModel: ObservableObject {
    @Published var selection: SidebarItem = .settings
    @Published var collapsed: Set<String> { didSet { preferences.set(Array(collapsed), forKey: "sidebarCollapsed") } }
    /// Sessions on paired Macs that include this Mac, by Mac.
    @Published var remoteRooms: [String: [TeamRoom]] = [:]
    /// An agent is being added to a project (the save checks it on this Mac).
    var addingAgent: Bool { projects.busy }
    let team: TeamRoomModel
    let projects: ProjectSettingsModel
    let activity: TaskBrowserModel
    /// The session from another Mac that is open now.
    @Published var remote: TeamRoomModel?
    private let preferences: UserDefaults
    /// Paired Macs whose service can't list shared sessions yet; asked again after a while.
    private var unsupported: [String: Date] = [:]
    private var polling = false
    private var bag: Set<AnyCancellable> = []

    init(team: TeamRoomModel, projects: ProjectSettingsModel, activity: TaskBrowserModel, preferences: UserDefaults = .standard) {
        self.team = team; self.projects = projects; self.activity = activity; self.preferences = preferences
        collapsed = Set(preferences.stringArray(forKey: "sidebarCollapsed") ?? [])
        // Redraw the sidebar when the pieces it is built from change.
        team.objectWillChange.merge(with: projects.objectWillChange).sink { [weak self] in self?.objectWillChange.send() }.store(in: &bag)
        projects.onSaved = { [weak self] key in
            guard let self = self else { return }
            self.team.refresh(discover: true)
            if !key.isEmpty, case .addProject = self.selection { self.selection = .project(key) }
        }
    }

    /// The project a session belongs to, judged from the member whose project this Mac knows best.
    func projectKey(_ room: TeamRoom) -> String {
        for member in [room.leader, room.follower] where member.target == "local" {
            if let project = projects.projects.first(where: { $0.id == member.project }) { return ProjectKey.slug(project.shownName) }
        }
        for member in [room.leader, room.follower] {
            if let choice = team.choices.first(where: { $0.member.target == member.target && $0.member.project == member.project }) { return choice.projectKey }
        }
        return ProjectKey.slug(ProjectKey.name(fromID: room.leader.project))
    }

    var sessions: [SessionRow] {
        let local = team.rooms.map { SessionRow(room: $0, host: "local", project: projectKey($0)) }
        let others = remoteRooms.flatMap { host, rooms in rooms.map { SessionRow(room: $0, host: host, project: projectKey($0)) } }
        return (local + others).sorted { ($0.room.updated_at ?? 0) > ($1.room.updated_at ?? 0) }
    }

    var entries: [ProjectEntry] {
        let groups = projects.groups
        let all = sessions
        var keys = groups.map(\.key)
        for key in team.choices.map(\.projectKey) + all.map(\.project) where !keys.contains(key) { keys.append(key) }
        return keys.map { key in
            let group = groups.first { $0.key == key }
            let agents = team.choices.filter { $0.projectKey == key }
            let name = group?.name ?? agents.first?.projectName ?? key
            return ProjectEntry(key: key, name: name, local: group, agents: agents, sessions: all.filter { $0.project == key })
        }
    }

    func entry(_ key: String) -> ProjectEntry? { entries.first { $0.key == key } }

    /// Agents this Mac could add to a project (it can't add agents on another Mac).
    func missingAgents(_ key: String) -> [String] {
        guard let group = projects.groups.first(where: { $0.key == key }) else { return [] }
        return ProjectSettingsModel.agents.filter { !group.agents.contains($0) }
    }

    func addAgent(_ agent: String, to key: String) {
        projects.addAgent(agent, to: key)
    }

    func select(_ item: SidebarItem) {
        selection = item
        switch item {
        case .session(let host, let id):
            if host == "local" {
                team.selected = id; team.showAssignment = false; team.draft = ""; team.refresh()
            } else {
                if remote?.host != host || remote?.selected != id {
                    let viewer = TeamRoomModel(activity: activity, host: host)
                    viewer.deviceLabels = team.deviceLabels; viewer.choices = team.choices; viewer.selected = id
                    remote = viewer
                }
                remote?.refresh()
            }
        case .newSession(let key):
            team.newSession(in: key)
        case .addProject:
            projects.startNew()
        case .project(let key):
            if collapsed.contains(key) { collapsed.remove(key) }
        default: break
        }
    }

    /// The window opens on the session that needs attention most, or the newest one.
    func openDefault() {
        if case .settings = selection, let first = sessions.first(where: { $0.room.status == "awaiting_admin" }) ?? sessions.first(where: { $0.room.running }) ?? sessions.first {
            select(first.item)
        } else if case .settings = selection, let first = entries.first {
            select(.project(first.key))
        }
    }

    /// Lists the sessions on each reachable paired Mac that include this Mac.
    func pollRemote(online: [String]) {
        guard !polling else { return }
        let hosts = online.filter { unsupported[$0].map { Date().timeIntervalSince($0) > 600 } ?? true }
        guard !hosts.isEmpty else { return }
        polling = true
        DispatchQueue.global(qos: .utility).async {
            var found: [String: [TeamRoom]] = [:]
            var refused: [String] = []
            for host in hosts {
                struct Reply: Decodable { let teams: [TeamRoom] }
                do { found[host] = try JSONDecoder().decode(Reply.self, from: self.activity.call("teams", target: host)).teams }
                catch { refused.append(host) }
            }
            DispatchQueue.main.async {
                self.polling = false
                for host in refused { self.unsupported[host] = Date() }
                var rooms = self.remoteRooms.filter { online.contains($0.key) }
                for (host, list) in found { rooms[host] = list }
                self.remoteRooms = rooms
            }
        }
    }
}

// MARK: - Sidebar

struct WorkspaceSidebar: View {
    @ObservedObject var workspace: WorkspaceModel
    @ObservedObject var devices: DeviceStatusModel
    @State private var hovered: String?
    @State private var info: SessionRow?

    var body: some View {
        VStack(spacing: 0) {
            ScrollView { LazyVStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    SectionLabel("Projects")
                    Spacer()
                    IconButton(symbol: "plus", help: "Add a project", size: 22) { workspace.select(.addProject) }
                }.padding(.leading, 10).padding(.trailing, 4).padding(.top, 12).padding(.bottom, 4)
                let entries = workspace.entries
                if entries.isEmpty {
                    Button { workspace.select(.addProject) } label: {
                        Label("Add your first project", systemImage: "folder.badge.plus").font(.bridge(.callout))
                            .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 10).padding(.vertical, 8)
                            .background(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Theme.stroke, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
                    }.buttonStyle(.plain).foregroundStyle(.tint).padding(.horizontal, 6).padding(.top, 4)
                }
                ForEach(entries) { entry in project(entry) }
            }.padding(.horizontal, 8).padding(.bottom, 12) }
            Hairline()
            VStack(spacing: 1) {
                tool(.devices, "Devices", "laptopcomputer.and.iphone", badge: devices.healthy ? nil : devices.report == nil ? nil : "exclamationmark.circle.fill")
                tool(.history, "All agent tasks", "clock.arrow.circlepath")
                tool(.settings, "Settings", "gearshape")
            }.padding(8)
        }.frame(maxHeight: .infinity).font(.bridge(.callout))
            .sheet(item: $info) { session in
                SessionInfoSheet(room: session.room, host: session.host, deviceLabel: workspace.team.deviceLabel)
            }
    }

    private func project(_ entry: ProjectEntry) -> some View {
        let open = !workspace.collapsed.contains(entry.key)
        let chosen = workspace.selection == .project(entry.key)
        let attention = entry.sessions.contains(where: { $0.room.status == "awaiting_admin" })
        return VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 6) {
                Button {
                    withAnimation(.easeOut(duration: 0.15)) { if open { workspace.collapsed.insert(entry.key) } else { workspace.collapsed.remove(entry.key) } }
                } label: {
                    Image(systemName: "chevron.right").font(.system(size: 9, weight: .bold)).foregroundStyle(.secondary)
                        .rotationEffect(.degrees(open ? 90 : 0)).frame(width: 14, height: 18).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityLabel(open ? "Collapse \(entry.name)" : "Expand \(entry.name)")
                Button { workspace.select(.project(entry.key)) } label: {
                    HStack(spacing: 7) {
                        Image(systemName: open ? "folder.fill" : "folder").font(.system(size: 12)).foregroundStyle(.secondary)
                        Text(entry.name).font(.bridge(.callout).weight(.semibold)).lineLimit(1)
                        if attention { Circle().fill(Color.orange).frame(width: 6, height: 6).accessibilityLabel("Needs you") }
                        Spacer(minLength: 0)
                    }.contentShape(Rectangle())
                }.buttonStyle(.plain)
                if hovered == entry.key || chosen {
                    IconButton(symbol: "square.and.pencil", help: "New session in \(entry.name)", size: 22) { workspace.select(.newSession(entry.key)) }
                } else if !entry.sessions.isEmpty {
                    Text("\(entry.sessions.count)").font(.bridge(.caption2).monospacedDigit()).foregroundStyle(.tertiary).frame(minWidth: 22)
                }
            }.padding(.leading, 4).padding(.trailing, 4).frame(height: 30)
                .rowPlate(selected: chosen, hovering: hovered == entry.key)
                .onHover { hovered = $0 ? entry.key : (hovered == entry.key ? nil : hovered) }
                .contextMenu {
                    Button("New session") { workspace.select(.newSession(entry.key)) }
                    if entry.local != nil { Button("Project settings") { workspace.projects.edit(entry.key); workspace.selection = .addProject } }
                }
            if open {
                ForEach(entry.sessions) { session in row(session) }
                if entry.sessions.isEmpty {
                    Button { workspace.select(.newSession(entry.key)) } label: {
                        Text("Start a session").font(.bridge(.callout)).foregroundStyle(.tertiary).frame(maxWidth: .infinity, alignment: .leading)
                    }.buttonStyle(.plain).padding(.leading, 40).frame(height: 26)
                }
            }
        }.padding(.top, 6)
    }

    private func row(_ session: SessionRow) -> some View {
        let status = TeamStatus(room: session.room)
        let chosen = workspace.selection == session.item
        let hovering = hovered == session.id
        return Button { workspace.select(session.item) } label: {
            HStack(spacing: 6) {
                StatusDot(color: status.color, pulsing: session.room.running, size: 6).frame(width: 14)
                Text(session.room.displayTitle).lineLimit(1).font(.bridge(.callout))
                    .foregroundStyle(chosen ? Color.primary : Color.primary.opacity(0.85))
                Spacer(minLength: 4)
                if session.host != "local" {
                    Image(systemName: "arrow.up.forward.square").font(.system(size: 10)).foregroundStyle(.tertiary)
                        .help("Run from \(workspace.team.deviceLabel(session.host)). This Mac takes part in it.")
                }
                if hovering {
                    IconButton(symbol: "info.circle", help: "Session info", size: 20) { info = session }
                } else {
                    Text(relativeShort(session.room.updated_at)).font(.bridge(.caption2).monospacedDigit()).foregroundStyle(.tertiary)
                }
            }.padding(.leading, 18).padding(.trailing, 6).frame(height: 28).frame(maxWidth: .infinity, alignment: .leading)
                .rowPlate(selected: chosen, hovering: hovering).contentShape(Rectangle())
        }.buttonStyle(.plain).onHover { hovered = $0 ? session.id : (hovered == session.id ? nil : hovered) }
            .contextMenu {
                Button { info = session } label: { Label("Session info", systemImage: "info.circle") }
                Button("Copy session ID") { copyToPasteboard(session.room.id) }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(session.room.displayTitle). \(status.text)." + (session.host == "local" ? "" : " Run from \(workspace.team.deviceLabel(session.host))."))
            .accessibilityAddTraits(chosen ? [.isButton, .isSelected] : .isButton)
            .accessibilityAction(named: "Session info") { info = session }
    }

    private func tool(_ item: SidebarItem, _ title: String, _ icon: String, badge: String? = nil) -> some View {
        let chosen = workspace.selection == item
        let key = "tool-" + title
        return Button { workspace.select(item) } label: {
            HStack(spacing: 9) {
                Image(systemName: icon).font(.system(size: 13)).frame(width: 18).foregroundStyle(chosen ? Color.accentColor : Color.secondary)
                Text(title)
                Spacer()
                if let badge = badge { Image(systemName: badge).foregroundStyle(.orange) }
            }.padding(.horizontal, 10).frame(height: 30).frame(maxWidth: .infinity, alignment: .leading)
                .rowPlate(selected: chosen, hovering: hovered == key).contentShape(Rectangle())
        }.buttonStyle(.plain).onHover { hovered = $0 ? key : (hovered == key ? nil : hovered) }
    }
}

// MARK: - Project home

struct ProjectHomeView: View {
    @ObservedObject var workspace: WorkspaceModel
    let key: String
    @State private var info: SessionRow?
    @State private var hovered: String?

    var body: some View {
        if let entry = workspace.entry(key) {
            ScrollView { VStack(alignment: .leading, spacing: 32) {
                HStack(alignment: .center, spacing: 16) {
                    Image(systemName: "folder.fill").font(.system(size: 22)).foregroundStyle(.white)
                        .frame(width: 48, height: 48)
                        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .fill(LinearGradient(colors: [Color.accentColor.opacity(0.75), Color.accentColor], startPoint: .top, endPoint: .bottom)))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(entry.name).font(.bridge(.title).weight(.semibold))
                        Text(entry.local?.repository ?? "Not on this Mac. Its agents are on a paired Mac.").font(.bridge(.caption, design: entry.local == nil ? .default : .monospaced))
                            .foregroundStyle(.secondary).textSelection(.enabled).lineLimit(1).truncationMode(.middle)
                    }
                    Spacer()
                    if entry.local != nil {
                        Button { workspace.projects.edit(key); workspace.selection = .addProject } label: { Label("Settings", systemImage: "slider.horizontal.3") }
                            .controlSize(.large)
                    }
                    Button { workspace.select(.newSession(key)) } label: { Label("New session", systemImage: "square.and.pencil") }
                        .buttonStyle(.borderedProminent).controlSize(.large).keyboardShortcut("n", modifiers: .command).disabled(workspace.team.hasPending)
                }
                VStack(alignment: .leading, spacing: 12) {
                    SectionLabel("Agents")
                    if entry.agents.isEmpty {
                        Text("No agent is ready for this project yet.").foregroundStyle(.secondary)
                    }
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 10)], alignment: .leading, spacing: 10) {
                        ForEach(entry.agents) { choice in
                            HStack(spacing: 10) {
                                AgentGlyph(agent: choice.member.agent, size: 30)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(agentName(choice.member.agent)).font(.bridge(.callout).weight(.semibold))
                                    Text(workspace.team.deviceLabel(choice.member.target)).font(.bridge(.caption)).foregroundStyle(.secondary)
                                }
                                Spacer(minLength: 0)
                                StatusDot(color: .green, size: 6)
                            }.padding(12).background(RoundedRectangle(cornerRadius: Theme.corner, style: .continuous).fill(Theme.raised))
                                .overlay(RoundedRectangle(cornerRadius: Theme.corner, style: .continuous).strokeBorder(Theme.stroke))
                        }
                        ForEach(workspace.missingAgents(key), id: \.self) { agent in
                            Button { workspace.addAgent(agent, to: key) } label: {
                                HStack(spacing: 10) {
                                    Image(systemName: "plus").font(.system(size: 13, weight: .semibold)).frame(width: 30, height: 30)
                                        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Theme.fillStrong))
                                    Text("Add \(agentName(agent)) on this Mac").font(.bridge(.callout))
                                    Spacer(minLength: 0)
                                    if workspace.addingAgent { ProgressView().controlSize(.small) }
                                }.padding(12).foregroundStyle(.secondary).contentShape(Rectangle())
                                    .background(RoundedRectangle(cornerRadius: Theme.corner, style: .continuous).strokeBorder(Theme.stroke, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
                            }.buttonStyle(.plain).disabled(workspace.addingAgent)
                        }
                    }
                    if let problems = workspace.projects.editing == key ? workspace.projects.agentErrors : nil, !problems.isEmpty {
                        ForEach(problems.sorted(by: { $0.key < $1.key }), id: \.key) { agent, problem in
                            Label("\(agentName(agent)): \(problem)", systemImage: "exclamationmark.triangle.fill").font(.bridge(.callout)).foregroundStyle(.orange).textSelection(.enabled)
                        }
                    }
                    Text("Any of these can lead or do the work in a session. Add the project on your other Mac, with the same name, to use its agents too.")
                        .font(.bridge(.caption)).foregroundStyle(.secondary)
                }
                VStack(alignment: .leading, spacing: 12) {
                    SectionLabel("Sessions")
                    if entry.sessions.isEmpty {
                        VStack(spacing: 10) {
                            Image(systemName: "bubble.left.and.text.bubble.right").font(.system(size: 28, weight: .light)).foregroundStyle(.tertiary)
                            Text("No sessions yet").font(.bridge(.headline))
                            Text("Start one to give the agents a task.").font(.bridge(.callout)).foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity).padding(.vertical, 36)
                            .background(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).strokeBorder(Theme.stroke, style: StrokeStyle(lineWidth: 1, dash: [5, 4])))
                    } else {
                        GroupedList(data: entry.sessions) { session in sessionRow(session) }
                    }
                }
            }.padding(.horizontal, 40).padding(.vertical, 32).frame(maxWidth: 920, alignment: .leading).frame(maxWidth: .infinity) }
                .background(Theme.canvas)
                .sheet(item: $info) { session in SessionInfoSheet(room: session.room, host: session.host, deviceLabel: workspace.team.deviceLabel) }
        } else {
            Text("This project is no longer available.").foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func sessionRow(_ session: SessionRow) -> some View {
        let status = TeamStatus(room: session.room)
        return Button { workspace.select(session.item) } label: {
            HStack(spacing: 12) {
                StatusDot(color: status.color, pulsing: session.room.running, size: 8)
                VStack(alignment: .leading, spacing: 3) {
                    Text(session.room.displayTitle).font(.bridge(.callout).weight(.medium)).lineLimit(1)
                    HStack(spacing: 5) {
                        AgentGlyph(agent: session.room.leader.agent, role: "leader", size: 14)
                        Text(agentName(session.room.leader.agent) + " leads")
                        Text("·").foregroundStyle(.tertiary)
                        AgentGlyph(agent: session.room.follower.agent, role: "follower", size: 14)
                        Text(agentName(session.room.follower.agent) + " builds")
                        if session.host != "local" { Text("· run from \(workspace.team.deviceLabel(session.host))") }
                    }.font(.bridge(.caption)).foregroundStyle(.secondary)
                }
                Spacer()
                Text(status.text).font(.bridge(.caption).weight(.medium)).foregroundStyle(status.color)
                Text(relativeShort(session.room.updated_at)).font(.bridge(.caption).monospacedDigit()).foregroundStyle(.tertiary).frame(minWidth: 28, alignment: .trailing)
                IconButton(symbol: "info.circle", help: "Session info", size: 24) { info = session }
                    .opacity(hovered == session.id ? 1 : 0.35)
            }.padding(.horizontal, 14).padding(.vertical, 11).background(hovered == session.id ? Theme.hover : Color.clear).contentShape(Rectangle())
        }.buttonStyle(.plain).onHover { hovered = $0 ? session.id : (hovered == session.id ? nil : hovered) }
            .contextMenu { Button { info = session } label: { Label("Session info", systemImage: "info.circle") } }
    }
}

// MARK: - Window

struct DesktopView: View {
    @ObservedObject var setup: SetupModel
    @StateObject private var activity: TaskBrowserModel
    @StateObject private var projects: ProjectSettingsModel
    @StateObject private var team: TeamRoomModel
    @StateObject private var devices: DeviceStatusModel
    @StateObject private var workspace: WorkspaceModel
    private let fast = Timer.publish(every: 1, on: .main, in: .common).autoconnect()
    private let slow = Timer.publish(every: 10, on: .main, in: .common).autoconnect()
    @State private var tick = 0

    init(setup: SetupModel, resources: URL) {
        self.setup = setup
        let browser = TaskBrowserModel(resources: resources)
        let projects = ProjectSettingsModel(activity: browser)
        let team = TeamRoomModel(activity: browser)
        let devices = DeviceStatusModel(activity: browser)
        devices.onReconnect = { team.refresh(discover: true) }
        _activity = StateObject(wrappedValue: browser)
        _projects = StateObject(wrappedValue: projects)
        _team = StateObject(wrappedValue: team)
        _devices = StateObject(wrappedValue: devices)
        _workspace = StateObject(wrappedValue: WorkspaceModel(team: team, projects: projects, activity: browser))
    }

    var body: some View {
        NavigationSplitView {
            WorkspaceSidebar(workspace: workspace, devices: devices).navigationSplitViewColumnWidth(min: 220, ideal: 264, max: 360)
        } detail: {
            detail.frame(minWidth: 640, maxWidth: .infinity, maxHeight: .infinity).background(Theme.canvas)
        }.font(.bridge(.body)).frame(minWidth: 980, minHeight: 680)
            .toolbar { ToolbarItem(placement: .primaryAction) { DeviceToolbarStatus(model: devices) { workspace.select(.devices) } } }
            .onAppear {
                devices.refresh(); projects.refresh(); team.refresh(discover: true)
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { workspace.openDefault() }
            }
            .onChange(of: team.selected) { id in
                // A session just started from the form: show it in the sidebar too.
                if case .newSession = workspace.selection, !team.showAssignment, !id.isEmpty { workspace.selection = .session(host: "local", id: id) }
            }
            .onReceive(fast) { _ in
                tick += 1
                switch workspace.selection {
                case .session(let host, _):
                    if host == "local" { team.refresh() } else if tick % 2 == 0 { workspace.remote?.refresh() }
                case .newSession: break
                default: if tick % 5 == 0 { team.refresh() }
                }
            }
            .onReceive(slow) { _ in
                devices.refresh()
                if team.rediscover { team.refresh() }
                workspace.pollRemote(online: devices.connected.map(\.id))
            }
    }

    private func navigate(_ section: Int) {
        workspace.select([1: .devices, 2: .addProject, 3: .settings, 4: .history][section] ?? .settings)
    }

    @ViewBuilder private var detail: some View {
        switch workspace.selection {
        case .session(let host, _):
            if host == "local" { TeamRoomView(model: team, active: true, navigate: navigate) }
            else if let remote = workspace.remote { TeamRoomView(model: remote, active: true, navigate: navigate) }
        case .newSession(let key):
            TeamRoomView(model: team, active: true, navigate: navigate, missingAgents: workspace.missingAgents(key),
                         addAgent: { workspace.addAgent($0, to: key) }, addingAgent: workspace.addingAgent,
                         cancel: { workspace.select(.project(key)) })
        case .project(let key): ProjectHomeView(workspace: workspace, key: key)
        case .addProject: ProjectSettingsView(model: projects)
        case .devices: DevicesView(model: team, devices: devices)
        case .history: TaskBrowserView(model: activity, active: true)
        case .settings: ScrollView { SetupView(model: setup).frame(maxWidth: 860).frame(maxWidth: .infinity) }
        }
    }
}
