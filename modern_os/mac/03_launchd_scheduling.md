# 03 — launchd & Scheduling

> **Audience:** Linux engineers automating a Mac. You know `cron`, `systemd`, unit files, and `systemctl`. macOS throws all of that out: there is no `systemd`, `cron` is deprecated, and PID 1 is **`launchd`**. This chapter maps your `systemd` instincts onto launchd's `.plist` jobs and `launchctl` so you can schedule, daemonize, and debug work on a Mac.

---

## 1. launchd: PID 1, init, and scheduler in one

On macOS, **`launchd`** is the first process at boot (PID 1) and the single manager for every service, agent, and scheduled job. There is no `systemd`, no `init.d`, no `cron` daemon doing the real work. Mentally, `launchd` ≈ `systemd` + `cron` + `inetd`.

A unit of work is a **job**, described by an XML **`.plist`** (property list) file. launchd reads the plist, and runs your program on the trigger you declare — at load, on a timer, on a schedule, or on a filesystem change.

```bash
# launchd is PID 1
ps -p 1 -o comm=        # /sbin/launchd

# Every running/loaded job in the current user domain
launchctl list          # legacy view (PID, exit status, Label)
```

Key concept new to Linux folks: launchd jobs live in **domains** — `system` (boot, root) and `gui/<UID>` (a logged-in user's GUI session). The domain decides *who* and *when*, which leads directly to Agents vs Daemons.

---

## 2. Launch Agents vs Launch Daemons

This is the first decision for any job. It is the macOS analog of "user systemd unit" vs "system systemd unit," but the GUI-session rule is stricter.

| | **Launch Agent** | **Launch Daemon** |
|---|---|---|
| Runs as | The logged-in user | `root` (by default) |
| When | While a user is logged into a GUI session | At boot, before/without login |
| GUI / UI access | Yes (can show UI, access user keychain, Aqua session) | No GUI context |
| Domain | `gui/<UID>` | `system` |
| systemd analog | `systemctl --user` unit | system unit |
| Use for | Per-user sync, menu-bar helpers, user backups | System services, fleet agents, anything pre-login |

**Directory locations** (launchd discovers plists here):

```bash
~/Library/LaunchAgents       # agents for THIS user only
/Library/LaunchAgents        # agents for ALL users (run per-login)
/Library/LaunchDaemons       # system daemons (run as root at boot)
/System/Library/LaunchAgents     # Apple-owned, SIP-protected — DO NOT TOUCH
/System/Library/LaunchDaemons    # Apple-owned, SIP-protected — DO NOT TOUCH
```

Rule of thumb: if it needs the GUI/keychain or "the user's stuff," it's an **Agent**. If it must run at boot with no login and as root, it's a **Daemon**. Put *your* files under `/Library/...` or `~/Library/...` — never under `/System/...` (System Integrity Protection blocks writes there).

---

## 3. Anatomy of a `.plist`

A plist is XML with a strict DTD. The minimum is `Label` + `ProgramArguments` + a trigger.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- Reverse-DNS unique ID; also the on-disk filename -->
    <key>Label</key>
    <string>com.takeda.example</string>

    <!-- argv[]. argv[0] MUST be an absolute path -->
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/myscript.sh</string>
        <string>--flag</string>
    </array>
</dict>
</plist>
```

`ProgramArguments` is an array, not a shell string — there is no shell parsing. If you need a pipeline or globbing, point it at `/bin/sh -c "…"` explicitly. `argv[0]` and every file path must be **absolute**: launchd jobs do not inherit your interactive `PATH`.

---

## 4. Triggers — when the job runs

You attach one or more trigger keys to the `<dict>`. A job may combine several (e.g., `RunAtLoad` + `StartCalendarInterval`).

| Key | Type | Behavior | systemd/cron analog |
|---|---|---|---|
| `RunAtLoad` | bool | Run once immediately when loaded/booted | `[Install] WantedBy` + start |
| `StartInterval` | int (seconds) | Run every N seconds | `OnUnitActiveSec` timer |
| `StartCalendarInterval` | dict / array | Run at clock times (cron-like) | `OnCalendar` / `cron` |
| `WatchPaths` | array of paths | Run when a path changes | `path` unit |
| `QueueDirectories` | array of dirs | Run when a watched dir becomes non-empty | inotify + worker |
| `KeepAlive` | bool / dict | Restart on exit (daemonize) | `Restart=` |

### StartInterval — simple timer

```xml
<key>StartInterval</key>
<integer>3600</integer>          <!-- every hour -->
```

### StartCalendarInterval — cron-like

A dict of `Minute` / `Hour` / `Day` / `Weekday` / `Month`. Any **omitted** key is a wildcard (`*`). `Weekday` 0 and 7 both mean Sunday.

```xml
<!-- Daily at 03:00 -->
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key><integer>3</integer>
    <key>Minute</key><integer>0</integer>
</dict>
```

For **multiple times**, use an **array of dicts** (there is no comma-list like cron's `0,12`):

```xml
<!-- 09:30 and 17:30 every day -->
<key>StartCalendarInterval</key>
<array>
    <dict>
        <key>Hour</key><integer>9</integer>
        <key>Minute</key><integer>30</integer>
    </dict>
    <dict>
        <key>Hour</key><integer>17</integer>
        <key>Minute</key><integer>30</integer>
    </dict>
</array>
```

> **Sleep caveat:** if the Mac is asleep at the scheduled time, the job runs once on wake (it does not run "for every missed slot"). `StartInterval` timers also coalesce while asleep.

### WatchPaths / QueueDirectories — run on filesystem change

```xml
<key>WatchPaths</key>
<array>
    <string>/etc/myapp/config.yml</string>   <!-- fires on any change -->
</array>
```

### KeepAlive — restart on exit (daemonize)

`true` restarts the job whenever it exits. The dict form is conditional:

```xml
<key>KeepAlive</key>
<dict>
    <key>SuccessfulExit</key><false/>   <!-- restart only if exit != 0 -->
    <key>Crashed</key><true/>           <!-- restart if it crashed -->
</dict>
```

---

## 5. Logging, environment, and working directory

launchd jobs start with a **minimal environment** and no controlling terminal. Capture output and set context explicitly:

```xml
<key>StandardOutPath</key>
<string>/var/log/myjob.out.log</string>

<key>StandardErrorPath</key>
<string>/var/log/myjob.err.log</string>

<key>EnvironmentVariables</key>
<dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key><string>en_US.UTF-8</string>
</dict>

<key>WorkingDirectory</key>
<string>/opt/myapp</string>
```

The log directory must already exist and be writable by the job's user. For an Agent, prefer `~/Library/Logs/...`; for a Daemon, `/var/log/...` or `/Library/Logs/...`.

---

## 6. launchctl — modern vs legacy

Apple split `launchctl` into a **modern** domain-aware syntax (macOS 10.10+, now required for daemons) and a **legacy** syntax that still works but is deprecated and operates on the calling context only.

```bash
# ---- MODERN (preferred) ----
# Domains: system  (daemons)   |  gui/$UID  (agents for current user)

# Load (register) a daemon
sudo launchctl bootstrap system /Library/LaunchDaemons/com.takeda.backup.plist

# Load an agent into your GUI session
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.takeda.sync.plist

# Unload
sudo launchctl bootout system/com.takeda.backup
launchctl bootout gui/$UID/com.takeda.sync

# Persist enable/disable across loads
sudo launchctl enable system/com.takeda.backup
sudo launchctl disable system/com.takeda.backup

# Force a run NOW (great for testing a scheduled job)
sudo launchctl kickstart -k system/com.takeda.backup   # -k = restart if running

# Inspect a job (state, last exit, env, paths)
sudo launchctl print system/com.takeda.backup
launchctl print gui/$UID/com.takeda.sync
```

```bash
# ---- LEGACY (deprecated, but you'll see it everywhere) ----
launchctl load -w   ~/Library/LaunchAgents/com.takeda.sync.plist   # -w persists
launchctl unload    ~/Library/LaunchAgents/com.takeda.sync.plist
launchctl start     com.takeda.sync     # trigger by Label
launchctl stop      com.takeda.sync
launchctl list | grep takeda            # PID  Status  Label
```

Mapping: `bootstrap` ≈ `load`, `bootout` ≈ `unload`, `kickstart -k` ≈ `start`, `print` ≈ `list` + much more. For new daemon work use `bootstrap system …`; `load -w` still works for agents but is on its way out.

---

## 7. Complete working example — daily backup at 02:30

A Launch Daemon that runs a backup script every day at 2:30 AM, with logging.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.takeda.backup</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/backup.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>2</integer>
        <key>Minute</key><integer>30</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/var/log/com.takeda.backup.out.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/com.takeda.backup.err.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>WorkingDirectory</key>
    <string>/opt/backup</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

```bash
# 1. Install with correct ownership/perms (daemon REQUIRES root:wheel 644)
sudo cp com.takeda.backup.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/com.takeda.backup.plist
sudo chmod 644        /Library/LaunchDaemons/com.takeda.backup.plist

# 2. Validate XML before loading
plutil -lint /Library/LaunchDaemons/com.takeda.backup.plist   # -> OK

# 3. Make the script executable, absolute path
sudo chmod +x /usr/local/bin/backup.sh

# 4. Load it
sudo launchctl bootstrap system /Library/LaunchDaemons/com.takeda.backup.plist

# 5. Verify it's registered and test-run it now
sudo launchctl print system/com.takeda.backup | grep -E "state|path"
sudo launchctl kickstart -k system/com.takeda.backup
cat /var/log/com.takeda.backup.out.log
```

---

## 8. Debugging — "it didn't run"

**Symptom:** Job never fires or exits immediately.
**Cause / Fix:**

- **Wrong path** — `ProgramArguments[0]` is relative or wrong. *Fix:* use absolute paths everywhere; `launchctl print system/<Label>` shows the resolved program.
- **Not executable** — script lacks `+x` or wrong interpreter. *Fix:* `chmod +x`, confirm the shebang exists.
- **Minimal PATH** — script calls `aws`/`rsync` by bare name and they aren't found. *Fix:* set `EnvironmentVariables > PATH` or call binaries by absolute path.
- **TCC / privacy** — job touches Desktop, Documents, Downloads, or a network share and silently fails with EPERM. *Fix:* grant **Full Disk Access** to the binary (System Settings → Privacy & Security); daemons need this more often than you expect.
- **Bad ownership/perms** (daemons) — not `root:wheel` `644`. *Fix:* `chown root:wheel`, `chmod 644`; launchd refuses world-writable or non-root daemon plists.
- **Invalid XML** — silent load failure. *Fix:* `plutil -lint file.plist`.

```bash
# Inspect last exit code & live state
sudo launchctl print system/com.takeda.backup    # look for "last exit code"

# Stream launchd's own log for this job
log show --predicate 'process == "com.takeda.backup"' --last 1h
log stream --predicate 'subsystem == "com.apple.xpc.launchd"' --info
```

A nonzero **last exit code** in `launchctl print` is your fastest signal — pair it with the `StandardErrorPath` log.

---

## 9. Cron still exists (but don't)

`cron` is present and `crontab -e` works, but Apple deprecates it: it has no GUI/session context, doesn't survive cleanly under TCC, and isn't fleet-manageable.

```bash
crontab -l            # list (legacy)
# 30 2 * * *  /usr/local/bin/backup.sh   <- discouraged; use a LaunchDaemon
```

Use launchd instead — it is the supported, MDM-manageable, sleep-aware scheduler. See [01 — The macOS Shell Landscape](01_shell_landscape.md) for shell/PATH context, and [04 — System Config & Automation Tooling](04_system_config_tooling.md) for managing these jobs at fleet scale.

**Cross-platform analogs:** Linux `cron`/`systemd` timers are covered in [../linux/10_advanced_enterprise.md](../linux/10_advanced_enterprise.md); Windows Task Scheduler / `schtasks` in [../windows/07_advanced_enterprise.md](../windows/07_advanced_enterprise.md).

---

> Next: [04 — System Config & Automation Tooling](04_system_config_tooling.md) — `defaults`, `profiles`, MDM, and the configuration tooling that deploys these launchd jobs across a managed Mac fleet.
