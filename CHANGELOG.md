# Changelog

## 1.4 — 2026-10-21

- Updates run in the background instead of Terminal. When the update finishes, the restarted menu says so. If the release changes the VPN tunnel, it offers **Reconnect**, which asks for your password the same way **Connect** does.
- Updates download the release tag from the public GitHub repository, whatever remote your checkout uses. Failures show the reason and **Show Log** (`~/Library/Logs/Fly Access/update.log`).

## 1.3 — 2026-10-21

- **Check for Updates…** in the menu. Twice a day the menu also asks the GitHub API for the latest release, and the item becomes **Update to X…** when one is available.
- The update runs in Terminal. It fast-forwards your checkout to the release tag and rebuilds the menu. It asks for your password only when the tunnel files changed and the VPN is connected.
- **Changelog** in the menu opens this page.

## 1.2 — 2026-10-21

- `.flycast` and `.internal` names resolve on Macs without a global IPv6 address. The Fly resolvers now always request AAAA records (`flags a aaaa` in `/etc/resolver`).
- `macos.py check` reports a Fly resolver that does not request AAAA records.
- The app list shows whether each app is running or sleeping, and when it was last updated.

## 1.1 — withdrawn

Replaced by 1.2.

## 1.0 — 2026-09-26

- First public release: WireGuard tunnel with split DNS for `.flycast` and `.internal`, and a menu bar app that lists your Fly apps and opens them.
