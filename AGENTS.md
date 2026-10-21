# Fly Access

- Keep all UI, documentation, and errors in English. Keep the menu small.
- App discovery may only read Fly management metadata. Never probe an app URL, port, health endpoint, or favicon in the background.
- The hosts job may only query Fly private DNS and edit its own marked block in `/etc/hosts`. Keep `hosts.py` self-contained and compatible with `/usr/bin/python3`.
- Open app URLs only in response to a user click. Preserve sleeping Machines.
- Help opens the public GitHub README, never a local file.
- Keep private profiles, keys, QR codes, backups, logs, and app binaries outside Git.
- Preserve the active tunnel and profile directory when updating the menu. Keep legacy installation detection in `layout.py`.
- Use Trash for removals. Never overwrite unmanaged resolvers or tunnel state.
- Run Python tests and Swift type checking before publishing changes. Test network setup separately on each supported platform.
