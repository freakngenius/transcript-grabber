// IG Transcript: a native window around the local transcription server (app.py).
// The app starts the server with uv, shows its page in a web view, and stops the
// server when the app quits.

import AppKit
import UniformTypeIdentifiers
import WebKit

private let appName = "IG Transcript"
private let port = 3232
private let homeURL = URL(string: "http://127.0.0.1:\(port)/")!
private let pingURL = URL(string: "http://127.0.0.1:\(port)/api/ping")!
private let logURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/IG Transcript.log")

private func escapeHTML(_ text: String) -> String {
    text.replacingOccurrences(of: "&", with: "&amp;")
        .replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var server: Process?
    private var serverInput: Pipe?
    private var offline = false
    private var quitting = false
    private var startedAt = Date()

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        start()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        server?.terminate()
    }

    // MARK: - Server

    private func start() {
        showStatus("Starting…")
        Task {
            // Reuse a server that is already running, for example one started from Terminal.
            if await serverResponds() { loadApp() } else { launchServer() }
        }
    }

    private func launchServer() {
        guard let uv = findTool("uv") else {
            showProblem("uv is not installed", detail: "Install it with: brew install uv")
            return
        }
        guard let script = Bundle.main.url(forResource: "app", withExtension: "py") else {
            showProblem("app.py is missing from the app", detail: "Rebuild the app with build.sh.")
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: uv)
        // Instagram changes often and yt-dlp ships fixes fast, so check for a newer
        // yt-dlp at each start. Offline, run the installed copy.
        var arguments = ["run", "--quiet"]
        arguments += offline ? ["--offline"] : ["--upgrade-package", "yt-dlp"]
        arguments += [script.path, "--no-open", "--port", String(port), "--exit-with-stdin"]
        process.arguments = arguments

        var environment = ProcessInfo.processInfo.environment
        // Apps opened from Finder get a short PATH; ffmpeg, whisper-cli and deno live in Homebrew.
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        environment["PYTHONUNBUFFERED"] = "1"
        if let browser = UserDefaults.standard.string(forKey: "CookiesFromBrowser"), !browser.isEmpty {
            environment["IG_COOKIES_FROM_BROWSER"] = browser
        }
        process.environment = environment

        let input = Pipe()
        process.standardInput = input
        if let log = openLog() {
            process.standardOutput = log
            process.standardError = log
        }
        process.terminationHandler = { [weak self] finished in
            let id = ObjectIdentifier(finished)
            let status = finished.terminationStatus
            Task { @MainActor in self?.serverStopped(id: id, status: status) }
        }
        do {
            try process.run()
        } catch {
            showProblem("Could not start the transcription server", detail: error.localizedDescription)
            return
        }
        server = process
        serverInput = input  // app.py exits when this pipe closes, so a crash here cannot orphan it
        startedAt = Date()
        waitForServer(process)
    }

    private func waitForServer(_ process: Process) {
        Task {
            while process.isRunning && server === process {
                if await serverResponds() {
                    loadApp()
                    return
                }
                let seconds = Int(Date().timeIntervalSince(startedAt))
                if seconds >= 5 {
                    setDetail("The first start sets things up and can take a minute. \(seconds)s")
                }
                try? await Task.sleep(nanoseconds: 300_000_000)
            }
        }
    }

    private func serverStopped(id: ObjectIdentifier, status: Int32) {
        guard let current = server, ObjectIdentifier(current) == id else { return }
        server = nil
        serverInput = nil
        if quitting { return }
        // uv exits 2 when it cannot reach PyPI to check for a newer yt-dlp.
        if status == 2 && !offline {
            offline = true
            launchServer()
            return
        }
        showProblem("The transcription server stopped", detail: logTail())
    }

    private func serverResponds() async -> Bool {
        var request = URLRequest(url: pingURL)
        request.timeoutInterval = 1
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200 else { return false }
        return String(decoding: data, as: UTF8.self).contains("ig-transcript")
    }

    private func findTool(_ name: String) -> String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return ["/opt/homebrew/bin", "/usr/local/bin", "\(home)/.local/bin", "\(home)/.cargo/bin"]
            .map { "\($0)/\(name)" }
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    private func openLog() -> FileHandle? {
        let files = FileManager.default
        try? files.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let size = (try? files.attributesOfItem(atPath: logURL.path))?[.size] as? Int, size > 5_000_000 {
            try? files.removeItem(at: logURL)
        }
        if !files.fileExists(atPath: logURL.path) {
            files.createFile(atPath: logURL.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: logURL) else { return nil }
        handle.seekToEndOfFile()
        let stamp = DateFormatter()
        stamp.dateFormat = "yyyy-MM-dd HH:mm:ss"  // local time, not UTC
        handle.write(Data("\n--- \(stamp.string(from: Date())) starting the server\n".utf8))
        return handle
    }

    private func logTail() -> String {
        guard let data = try? Data(contentsOf: logURL) else { return "No log yet." }
        return String(decoding: data, as: UTF8.self)
            .split(separator: "\n", omittingEmptySubsequences: false)
            .suffix(14)
            .joined(separator: "\n")
    }

    // MARK: - Pages

    private func loadApp() {
        webView.load(URLRequest(url: homeURL))
    }

    private func showStatus(_ title: String) {
        webView.loadHTMLString(page(title: title, body: "<p id=\"detail\"></p><div class=\"progress\"><span></span></div>"), baseURL: nil)
    }

    private func showProblem(_ title: String, detail: String) {
        let button = "<button onclick=\"webkit.messageHandlers.app.postMessage('retry')\">Try again</button>"
        webView.loadHTMLString(page(title: title, body: "<pre>\(escapeHTML(detail))</pre>\(button)"), baseURL: nil)
    }

    private func setDetail(_ text: String) {
        guard let data = try? JSONEncoder().encode(text), let literal = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.setDetail && setDetail(\(literal))", completionHandler: nil)
    }

    // Status pages use the Kesterson Collage ink band, like the top of index.html.
    private func page(title: String, body: String) -> String {
        """
        <!doctype html><html><head><meta charset="utf-8">
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&family=Work+Sans:wght@400;700&display=swap">
        <style>
        html, body { height: 100%; }
        body { margin: 0; background: #141214; color: #F7F3EA; font: 15px/1.5 "Work Sans", system-ui, sans-serif; -webkit-font-smoothing: antialiased; }
        body::before { content: ""; position: fixed; inset: 0; pointer-events: none; opacity: .1; background-image: radial-gradient(rgba(247,243,234,.9) 1px, transparent 1.1px); background-size: 7px 7px; }
        .box { position: relative; max-width: 720px; padding: 40px clamp(22px, 5vw, 80px); }
        .eyebrow { font-size: 11px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; color: #ECBF42; margin: 0; }
        h1 { font: 700 38px/1 "Bricolage Grotesque", "Work Sans", sans-serif; letter-spacing: -.03em; margin: 14px 0 12px; }
        p { color: #C9C4C9; margin: 0; min-height: 1.5em; }
        .progress { position: relative; max-width: 420px; height: 16px; margin-top: 24px; border: 2px solid #F7F3EA; overflow: hidden; }
        .progress span { position: absolute; inset: 0 auto 0 0; width: 30%; background: #ECBF42; animation: slide 1.3s cubic-bezier(.45,0,.55,1) infinite alternate; }
        @keyframes slide { from { transform: translateX(0); } to { transform: translateX(234%); } }
        pre { white-space: pre-wrap; color: #B4AFB4; font: 12px/1.5 ui-monospace, Menlo, monospace; max-height: 50vh; overflow: auto; border: 2px solid #2A262A; padding: 14px 16px; margin: 20px 0 0; }
        button { margin-top: 24px; font: 700 11px/1 "Work Sans", sans-serif; letter-spacing: .1em; text-transform: uppercase; border: 0; border-radius: 0; padding: 15px 26px; background: #ECBF42; color: #141214; box-shadow: 4px 4px 0 #B7422D; cursor: pointer; }
        button:active { transform: translate(2px, 2px); box-shadow: 2px 2px 0 #B7422D; }
        @media (prefers-reduced-motion: reduce) { .progress span { animation: none; width: 100%; } }
        </style></head><body><div class="box"><p class="eyebrow">IG Transcript</p><h1>\(escapeHTML(title))</h1>\(body)</div>
        <script>function setDetail(text) { var el = document.getElementById('detail'); if (el) el.textContent = text; }</script>
        </body></html>
        """
    }

    // MARK: - Window and menus

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        for name in ["app", "save", "copy"] {
            config.userContentController.add(self, name: name)
        }
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        if #available(macOS 13.3, *) { webView.isInspectable = true }
        // The page opens on an ink band, so the transparent title bar is ink too.
        let ink = NSColor(srgbRed: 0x14 / 255, green: 0x12 / 255, blue: 0x14 / 255, alpha: 1)

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 860, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = appName
        window.titleVisibility = .hidden  // the page shows its own heading
        window.titlebarAppearsTransparent = true
        window.appearance = NSAppearance(named: .darkAqua)  // light traffic-light glyphs on ink
        window.backgroundColor = ink
        window.minSize = NSSize(width: 420, height: 480)
        window.contentView = webView
        window.center()
        window.setFrameAutosaveName("Main")
        window.makeKeyAndOrderFront(nil)
        if #available(macOS 14, *) { NSApp.activate() } else { NSApp.activate(ignoringOtherApps: true) }
    }

    private func buildMenu() {
        let bar = NSMenu()

        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About \(appName)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide \(appName)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
            .keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        addSubmenu(appMenu, to: bar)

        // Without these items, Command-V and Command-C never reach the web view.
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z").keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        addSubmenu(edit, to: bar)

        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(reloadPage), keyEquivalent: "r").target = self
        view.addItem(withTitle: "Open in Browser", action: #selector(openInBrowser), keyEquivalent: "").target = self
        view.addItem(withTitle: "Show Log", action: #selector(showLog), keyEquivalent: "").target = self
        addSubmenu(view, to: bar)

        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        addSubmenu(windowMenu, to: bar)
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = bar
    }

    private func addSubmenu(_ menu: NSMenu, to bar: NSMenu) {
        let item = NSMenuItem()
        item.submenu = menu
        bar.addItem(item)
    }

    @objc private func reloadPage() {
        if server == nil { start() } else { loadApp() }
    }

    @objc private func openInBrowser() {
        NSWorkspace.shared.open(homeURL)
    }

    @objc private func showLog() {
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        NSWorkspace.shared.open(logURL)
    }

    // MARK: - Web view

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { return decisionHandler(.cancel) }
        if url.scheme == "about" || url.host == "127.0.0.1" { return decisionHandler(.allow) }
        // Anything else opens in the default browser rather than inside the app.
        if navigationAction.navigationType == .linkActivated { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }

    // Links that ask for a new window (target="_blank") open in the default browser too.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, url.host != "127.0.0.1" { NSWorkspace.shared.open(url) }
        return nil
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        reloadPage()
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "app" {
            if message.body as? String == "retry" {
                offline = false
                start()
            }
            return
        }
        // Saving and copying answer only the app's own page.
        guard message.frameInfo.securityOrigin.host == "127.0.0.1" else { return }
        switch message.name {
        case "save":
            guard let body = message.body as? [String: Any], let text = body["text"] as? String else { return }
            save(text, suggestedName: body["filename"] as? String ?? "transcript.txt")
        case "copy":
            guard let text = message.body as? String else { return }
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
        default:
            break
        }
    }

    // A save panel instead of a silent write: the user picks the place, and macOS
    // grants access to that file without a Downloads permission prompt.
    private func save(_ text: String, suggestedName: String) {
        var name = (suggestedName as NSString).lastPathComponent
        if name.isEmpty || name.hasPrefix(".") { name = "transcript.txt" }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = name
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        panel.allowedContentTypes = [.plainText]
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            let saved = (try? text.write(to: url, atomically: true, encoding: .utf8)) != nil
            self?.webView.evaluateJavaScript("window.igSaved && igSaved(\(saved))", completionHandler: nil)
        }
    }
}

@main
enum Main {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()  // app.delegate is weak; this local keeps it alive while run() blocks
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }
}
