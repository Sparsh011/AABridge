import AppKit
import SwiftUI

/// The design system: a quiet native palette, one radius scale and a few shared pieces.
/// Every colour adapts to light and dark mode.
enum Theme {
    /// Agent colours: the same agent looks the same everywhere, whatever its role.
    static let claude = Color(red: 0.84, green: 0.45, blue: 0.32)
    static let codex = Color(red: 0.36, green: 0.38, blue: 0.90)
    static func agent(_ agent: String) -> Color { agent == "claude" ? claude : codex }
    /// Brand gradient ends for the app mark.
    static let leader = codex
    static let worker = claude
    static func role(_ role: String) -> Color { role == "leader" ? leader : worker }

    /// The reading surface of the main column and of raised cards.
    static let canvas = Color(nsColor: .textBackgroundColor)
    static let raised = Color(nsColor: .controlBackgroundColor)
    static let sidebar = Color(nsColor: .windowBackgroundColor)
    static let hairline = Color.primary.opacity(0.08)
    static let stroke = Color.primary.opacity(0.11)
    static let fill = Color.primary.opacity(0.035)
    static let fillStrong = Color.primary.opacity(0.06)
    static let hover = Color.primary.opacity(0.05)
    static let selected = Color.primary.opacity(0.09)
    static let muted = Color.secondary.opacity(0.75)

    static let corner: CGFloat = 10
    static let cardCorner: CGFloat = 14
    /// The widest a column of prose should get.
    static let readable: CGFloat = 820
}

/// "now", "4m", "3h", "2d", "Nov 3": how long ago, for lists.
func relativeShort(_ time: Double?) -> String {
    guard let time = time, time > 0 else { return "" }
    let seconds = Int(Date().timeIntervalSince1970 - time)
    if seconds < 60 { return "now" }
    if seconds < 3600 { return "\(seconds / 60)m" }
    if seconds < 86_400 { return "\(seconds / 3600)h" }
    if seconds < 7 * 86_400 { return "\(seconds / 86_400)d" }
    return Date(timeIntervalSince1970: time).formatted(.dateTime.month(.abbreviated).day())
}

func copyToPasteboard(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}

// MARK: - Pieces

/// One-point separator in the system's quiet line colour.
struct Hairline: View {
    var vertical = false
    var body: some View {
        Rectangle().fill(Theme.hairline).frame(width: vertical ? 1 : nil, height: vertical ? nil : 1)
    }
}

/// Small uppercase label above a group, as in Finder's sidebar.
struct SectionLabel: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text.uppercased()).font(.bridge(.caption2).weight(.semibold)).kerning(0.6).foregroundStyle(.secondary)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A quiet capsule of metadata.
struct Chip: View {
    let text: String
    var icon: String? = nil
    var tint: Color? = nil
    var body: some View {
        HStack(spacing: 4) {
            if let icon = icon { Image(systemName: icon).imageScale(.small) }
            Text(text).lineLimit(1)
        }.font(.bridge(.caption).weight(.medium)).foregroundStyle(tint ?? .secondary)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Capsule().fill(tint.map { $0.opacity(0.12) } ?? Theme.fillStrong))
    }
}

/// The square avatar of an agent. Symbol and colour both name the agent; its role is written next to it.
struct AgentGlyph: View {
    let agent: String
    var role: String? = nil
    var size: CGFloat = 24
    private var color: Color { Theme.agent(agent) }
    var body: some View {
        Image(systemName: agent == "claude" ? "sparkle" : "chevron.left.forwardslash.chevron.right")
            .font(.system(size: size * 0.46, weight: .bold)).foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(RoundedRectangle(cornerRadius: size * 0.3, style: .continuous)
                .fill(LinearGradient(colors: [color.opacity(0.85), color], startPoint: .top, endPoint: .bottom)))
            .accessibilityHidden(true)
    }
}

/// The status of a session as a dot. A running one breathes.
struct StatusDot: View {
    let color: Color
    var pulsing = false
    var size: CGFloat = 8
    @State private var on = false
    var body: some View {
        ZStack {
            if pulsing {
                Circle().fill(color.opacity(0.35)).frame(width: size * 2.1, height: size * 2.1)
                    .scaleEffect(on ? 1 : 0.45).opacity(on ? 0 : 1)
                    .animation(.easeOut(duration: 1.4).repeatForever(autoreverses: false), value: on)
            }
            Circle().fill(color).frame(width: size, height: size)
        }.frame(width: size * 2.1, height: size * 2.1).onAppear { if pulsing { on = true } }.accessibilityHidden(true)
    }
}

/// A borderless symbol button with a soft hover plate, for toolbars and rows.
struct IconButton: View {
    let symbol: String
    let help: String
    var size: CGFloat = 26
    var tint: Color = .secondary
    let action: () -> Void
    @State private var hovering = false
    var body: some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: size * 0.5, weight: .medium)).foregroundStyle(tint)
                .frame(width: size, height: size)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(hovering ? Theme.hover : Color.clear))
                .contentShape(Rectangle())
        }.buttonStyle(.plain).onHover { hovering = $0 }.help(help).accessibilityLabel(help)
    }
}

/// The heading of a full page: an optional eyebrow, a title and one line of context.
struct PageHeader<Trailing: View>: View {
    var eyebrow: String? = nil
    let title: String
    var subtitle: String? = nil
    @ViewBuilder var trailing: Trailing
    var body: some View {
        HStack(alignment: .bottom, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                if let eyebrow = eyebrow { SectionLabel(eyebrow) }
                Text(title).font(.bridge(.title).weight(.semibold)).lineLimit(2)
                if let subtitle = subtitle {
                    Text(subtitle).font(.bridge(.callout)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
            trailing
        }
    }
}

extension PageHeader where Trailing == EmptyView {
    init(eyebrow: String? = nil, title: String, subtitle: String? = nil) {
        self.init(eyebrow: eyebrow, title: title, subtitle: subtitle) { EmptyView() }
    }
}

/// A neutral raised container: the elegant replacement for GroupBox.
struct Panel<Content: View>: View {
    var padding: CGFloat = 18
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 14) { content }
            .padding(padding).frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).fill(Theme.raised))
            .overlay(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).strokeBorder(Theme.stroke))
            .shadow(color: .black.opacity(0.035), radius: 6, y: 2)
    }
}

/// A list grouped in one rounded container, rows separated by hairlines.
struct GroupedList<Data: RandomAccessCollection, Row: View>: View where Data.Element: Identifiable {
    let data: Data
    @ViewBuilder let row: (Data.Element) -> Row
    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(data.enumerated()), id: \.element.id) { index, item in
                row(item)
                if index < data.count - 1 { Hairline().padding(.leading, 14) }
            }
        }.background(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).fill(Theme.raised))
            .overlay(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous).strokeBorder(Theme.stroke))
            .clipShape(RoundedRectangle(cornerRadius: Theme.cardCorner, style: .continuous))
    }
}

/// A label and a value, aligned in two columns, for info sheets.
struct InfoRow: View {
    let label: String
    let value: String
    var monospaced = false
    var copyable = false
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label).font(.bridge(.callout)).foregroundStyle(.secondary).frame(width: 120, alignment: .leading)
            Text(value).font(.bridge(.callout, design: monospaced ? .monospaced : .default)).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
            if copyable { IconButton(symbol: "doc.on.doc", help: "Copy \(label.lowercased())", size: 22) { copyToPasteboard(value) } }
        }.padding(.vertical, 7)
    }
}

/// "Leader" or "Worker" with its symbol, as a quiet tag.
struct RoleTag: View {
    let leader: Bool
    var body: some View {
        Label(leader ? "Leader" : "Worker", systemImage: leader ? "crown" : "hammer")
            .labelStyle(CompactLabel()).font(.bridge(.caption2).weight(.medium)).foregroundStyle(.secondary)
    }
}

extension View {
    /// A soft plate behind a row on hover and when selected.
    func rowPlate(selected: Bool, hovering: Bool, corner: CGFloat = 7) -> some View {
        background(RoundedRectangle(cornerRadius: corner, style: .continuous)
            .fill(selected ? Theme.selected : hovering ? Theme.hover : Color.clear))
    }
}
