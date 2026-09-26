// Collects reel links from an Instagram account's Reels tab in a real browser window.
// Instagram's data endpoints refuse logged-out requests and yt-dlp's profile support is
// broken, but the Reels tab still lists reels and loads more as it scrolls.

import AppKit
import WebKit

@MainActor
final class ReelCollector: NSObject, WKNavigationDelegate, NSWindowDelegate {
    struct Outcome {
        var links: [String]
        var note: String?
    }

    private struct Page: Decodable {
        let ids: [String]
        let missing: Bool
        let isPrivate: Bool
        let walled: Bool
    }

    // Reel IDs in grid order, plus what the page says about the account.
    private static let readPage = """
    (() => {
      const ids = [];
      for (const a of document.querySelectorAll('a[href]')) {
        const m = (a.getAttribute('href') || '').match(/\\/(?:reel|p)\\/([A-Za-z0-9_-]+)/);
        if (m && !ids.includes(m[1])) ids.push(m[1]);
      }
      const text = document.body ? document.body.innerText : '';
      const path = location.pathname;
      return JSON.stringify({
        ids,
        missing: /Sorry, this page isn't available|Profile isn't available/i.test(text),
        isPrivate: /This account is private/i.test(text),
        walled: path === '/' || path.startsWith('/accounts/login'),
      });
    })()
    """

    // Bring the last reel into view, which is what makes Instagram load the next set.
    private static let scrollDown = """
    (() => {
      const reels = document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]');
      if (reels.length) reels[reels.length - 1].scrollIntoView({ block: 'end' });
      window.scrollBy(0, window.innerHeight);
      return 'ok';
    })()
    """

    private let username: String
    private let count: Int
    private let window: NSWindow
    private let webView: WKWebView
    private var cancelled = false

    init(username: String, count: Int, beside parent: NSWindow?, visible: Bool = true) {
        self.username = username
        self.count = count
        let frame = NSRect(x: 0, y: 0, width: 520, height: 780)
        // The default data store shares cookies with View > Instagram Login.
        webView = WKWebView(frame: frame, configuration: WKWebViewConfiguration())
        window = NSWindow(contentRect: frame, styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        super.init()
        webView.navigationDelegate = self
        window.title = "Finding reels on @\(username)"
        window.contentView = webView
        window.isReleasedWhenClosed = false
        window.delegate = self
        if !visible {
            window.setFrameOrigin(NSPoint(x: -20_000, y: -20_000))  // tests only: off every screen
        } else if let parent, let screen = parent.screen ?? NSScreen.main {
            var origin = NSPoint(x: parent.frame.maxX + 12, y: parent.frame.maxY - frame.height)
            if origin.x + frame.width > screen.visibleFrame.maxX {
                origin.x = max(screen.visibleFrame.minX, parent.frame.minX - frame.width - 12)
            }
            window.setFrameOrigin(origin)
        } else {
            window.center()
        }
        window.orderFront(nil)
    }

    func cancel() {
        cancelled = true
    }

    // Closing the window stops the search.
    func windowWillClose(_ notification: Notification) {
        cancelled = true
    }

    func run() async -> Outcome {
        guard let url = URL(string: "https://www.instagram.com/\(username)/reels/") else {
            return finish([], note: "That is not an Instagram username.")
        }
        webView.load(URLRequest(url: url))
        var found: [String] = []
        var idle = 0  // rounds in a row without a new reel
        let deadline = Date().addingTimeInterval(180)
        try? await Task.sleep(nanoseconds: 2_000_000_000)
        while !cancelled && Date() < deadline {
            if let page = await readPage() {
                if page.missing {
                    return finish([], note: "Instagram has no public account called @\(username).")
                }
                if page.isPrivate && page.ids.isEmpty {
                    return finish([], note: "@\(username) is a private account.")
                }
                let before = found.count
                for id in page.ids where !found.contains(id) {
                    found.append(id)
                }
                if found.count >= count || page.walled {
                    break
                }
                idle = found.count == before ? idle + 1 : 0
            } else {
                idle += 1  // still loading
            }
            // The first reels can take a while to draw; after that, six quiet rounds mean the end.
            if idle >= (found.isEmpty ? 20 : 6) {
                break
            }
            if !found.isEmpty {
                _ = await evaluate(Self.scrollDown)
            }
            try? await Task.sleep(nanoseconds: 1_500_000_000)
        }
        if cancelled {
            return finish([], note: "Stopped.")
        }
        let links = found.prefix(count).map { "https://www.instagram.com/reel/\($0)/" }
        var note: String?
        if links.isEmpty {
            note = "Instagram did not show any reels for @\(username). It may have none, or Instagram is limiting "
                + "logged-out visits right now. Log in with View > Instagram Login and try again."
        } else if links.count < count {
            note = "Instagram showed \(links.count) of the \(count) you asked for. "
                + "To see more, log in with View > Instagram Login and try again."
        }
        return finish(Array(links), note: note)
    }

    private func finish(_ links: [String], note: String?) -> Outcome {
        window.delegate = nil
        window.close()
        return Outcome(links: links, note: note)
    }

    private func readPage() async -> Page? {
        guard let json = await evaluate(Self.readPage) else { return nil }
        return try? JSONDecoder().decode(Page.self, from: Data(json.utf8))
    }

    private func evaluate(_ script: String) async -> String? {
        await withCheckedContinuation { continuation in
            webView.evaluateJavaScript(script) { result, _ in
                continuation.resume(returning: result as? String)
            }
        }
    }

    // Stay on Instagram inside this window.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        decisionHandler(isInstagram(navigationAction.request.url) ? .allow : .cancel)
    }
}

/// A plain window on Instagram's login page. The session it creates lives in the app's
/// default web data store, so the reels window sees the account as logged in.
@MainActor
final class InstagramLoginWindow: NSObject, WKNavigationDelegate {
    let window: NSWindow
    private let webView = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())

    override init() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 760),
                          styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        super.init()
        window.title = "Instagram Login"
        window.isReleasedWhenClosed = false
        window.contentView = webView
        webView.navigationDelegate = self
        if let url = URL(string: "https://www.instagram.com/accounts/login/") {
            webView.load(URLRequest(url: url))
        }
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    // Instagram's login can hand off to Facebook, so allow both.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        let host = navigationAction.request.url?.host ?? ""
        decisionHandler(isInstagram(navigationAction.request.url) || host == "facebook.com" || host.hasSuffix(".facebook.com")
                        ? .allow : .cancel)
    }
}

private func isInstagram(_ url: URL?) -> Bool {
    guard let url else { return false }
    if url.scheme == "about" { return true }
    let host = url.host ?? ""
    return host == "instagram.com" || host.hasSuffix(".instagram.com")
}
