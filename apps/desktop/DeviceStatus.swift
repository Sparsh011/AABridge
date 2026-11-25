import Foundation
import SwiftUI

/// `online` is nil for legacy SSH devices, which aren't probed in the background.
struct PairedDevice: Decodable, Identifiable {
    let id: String; let label: String; let host: String; let online: Bool?
    var release: String? = nil
}
struct DeviceReport: Decodable {
    let service: Bool; let listening: Bool; let service_error: String?; let devices: [PairedDevice]
    var app_release: String? = nil; var service_release: String? = nil
    /// Whether `other` runs different AABridge code than this app. "source" is a development run.
    func differs(_ other: String?) -> Bool {
        guard let app = app_release, let other = other, app != "source", other != "source" else { return false }
        return app != other
    }
    /// The installed bridge service on this Mac is not the version this app ships.
    var serviceOutdated: Bool { service && differs(service_release) }
    static let idle = DeviceReport(service: true, listening: true, service_error: nil, devices: [])
}

/// Saved pairings need no new card: this keeps the bridge running and notices
/// paired Macs coming back, so restarting either Mac reconnects by itself.
final class DeviceStatusModel: ObservableObject {
    @Published var report: DeviceReport?
    @Published var checked: Date?
    /// Runs when this Mac's bridge comes up or a paired Mac becomes reachable.
    var onReconnect: () -> Void = {}
    private let activity: TaskBrowserModel
    private var running = false
    private var known = Set<String>()
    private var serviceUp = false

    init(activity: TaskBrowserModel) { self.activity = activity }

    var devices: [PairedDevice] { report?.devices ?? [] }
    var connected: [PairedDevice] { devices.filter { $0.online == true } }
    var healthy: Bool { report?.service == true && report?.serviceOutdated != true && !devices.contains { $0.online == false } }
    var summary: String {
        guard let report = report else { return "Checking Macs…" }
        if !report.service { return "Bridge not running" }
        if report.serviceOutdated { return "Bridge service needs an update" }
        if devices.isEmpty { return "No Macs paired" }
        if devices.count == 1 {
            return devices[0].online == false ? "\(devices[0].label) offline" : "Connected to \(devices[0].label)"
        }
        return healthy ? "\(devices.count) Macs connected" : "\(connected.count) of \(devices.count) Macs connected"
    }

    func refresh() {
        guard !running else { return }
        running = true
        DispatchQueue.global(qos: .utility).async {
            let report = (try? self.activity.call("devices", target: "local")).flatMap { try? JSONDecoder().decode(DeviceReport.self, from: $0) }
            DispatchQueue.main.async {
                self.running = false
                guard let report = report else { return }
                let online = Set(report.devices.filter { $0.online == true }.map(\.id))
                let reconnected = !online.subtracting(self.known).isEmpty || (report.service && !self.serviceUp)
                self.known = online; self.serviceUp = report.service
                self.report = report; self.checked = Date()
                if reconnected { self.onReconnect() }
            }
        }
    }
}

/// Finds other Macs running AABridge on this network with Bonjour, and lets them find this one.
/// Discovery only shows who is there. Trust still comes from exchanging connection cards.
final class NearbyMacs: NSObject, ObservableObject, NetServiceBrowserDelegate, NetServiceDelegate {
    struct Found: Identifiable { let id: String; let name: String; var host: String? }
    @Published var found: [Found] = []
    @Published var problem = ""
    private let browser = NetServiceBrowser()
    private var advert: NetService?
    private var resolving: [NetService] = []
    private var started = false
    private let ownName = Host.current().localizedName ?? "Mac"

    func start() {
        guard !started, Bundle.main.bundleIdentifier != nil else { return }
        started = true
        // The port is a placeholder: the service only announces that AABridge is open here.
        let service = NetService(domain: "local.", type: "_aabridge._tcp.", name: ownName, port: 9)
        service.delegate = self; service.publish(); advert = service
        browser.delegate = self
        browser.searchForServices(ofType: "_aabridge._tcp.", inDomain: "local.")
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        guard service.name != ownName, !found.contains(where: { $0.id == service.name }) else { return }
        found.append(Found(id: service.name, name: service.name, host: nil))
        service.delegate = self; resolving.append(service); service.resolve(withTimeout: 5)
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didRemove service: NetService, moreComing: Bool) {
        found.removeAll { $0.id == service.name }
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String: NSNumber]) {
        problem = "macOS didn’t allow AABridge to look for other Macs. Allow it in System Settings → Privacy & Security → Local Network."
    }
    func netServiceDidResolveAddress(_ sender: NetService) {
        if let index = found.firstIndex(where: { $0.id == sender.name }), let host = sender.hostName {
            found[index].host = host.hasSuffix(".") ? String(host.dropLast()) : host
        }
        resolving.removeAll { $0 === sender }
    }
    func netService(_ sender: NetService, didNotResolve errorDict: [String: NSNumber]) { resolving.removeAll { $0 === sender } }
}

struct NearbyMacsView: View {
    @ObservedObject var nearby: NearbyMacs
    let paired: [PairedDevice]
    private func isPaired(_ mac: NearbyMacs.Found) -> Bool {
        paired.contains { $0.label == mac.name || (mac.host != nil && $0.host.caseInsensitiveCompare(mac.host!) == .orderedSame) }
    }
    var body: some View {
        Panel {
            Label("AABridge on this network", systemImage: "dot.radiowaves.left.and.right").font(.bridge(.headline))
            if !nearby.problem.isEmpty { Text(nearby.problem).font(.bridge(.callout)).foregroundStyle(.orange) }
            if nearby.found.isEmpty {
                Text("No other Mac with AABridge open was found yet. Open AABridge on your other Mac and keep both on the same Wi-Fi or network. You can still pair with a card if it doesn’t appear.")
                    .font(.bridge(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            ForEach(nearby.found) { mac in
                HStack(spacing: 10) {
                    Image(systemName: "laptopcomputer").foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(mac.name).font(.bridge(.callout).weight(.medium))
                        Text(mac.host ?? "Finding its address…").font(.bridge(.caption)).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                    Spacer()
                    Text(isPaired(mac) ? "Paired" : "Not paired yet").font(.bridge(.caption).bold()).foregroundStyle(isPaired(mac) ? Color.green : Color.orange)
                }
            }
            if nearby.found.contains(where: { !isPaired($0) }) {
                Text("A Mac listed as not paired is reachable, so pairing will work: exchange connection cards with it below.").font(.bridge(.caption)).foregroundStyle(.secondary)
            }
        }.onAppear { nearby.start() }
    }
}

struct DeviceRow: View {
    let device: PairedDevice
    var mismatch = false
    private var color: Color { device.online == nil ? .secondary : device.online! ? .green : .orange }
    private var state: String { device.online == nil ? "Saved · checked when used" : device.online! ? "Connected" : "Not reachable" }
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "laptopcomputer").font(.system(size: 13)).foregroundStyle(.secondary)
                .frame(width: 30, height: 30).background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Theme.fillStrong))
            VStack(alignment: .leading, spacing: 2) {
                Text(device.label).font(.bridge(.callout).weight(.medium))
                Text(device.host).font(.bridge(.caption)).foregroundStyle(.secondary).textSelection(.enabled)
            }
            Spacer()
            StatusDot(color: mismatch ? Color.orange : color, pulsing: device.online == true && !mismatch, size: 7)
            Text(mismatch ? "Different version (\(device.release == "older" ? "older" : device.release ?? "?"))" : state).font(.bridge(.caption)).foregroundStyle(.secondary)
        }
    }
}

struct DeviceList: View {
    @ObservedObject var model: DeviceStatusModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let error = model.report?.service_error { Text(error).font(.bridge(.callout)).foregroundStyle(.orange).textSelection(.enabled) }
            else if model.report?.service == true && model.report?.listening == false && !model.devices.isEmpty {
                Text("This Mac isn’t accepting connections yet, so your other Macs can’t reach it.").font(.bridge(.callout)).foregroundStyle(.orange)
            }
            if model.devices.isEmpty { Text("No paired Macs yet. Exchange connection cards once in Devices.").font(.bridge(.callout)).foregroundStyle(.secondary) }
            if let report = model.report, report.serviceOutdated {
                Label("The bridge service on this Mac (\(report.service_release == "older" ? "an older version" : report.service_release ?? "?")) is not the version this app ships (\(report.app_release ?? "?")). New features and fixes stay off until it is updated. Finish active tasks, then open Settings and press Set up this Mac.", systemImage: "arrow.triangle.2.circlepath")
                    .font(.bridge(.callout)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            ForEach(model.devices) { device in DeviceRow(device: device, mismatch: device.online == true && (model.report?.differs(device.release) ?? false)) }
            if model.devices.contains(where: { $0.online == true && (model.report?.differs($0.release) ?? false) }) {
                Text("A paired Mac runs a different AABridge version. Install the same build there and press Set up this Mac in its Settings, or some team features will be refused.")
                    .font(.bridge(.caption)).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            if model.devices.contains(where: { $0.online == false }) {
                Text("You don’t need a new card. Open AABridge on that Mac and keep it on this network; it reconnects by itself.")
                    .font(.bridge(.caption)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// The window toolbar's connection status, with each Mac's state in a popover.
struct DeviceToolbarStatus: View {
    @ObservedObject var model: DeviceStatusModel
    var manage: () -> Void
    @State private var showing = false
    private var color: Color { model.report == nil ? .secondary : model.healthy ? (model.devices.isEmpty ? .secondary : .green) : .orange }

    var body: some View {
        Button { showing.toggle() } label: {
            HStack(spacing: 6) {
                Circle().fill(color).frame(width: 8, height: 8)
                Text(model.summary).font(.bridge(.callout)).lineLimit(1)
            }.padding(.horizontal, 6)
        }.help("Paired Macs").popover(isPresented: $showing, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 12) {
                Text("Paired Macs").font(.bridge(.headline))
                DeviceList(model: model)
                Divider()
                HStack {
                    if let checked = model.checked { Text("Checked \(checked.formatted(date: .omitted, time: .standard))").font(.bridge(.caption)).foregroundStyle(.secondary) }
                    Spacer()
                    Button("Check now") { model.refresh() }
                    Button("Devices") { showing = false; manage() }
                }
            }.padding(16).frame(width: 380)
        }
    }
}
