# Linux / Unix — Bash Scripting & Operations — from scratch to expert 🐧

> **Audience:** from zero to staff/principal. **Bash** is the lingua franca of
> operations: it bootstraps hosts, drives CI/CD, wraps every tool, and is the first
> thing you write in an incident. Reading and writing robust Bash is a non-negotiable
> staff-level skill — and "robust" is the hard part. This series teaches the full
> language *and* the Unix philosophy and discipline around it (quoting, exit codes,
> idempotency, signals, injection-safety) that separate a production script from
> glue that fails on the first weird input.

The Unix philosophy: **small composable tools, text as the universal interface,
each program does one thing well, connected by pipes.** Bash is the glue; `grep`,
`sed`, `awk`, and coreutils are the power tools. You need both.

This folder has **two parts**: chapters **01–10** are the **Bash language** end to
end; chapters **11–15** are the **Linux operational craft** a principal actually
runs on a host every day — authoring systemd services, securing access, managing
storage, shipping binaries through dynamic-linking hell, and debugging a crashed
process. For the *internals theory* beneath all of this — schedulers, the memory
subsystem, the network stack, container mechanics, the observability toolset, and
~40 incident runbooks — see the sibling [`../../os_net/`](../../os_net/README.md)
reference; this folder stays at the command line, and links there for the "why."

---

## 📚 Chapters

### Part I — The Bash language (01–10)

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [Fundamentals](01_fundamentals.md) | shells & the shebang, `sh` vs `bash` vs POSIX, running scripts, the execution model, `set` options preview |
| 02 | [Variables, Quoting & Expansion](02_variables_quoting_expansion.md) | variables, **quoting** (`'` vs `"` vs none), word-splitting & globbing, the **expansion order**, command/arithmetic substitution |
| 03 | [Parameter Expansion](03_parameter_expansion.md) | the deep arsenal: `${v:-d}` `${v:=d}` `${v:?}` `${v#p}` `${v%p}` `${v//a/b}` `${v:off:len}` `${#v}` `${v^^}` `${!ref}` — string surgery without `sed` |
| 04 | [Control Flow](04_control_flow.md) | `test`/`[ ]`/`[[ ]]`, `if`/`case`, `for`/`while`/`until`, `&&`/`\|\|`, arithmetic `(( ))`, the exit-status logic |
| 05 | [Functions & Arrays](05_functions_arrays.md) | functions, scope (`local`), returning values, indexed & **associative arrays**, `"${arr[@]}"` correctness |
| 06 | [I/O, Redirection & Here-Docs](06_io_redirection.md) | fds, `>`/`>>`/`2>&1`, here-docs/here-strings, **process substitution** `<()`, named pipes, `read` |
| 07 | [Text Processing Toolkit](07_text_processing.md) | `grep`/`sed`/`awk` in depth, `cut`/`sort`/`uniq`/`tr`/`jq`, regex, the pipeline mindset |
| 08 | [Error Handling, Strict Mode & Debugging](08_error_handling_debugging.md) | `set -euo pipefail` (and its traps), `trap`, `ERR`/`EXIT`, `set -x`, `shellcheck`, defensive patterns |
| 09 | [Processes, Jobs & Signals](09_processes_signals.md) | foreground/background, subshells, `wait`, signals, `trap` cleanup, daemons, `nohup`/`setsid`, exit-on-signal |
| 10 | [Advanced & Enterprise](10_advanced_enterprise.md) | `getopts` CLI design, parallelism (`xargs -P`, GNU `parallel`), `flock` locking, idempotency, cron/systemd, security/injection, `bats` testing, performance & style |

### Part II — Linux operational craft (11–15)

> The work that *isn't* the Bash language but is squarely a principal's daily Linux
> job — running services, locking down hosts, managing storage, shipping binaries,
> and debugging crashes. Theory lives in [`../../os_net/`](../../os_net/README.md); these stay operational.

| # | Doc | What it covers |
|---|-----|----------------|
| 11 | [systemd: Service Authoring & Operations](11_systemd_services.md) | unit anatomy, `Type=` (simple/notify/oneshot…), `Restart=`/watchdog, **resource control** (`MemoryMax`/`CPUQuota`), **sandboxing** (`ProtectSystem`/`NoNewPrivileges`/seccomp), drop-ins, **timers** (cron replacement), socket activation, **journald** |
| 12 | [Security & Access Control](12_security_access_control.md) | users/PAM/NSS, permissions deep (setuid/setgid/sticky/umask), **ACLs**, **capabilities** (`setcap`), `sudoers`, **SELinux/AppArmor** (`audit2allow`), **SSH at depth** (ProxyJump, CA-signed certs, sshd hardening), `auditd` |
| 13 | [Storage & Filesystem Operations](13_storage_filesystems.md) | the storage stack, partitioning, **LVM** (extend/resize/snapshots online), ext4 vs XFS, `/etc/fstab` by UUID, **disk-full/inode triage** (deleted-open files), swap, `mdadm`, `fsck`/`fstrim` |
| 14 | [Packages, Builds & Dynamic Linking](14_packages_linking.md) | `apt`/`dpkg` vs `dnf`/`rpm`, building from source, **`.so` soname versioning**, `ld.so` search order, `ldd`/`readelf`, the `GLIBC_2.34 not found` disaster, `LD_LIBRARY_PATH`/RUNPATH, `patchelf`, glibc vs musl/static |
| 15 | [User-Space Debugging](15_debugging_gdb_coredumps.md) | `/proc/PID` introspection, `strace`/`ltrace`, **core dumps** (`coredumpctl`), **`gdb`** triage (`bt`/`thread apply all bt`), symbols/`debuginfod`, recipes for segfault/hang/spin/leak |
| 16 | [Fleet & Configuration Management at Scale](16_fleet_config_management.md) | SSH-at-scale (`pssh`/`pdsh`/SSH-CA), push vs pull, **Ansible** (inventory, idempotent playbooks, roles, Vault, `--check`, `serial:` canary/rollout), **immutable infra** (Packer golden images, cattle-not-pets), `cloud-init`, GitOps, config-drift discipline |

---

## 🚀 Running Bash

```bash
#!/usr/bin/env bash       # the portable shebang (finds bash on PATH)
set -euo pipefail         # strict mode — fail fast, fail loud (chapter 08)
echo "Hello from $(hostname)"
```

```bash
chmod +x script.sh        # make it executable
./script.sh               # run it (uses the shebang)
bash script.sh            # run with bash explicitly (shebang ignored)
bash -x script.sh         # trace every command (debugging)
```

- **`#!/usr/bin/env bash`** over `#!/bin/bash`: finds bash wherever it's installed
  (critical on macOS/BSD where `/bin/bash` is ancient or absent — see
  [`../mac/`](../mac/README.md)).
- **`sh` is not `bash`.** A `#!/bin/sh` script must be POSIX; bashisms (`[[ ]]`,
  arrays, `${v^^}`) will fail under `dash` (Debian/Ubuntu `/bin/sh`). Know which you
  target ([01](01_fundamentals.md)).

---

## 🎯 The discipline that makes Bash production-grade

1. **Quote everything.** `"$var"`, `"${arr[@]}"`, `"$(cmd)"` — unquoted expansion
   word-splits and globs, the #1 Bash bug and an injection vector ([02](02_variables_quoting_expansion.md)).
2. **Strict mode + traps.** `set -euo pipefail` and a `trap ... EXIT` cleanup turn
   silent failures into loud ones and guarantee teardown ([08](08_error_handling_debugging.md)).
3. **Check exit codes; compose with them.** `cmd && next`, `if cmd; then`, and never
   ignore `$?` on a critical step.
4. **Idempotency & locking** for anything that runs repeatedly or concurrently
   (cron, deploys) — re-runnable, single-instance via `flock` ([10](10_advanced_enterprise.md)).
5. **Run `shellcheck`.** It catches the quoting/expansion bugs humans miss — make it
   a CI gate ([08](08_error_handling_debugging.md)).

> **When to leave Bash:** when the logic needs data structures, real error handling
> across modules, or testing beyond a few cases — reach for **Python**
> ([`../../python_book/`](../../python_book/README.md)). Bash excels at gluing
> commands; it is a poor application language. Rule of thumb: **> ~100 lines or any
> nested data → Python.**

---

## 🛠️ The operational craft (Part II) that makes you dangerous on a host

1. **Run services under systemd, never by hand.** A `&`-ed daemon has no restart,
   no resource limit, no log, no clean shutdown. A hardened unit gets you all four
   ([11](11_systemd_services.md)).
2. **Least privilege by default.** Dedicated unprivileged users, `setcap` instead of
   root, scoped `sudoers`, don't `setenforce 0` — debug the AVC ([12](12_security_access_control.md)).
3. **Storage fails predictably.** Mount by UUID, extend LVM+FS together, and know the
   `df` full / `du` not-full = deleted-open-file trick before the incident ([13](13_storage_filesystems.md)).
4. **A binary that runs here may not run there.** `GLIBC_2.34 not found` is a build-host
   problem; build on the oldest target, or ship static/containers ([14](14_packages_linking.md)).
5. **Get the core, not the live process.** `bt full` + `thread apply all bt` on a core
   solves most crashes; pausing prod with `gdb` is a second outage ([15](15_debugging_gdb_coredumps.md)).
6. **Change the code, not the host.** A `for host in …; do ssh …; done` loop doesn't
   scale and drifts; converge with idempotent config (Ansible) or rebake an immutable
   image — and roll out canary-first, never to the whole fleet at once ([16](16_fleet_config_management.md)).

> Related: [`../windows/`](../windows/README.md) (the batch counterpart),
> [`../mac/`](../mac/README.md) (where these scripts break on BSD userland),
> [`../../os_net/`](../../os_net/README.md) (OS internals the scripts drive).
