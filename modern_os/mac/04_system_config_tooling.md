# 04 — System Config & Automation Tooling

> **Audience:** Linux engineers automating a Mac. You know `sysctl`, `nmcli`, `lsblk`, `systemd`, and dotfiles. macOS replaces most of that with a handful of Apple-specific CLIs that read and write a *preferences database* rather than text files in `/etc`. This chapter maps each tool, shows real output, and flags the gotchas that bite people coming from Linux — chiefly that editing config files directly often does nothing because a daemon owns the cache.

---

## 1. The mental model: there is no `/etc`

On Linux, config is text files; you edit them and reload a service. On macOS, most app and system settings live in **plists** (property lists, XML or binary) managed by **`cfprefsd`**, a per-user daemon that caches everything in memory. You do **not** edit these files by hand — you go through `defaults`, and `cfprefsd` writes them out when it feels like it.

| Tool | What it configures |
|---|---|
| `defaults` | App & system preferences (the plist database) |
| `sw_vers` / `sysctl` / `system_profiler` | Read-only OS & hardware facts |
| `networksetup` | Network services, DNS, Wi-Fi, proxies |
| `scutil` | Hostnames, DNS/proxy state, system config store |
| `diskutil` | Disks, volumes, APFS containers |
| `pmset` | Power management, sleep, wake schedules |
| `softwareupdate` | OS & system software updates, Rosetta |
| `caffeinate` | Prevent sleep around a long-running command |
| `osascript` | Run AppleScript/JXA (see [05 — AppleScript, osascript & JXA](05_applescript_jxa.md)) |

---

## 2. `defaults` — the preferences database

`defaults` is the closest thing to "edit a config file." Settings are organized by **domain** (usually a reverse-DNS bundle id like `com.apple.dock`).

```bash
# Read an entire domain (dumps the plist as text)
defaults read com.apple.dock
# {
#     autohide = 0;
#     orientation = bottom;
#     tilesize = 48;
#     ...
# }

# Read a single key
defaults read com.apple.dock tilesize
# 48

# List every domain that has preferences
defaults domains | tr ',' '\n' | head
# com.apple.AppleMultitouchTrackpad
# com.apple.dock
# com.apple.finder
# ...
```

### Writing — types matter

Always pass an explicit type, or `defaults` guesses (and often guesses "string").

```bash
defaults write com.apple.dock autohide -bool true       # boolean
defaults write com.apple.dock tilesize -int 36          # integer
defaults write com.apple.dock autohide-time-modifier -float 0.4
defaults write com.apple.screencapture location -string "$HOME/Screenshots"
defaults write com.apple.dock persistent-others -array     # empty array
defaults write com.example.app Window -dict x 100 y 200    # dictionary
```

### Deleting

```bash
defaults delete com.apple.dock tilesize     # one key (reverts to default)
defaults delete com.apple.dock              # the whole domain — careful
```

### Where they live

```bash
ls ~/Library/Preferences/com.apple.dock.plist
# Per-user prefs (most common)

ls /Library/Preferences/                    # system-wide (often needs sudo)
defaults read /Library/Preferences/com.apple.SoftwareUpdate AutomaticCheckEnabled
```

System-wide domains use the `-currentHost` flag or an absolute path; the `-g` / `NSGlobalDomain` domain holds global settings (e.g. key repeat).

```bash
defaults write -g InitialKeyRepeat -int 15
defaults write NSGlobalDomain KeyRepeat -int 2
```

### Gotcha 1 — `killall` to apply

Writing a pref does **not** restart the app reading it. The Dock, Finder, and SystemUIServer cache their settings and must be HUP'd:

```bash
defaults write com.apple.dock autohide -bool true
killall Dock        # Dock relaunches and picks up the change
killall Finder      # for com.apple.finder changes
killall SystemUIServer
```

- **Symptom:** "I set `autohide` but nothing happened."
- **Cause:** The Dock is still running with old settings in memory.
- **Fix:** `killall Dock` (it auto-relaunches; you don't log out).

### Gotcha 2 — `cfprefsd` caching

If the *target app is running*, your `defaults write` may be silently overwritten when that app flushes its own in-memory copy. Reading right after writing can also return stale data.

- **Symptom:** Change reverts on next launch, or `defaults read` shows the old value.
- **Cause:** `cfprefsd` (and the owning app) cache the plist; direct writes race with the daemon.
- **Fix:** Quit the owning app *before* writing, then `killall cfprefsd` to drop the cache if needed. Never hand-edit the `.plist` file under a running app — your edit will be clobbered.

```bash
osascript -e 'quit app "Finder"'   # or just target apps that aren't running
defaults write com.apple.finder AppleShowAllFiles -bool true
killall cfprefsd                    # nuke the cache (rarely needed)
open -a Finder
```

> **Tip:** Binary plists aren't grep-friendly. Convert with `plutil -convert xml1 -o - file.plist` to read them, or just use `defaults read`.

---

## 3. OS & hardware facts (read-only)

```bash
sw_vers
# ProductName:    macOS
# ProductVersion: 14.5
# BuildVersion:   23F79

sw_vers -productVersion     # 14.5  — script-friendly single value
sw_vers -buildVersion       # 23F79

uname -m                    # arm64  (Apple Silicon) or x86_64 (Intel)
```

`sysctl` exists like on Linux but the keys are different (`hw.*`, `machdep.cpu.*`):

```bash
sysctl -n hw.memsize                 # 17179869184  (bytes of RAM)
sysctl -n hw.ncpu                    # 10
sysctl -n machdep.cpu.brand_string   # Apple M1 Pro
sysctl -n hw.model                   # MacBookPro18,3
```

`system_profiler` is the heavyweight inventory tool — slow, but exhaustive, and supports JSON:

```bash
system_profiler SPHardwareDataType
#       Model Name: MacBook Pro
#       Chip: Apple M1 Pro
#       Total Number of Cores: 10
#       Memory: 16 GB
#       Serial Number: C02XXXXX

# JSON is parseable with jq — list available data types with -listDataTypes
system_profiler -json SPHardwareDataType | jq -r '.SPHardwareDataType[0].serial_number'
```

> **Tip:** For fast scripted facts use `sysctl`/`sw_vers`; reach for `system_profiler` only when you need serial numbers, GPU, or storage detail. Detecting Apple Silicon vs Intel for conditional installs is `[ "$(uname -m)" = arm64 ]`.

---

## 4. `networksetup` — network configuration

Networks are organized as **services** (e.g. "Wi-Fi", "Ethernet"). Hardware ports map to BSD device names.

```bash
networksetup -listallnetworkservices
# Wi-Fi
# Thunderbolt Bridge

networksetup -listallhardwareports
# Hardware Port: Wi-Fi
# Device: en0

# DNS
networksetup -getdnsservers Wi-Fi
networksetup -setdnsservers Wi-Fi 1.1.1.1 8.8.8.8     # set; use "Empty" to clear

# Wi-Fi power (device, not service name)
networksetup -setairportpower en0 off
networksetup -setairportpower en0 on

# Proxies
networksetup -setwebproxy Wi-Fi proxy.corp 8080
networksetup -setwebproxystate Wi-Fi on
networksetup -getwebproxy Wi-Fi
```

Most write operations require **sudo** (see §10). Reads generally do not.

---

## 5. `diskutil` — disks & volumes

```bash
diskutil list
# /dev/disk0 (internal):
#    #:  TYPE NAME              SIZE       IDENTIFIER
#    0:  GUID_partition_scheme  994.7 GB   disk0
#    1:  Apple_APFS Container    994.2 GB   disk0s2

diskutil info disk0s2          # detailed info for one identifier
diskutil mount disk4s1         # mount a volume
diskutil unmount /Volumes/USB  # unmount

# APFS subcommands
diskutil apfs list
diskutil apfs listVolumeGroups
```

> **Warning — destructive:** `diskutil eraseDisk`, `diskutil apfs deleteContainer`, and `diskutil partitionDisk` **wipe data with no undo and no confirmation in scripts**. Always pin the exact `diskNsM` identifier (they can renumber between boots) and double-check with `diskutil info` first. Never feed a disk identifier from an unvalidated variable into an erase command.

---

## 6. `pmset` — power management

```bash
pmset -g                       # current settings
# System-wide power settings:
#  sleep                21
#  displaysleep         10
#  hibernatemode        3

pmset -g batt                  # battery state
# Now drawing from 'Battery Power'
#  -InternalBattery-0 (id=...)   84%; discharging; 4:12 remaining

# Change settings (needs sudo). -a all, -b battery, -c charger
sudo pmset -a displaysleep 5           # display sleeps after 5 min
sudo pmset -c sleep 0                  # never sleep while on charger
sudo pmset -b disablesleep 0

# Schedule a wake
sudo pmset repeat wakeorpoweron MTWRF 08:30:00
pmset -g sched                         # show scheduled events
```

> **Tip:** For a *transient* "don't sleep right now" need, don't change `pmset` — use `caffeinate` (§9) so settings revert automatically.

---

## 7. `scutil` — hostnames & system config store

macOS has **three** names; set all three for consistency.

```bash
scutil --get ComputerName        # friendly name shown in Sharing / AirDrop
scutil --get HostName            # the actual hostname (FQDN-ish)
scutil --get LocalHostName       # Bonjour name (.local), no spaces

sudo scutil --set ComputerName  "build-mac-07"
sudo scutil --set HostName      "build-mac-07.corp.example.com"
sudo scutil --set LocalHostName "build-mac-07"

# Live DNS and proxy configuration (read-only view of the SC store)
scutil --dns | head
scutil --proxy
```

Setting names requires **sudo**. `scutil --dns`/`--proxy` reflect the *resolved* runtime config (what the system actually uses), which is more authoritative than `/etc/resolv.conf` — that file is generated and should not be edited.

---

## 8. `softwareupdate` — OS & system updates

```bash
softwareupdate -l                      # --list available updates
# * Label: macOS Sonoma 14.5-23F79
#    Title: macOS Sonoma 14.5, Size: 1234567K

sudo softwareupdate -i -a              # --install --all
sudo softwareupdate -i "macOS Sonoma 14.5-23F79"   # install one label
sudo softwareupdate -i -a --restart    # install and reboot

# Install Rosetta 2 non-interactively (Apple Silicon, to run x86_64 binaries)
softwareupdate --install-rosetta --agree-to-license
```

- **MDM caveat:** On managed Macs, OS updates are frequently deferred or controlled by the MDM server; `softwareupdate -i -a` may report "no updates" even when Apple has released one. See [06 — Enterprise Mac](06_enterprise_mac.md).

---

## 9. `caffeinate` — keep the Mac awake

The CI engineer's best friend. Wraps a command and prevents idle sleep until it exits.

```bash
caffeinate -i ./long_build.sh          # -i: prevent idle sleep for this command
caffeinate -dimsu ./run_tests.sh       # also prevent display sleep, system sleep
caffeinate -t 3600                      # keep awake for 3600s, then exit
caffeinate -w 12345                     # stay awake until PID 12345 exits
```

- `-i` idle, `-d` display, `-m` disk, `-s` system (on AC), `-u` declare user active.
- No sudo required. Unlike editing `pmset`, settings need no cleanup — sleep behavior returns to normal the instant the command finishes.

---

## 10. `osascript` quick mention

For a one-line desktop notification from a script (full coverage in [05 — AppleScript, osascript & JXA](05_applescript_jxa.md)):

```bash
osascript -e 'display notification "Build finished" with title "CI"'
```

---

## 11. sudo, TCC & MDM — what needs elevated rights

| Operation | sudo? | TCC / MDM risk |
|---|---|---|
| `defaults read`, `sw_vers`, `sysctl`, `pmset -g`, `scutil --get` | No | None |
| `defaults write` (user domain) | No | None |
| `defaults write` (`/Library/...` system domain) | Yes | None |
| `networksetup -set*`, `scutil --set`, `pmset -a` | Yes | MDM may lock these |
| `softwareupdate -i` | Yes | MDM often controls/blocks |
| `diskutil erase*` | Yes | — (destructive) |
| Scripting Finder/System Events via `osascript` | No | **TCC: Automation prompt** |
| Screen recording / Accessibility-driven automation | No | **TCC: must be pre-approved** |

- **TCC** (Transparency, Consent & Control) is macOS's per-app privacy layer. The first time a script tries to control another app or read protected data, the user gets a **prompt**; in headless CI there is no one to click "Allow," so the action silently fails. Pre-grant via MDM PPPC profiles — see [06 — Enterprise Mac](06_enterprise_mac.md).
- **MDM** can lock configuration domains so even `sudo` writes are reverted on the next policy check. If a `networksetup` or `pmset` change "doesn't stick," check for a managed profile (`profiles show -type configuration`).

---

## 12. Worked example — provision a Mac's defaults

A small idempotent script tying several tools together. Run on a fresh machine.

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Provisioning $(scutil --get LocalHostName 2>/dev/null || echo unknown) "\
     "on macOS $(sw_vers -productVersion) ($(uname -m))"

# --- Identity ---
sudo scutil --set ComputerName  "build-mac-07"
sudo scutil --set HostName      "build-mac-07.corp.example.com"
sudo scutil --set LocalHostName "build-mac-07"

# --- Rosetta on Apple Silicon (so x86_64 toolchains run) ---
if [ "$(uname -m)" = "arm64" ]; then
  softwareupdate --install-rosetta --agree-to-license
fi

# --- Dock: autohide, small tiles, no recents ---
defaults write com.apple.dock autohide -bool true
defaults write com.apple.dock tilesize -int 36
defaults write com.apple.dock show-recents -bool false
killall Dock

# --- Finder: show all files, status bar, path bar ---
defaults write com.apple.finder AppleShowAllFiles -bool true
defaults write com.apple.finder ShowStatusBar -bool true
defaults write com.apple.finder ShowPathbar -bool true
killall Finder

# --- Fast keyboard repeat ---
defaults write -g InitialKeyRepeat -int 15
defaults write -g KeyRepeat -int 2

# --- Network: corp DNS ---
sudo networksetup -setdnsservers Wi-Fi 10.0.0.53 1.1.1.1

# --- Power: build machines never sleep on AC ---
sudo pmset -c sleep 0 displaysleep 30

echo "Done. Some changes (keyboard repeat) apply on next login."
```

- **Idempotent?** `defaults write` and `scutil --set` are naturally idempotent — re-running sets the same value. Good for re-applying with config-management tools.
- **Headless note:** This script avoids any `osascript` app control, so it triggers no TCC prompts and runs cleanly over SSH. Pair it with a launchd job from [03 — launchd & Scheduling](03_launchd_scheduling.md) to run on first boot.

---

> Next: [05 — AppleScript, osascript & JXA](05_applescript_jxa.md) — when `defaults` and CLI tools run out of road, you script the GUI itself: AppleScript fundamentals, the `osascript` bridge, JavaScript for Automation, and surviving the TCC Automation prompts this chapter warned you about.
