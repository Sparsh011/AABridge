import Foundation
import SwiftUI

// `Text(someString)` never parses Markdown, and AttributedString(markdown:)
// keeps inline styling but flattens headings, lists and code blocks. Chat text
// is split into blocks here, and each block's inline text goes through Foundation.
enum MarkdownBlock: Equatable {
    case heading(Int, AttributedString)
    case paragraph(AttributedString)
    case quote(AttributedString)
    case code(String)
    case list([MarkdownItem])
    case table([AttributedString], [[AttributedString]])
    case rule
}

struct MarkdownItem: Equatable {
    let depth: Int; let marker: String; var text: String
    var inline: AttributedString { MarkdownDocument.inline(text) }
}

enum MarkdownDocument {
    private final class Parsed { let blocks: [MarkdownBlock]; init(_ blocks: [MarkdownBlock]) { self.blocks = blocks } }
    private static let cache = NSCache<NSString, Parsed>()

    /// Chat views redraw every second while a team runs, so parsed text is kept.
    static func blocks(_ text: String) -> [MarkdownBlock] {
        if let saved = cache.object(forKey: text as NSString) { return saved.blocks }
        let parsed = parse(text)
        cache.setObject(Parsed(parsed), forKey: text as NSString)
        return parsed
    }

    static func inline(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace, failurePolicy: .returnPartiallyParsedIfPossible)
        guard var value = try? AttributedString(markdown: text, options: options) else { return AttributedString(text) }
        for run in value.runs {
            // Agent text is untrusted: only web links stay clickable.
            if let scheme = run.link?.scheme?.lowercased(), !["http", "https"].contains(scheme) { value[run.range].link = nil }
            if run.inlinePresentationIntent?.contains(.code) == true { value[run.range].backgroundColor = Color.primary.opacity(0.08) }
        }
        return value
    }

    private static func match(_ line: String, _ pattern: String) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let found = regex.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) else { return nil }
        return (0..<found.numberOfRanges).map { Range(found.range(at: $0), in: line).map { String(line[$0]) } ?? "" }
    }

    private static func cells(_ line: String) -> [String] {
        var row = line.trimmingCharacters(in: .whitespaces)
        if row.hasPrefix("|") { row.removeFirst() }
        if row.hasSuffix("|") && !row.hasSuffix("\\|") { row.removeLast() }
        // A pipe inside a code span or escaped with \ belongs to the cell.
        var cells: [String] = [], current = "", code = false, previous: Character = " "
        for character in row {
            if character == "`" { code.toggle() }
            if character == "|" && !code && previous != "\\" { cells.append(current); current = "" }
            else { current.append(character) }
            previous = character
        }
        return (cells + [current]).map { $0.trimmingCharacters(in: .whitespaces) }
    }

    private static func parse(_ text: String) -> [MarkdownBlock] {
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var blocks: [MarkdownBlock] = [], paragraph: [String] = [], items: [MarkdownItem] = [], quote: [String] = []
        func flush() {
            if !paragraph.isEmpty { blocks.append(.paragraph(inline(paragraph.joined(separator: "\n")))); paragraph = [] }
            if !items.isEmpty { blocks.append(.list(items)); items = [] }
            if !quote.isEmpty { blocks.append(.quote(inline(quote.joined(separator: "\n")))); quote = [] }
        }
        var index = 0
        while index < lines.count {
            let line = lines[index], trimmed = line.trimmingCharacters(in: .whitespaces)
            index += 1
            if let fence = match(trimmed, "^(`{3,}|~{3,})") {
                flush()
                var code: [String] = []
                // An unclosed fence runs to the end, which also suits text still being written.
                while index < lines.count {
                    let next = lines[index].trimmingCharacters(in: .whitespaces)
                    index += 1
                    if next.hasPrefix(fence[1]) && next.allSatisfy({ $0 == fence[1].first }) { break }
                    code.append(lines[index - 1])
                }
                blocks.append(.code(code.joined(separator: "\n")))
            } else if trimmed.isEmpty {
                // A blank line between items doesn't end the list.
                if items.isEmpty || !(lines[index...].first { !$0.trimmingCharacters(in: .whitespaces).isEmpty }.map { match($0, #"^\s*([-*+]|\d{1,9}[.)])\s+"#) != nil } ?? false) { flush() }
            } else if !paragraph.isEmpty, match(trimmed, "^(=+|-+)$") != nil {
                let title = paragraph.joined(separator: " "); paragraph = []
                blocks.append(.heading(trimmed.hasPrefix("=") ? 1 : 2, inline(title)))
            } else if let heading = match(trimmed, #"^(#{1,6})\s+(.*?)(\s+#+)?$"#) {
                flush(); blocks.append(.heading(heading[1].count, inline(heading[2])))
            } else if match(trimmed.replacingOccurrences(of: " ", with: ""), #"^(\*{3,}|-{3,}|_{3,})$"#) != nil {
                flush(); blocks.append(.rule)
            } else if trimmed.contains("|"), index < lines.count, match(lines[index].trimmingCharacters(in: .whitespaces), #"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$"#) != nil {
                flush()
                let header = cells(line)
                var rows: [[AttributedString]] = []
                index += 1
                while index < lines.count, lines[index].contains("|") {
                    let row = cells(lines[index]).prefix(header.count).map(inline)
                    rows.append(row + Array(repeating: AttributedString(""), count: header.count - row.count))
                    index += 1
                }
                blocks.append(.table(header.map(inline), rows))
            } else if let found = match(line, #"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$"#) {
                if !paragraph.isEmpty || !quote.isEmpty { flush() }
                var marker = found[2], body = found[3]
                if marker.first?.isNumber != true {
                    marker = "•"
                    if let task = match(body, #"^\[([ xX])\]\s+(.*)$"#) { marker = task[1] == " " ? "☐" : "☑"; body = task[2] }
                }
                items.append(MarkdownItem(depth: min(found[1].count / 2, 5), marker: marker, text: body))
            } else if let found = match(line, #"^\s{0,3}>\s?(.*)$"#) {
                if !paragraph.isEmpty || !items.isEmpty { flush() }
                quote.append(found[1])
            } else if !items.isEmpty {
                items[items.count - 1].text += " " + trimmed  // A wrapped list item.
            } else if !quote.isEmpty {
                quote.append(trimmed)
            } else {
                paragraph.append(trimmed)
            }
        }
        flush()
        return blocks
    }
}

struct MarkdownText: View {
    let text: String
    var style: Font.TextStyle = .body

    private func heading(_ level: Int) -> Font {
        .bridge(level == 1 ? .title2 : level == 2 ? .title3 : .headline).weight(.semibold)
    }

    @ViewBuilder private func view(_ block: MarkdownBlock) -> some View {
        switch block {
        case .heading(let level, let value):
            Text(value).font(heading(level)).padding(.top, level <= 2 ? 6 : 2)
        case .paragraph(let value):
            Text(value)
        case .quote(let value):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 1.5).fill(Color.secondary.opacity(0.45)).frame(width: 3)
                Text(value).foregroundStyle(.secondary)
            }.fixedSize(horizontal: false, vertical: true)
        case .code(let value):
            Text(value).font(.bridge(.callout, design: .monospaced))
                .frame(maxWidth: .infinity, alignment: .leading).padding(10)
                .background(Color.primary.opacity(0.06)).clipShape(RoundedRectangle(cornerRadius: 8))
        case .list(let items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Text(item.marker).foregroundStyle(.secondary).monospacedDigit()
                        Text(item.inline)
                    }.padding(.leading, CGFloat(item.depth) * 20)
                }
            }
        case .table(let header, let rows):
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                GridRow { ForEach(Array(header.enumerated()), id: \.offset) { _, cell in Text(cell).fontWeight(.semibold) } }
                Divider()
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    GridRow { ForEach(Array(row.enumerated()), id: \.offset) { _, cell in Text(cell) } }
                }
            }.padding(10).overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.primary.opacity(0.12)))
        case .rule:
            Divider()
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            ForEach(Array(MarkdownDocument.blocks(text).enumerated()), id: \.offset) { _, block in
                view(block).fixedSize(horizontal: false, vertical: true)
            }
        }.font(.bridge(style)).textSelection(.enabled)
    }
}
