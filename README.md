# Fly Access

A small macOS menu bar app for your private Fly.io network.

Connect once, browse your apps, and open them when you need them. App discovery does **not** wake sleeping Machines.

```text
Disconnect
Apps (2)  >  ◐ my-api
             ☾ my-worker
───────────
Refresh
Help
Quit
```

- **Connect / Disconnect** switches the VPN and private DNS together.
- **Apps** lists every app available to your Fly login, including sleeping apps. Click to open it in your default browser.
- **Refresh** updates the connection and app list. Updates also happen automatically.
- **Help** opens this README online.
- **Quit** closes the menu; the VPN keeps running.

App state and addresses are available in tooltips. HTTP apps open through Flycast when available, otherwise their public Fly URL. Databases and services without an HTTP route open in the Fly dashboard.

## Install on macOS

Requires Python 3.10+, Homebrew, and the Xcode command line tools (`xcode-select --install`). The menu is built locally; no downloaded app binary or Python packages are needed.

```sh
brew install flyctl wireguard-tools sops age trash
fly auth login
git clone https://github.com/v-samodelkin/fly-access.git
cd fly-access
```

Create an age key if you do not already have one. Keep the private key in your password manager or another protected backup.

```sh
mkdir -p ~/.config/sops/age
chmod 700 ~/.config/sops/age
# Skip this command if keys.txt already exists.
age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt
```

Find your organization with `fly orgs list`, then prepare a device-specific peer:

```sh
python3 prepare.py --org YOUR_ORG --region ams \
  --age-recipient "$(age-keygen -y ~/.config/sops/age/keys.txt)"
```

Install the tunnel, then the menu. The first command uses the standard macOS administrator prompt in your terminal.

```sh
sudo "$(command -v python3)" macos.py install
python3 install-menu.py
```

The app is installed at `~/Applications/Fly Access.app` and starts at login. Private profiles and verified SOPS backups live outside the checkout, under `~/.config/fly-access/`, with restricted permissions. Updating the menu does not change the tunnel or credentials:

```sh
git pull --ff-only
python3 install-menu.py
```

Existing installations retain their profile directory and tunnel identity. No peer is silently replaced. Use a separate `--directory` and `--prefix` when preparing another organization or another device of the same type.

## Sleeping apps stay asleep

Discovery runs every minute and uses only `fly apps list`, `fly machine list`, and `fly ips list`. Connection status checks use the private DNS server, not your applications. There are no HTTP health probes, database connections, favicon downloads, or app previews in the background.

An app-directed request begins only when you click its menu item. Browsers and applications can keep connections open afterward, which may prevent that app from sleeping. The app list is kept in memory and refreshed from Fly; during an API outage the previous list remains marked as stale.

Flycast requires a private IP and a configured service. Private web apps normally use `http://APP.flycast` with `force_https = false`. Apps in another organization or custom network require access to that network. Fly Access does not create, restart, deploy, or reconfigure your applications.

## Network behavior

Only Fly's private IPv6 `/48` is routed through the VPN. On macOS, DNS for `.flycast` and `.internal` goes to Fly; your default route and public DNS remain unchanged. Turn off another conflicting VPN if it captures these routes or domains. Browser Secure DNS / DoH can bypass the system's private DNS settings.

Disconnect persists across reboot. Reconnect through the menu to enable automatic startup again. The menu and the tunnel are separate processes, so quitting the menu does not disconnect the network.

## Troubleshooting

```sh
python3 macos.py status
python3 fly_apps.py
```

If the app list is unavailable, check your internet connection and run `fly auth login`. The menu uses your existing Fly CLI login and never opens a login flow in the background.

For an intentional connection test, use an existing private app. Adding `--port` makes a real connection and can wake the service:

```sh
python3 macos.py check --host YOUR_APP.flycast
python3 macos.py check --host YOUR_APP.flycast --port 80
sudo "$(command -v python3)" macos.py on
```

Tunnel logs: `/Library/Application Support/FlyAccess/tunnel.log`. Older installations retain their original state directory. To remove the managed tunnel and resolvers while retaining your private backups and remote peers:

```sh
sudo "$(command -v python3)" macos.py uninstall
```

To remove the menu as well, quit it, unload its login job, then move the app and login file to Trash:

```sh
launchctl bootout "gui/$(id -u)/io.flyaccess.menu"
trash "$HOME/Applications/Fly Access.app" \
  "$HOME/Library/LaunchAgents/io.flyaccess.menu.plist"
```

## iPhone and Windows profiles

Optional profile helpers use a separate peer for each device. They route private Fly traffic through WireGuard; on these platforms Fly DNS is the system DNS while connected.

```sh
brew install qrencode
python3 prepare.py --org YOUR_ORG --region ams --devices macos iphone windows \
  --age-recipient "$(age-keygen -y ~/.config/sops/age/keys.txt)"
```

**iPhone:** install [WireGuard](https://apps.apple.com/app/wireguard/id1441195209), import `~/.config/fly-access/iphone/import.png`, and optionally enable On-Demand for Wi-Fi and cellular. The QR contains a private key: import it locally, never upload it to a public QR service.

**Windows:** install [WireGuard](https://www.wireguard.com/install/), securely transfer the Windows `flyaccess.conf`, then run from an administrator PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File windows.ps1 -Profile "$HOME\Downloads\flyaccess.conf"
```

Disable the tunnel if Fly DNS becomes unreachable and affects public name resolution. Windows and iPhone setup must be verified on the device; macOS tests do not validate their networking behavior. To revoke a lost device, explicitly remove its peer with `fly wireguard remove YOUR_ORG fly-access-PLATFORM`.

## Development

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
swiftc -swift-version 5 -typecheck -framework Cocoa FlyMenu.swift
```

Never add profiles, private keys, QR codes, decrypted files, logs, or app binaries to this repository. The tests use synthetic profiles. Peer preparation takes an explicit public age recipient and does not depend on a parent project's `.sops.yaml`.

References: [Fly private networking](https://fly.io/docs/networking/private-networking/), [Flycast](https://fly.io/docs/networking/flycast/), [SOPS](https://github.com/getsops/sops), [WireGuard](https://www.wireguard.com/).
