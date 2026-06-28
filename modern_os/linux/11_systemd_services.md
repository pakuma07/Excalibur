# 11 — systemd: Service Authoring & Operations

> **Audience:** Staff/principal engineers who run services on Linux at scale. Chapters 01–10 taught the Bash *language*; this chapter pivots to Linux *operations*. We focus on the craft of authoring and running services with systemd — the supervisor that actually keeps your daemon alive in production. Kernel/boot internals live in a sibling reference; we link to it rather than repeat it.

---

## 1. Why systemd (and not your own `&` + `nohup`)

In [09 — Processes, Jobs & Signals](09_processes_signals.md) we daemonized by hand: `fork`, `setsid`, redirect FDs, write a PID file, trap signals. **Don't ship that.** systemd is PID 1 — the init process the kernel starts after boot — and it is purpose-built to supervise long-running processes.

| Hand-rolled daemon (ch. 09)         | systemd unit                                  |
| ----------------------------------- | --------------------------------------------- |
| You write the double-fork / setsid  | systemd owns the process directly             |
| PID file races, stale PIDs          | cgroup tracks every child reliably            |
| `while true; do app; done` restarts | `Restart=on-failure` with backoff             |
| Logs to a file you must rotate      | journald captures stdout/stderr, indexed      |
| No resource limits                  | cgroup-v2: memory/CPU/IO caps                 |
| Runs as root because it's easy      | `DynamicUser`, seccomp, namespaces            |

> **Rule:** if a script in chapter 09 ends with `nohup ... &`, replace it with a unit file. Every benefit below is free once you do.

systemd models the world as **units** (services, sockets, timers, mounts, targets) connected by a **dependency graph**. You declare *what* should run and *what it needs*; systemd computes ordering and parallelizes the rest. Boot/PID-1 internals: [../../os_net/operating_system/08_linux_internals_observability.md](../../os_net/operating_system/08_linux_internals_observability.md).

---

## 2. Unit file anatomy & where units live

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=MyApp API server
Documentation=https://wiki.internal/myapp
After=network-online.target            # ordering only: start AFTER network is up
Wants=network-online.target            # weak dep: pull it in, but don't fail if it fails
Requires=postgresql.service            # hard dep: if it stops, we stop too (+ implies nothing about order!)
# BindsTo=, Requisite=, PartOf= — see table below

[Service]
ExecStart=/usr/bin/myapp --config /etc/myapp/config.toml

[Install]
WantedBy=multi-user.target             # `systemctl enable` hooks us here for boot
```

**Ordering vs requirement is the #1 thing engineers conflate.** They are orthogonal axes:

| Directive    | Pulls dep in? | Enforces order? | If dep fails / stops…                       |
| ------------ | ------------- | --------------- | ------------------------------------------- |
| `After=`     | no            | **yes**         | nothing (ordering only)                     |
| `Before=`    | no            | **yes**         | nothing                                     |
| `Wants=`     | yes (weak)    | no              | we still start                              |
| `Requires=`  | yes (strong)  | no              | dep fails at start → we don't start         |
| `Requisite=` | no            | no              | dep not *already* active → we fail at start |
| `BindsTo=`   | yes (strong)  | no              | dep stops for any reason → we stop too      |
| `PartOf=`    | no            | no              | dep restart/stop propagates to us           |

> **Symptom:** "I set `Requires=postgresql` but my app still started before Postgres was ready." **Cause:** `Requires=` is a *requirement*, not *ordering*. **Fix:** add `After=postgresql.service` alongside it. Almost always you want both.

**Where units live (precedence high → low):**

| Path                              | Owner            | Use                                |
| --------------------------------- | ---------------- | ---------------------------------- |
| `/etc/systemd/system/`            | you (admin)      | your units & overrides; wins ties  |
| `/run/systemd/system/`            | runtime          | transient, vanishes on reboot      |
| `/usr/lib/systemd/system/`        | the package      | vendor units — **never edit**      |
| `~/.config/systemd/user/`         | you (per-user)   | `systemctl --user` services        |

After editing any unit on disk: **`sudo systemctl daemon-reload`** (re-parses the graph). Forgetting this is the second-most-common footgun.

---

## 2.5. `Type=` — the readiness contract

`Type=` tells systemd *when the service is considered "started"* — which gates `After=` dependents.

| `Type=`     | "Ready" when…                          | Use for                                   |
| ----------- | -------------------------------------- | ----------------------------------------- |
| `simple`    | the moment `ExecStart` is `fork`'d     | trivial apps (default; no readiness sync) |
| `exec`      | `ExecStart` has `execve`'d the binary  | better default than `simple` (catches exec failures) |
| `forking`   | parent exits, child keeps running      | legacy daemons that background themselves; set `PIDFile=` |
| `notify`    | app calls `sd_notify(READY=1)`         | **the gold standard** — true readiness    |
| `oneshot`   | process exits 0                        | setup/migrations; pair with `RemainAfterExit=yes` |
| `dbus`      | name appears on D-Bus (`BusName=`)     | D-Bus services                            |
| `idle`      | like `simple`, but waits for jobs idle | reduce console-log interleaving only      |

```ini
# notify: the app signals readiness — dependents wait until it's truly up
[Service]
Type=notify
ExecStart=/usr/bin/myapp        # calls sd_notify(0, "READY=1") after binding its socket
```

With `Type=simple`, a dependent ordered `After=` you starts as soon as your process is *spawned* — not when it's listening. Under `notify`, it waits for your explicit `READY=1`. Sockets (§9) sidestep this differently. **Prefer `notify` for anything that accepts connections; `exec` otherwise; `simple` only for throwaways.**

---

## 3. Restart policy & the backoff that bites everyone

```ini
[Service]
Restart=on-failure          # restart on non-zero exit / signal / watchdog; NOT on clean exit
# Restart=always            # also restart on clean exit — use sparingly
RestartSec=2                # wait 2s between restarts (default 100ms — too aggressive)
SuccessExitStatus=143 SIGTERM   # treat 143 (=128+SIGTERM) as success, not a crash
StartLimitIntervalSec=60    # window…
StartLimitBurst=5           # …allow 5 starts in it; the 6th → unit enters "failed", STAYS DOWN
```

> **Symptom:** a crashing service silently stops restarting and `systemctl status` shows `start request repeated too quickly`. **Cause:** it tripped `StartLimitBurst` — by design, to stop a thrash loop from melting the box. **Fix:** fix the crash; then `systemctl reset-failed myapp` to clear the counter. Tune the window if your start is genuinely slow.

Note `StartLimit*` lives logically in `[Unit]` (it's a unit-level limit), though older docs show it in `[Service]`. Pair restart policy with a **watchdog** for hang detection:

```ini
[Service]
Type=notify
WatchdogSec=30              # app must ping within 30s or systemd kills+restarts it
# In the app: sd_notify(0, "WATCHDOG=1") on a timer < WatchdogSec, e.g. every 10s.
```

A hung (but not crashed) process is invisible to `Restart=on-failure`. The watchdog is how you catch deadlocks. See graceful shutdown / `SIGTERM` handling in [09 — Processes, Jobs & Signals](09_processes_signals.md) — your app must exit cleanly on `TimeoutStopSec` or systemd `SIGKILL`s it.

---

## 4. Resource control (cgroup-v2)

Every service is its own cgroup; these directives wire straight into cgroup-v2 controllers. This is your blast-radius containment.

```ini
[Service]
MemoryHigh=1.5G             # soft cap: heavy reclaim pressure above this (throttles, doesn't kill)
MemoryMax=2G               # hard cap: cgroup OOM-kill if it can't reclaim under this
CPUQuota=200%              # at most 2 full cores' worth of CPU time
CPUWeight=100              # relative share under contention (default 100; range 1–10000)
IOWeight=100               # relative block-IO share
TasksMax=512               # max threads/processes (fork-bomb guard)
Slice=myapp.slice          # group related services to cap them collectively
```

`MemoryHigh` *throttles*, `MemoryMax` *kills*. Set `MemoryHigh` a bit below `MemoryMax` so you get reclaim pressure (and metrics) before the hard kill. **Slices** let you cap a whole tier: put 10 services in `databases.slice` with `MemoryMax=` on the slice and they share that budget.

> **Symptom:** "the service got SIGKILL but the system has free RAM." **Cause:** *cgroup* OOM from `MemoryMax`, not system OOM. **Fix:** check `systemctl status` / `journalctl` for `Killed process … memory cgroup out of memory`; raise the cap or fix the leak. Throttle/OOM debugging playbook: [../../os_net/enterprise_scenarios/01_cpu_memory_incidents.md](../../os_net/enterprise_scenarios/01_cpu_memory_incidents.md).

Inspect live: `systemctl status myapp` shows current memory; `systemd-cgtop` shows per-cgroup CPU/mem/IO live.

---

## 5. Sandboxing & hardening (least privilege)

At scale, *every* service should run with the minimum privilege it needs. systemd gives you namespacing, seccomp, and capability dropping declaratively — no code changes.

```ini
[Service]
# --- identity ---
DynamicUser=yes                 # allocate a throwaway UID/GID for the unit's lifetime (no /etc/passwd entry)
# Or: User=myapp / Group=myapp for a fixed service account
NoNewPrivileges=yes             # process & children can NEVER gain privs (blocks setuid escalation)

# --- filesystem ---
ProtectSystem=strict            # entire FS read-only except /dev, /proc, /sys
ProtectHome=yes                 # /home, /root, /run/user hidden
PrivateTmp=yes                  # private /tmp namespace (no /tmp snooping or symlink attacks)
PrivateDevices=yes              # minimal /dev, no raw device access
ReadWritePaths=/var/lib/myapp /var/log/myapp   # punch RW holes through ProtectSystem=strict

# --- capabilities ---
CapabilityBoundingSet=          # drop ALL capabilities…
AmbientCapabilities=CAP_NET_BIND_SERVICE   # …then grant exactly one (bind :80 without root)

# --- syscalls & network ---
SystemCallFilter=@system-service   # seccomp allowlist of "normal service" syscalls
SystemCallFilter=~@privileged @resources   # then subtract dangerous groups
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX   # no raw/packet/netlink sockets
RestrictNamespaces=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes       # block W^X mappings (kills many exploit techniques)
```

Score your hardening — this is the fastest audit you can run:

```bash
systemd-analyze security myapp.service   # 0.0 = safe … 10.0 = UNSAFE; lists every gap
```

> Use it as a CI gate: fail the build if a new service scores worse than a threshold. `DynamicUser=yes` + `ProtectSystem=strict` + `SystemCallFilter=@system-service` typically drops you into the "OK" band with zero app changes. Broader access-control model (users, sudo, SELinux/AppArmor): [12 — Security & Access Control](12_security_access_control.md).

---

## 6. Environment & exec lifecycle

```ini
[Service]
WorkingDirectory=/var/lib/myapp
Environment=LOG_LEVEL=info "APP_NAME=My App"   # inline; quote values with spaces
EnvironmentFile=-/etc/myapp/env                # leading '-' = OK if missing; keep secrets here, 0600
ExecStartPre=/usr/bin/myapp migrate            # runs before ExecStart; non-zero aborts the start
ExecStart=/usr/bin/myapp serve
ExecStartPost=/usr/bin/curl -fsS http://localhost:8080/healthz   # post-start hook
ExecReload=/bin/kill -HUP $MAINPID             # what `systemctl reload` runs
ExecStop=/usr/bin/myapp drain                  # graceful pre-stop (then SIGTERM)
```

`EnvironmentFile` is the clean way to ship secrets/config separately from the unit (mode `0600`, owned by the service user). Multiple `ExecStartPre=` lines run in order; any failing one aborts the start.

---

## 7. Drop-ins — customize vendor units the right way

**Never edit a file in `/usr/lib/systemd/system/` — a package update overwrites it.** Layer a drop-in instead:

```bash
sudo systemctl edit nginx.service     # opens an empty override; writes the snippet below
```

```ini
# /etc/systemd/system/nginx.service.d/override.conf
[Service]
MemoryMax=4G
Restart=on-failure
```

Drop-ins are *merged* over the vendor unit (last value wins for scalars; list directives append). To **reset** a list directive before re-adding, assign it empty first:

```ini
[Service]
ExecStart=                       # clear the inherited value…
ExecStart=/usr/bin/myapp --new   # …then set yours (required for ExecStart, which is a list)
```

`systemctl cat nginx.service` shows the fully-merged result. `systemctl edit --full` copies the whole unit into `/etc/` if you truly must fork it (rare).

---

## 8. Timers — the cron replacement

Pair a `.timer` with a `oneshot` `.service` of the same name. This supersedes the cron section in [10 — Advanced & Enterprise](10_advanced_enterprise.md).

```ini
# /etc/systemd/system/backup.timer
[Unit]
Description=Nightly backup timer
[Timer]
OnCalendar=*-*-* 02:30:00       # daily at 02:30 (see `systemd-analyze calendar "..."` to test)
Persistent=true                 # if the box was off at 02:30, run on next boot (anacron-style)
RandomizedDelaySec=300          # jitter 0–5min: avoid thundering-herd across a fleet
# OnBootSec=15min / OnUnitActiveSec=1h — relative timers for "every N after boot/last run"
[Install]
WantedBy=timers.target
```

| Timer                     | cron                                   |
| ------------------------- | -------------------------------------- |
| Output → journald, indexed | output emailed / lost                  |
| `After=`/`Requires=` deps | none                                   |
| cgroup resource limits    | none                                   |
| `Persistent=` catch-up    | needs anacron                          |
| `RandomizedDelaySec=` jitter | manual `sleep $RANDOM` hacks         |
| `systemctl list-timers`   | `crontab -l` (per-user, opaque)        |

```bash
systemctl list-timers --all          # next/last run for every timer
systemctl start backup.service       # run the job NOW, on demand, for testing
```

---

## 9. Socket activation

A `.socket` unit lets systemd own the listening socket and start the service **on first connection** (or at boot). Faster boots, no startup ordering races, zero-downtime restarts (the socket buffers while the service restarts).

```ini
# foo.socket — systemd listens; passes the FD to foo.service on connect
[Socket]
ListenStream=8080
[Install]
WantedBy=sockets.target
```

```ini
# foo.service — no ExecStart bind needed; inherits the FD via sd_listen_fds()
[Service]
ExecStart=/usr/bin/foo            # Type=notify or simple; reads $LISTEN_FDS
```

`.socket` and `.service` share a name; `systemctl start foo.socket` arms the listener. This is also how `xinetd`-style on-demand services and per-connection (`Accept=yes`) services work.

---

## 10. journald & log management

stdout/stderr from every service is captured, indexed, and queryable — **journald replaces logrotate for service logs** (it self-manages disk via `SystemMaxUse=`).

```bash
journalctl -u myapp                  # all logs for the unit
journalctl -u myapp -f               # follow (tail -f)
journalctl -u myapp --since "1h ago" --until "10m ago"
journalctl -u myapp -p err           # priority err and worse (0 emerg … 7 debug)
journalctl -u myapp -b               # this boot only;  -b -1 = previous boot
journalctl -u myapp -o json-pretty   # structured fields (great for piping to jq)
```

Make logs survive reboot and tag them:

```ini
# /etc/systemd/journald.conf
[Journal]
Storage=persistent                   # write to /var/log/journal (else RAM-only, lost on reboot)
SystemMaxUse=2G                      # cap disk usage (this is your "rotation")
RateLimitIntervalSec=30              # rate-limiting: drop bursts…
RateLimitBurst=10000                 # …above this many msgs per interval
```

```ini
# in the .service
[Service]
SyslogIdentifier=myapp               # clean tag in the journal instead of the binary basename
```

> **Symptom:** "logs vanish after reboot." **Cause:** default `Storage=auto` keeps logs in RAM if `/var/log/journal` doesn't exist. **Fix:** `Storage=persistent` (creates the dir) + `systemctl restart systemd-journald`.

Structured fields (`journalctl _UID=`, `_SYSTEMD_UNIT=`, `PRIORITY=`) let you query by metadata, not regex over text — a key advantage over flat log files.

---

## 11. Day-2 operations

```bash
# lifecycle
systemctl start|stop|restart|reload myapp
systemctl enable --now myapp         # enable at boot AND start immediately
systemctl disable myapp              # remove from boot
systemctl mask myapp                 # symlink to /dev/null — CANNOT be started even as a dep (strong off)

# introspection
systemctl status myapp               # state, cgroup tree, recent logs, main PID
systemctl is-active myapp            # → active / inactive  (scriptable; sets exit code)
systemctl is-enabled myapp           # → enabled / disabled / masked
systemctl list-units --failed        # everything currently broken — first thing to check on a sick box
systemctl reset-failed myapp         # clear failed state / restart counter (see §3)
systemctl cat myapp                  # merged unit (vendor + drop-ins)

# boot performance
systemd-analyze                      # total boot time breakdown
systemd-analyze blame                # slowest units, descending
systemd-analyze critical-chain      # the dependency chain on the critical path
```

`mask` vs `disable`: `disable` just unhooks from boot (it can still be pulled in as a dependency); `mask` makes the unit *unstartable*. Use `mask` to truly guarantee something never runs.

**Transient units** — run a one-off under full resource/sandbox control without writing a file:

```bash
# run a command capped at 1 core / 512M, in its own cgroup
systemd-run --scope -p CPUQuota=100% -p MemoryMax=512M /usr/bin/heavy-batch

# a throwaway timer: run /usr/bin/report once, 90 min from now
systemd-run --on-active=90min --unit=oneoff-report /usr/bin/report
```

`systemd-run` is invaluable for ad-hoc batch jobs you want isolated — far better than a bare `nohup`.

---

## 12. Complete production example

A hardened web app (notify + restart + cgroup caps + full sandbox), plus a nightly backup timer.

```ini
# /etc/systemd/system/webapp.service
[Unit]
Description=WebApp HTTP API
Documentation=https://wiki.internal/webapp
After=network-online.target postgresql.service
Wants=network-online.target
Requires=postgresql.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=notify
WatchdogSec=30
ExecStartPre=/usr/bin/webapp migrate
ExecStart=/usr/bin/webapp serve --listen 0.0.0.0:443
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=2
TimeoutStopSec=30
EnvironmentFile=/etc/webapp/env          # secrets, mode 0600
WorkingDirectory=/var/lib/webapp

# resource control
MemoryHigh=1.5G
MemoryMax=2G
CPUQuota=200%
TasksMax=512

# hardening
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths=/var/lib/webapp /var/log/webapp
CapabilityBoundingSet=
AmbientCapabilities=CAP_NET_BIND_SERVICE
SystemCallFilter=@system-service
SystemCallFilter=~@privileged
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
MemoryDenyWriteExecute=yes
SyslogIdentifier=webapp

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/backup.service   (oneshot, invoked by the timer)
[Unit]
Description=Nightly DB backup
After=postgresql.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/backup.sh
Nice=10
IOWeight=20
User=backup
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=/srv/backups
```

```ini
# /etc/systemd/system/backup.timer
[Unit]
Description=Run nightly DB backup
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
RandomizedDelaySec=300
[Install]
WantedBy=timers.target
```

Install & verify:

```bash
sudo systemctl daemon-reload                 # always after adding/editing units
sudo systemctl enable --now webapp.service   # boot + start now
sudo systemctl enable --now backup.timer     # arm the timer (NOT backup.service)

systemd-analyze security webapp.service      # confirm hardening score is in the OK band
systemctl status webapp.service              # check active + readiness reached
journalctl -u webapp -f                       # tail it
systemctl list-timers backup.timer           # confirm next run time
sudo systemctl start backup.service          # dry-run the backup once, on demand
```

---

> Next: [12 — Security & Access Control](12_security_access_control.md) — users, groups, `sudo` policy, file capabilities, and the SELinux/AppArmor MAC layer that complements the per-service sandboxing you just built here.
