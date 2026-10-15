import Cocoa
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var backendProcess: Process?
    let port = 8765

    func startBackendIfNeeded() {
        let url = URL(string: "http://127.0.0.1:\(port)/api/health")!
        var request = URLRequest(url: url)
        request.timeoutInterval = 0.4

        let semaphore = DispatchSemaphore(value: 0)
        var isRunning = false

        let task = URLSession.shared.dataTask(with: request) { _, response, _ in
            if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 {
                isRunning = true
            }
            semaphore.signal()
        }
        task.resume()
        _ = semaphore.wait(timeout: .now() + 0.5)

        if !isRunning {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            proc.arguments = ["/Users/tancao/atomic-habit-tracker/server.py"]
            proc.currentDirectoryURL = URL(fileURLWithPath: "/Users/tancao/atomic-habit-tracker")

            do {
                try proc.run()
                self.backendProcess = proc
                Thread.sleep(forTimeInterval: 0.45)
            } catch {
                print("Failed to start local server: \(error)")
            }
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        startBackendIfNeeded()

        let rect = NSRect(x: 0, y: 0, width: 1280, height: 840)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.center()
        window.title = "Atomic Habit & Streak Tracker — Identity OS"
        window.titlebarAppearsTransparent = true
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.backgroundColor = NSColor(red: 11/255.0, green: 15/255.0, blue: 25/255.0, alpha: 1.0)

        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.setValue(false, forKey: "drawsBackground")

        window.contentView?.addSubview(webView)

        let appUrl = URL(string: "http://127.0.0.1:\(port)")!
        webView.load(URLRequest(url: appUrl))

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        backendProcess?.terminate()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
