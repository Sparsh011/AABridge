import Foundation
import SwiftUI

/// `online` is nil for legacy SSH devices, which aren't probed in the background.
struct PairedDevice: Decodable, Identifiable {
    let id: String; let label: String; let host: String; let online: Bool?
}
struct DeviceReport: Decodable {
    let service: Bool; let listening: Bool; let service_error: String?; let devices: [PairedDevice]
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
    var healthy: Bool { report?.service == true && !devices.contains { $0.online == false } }
    var summary: String {
        guard let report = report else { return "Checking Macs…" }
        if !report.service { return "Bridge not running" }
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

struct DeviceRow: View {
    let device: PairedDevice
    private var color: Color { device.online == nil ? .secondary : device.online! ? .green : .orange }
    private var state: String { device.online == nil ? "Saved · checked when used" : device.online! ? "Connected" : "Not reachable" }
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "laptopcomputer").foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(device.label).font(.bridge(.callout).weight(.medium))
                Text(device.host).font(.bridge(.caption)).foregroundStyle(.secondary).textSelection(.enabled)
            }
            Spacer()
            Circle().fill(color).frame(width: 8, height: 8)
            Text(state).font(.bridge(.caption)).foregroundStyle(.secondary)
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
            ForEach(model.devices) { DeviceRow(device: $0) }
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
