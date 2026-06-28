# 10 — Advanced & Enterprise

> **Audience:** Engineers shipping Bash into production — CLIs, deploy scripts, cron/timer jobs, and CI. You know functions, arrays, strict mode, and signals; this capstone wires it all into scripts that are safe, idempotent, parallel, testable, and operable by a team. This is the final chapter: it ties the series together with the anatomy of a production script.

By now your scripts work on your machine. Enterprise is the gap between "works" and "runs unattended for two years, on someone else's box, without paging anyone." This chapter is that gap.

---

## 1. CLI design with `getopts`

A real tool parses flags, validates them, and prints usage. Bash has a builtin, `getopts`, for **short** options.

```bash
#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deploy [-v] [-n] [-e ENV] -t TARGET
  -e ENV     Environment (dev|stage|prod)   [default: dev]
  -t TARGET  Deploy target (required)
  -n         Dry run, change nothing
  -v         Verbose
  -h         Show this help
EOF
}

env=dev target="" dry_run=0 verbose=0

# Leading ':' = silent error mode (we handle errors ourselves).
# A ':' after a letter means that option TAKES an argument.
while getopts ':e:t:nvh' opt; do
  case "$opt" in
    e) env="$OPTARG" ;;       # OPTARG holds the option's argument
    t) target="$OPTARG" ;;
    n) dry_run=1 ;;
    v) verbose=1 ;;
    h) usage; exit 0 ;;
    :) echo "Error: -$OPTARG requires an argument" >&2; usage; exit 2 ;;
    \?) echo "Error: unknown option -$OPTARG" >&2; usage; exit 2 ;;
  esac
done
shift "$((OPTIND - 1))"   # drop parsed options; "$@" is now positional args
```

- **`OPTARG`** — the argument captured for the current option (`-e prod` → `prod`).
- **`OPTIND`** — index of the *next* arg to process. After the loop, `shift $((OPTIND-1))` removes everything `getopts` consumed, leaving real positionals in `"$@"`.
- **Leading colon** (`':e:t:...'`) switches on *silent* error reporting so you control the messages via the `:` and `\?` cases.

> **WRONG** — manual `$1`/`shift` flag parsing: it breaks on combined flags (`-nv`), `--flag=val`, and gives no validation.
>
> **RIGHT** — `getopts` for the standard cases; reach for manual parsing only when you genuinely need long options.

**No long options.** `getopts` does not support `--env`. Two escape hatches:

```bash
# (a) Manual: pre-translate long → short before getopts, or hand-roll a while/case.
case "$1" in --env) env="$2"; shift 2 ;; --env=*) env="${1#*=}"; shift ;; esac

# (b) GNU getopt(1) — a separate binary, NOT the builtin. Supports long opts but is
#     non-portable (BSD/macOS getopt differs) and fiddly. Use only if you must.
parsed=$(getopt -o e:t: --long env:,target: -- "$@") || { usage; exit 2; }
eval set -- "$parsed"
```

See [05 — Functions & Arrays](05_functions_arrays.md) for building the argv arrays these flags feed into.

---

## 2. Parallelism

Serial loops waste cores. Three tools, in order of reach:

```bash
# xargs: -n batches args per invocation, -P runs N in parallel, -0 reads NUL-delimited.
# -print0 + -0 is the ONLY safe way to pass filenames (handles spaces, newlines).
find . -name '*.log' -print0 | xargs -0 -P "$(nproc)" -n 1 gzip

# WRONG — word-splits on whitespace, explodes on "my file.log":
find . -name '*.log' | xargs gzip
```

```bash
# GNU parallel: nicer for complex jobs, per-job logs, {} placeholders, --jobs.
find . -name '*.log' -print0 | parallel -0 --jobs 8 'gzip {}'
parallel --jobs 4 'process {}' ::: img1 img2 img3   # ::: feeds the arg list inline
```

```bash
# Pure-bash bounded pool with `wait -n` (bash 4.3+): no external deps.
max=4
for item in "${items[@]}"; do
  process "$item" &                       # launch in background
  while (( $(jobs -rp | wc -l) >= max )); do
    wait -n                               # block until ANY one child exits
  done
done
wait                                      # drain the rest
```

- **Tip:** `nproc` gives core count; don't hardcode `-P 8`.
- **Symptom:** parallel jobs interleave stdout into garbage. **Cause:** shared stdout. **Fix:** `parallel` buffers per-job by default; with `xargs` redirect each job to its own file or use `--line-buffered`.
- Background jobs and `wait` semantics are covered in [09 — Processes, Jobs & Signals](09_processes_signals.md).

---

## 3. Single-instance locking with `flock`

Cron firing a slow job every minute will stack copies. Lock so only one runs:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Canonical idiom: open FD 200 onto a lockfile, take a non-blocking lock.
exec 200>/var/lock/myjob.lock
flock -n 200 || { echo "Already running; exiting." >&2; exit 1; }

# ... critical section. Lock is held by the open FD and released
#     automatically when the process (and FD 200) dies — even on crash.
do_work
```

- **`-n`** = non-blocking: fail immediately if held. Drop it to *wait* for the lock instead.
- The lock lives with the **file descriptor**, not the file — no stale PID files to clean up, no race on crash.
- **Tip:** wrap a whole script without editing it: `flock -n /var/lock/myjob.lock myscript.sh`.

> **WRONG** — `[[ -f /tmp/lock ]] && exit; touch /tmp/lock`: a race window between the check and the touch, and a crash leaves the file forever.

---

## 4. Idempotency

A production script must be safe to **re-run**. Same inputs, same end state, no errors on the second run.

```bash
# WRONG — fails the second time, or stacks duplicates:
mkdir /opt/app
useradd appuser
echo "export PATH=..." >> ~/.bashrc     # appends every run

# RIGHT — check before act, declare the desired state:
mkdir -p /opt/app                                   # -p is a no-op if it exists
id appuser &>/dev/null || useradd appuser           # create only if absent
grep -qxF 'export PATH=...' ~/.bashrc \
  || echo 'export PATH=...' >> ~/.bashrc            # line present? skip
```

**The deploy-script discipline:**

- Every step is **declarative**: "ensure X exists/equals Y," not "do X."
- Use tool-native idempotent flags: `mkdir -p`, `ln -sf`, `install -D`, `rsync` (syncs to a state), `cp -u`.
- Guard mutations with a check (`grep -q`, `id`, `test -e`, `systemctl is-active`).
- Make the script **resumable**: a half-failed run, fixed and re-run, converges. This is the entire premise of config-management tools (Ansible/Terraform) — you're hand-rolling a small one.

---

## 5. Scheduling: cron vs systemd timers

### cron

```cron
# ┌ min ┌ hour ┌ day-of-month ┌ month ┌ day-of-week   command
# │     │      │              │       │
  */15  *      *              *       *    /opt/app/sync.sh
  0     2      *              *       1-5  /opt/app/backup.sh   # 02:00 Mon–Fri
```

**Cron env pitfalls** (the #1 source of "works in my shell, fails in cron"):

- Cron runs with a **minimal `PATH`** (often `/usr/bin:/bin`) and **no profile**. `command not found`? Use absolute paths or set `PATH=` at the top of the crontab.
- No `$HOME`-dependent tooling, no `source ~/.bashrc`, sparse env. Set what you need explicitly.
- `%` is special in crontab (means newline) — escape as `\%`.
- Stdout/stderr is **mailed**, not logged. Redirect: `... >> /var/log/sync.log 2>&1`.

### systemd timers

```ini
# /etc/systemd/system/sync.service
[Unit]
Description=Sync job

[Service]
Type=oneshot
ExecStart=/opt/app/sync.sh
Environment=PATH=/usr/local/bin:/usr/bin:/bin
```

```ini
# /etc/systemd/system/sync.timer
[Unit]
Description=Run sync every 15 min

[Timer]
OnCalendar=*:0/15            # every 15 minutes
Persistent=true              # run on boot if a scheduled run was missed
RandomizedDelaySec=30        # jitter to avoid thundering herd

[Install]
WantedBy=timers.target
```

```bash
systemctl enable --now sync.timer
systemctl list-timers                 # next/last fire times
journalctl -u sync.service            # logs, structured, queryable
```

| Dimension          | cron                          | systemd timer                          |
|--------------------|-------------------------------|----------------------------------------|
| Setup              | One crontab line              | Two unit files + enable                |
| Logging            | Mailed stdout; you redirect   | `journalctl -u`, structured            |
| Missed runs        | Lost                          | `Persistent=true` catches up           |
| Dependencies       | None                          | `After=`, `Requires=` ordering         |
| Resource limits    | None                          | CPU/memory/IO via cgroups              |
| Jitter             | Manual                        | `RandomizedDelaySec=`                  |
| Portability        | Everywhere                    | systemd hosts only                     |

**When to use which:** cron for trivial, portable, log-to-file jobs. systemd timers when you need missed-run recovery, real logging, resource limits, or dependency ordering — i.e. most enterprise work.

Counterparts: Windows uses Task Scheduler ([../windows/07_advanced_enterprise.md](../windows/07_advanced_enterprise.md)); macOS uses `launchd` ([../mac/README.md](../mac/README.md)).

---

## 6. Security & injection-safety

Untrusted input — filenames, env vars, API payloads, CLI args — is the attack surface.

```bash
# WRONG — eval on untrusted input is remote code execution. Never.
eval "process $user_input"          # user_input='; rm -rf /' → game over

# WRONG — unquoted; word-splits and glob-expands attacker-controlled data:
cmd $args

# RIGHT — build an argv ARRAY; no shell re-parsing, no injection:
args=(--env "$env" --target "$target")
deploy "${args[@]}"
```

- **Quote everything**: `"$var"`, `"$@"`, `"${arr[@]}"`. Unquoted expansion is where injection lives.
- **`--` ends options** so a filename like `-rf` or `--config` is treated as data, not a flag:
  ```bash
  rm -- "$file"          # $file="-rf /" is now just a (nonexistent) filename
  grep -- "$pattern" "$file"
  ```
- **Temp files**: never predictable names (`/tmp/work.$$` is guessable → symlink attack). Use `mktemp`:
  ```bash
  tmp=$(mktemp) || exit 1
  trap 'rm -f "$tmp"' EXIT       # clean up even on signal; see ch. 08
  ```
- **Secrets**: restrictive perms from creation, not after.
  ```bash
  umask 077                       # new files → 600, dirs → 700
  install -m 600 /dev/null secret.key
  ```
  Never pass secrets on the command line (visible in `ps`); use files or env, and `chmod 600`.

Strict mode (`set -euo pipefail`) and `trap` cleanup are detailed in [08 — Error Handling, Strict Mode & Debugging](08_error_handling_debugging.md).

---

## 7. Testing with `bats`

Shell is testable. [Bats](https://github.com/bats-core/bats-core) (Bash Automated Testing System) gives you TAP-style tests.

```bash
# lib.sh — code under test
slugify() { echo "$1" | tr '[:upper:] ' '[:lower:]-'; }
```

```bash
# test_lib.bats — run with:  bats test_lib.bats
setup() { load lib.sh; }            # sourced before each test

@test "slugify lowercases and dashes spaces" {
  run slugify "Hello World"
  [ "$status" -eq 0 ]               # exit code
  [ "$output" = "hello-world" ]     # captured stdout
}

@test "deploy rejects missing target" {
  run ./deploy -e prod              # no -t
  [ "$status" -eq 2 ]
  [[ "$output" == *"required"* ]]
}
```

- **`run`** captures `$status` and `$output`; the test fails if any bare command fails.
- **Tip:** `setup`/`teardown` run per test; `mktemp -d` a sandbox in `setup`, `rm -rf` it in `teardown`.

---

## 8. Performance & style

```bash
# WRONG — a fork PER iteration (cat, grep subshell, $(...)): slow at scale.
for f in *.txt; do
  lines=$(cat "$f" | wc -l)         # useless cat; subshell; pipe
done

# RIGHT — builtins, no useless forks:
for f in *.txt; do
  mapfile -t arr < "$f"             # builtin read into array
  lines=${#arr[@]}                  # no external process at all
done
```

- **Avoid useless forks in loops**: each `$(...)`, pipe, and external command is a process. Prefer builtins — parameter expansion over `sed`/`awk` for simple edits, `[[ ]]` over `test`, `mapfile`/`read` over `cat`.
- **Capability checks** use `command -v`, not `which` (which is an external, non-portable):
  ```bash
  command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }
  ```
- **In CI**, gate every script:
  ```bash
  shellcheck script.sh              # static analysis — catches quoting/injection bugs
  shfmt -d -i 2 script.sh           # formatting diff (fails CI if unformatted)
  ```
- **The ~100-line rule:** when a script grows past roughly 100 lines, sprouts real data structures, nested logic, or needs unit-tested business logic — **switch to Python**. Bash is glue; past a point you're fighting it. See [../../python_book/README.md](../../python_book/README.md) to leave the shell cleanly.

---

## 9. Anatomy of a production script

Everything above, assembled:

```bash
#!/usr/bin/env bash
#
# deploy — ship the app to a target environment. Idempotent, lockable.
#
set -euo pipefail                              # strict mode (ch. 08)
IFS=$'\n\t'

readonly LOCKFILE=/var/lock/deploy.lock
tmp=""

cleanup() { [[ -n "$tmp" ]] && rm -rf "$tmp"; }
trap cleanup EXIT                              # always clean up (ch. 08)

usage() {
  cat <<'EOF'
Usage: deploy [-nv] [-e ENV] -t TARGET
  -e ENV     Environment (dev|stage|prod)  [default: dev]
  -t TARGET  Deploy target (required)
  -n         Dry run
  -v         Verbose
  -h         Help
EOF
}

log() { (( verbose )) && echo "[$(date +%T)] $*" >&2; return 0; }

main() {
  local env=dev target="" dry_run=0 verbose=0

  while getopts ':e:t:nvh' opt; do
    case "$opt" in
      e) env="$OPTARG" ;;
      t) target="$OPTARG" ;;
      n) dry_run=1 ;;
      v) verbose=1 ;;
      h) usage; exit 0 ;;
      :) echo "Error: -$OPTARG needs an argument" >&2; exit 2 ;;
      \?) echo "Error: unknown -$OPTARG" >&2; usage; exit 2 ;;
    esac
  done
  shift "$((OPTIND - 1))"

  [[ -n "$target" ]] || { echo "Error: -t TARGET required" >&2; usage; exit 2; }
  command -v rsync >/dev/null || { echo "rsync required" >&2; exit 1; }

  exec 200>"$LOCKFILE"
  flock -n 200 || { echo "Another deploy is running." >&2; exit 1; }

  tmp=$(mktemp -d)
  log "Deploying to $env:$target (dry_run=$dry_run) via $tmp"

  local args=(-a --delete)
  (( dry_run )) && args+=(--dry-run)
  rsync "${args[@]}" -- ./build/ "$target":/opt/app/   # quoted, argv array, --

  log "Done."
}

main "$@"
```

The pattern in one breath: **shebang → strict mode → readonly config → trap cleanup → usage → small functions → `main "$@"`**. Wrapping logic in `main` keeps the global namespace clean, makes the script sourceable for testing (`bats`), and gives one obvious entry point.

---

That closes the series. You can now write shell that a team trusts in production: parsed CLIs, safe parallelism, locked single-instance jobs, idempotent re-runnable deploys, scheduled and logged, injection-hardened, tested, and linted — and you know the line past which you should reach for [Python](../../python_book/README.md) instead.

> Related: [../windows/](../windows/README.md) · [../mac/](../mac/README.md) · [../../os_net/](../../os_net/README.md)
