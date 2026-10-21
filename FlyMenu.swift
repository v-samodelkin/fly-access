import Cocoa

struct VPNStatus: Decodable {
    let state: String
    let title: String
    let detail: String
    let action: String
    let autostart: Bool
}

struct FlyApp: Decodable {
    let name: String
    let organization: String
    let url: String
    let status: String
    let statusLabel: String
    let detail: String
}

struct FlyAppsResult: Decodable {
    let ok: Bool
    let apps: [FlyApp]
    let partial: Int
    let error: String?
}

struct UpdateResult: Decodable {
    let ok: Bool
    let tag: String?
    let available: Bool?
    let error: String?
}

final class FlyMenu: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var item: NSStatusItem!
    private let menu = NSMenu()
    private let appsMenu = NSMenu(title: "Apps")
    private let appsItem = NSMenuItem(title: "Apps", action: nil, keyEquivalent: "")
    private let toggleItem = NSMenuItem(title: "Checking...", action: #selector(toggle), keyEquivalent: "")
    private var timers: [Timer] = []
    private var refreshing = false
    private var refreshingApps = false
    private var changing = false
    private var nextAction = "on"
    private var apps: [FlyApp] = []
    private var lastAppsAttempt = Date.distantPast
    private var hasAppList = false
    private var lastAppsSuccess: Date?
    private var appsRefreshError: String?
    private var partialAppList = false
    private let appsFreshnessItem = NSMenuItem(title: "Not updated yet", action: nil, keyEquivalent: "")
    private let updateItem = NSMenuItem(title: "Check for Updates…", action: #selector(updateClicked), keyEquivalent: "")
    private var availableTag: String?
    private var checkingUpdates = false
    private var nextUpdateCheck = Date.distantPast
    private let python = Bundle.main.object(forInfoDictionaryKey: "PythonPath") as! String
    private let fly = Bundle.main.object(forInfoDictionaryKey: "FlyPath") as! String
    private let directory = Bundle.main.object(forInfoDictionaryKey: "ProfileDirectory") as! String
    private let source = Bundle.main.object(forInfoDictionaryKey: "SourceDirectory") as? String
    private let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    private let helpURL = URL(string: "https://github.com/v-samodelkin/fly-access#readme")!
    private let changelogURL = URL(string: "https://github.com/v-samodelkin/fly-access/blob/main/CHANGELOG.md")!
    private func resource(_ name: String) -> String { Bundle.main.resourceURL!.appendingPathComponent(name).path }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.autosaveName = "FlyAccess"
        setIcon(connected: false)
        item.button?.setAccessibilityLabel("Fly Access")
        menu.autoenablesItems = false
        appsMenu.autoenablesItems = false
        menu.delegate = self
        appsMenu.delegate = self
        toggleItem.target = self
        toggleItem.isEnabled = false
        menu.addItem(toggleItem)
        appsItem.submenu = appsMenu
        menu.addItem(appsItem)
        menu.addItem(.separator())
        add("Refresh", #selector(refreshAll))
        updateItem.target = self
        menu.addItem(updateItem)
        add("Changelog", #selector(changelog))
        add("Help", #selector(help))
        add("Quit", #selector(quit))
        renderApps()
        item.menu = menu
        for (seconds, selector) in [(15.0, #selector(refresh)), (60.0, #selector(loadApps)),
                                    (1.0, #selector(updateAppsFreshness)), (900.0, #selector(checkForUpdatesWhenDue))] {
            let timer = Timer(timeInterval: seconds, target: self, selector: selector,
                              userInfo: nil, repeats: true)
            RunLoop.main.add(timer, forMode: .common)
            timers.append(timer)
        }
        refreshAll()
        checkForUpdatesWhenDue()
    }

    @discardableResult private func add(_ title: String, _ action: Selector) -> NSMenuItem {
        let row = NSMenuItem(title: title, action: action, keyEquivalent: "")
        row.target = self
        menu.addItem(row)
        return row
    }

    func menuWillOpen(_ openedMenu: NSMenu) {
        updateAppsFreshness()
        if openedMenu === menu { refresh() }
        if Date().timeIntervalSince(lastAppsAttempt) >= 60 { loadApps() }
        checkForUpdatesWhenDue()
    }

    // Asks GitHub for the latest release twice a day; a failed check retries after an hour.
    @objc private func checkForUpdatesWhenDue() {
        if Date() >= nextUpdateCheck { checkForUpdates(manual: false) }
    }

    private func checkForUpdates(manual: Bool) {
        guard !checkingUpdates else { return }
        checkingUpdates = true
        if manual {
            updateItem.title = "Checking for Updates…"
            updateItem.isEnabled = false
        }
        DispatchQueue.global(qos: .utility).async {
            let result = self.run(self.python, ["-B", self.resource("update.py"), "check", "--current", self.version])
            let payload = try? JSONDecoder().decode(UpdateResult.self, from: result.1)
            DispatchQueue.main.async {
                self.checkingUpdates = false
                self.updateItem.isEnabled = true
                let checked = payload?.ok == true
                self.nextUpdateCheck = Date().addingTimeInterval(checked ? 43200 : 3600)
                if checked { self.availableTag = payload?.available == true ? payload?.tag : nil }
                self.updateItem.title = self.availableTag.map { "Update to \($0.dropFirst())…" } ?? "Check for Updates…"
                guard manual else { return }
                if let tag = self.availableTag {
                    self.offerUpdate(tag)
                } else if checked {
                    self.inform("Fly Access \(self.version) is up to date", "")
                } else {
                    self.inform("Unable to check for updates", (payload?.error ?? "Check your internet connection") + ".")
                }
            }
        }
    }

    @objc private func updateClicked() {
        if let tag = availableTag { offerUpdate(tag) } else { checkForUpdates(manual: true) }
    }

    private func offerUpdate(_ tag: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Fly Access \(tag.dropFirst()) is available"
        alert.informativeText = "You have \(version). The update runs in Terminal: it fast-forwards your fly-access checkout to \(tag), rebuilds the menu, and asks for your password only if the tunnel changed."
        alert.addButton(withTitle: "Update")
        alert.addButton(withTitle: "View Changelog")
        alert.addButton(withTitle: "Later")
        switch alert.runModal() {
        case .alertFirstButtonReturn: startUpdate(tag)
        case .alertSecondButtonReturn: changelog()
        default: break
        }
    }

    // Terminal runs the update outside this app, so rebuilding and restarting the menu cannot cut it short.
    private func startUpdate(_ tag: String) {
        guard let source = source, FileManager.default.fileExists(atPath: source + "/update.py") else {
            inform("Unable to find your fly-access checkout",
                   "Run git pull --ff-only and python3 install-menu.py in the checkout. See Help.")
            return
        }
        let script = "#!/bin/sh\ncd \(shellQuote(source)) || exit 1\nexec \(shellQuote(python)) -B update.py apply \(shellQuote(tag)) --directory \(shellQuote(directory))\n"
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("Fly Access Update.command")
        do {
            try script.write(to: file, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        } catch {
            inform("Unable to start the update", error.localizedDescription)
            return
        }
        NSWorkspace.shared.open(file)
    }

    private func inform(_ title: String, _ text: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = text
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    @objc private func refreshAll() { refresh(); loadApps() }

    @objc private func loadApps() {
        guard !refreshingApps else { return }
        refreshingApps = true
        lastAppsAttempt = Date()
        updateAppsFreshness()
        DispatchQueue.global(qos: .utility).async {
            let result = self.run(self.python, ["-B", self.resource("fly_apps.py"), "--fly", self.fly])
            let payload = try? JSONDecoder().decode(FlyAppsResult.self, from: result.1)
            DispatchQueue.main.async {
                self.refreshingApps = false
                if let payload = payload, payload.ok, result.0 == 0 {
                    self.apps = payload.apps
                    self.hasAppList = true
                    self.lastAppsSuccess = Date()
                    self.partialAppList = payload.partial > 0
                    self.appsRefreshError = nil
                } else {
                    self.appsRefreshError = payload?.error ?? "Unable to refresh apps"
                }
                self.renderApps()
            }
        }
    }

    private func renderApps() {
        appsItem.title = apps.isEmpty ? "Apps" : "Apps (\(apps.count))"
        appsMenu.removeAllItems()
        if apps.isEmpty && hasAppList {
            let row = NSMenuItem(title: "No apps yet", action: nil, keyEquivalent: "")
            row.isEnabled = false
            appsMenu.addItem(row)
        }
        for app in apps {
            let row = NSMenuItem(title: "\(app.name) — \(app.statusLabel)",
                                 action: #selector(openApp(_:)), keyEquivalent: "")
            row.target = self
            row.representedObject = app.url
            appsMenu.addItem(row)
        }
        if appsMenu.numberOfItems > 0 { appsMenu.addItem(.separator()) }
        appsFreshnessItem.isEnabled = false
        appsMenu.addItem(appsFreshnessItem)
        updateAppsFreshness()
    }

    // This timer changes text only. Fly requests still run once a minute.
    @objc private func updateAppsFreshness() {
        guard let updated = lastAppsSuccess else {
            appsFreshnessItem.title = refreshingApps ? "Updating..." :
                appsRefreshError == nil ? "Not updated yet" : "Update failed"
            return
        }
        let seconds = max(0, Int(Date().timeIntervalSince(updated)))
        let age: String
        if seconds < 5 { age = "just now" }
        else if seconds < 60 { age = "\(seconds)s ago" }
        else if seconds < 3600 { age = "\(seconds / 60)m ago" }
        else if seconds < 86400 { age = "\(seconds / 3600)h ago" }
        else { age = "\(seconds / 86400)d ago" }
        if refreshingApps {
            appsFreshnessItem.title = "Updating · last updated \(age)"
        } else if appsRefreshError != nil || seconds >= 120 {
            appsFreshnessItem.title = "Stale · updated \(age)"
        } else if partialAppList {
            appsFreshnessItem.title = "Partial · updated \(age)"
        } else {
            appsFreshnessItem.title = "Updated \(age)"
        }

    }

    @objc private func openApp(_ sender: NSMenuItem) {
        guard let address = sender.representedObject as? String, let url = URL(string: address),
              ["http", "https"].contains(url.scheme ?? "") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func refresh() {
        guard !refreshing && !changing else { return }
        refreshing = true
        DispatchQueue.global(qos: .utility).async {
            let result = self.run(self.python, ["-B", self.resource("macos.py"), "status", "--directory", self.directory])
            let status = try? JSONDecoder().decode(VPNStatus.self, from: result.1)
            DispatchQueue.main.async {
                self.refreshing = false
                guard !self.changing else { return }
                if let status = status, result.0 == 0 {
                    self.nextAction = status.action
                    self.setIcon(connected: status.state == "on")
                    self.toggleItem.title = status.action == "off" ? "Disconnect" : "Connect"
                } else {
                    self.setIcon(connected: false)
                    self.toggleItem.title = "Connect"
                    self.nextAction = "on"
                }
                self.toggleItem.isEnabled = true
            }
        }
    }

    private func run(_ executable: String, _ arguments: [String]) -> (Int32, Data, String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let output = Pipe(), errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        do { try process.run() } catch { return (1, Data(), error.localizedDescription) }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        let errorData = errors.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return (process.terminationStatus, data, String(data: errorData, encoding: .utf8) ?? "")
    }

    private func setIcon(connected: Bool) {
        let symbol = connected ? "shield.lefthalf.filled" : "shield"
        guard let glyph = NSImage(systemSymbolName: symbol, accessibilityDescription: "Fly VPN")?
            .withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: 16, weight: .regular)) else { return }
        // SF Symbols carry font baseline insets. Flatten those into a centered
        // template so NSStatusBarButton aligns the shield as an icon, not text.
        let canvas = NSSize(width: 18, height: 18)
        let icon = NSImage(size: canvas, flipped: false) { rect in
            let scale = min(rect.width / glyph.size.width, rect.height / glyph.size.height)
            let size = NSSize(width: glyph.size.width * scale, height: glyph.size.height * scale)
            let target = NSRect(x: rect.midX - size.width / 2, y: rect.midY - size.height / 2,
                                width: size.width, height: size.height)
            glyph.draw(in: target, from: .zero, operation: .sourceOver, fraction: 1)
            return true
        }
        icon.alignmentRect = NSRect(origin: .zero, size: canvas)
        icon.isTemplate = true
        item.button?.title = ""
        item.button?.imagePosition = .imageOnly
        item.button?.imageScaling = .scaleNone
        item.button?.image = icon
    }

    private func shellQuote(_ text: String) -> String { "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'" }
    private func appleQuote(_ text: String) -> String {
        "\"" + text.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    private func change(_ action: String) {
        guard !changing else { return }
        changing = true
        toggleItem.isEnabled = false
        toggleItem.title = action == "on" ? "Connecting..." : "Disconnecting..."
        let command = [python, "-B", resource("macos.py"), action, "--directory", directory].map(shellQuote).joined(separator: " ")
        let script = "do shell script " + appleQuote(command) + " with administrator privileges"
        DispatchQueue.global(qos: .userInitiated).async {
            let result = self.run("/usr/bin/osascript", ["-e", script])
            DispatchQueue.main.async {
                self.changing = false
                if result.0 != 0 && !result.2.contains("(-128)") {
                    self.inform("Connection failed", "See Help for setup and troubleshooting.")
                }
                self.refresh()
            }
        }
    }

    @objc private func toggle() { change(nextAction) }
    @objc private func help() { NSWorkspace.shared.open(helpURL) }
    @objc private func changelog() { NSWorkspace.shared.open(changelogURL) }
    @objc private func quit() { NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = FlyMenu()
app.delegate = delegate
app.run()
