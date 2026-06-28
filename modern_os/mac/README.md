# macOS Scripting — what's different 🍎

> **Audience:** engineers who already script on Linux ([`../linux/`](../linux/README.md))
> and now have to automate a Mac — a developer laptop, a CI build agent (iOS/macOS
> builds *must* run on Apple hardware), or an MDM-managed fleet. macOS is a certified
> **Unix** (it passes the Single UNIX Specification), so most Bash transfers — but
> the parts that *don't* cause the majority of "works on Linux, breaks on the Mac"
> incidents. This series focuses on the **differences and the Mac-specific tooling**,
> not on re-teaching Bash.

The two things that surprise every Linux engineer: (1) macOS ships a **BSD
userland**, so `sed`, `date`, `readlink`, etc. behave differently from GNU; and
(2) macOS uses **`launchd`**, not cron/systemd, and a pile of Apple-specific
config tools (`defaults`, `networksetup`, `diskutil`).

---

## 📚 Chapters

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [The macOS Shell Landscape](01_shell_landscape.md) | **zsh** default (since Catalina), the ancient **bash 3.2**, `sh`, SIP & read-only system volume, the PATH/`path_helper` story |
| 02 | [BSD vs GNU Userland](02_bsd_vs_gnu.md) | the portability traps: `sed -i`, `date`, `readlink`/`realpath`, `grep`, `stat`, `xargs`, `base64` — and how to write **portable** scripts (or install `coreutils`) |
| 03 | [launchd & Scheduling](03_launchd_scheduling.md) | **launchd** vs cron/systemd, `.plist` agents & daemons, `launchctl`, `StartCalendarInterval`/`StartInterval`/`WatchPaths`, logging |
| 04 | [System Config & Automation Tooling](04_system_config_tooling.md) | `defaults` (the preferences DB), `networksetup`, `diskutil`, `pmset`, `scutil`, `sw_vers`, `system_profiler`, `softwareupdate`, `caffeinate` |
| 05 | [AppleScript, osascript & JXA](05_applescript_jxa.md) | GUI/app automation: AppleScript, driving it from shell via `osascript`, JavaScript for Automation (JXA), `open`, UI scripting & the permissions model |
| 06 | [Enterprise Mac](06_enterprise_mac.md) | Homebrew & environments, MDM/configuration profiles, **codesigning & notarization**, the **keychain**/`security`, Gatekeeper/quarantine, TCC privacy |

---

## 🚀 The first things to know

```bash
sw_vers                      # macOS version (ProductVersion / BuildVersion)
echo $SHELL                  # /bin/zsh on modern macOS (was /bin/bash pre-Catalina)
bash --version               # 3.2.57 — ANCIENT (GPLv2; Apple won't ship GPLv3 bash)
```

- **Default shell is zsh** since macOS Catalina (10.15, 2019). Login scripts and
  interactive config use zsh; but `#!/bin/bash` still works (at bash 3.2) and
  `#!/bin/sh` runs as a POSIX shell. For modern Bash, **`brew install bash`** and use
  `#!/usr/bin/env bash` ([01](01_shell_landscape.md)).
- **The system volume is read-only (SIP).** You cannot scribble in `/usr`, `/System`,
  etc.; user-installed tools live under `/usr/local` or `/opt/homebrew` ([01](01_shell_landscape.md)).

---

## 🎯 The Mac gotchas that bite Linux engineers

1. **BSD tools ≠ GNU tools.** `sed -i` **requires an argument** on macOS
   (`sed -i '' 's/a/b/' f`); `date -d` doesn't exist (`date -v`); `readlink -f`
   isn't there. Your Linux script *will* break — [02](02_bsd_vs_gnu.md) is the most
   important chapter here.
2. **bash is 3.2** — no associative arrays, no `${v^^}`, no `mapfile`. Either target
   POSIX/bash-3.2 or install modern bash via Homebrew ([01](01_shell_landscape.md)).
3. **No cron culture; use launchd.** `crontab` still works but Apple's way is
   `launchd` agents/daemons, with richer triggers ([03](03_launchd_scheduling.md)).
4. **Permissions/privacy (TCC) block automation.** A script that controls apps,
   reads the disk, or sends keystrokes needs the user to grant Automation/Full Disk
   Access; silent failures are usually TCC ([05](05_applescript_jxa.md), [06](06_enterprise_mac.md)).
5. **Code must be signed/notarized to run elsewhere.** Gatekeeper quarantines
   downloaded binaries/scripts; distributing a tool means codesign + notarize
   ([06](06_enterprise_mac.md)).

> **Portability rule:** if a script must run on both Linux and macOS, either restrict
> yourself to POSIX + portable tool flags, or `brew install coreutils gnu-sed
> gawk findutils` and use the `g`-prefixed GNU tools. [02](02_bsd_vs_gnu.md) shows
> both approaches.

> Related: [`../linux/`](../linux/README.md) (the Bash language this builds on),
> [`../windows/`](../windows/README.md), [`../../os_net/`](../../os_net/README.md).
