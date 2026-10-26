import AppKit
import SwiftUI

extension Font {
    /// One scale for the entire product, including captions and monospace text.
    static func bridge(_ style: Font.TextStyle, design: Font.Design = .default) -> Font {
        let native: NSFont.TextStyle
        switch style {
        case .largeTitle: native = .largeTitle
        case .title: native = .title1
        case .title2: native = .title2
        case .title3: native = .title3
        case .headline: native = .headline
        case .subheadline: native = .subheadline
        case .callout: native = .callout
        case .caption: native = .caption1
        case .caption2: native = .caption2
        case .footnote: native = .footnote
        default: native = .body
        }
        let font = Font.system(size: NSFont.preferredFont(forTextStyle: native).pointSize * 1.2, design: design)
        return style == .headline ? font.weight(.semibold) : font
    }
}
