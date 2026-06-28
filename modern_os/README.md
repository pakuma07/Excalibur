# Modern OS Scripting 🖥️🐧🍎

End-to-end shell and automation scripting for the three operating systems a
staff/principal engineer actually runs in production — **Windows**, **Linux/Unix**,
and **macOS** — from first principles to the advanced patterns used to operate
fleets at FAANG-scale enterprises for two decades.

Scripting is the connective tissue of operations: it is how you bootstrap a host,
deploy a service, rotate a secret, triage an incident, and glue together every tool
that has no API. The difference between a junior script and a staff-level one is
**robustness** — correct quoting, error handling, idempotency, signal handling, and
the dozens of platform quirks that turn a "works on my laptop" script into a 3am
outage. This reference teaches both: the language *and* the discipline.

---

## 📁 Structure

### 1. [`windows/`](windows/README.md) — Windows Batch (cmd) + PowerShell bridge
The complete **batch / `cmd.exe`** language from scratch to expert: variables and
the two expansion phases (the infamous delayed expansion), every `for` variant,
subroutines, string surgery, `errorlevel` and robust error handling, and the
real-world enterprise toolkit (`robocopy`, `schtasks`, `sc`, `reg`, `wmic`,
deployment scripts) — plus a **PowerShell bridge** chapter on why/when to move and
how to interop. **9 docs.**

### 2. [`linux/`](linux/README.md) — Linux / Unix: Bash + Operations
Two parts. **Part I (01–10) — the Bash language:** quoting and the full
parameter-expansion arsenal, control flow, functions and arrays, I/O and process
substitution, the text-processing toolkit (`grep`/`sed`/`awk`/coreutils),
processes/signals/traps, **strict-mode** error handling, CLI argument parsing, and
the enterprise patterns (parallelism, `flock` locking, idempotency, cron/systemd,
injection-safe scripting, `shellcheck`/`bats` testing). **Part II (11–15) — Linux
operational craft:** authoring & hardening **systemd** services, **security & access
control** (permissions/ACLs/capabilities/sudo/SELinux/SSH-at-depth), **storage &
filesystems** (LVM/fstab/disk-full triage), **packaging & dynamic linking**
(`apt`/`rpm`, `ld.so`, the `GLIBC` version trap), **user-space debugging**
(`gdb`/core dumps/`/proc`), and **fleet & config management at scale** (SSH-at-scale,
**Ansible**, **immutable images**/Packer, `cloud-init`, GitOps). Internals theory
lives in [`../os_net/`](../os_net/README.md). **16 docs.**

### 3. [`mac/`](mac/README.md) — macOS
What's **different** about scripting on a Mac: the zsh-default / bash-3.2 landscape,
the **BSD-vs-GNU userland** portability traps that bite every Linux engineer,
**launchd** (not cron/systemd), the macOS system-config tooling (`defaults`,
`networksetup`, `diskutil`, `pmset`, `scutil`), **AppleScript/`osascript`/JXA**
automation, and enterprise Mac (Homebrew, MDM/profiles, codesigning/notarization,
the keychain). **6 docs.**

---

## 🎯 How to use

| Goal | Start here |
|------|-----------|
| Learn Windows batch from zero | [`windows/01_fundamentals.md`](windows/01_fundamentals.md) in order |
| Learn Bash from zero | [`linux/01_fundamentals.md`](linux/01_fundamentals.md) in order |
| Write a *robust* production script | Linux [08 — Error handling & strict mode](linux/08_error_handling_debugging.md) · Windows [06 — Error handling](windows/06_error_handling.md) |
| Port a Linux script to a Mac (and why it breaks) | [`mac/02_bsd_vs_gnu.md`](mac/02_bsd_vs_gnu.md) |
| Schedule a recurring job | Linux [10](linux/10_advanced_enterprise.md) (cron/systemd) · Windows [07](windows/07_advanced_enterprise.md) (`schtasks`) · Mac [03](mac/03_launchd_scheduling.md) (launchd) |
| Decide batch vs PowerShell | [`windows/09_powershell_bridge.md`](windows/09_powershell_bridge.md) |

---

## 🧵 The cross-platform through-lines

- **Quoting and word-splitting cause most bugs.** Batch's `%`/`!` expansion phases
  and Bash's word-splitting/globbing are different mechanisms with the *same*
  failure mode: a space or special character in input silently breaks the script
  (or becomes an injection). Every chapter treats quoting as a first-class topic.
- **Exit codes are the contract.** `errorlevel` (Windows) and `$?`/exit status
  (Unix) are how scripts compose; a script that doesn't set and check them can't be
  built upon. Error handling is covered deeply on both.
- **The userland is not portable.** A Bash script that runs on Linux often *fails
  on macOS* because the tools (`sed`, `date`, `readlink`) are BSD, not GNU — the
  single biggest surprise for Linux engineers on a Mac (mac [02](mac/02_bsd_vs_gnu.md)).
- **Idempotency and signals separate ops-grade scripts from glue.** A deploy script
  must be safe to re-run and must clean up on `Ctrl-C`/`SIGTERM`. These appear in
  the enterprise chapters of each OS.

> Staff/principal engineers are expected to write scripts that other people's
> automation depends on — correct under bad input, re-runnable, observable, and
> portable across the platforms in the fleet. That bar is what this folder targets.

> Related: [`../os_net/`](../os_net/README.md) for OS internals & networking,
> [`../system_design/`](../system_design/README.md) for architecture above.
