// Draws the app icon in the Kesterson Collage style: a yellow card pinned at a
// slight tilt on ink, hard red offset shadow, a sound wave beside lines of text.
// Usage: swift make_icon.swift out.png

import AppKit
import SwiftUI

let size: CGFloat = 1024
let output = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let space = CGColorSpace(name: CGColorSpace.sRGB)!

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
}

let ink = color(0x141214)
let yellow = color(0xECBF42)
let accent = color(0xB7422D)

guard let ctx = CGContext(data: nil, width: Int(size), height: Int(size), bitsPerComponent: 8, bytesPerRow: 0,
                          space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
    fatalError("Could not create a drawing context")
}
// Draw in top-left coordinates.
ctx.translateBy(x: 0, y: size)
ctx.scaleBy(x: 1, y: -1)

// The macOS icon grid: an 824 px square with continuous corners, centered on the canvas.
// The squircle is the Dock's shape; everything inside keeps the system's hard corners.
let body = RoundedRectangle(cornerRadius: 185.4, style: .continuous)
    .path(in: CGRect(x: 100, y: 100, width: 824, height: 824)).cgPath

ctx.saveGState()
// Shadow offsets ignore the flip above, so a negative height points down.
ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 28, color: color(0x000000, 0.35))
ctx.addPath(body)
ctx.setFillColor(ink)
ctx.fillPath()
ctx.restoreGState()

ctx.saveGState()
ctx.addPath(body)
ctx.clip()

// The card, tilted -2.2deg like a pinned mat (the system allows 1 to 2.6).
ctx.saveGState()
ctx.translateBy(x: 512, y: 506)
ctx.rotate(by: -2.2 * .pi / 180)
let card = CGRect(x: -300, y: -220, width: 600, height: 440)
ctx.setFillColor(accent)
ctx.fill(card.offsetBy(dx: 26, dy: 26))  // hard offset shadow, no blur
ctx.setFillColor(ink)
ctx.fill(card.insetBy(dx: -5, dy: -5))  // the ink border, visible where it crosses the red
ctx.setFillColor(yellow)
ctx.fill(card)

// Sound wave on the left, lines of text on the right. No rounded ends anywhere.
ctx.setFillColor(ink)
for (index, height) in [CGFloat(120), 220, 300, 190, 130].enumerated() {
    ctx.fill(CGRect(x: -250 + CGFloat(index) * 50, y: -height / 2, width: 34, height: height))
}
for (y, width) in [(CGFloat(-70), CGFloat(210)), (0, 180), (70, 120)] {
    ctx.fill(CGRect(x: 30, y: y - 17, width: width, height: 34))
}
ctx.restoreGState()

// A faint inner edge so the ink body reads against dark wallpapers.
ctx.addPath(body)
ctx.setLineWidth(4)
ctx.setStrokeColor(color(0xffffff, 0.1))
ctx.strokePath()
ctx.restoreGState()

guard let image = ctx.makeImage(),
      let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else {
    fatalError("Could not render the icon")
}
try png.write(to: URL(fileURLWithPath: output))
