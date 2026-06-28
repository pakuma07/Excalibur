# 09 — Processes, Jobs & Signals

> **Audience:** Scripters who can write robust, debuggable scripts and now need to run work *concurrently*, supervise child processes, and shut down cleanly. By the end you can launch parallel jobs, collect their exit codes, trap signals, propagate them to children, and detach long-running work — knowing when to reach for `systemd` instead.

---

## 1. Foreground vs background (`&`)

Every command runs in the **foreground** by default: the shell blocks until it exits. Append `&` to run it in the **background** and get your prompt back.

```bash
# WRONG — these run sequentially; total time = sum of all three
process a.dat   # 10s
process b.dat   # 10s
process c.dat   # 10s   → 30s wall-clock

# RIGHT — run concurrently; total time ≈ the slowest one
process a.dat &
process b.dat &
process c.dat &
wait            # block until all background jobs finish → ~10s
```

A backgrounded command's stdout/stderr still go to the terminal unless redirected — see [06 — I/O, Redirection & Here-Docs](06_io_redirection.md). Redirect noisy jobs:

```bash
process a.dat >a.log 2>&1 &
```

---

## 2. Job control: `jobs`, `fg`, `bg`, `%1`

Interactive shells track backgrounded commands as **jobs**, numbered per shell.

```bash
sleep 300 &        # [1] 48213
tail -f app.log &  # [2] 48214

jobs               # list jobs with state
# [1]-  Running   sleep 300 &
# [2]+  Running   tail -f app.log &

fg %1              # bring job 1 to foreground
# (Ctrl-Z suspends it back to the background, Stopped state)
bg %1              # resume a stopped job in the background
kill %2            # signal a job by jobspec (note the % )
```

| Jobspec | Means |
|---------|-------|
| `%1`    | job number 1 |
| `%+` or `%%` | current job (the `+` in `jobs`) |
| `%-`    | previous job (the `-` in `jobs`) |
| `%sleep`| job whose command begins with `sleep` |

Job control is an *interactive* feature. In scripts you address children by **PID**, not jobspec.

---

## 3. Process identity: `$$`, `$BASHPID`, `$!`

```bash
echo "Script PID:   $$"        # PID of the script's main shell
echo "Subshell PID: $BASHPID"  # PID of the *current* shell/subshell

sleep 100 &
echo "Last bg PID:  $!"        # PID of the most recent background command
```

`$$` is fixed for the life of the script — even inside a subshell it reports the *parent* script's PID. `$BASHPID` reports the actual current process, so it differs inside `( … )`:

```bash
echo "$$ $BASHPID"           # 100  100
( echo "$$ $BASHPID" )       # 100  10042   ← $$ unchanged, $BASHPID is the subshell
```

Capture `$!` *immediately* — it changes with every new background command:

```bash
long_task & task_pid=$!      # grab it now, on the same line
other_task &                 # $! now points here, not long_task
wait "$task_pid"             # wait specifically for long_task
```

---

## 4. `wait` — and capturing parallel exit codes

`wait` with no args blocks until **all** children exit (and returns 0). To get a *specific* job's exit status, `wait` on its PID:

```bash
slow_job & pid=$!
wait "$pid"           # returns slow_job's actual exit code
echo "exit=$?"
```

`wait -n` (Bash 4.3+) returns when **any one** child finishes — useful for bounded concurrency. Bash 5.1+ sets `$?` to that job's status and `wait -p var` captures its PID.

### Parallel jobs collecting exit codes

```bash
#!/usr/bin/env bash
set -uo pipefail

jobs=(alpha bravo charlie delta)
declare -A pid_of           # name -> pid
declare -A rc_of            # name -> exit code

# Launch all jobs, remembering each PID
for name in "${jobs[@]}"; do
  process_one "$name" &
  pid_of["$name"]=$!
done

# Wait for each specific PID and record its status
failures=0
for name in "${jobs[@]}"; do
  if wait "${pid_of[$name]}"; then
    rc_of["$name"]=0
  else
    rc_of["$name"]=$?
    ((failures++))
  fi
done

for name in "${jobs[@]}"; do
  printf '%-8s exit=%s\n' "$name" "${rc_of[$name]}"
done
exit "$(( failures > 0 ? 1 : 0 ))"
```

> **Note:** `set -e` does *not* abort on a failed background job — the failure only surfaces through `wait`. Always check `wait`'s return. For higher-level parallelism (`xargs -P`, GNU `parallel`) see [10 — Advanced & Enterprise](10_advanced_enterprise.md).

---

## 5. Subshells `( )` vs grouping `{ }`

Both group commands, but with a crucial difference:

```bash
# ( ) runs in a SUBSHELL — a forked child process
( cd /tmp; rm -rf build )   # cwd change & vars stay inside; parent unaffected

# { } runs in the CURRENT shell — no fork
{ cd /tmp; rm -rf build; }  # changes leak into the rest of the script!
```

Syntax gotchas for `{ }`: needs a **space after `{`** and a **terminator (`;` or newline) before `}`**.

| Concern | `( … )` subshell | `{ …; }` grouping |
|---------|------------------|-------------------|
| New process? | Yes (fork) | No |
| Variable changes visible to parent? | No (isolated) | Yes |
| `cd`, `set`, `umask` leak out? | No | Yes |
| Performance | Fork cost per call | Free |

```bash
# WRONG — capturing output forks a subshell, so the var assigned inside is lost
echo "data" | { read -r line; }
echo "$line"                 # empty — read ran in the pipe's subshell

# RIGHT — keep it in the current shell
read -r line <<< "data"
echo "$line"                 # data
```

Use `( )` when you *want* isolation (temporary `cd`, trapped scope). Use `{ }` for redirecting or grouping without the fork. Don't fork in hot loops.

---

## 6. Signals — the catalog

A signal is an async notification to a process. Most can be **caught** (handled) or ignored; two cannot.

| # | Name | Catchable? | Default action | Typical use |
|---|------|-----------|----------------|-------------|
| 1  | SIGHUP  | yes | terminate | controlling terminal closed; daemons reload config |
| 2  | SIGINT  | yes | terminate | Ctrl-C |
| 9  | SIGKILL | **no**  | terminate | force kill — process cannot clean up |
| 15 | SIGTERM | yes | terminate | polite "please exit" (default of `kill`) |
| 17 | SIGCHLD | yes | ignore | a child stopped or exited |
| 18/19 | SIGCONT/SIGSTOP | CONT yes / **STOP no** | resume / stop | job control |

`SIGKILL (9)` and `SIGSTOP (19)` are enforced by the kernel and **cannot be trapped, blocked, or ignored**. Reach for `SIGTERM` first; escalate to `SIGKILL` only if a process won't die.

```bash
kill -l          # list all signal names/numbers
```

---

## 7. `trap` — run code on signals & EXIT

```bash
#!/usr/bin/env bash
set -euo pipefail

tmpdir=$(mktemp -d)
cleanup() {
  rm -rf "$tmpdir"           # always runs, however we exit
}
trap cleanup EXIT            # EXIT fires on normal exit, error, or signal
trap 'echo "interrupted"; exit 130' INT TERM
```

`EXIT` is the workhorse: it runs whenever the script terminates, so one `trap … EXIT` covers all your cleanup. Conventional exit codes: **130** = killed by SIGINT (128+2), **143** = SIGTERM (128+15). See the strict-mode/`trap ERR` discussion in [08 — Error Handling, Strict Mode & Debugging](08_error_handling_debugging.md).

```bash
trap - INT          # remove a trap (reset to default)
trap '' INT         # IGNORE a signal (empty handler)
```

---

## 8. Graceful shutdown of a supervised child

A wrapper that launches a child must forward signals so the child can clean up — otherwise it's left orphaned or hard-killed.

```bash
#!/usr/bin/env bash
set -euo pipefail

child_pid=""

terminate() {
  echo "Wrapper received signal; forwarding to child $child_pid"
  [[ -n $child_pid ]] && kill -TERM "$child_pid" 2>/dev/null
}
trap terminate INT TERM

./worker &                  # run child in background so the shell stays responsive
child_pid=$!

# wait returns when the child exits OR when a trapped signal interrupts it.
# After the trap fires, wait again to reap the child and get its real code.
wait "$child_pid"
status=$?
wait "$child_pid" 2>/dev/null || true
echo "child exited with $status"
exit "$status"
```

**Symptom:** Ctrl-C kills your wrapper but the child keeps running.
**Cause:** the signal hit the wrapper only; the child wasn't sent SIGTERM, or the wrapper ran the child in the foreground and blocked, so the trap never executed.
**Fix:** run the child with `&`, save `$!`, `wait` on it, and forward the signal from the trap as above.

---

## 9. Sending signals: `kill`, `pkill`, `killall`

```bash
kill 48213              # send SIGTERM (default) to a PID
kill -TERM 48213        # explicit
kill -9 48213           # SIGKILL — last resort, no cleanup
kill -0 48213           # send NO signal; just test "is this PID alive?" ($?=0 if yes)
kill -- -48213          # negative PID = signal the whole process GROUP

pkill -f 'python app.py'  # by command-line pattern (-f matches full cmdline)
pkill -u alice            # all of alice's processes
killall nginx             # by exact process NAME (beware: matches everything named nginx)
```

```bash
# WRONG — escalate to KILL immediately; no chance to flush/clean up
kill -9 "$pid"

# RIGHT — ask politely, wait, then escalate only if needed
kill -TERM "$pid"
for _ in {1..10}; do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
kill -0 "$pid" 2>/dev/null && kill -KILL "$pid"
```

> Prefer `pkill -f` over `killall` on Linux — it's pattern-based and avoids the cross-platform naming surprises of `killall`.

---

## 10. Detaching: `nohup` vs `disown` vs `setsid`

When you log out, the shell sends `SIGHUP` to its jobs. To survive that:

```bash
nohup ./long.sh &              # ignore SIGHUP + redirect stdout/stderr to nohup.out
disown -h %1                   # tell shell to NOT send SIGHUP to job 1 (already running)
disown %1                      # remove job from the shell's table entirely
setsid ./long.sh &             # start in a NEW session (no controlling terminal at all)
```

| Tool | When | Effect |
|------|------|--------|
| `nohup` | at launch | ignores HUP, redirects output to `nohup.out` |
| `disown`| after launch | removes job from shell's HUP list |
| `setsid`| at launch | new session/process group — true detachment |

```bash
# Fully detached, no terminal, output captured:
setsid ./long.sh >/var/log/long.log 2>&1 < /dev/null &
```

---

## 11. Daemonizing — and why you usually shouldn't

The classic Unix daemon recipe is: `fork`, `setsid`, `fork` again, `chdir /`, reset `umask`, close/redirect stdio. In a shell script `setsid` plus stdio redirection gets you most of the way:

```bash
setsid bash -c './service.sh >>/var/log/service.log 2>&1 < /dev/null' &
```

**Be honest with yourself:** hand-rolled daemons lack restart-on-crash, log rotation, resource limits, dependency ordering, and clean shutdown. On any modern Linux box, **write a `systemd` unit instead** — it handles all of that and gives you `systemctl status`/`journalctl`. The full unit-file walkthrough is in [10 — Advanced & Enterprise](10_advanced_enterprise.md).

---

## 12. `timeout` — bound a command's runtime

```bash
timeout 30s ./maybe_hangs.sh        # SIGTERM after 30s; exit 124 if it timed out
timeout -k 5s 30s ./stubborn.sh     # TERM at 30s, then KILL 5s later if still alive
timeout -s INT 10s ./job.sh         # send SIGINT instead of TERM
```

Exit code **124** means "timed out." Use it to keep flaky network calls or test runs from hanging a pipeline forever.

---

## 13. Zombies & orphans

- **Zombie** (`<defunct>` in `ps`): a child that has exited but whose parent hasn't `wait`ed for it. It holds only a PID + exit status. Reap it by calling `wait` (or handling `SIGCHLD`). Many zombies = a buggy parent that never reaps.
- **Orphan:** a child whose parent died first. It's re-parented to `init`/`systemd` (PID 1), which `wait`s for it automatically — so orphans are harmless, but a long-lived orphan may be unsupervised work you didn't intend.

```bash
ps -eo pid,ppid,stat,cmd | grep -w Z   # find zombies (STAT contains Z)
```

If your script forks children, `wait` for them — it both collects exit codes (§3) and prevents zombies.

---

## 14. `exec` — replace the current process

`exec` does *not* fork; it **replaces** the running shell with the new program, keeping the same PID. Nothing after it runs.

```bash
#!/usr/bin/env bash
# entrypoint.sh — common container pattern
setup_environment
exec "$@"        # become the real app; signals now go straight to it (PID 1)
```

This matters in containers: the entrypoint typically becomes **PID 1**, so `exec`ing the real process lets Docker/Kubernetes deliver `SIGTERM` directly to it for graceful shutdown — no shell wrapper swallowing signals. (`exec` also replaces the shell when you only need to redirect fds permanently — see [06 — I/O, Redirection & Here-Docs](06_io_redirection.md).)

```bash
# WRONG — shell stays PID 1, child gets signals only if you forward them
./myapp "$@"

# RIGHT — myapp becomes PID 1 and receives SIGTERM directly
exec ./myapp "$@"
```

---

## 15. Checklist

- Background with `&`, capture `$!` on the same line, `wait "$pid"` for the real exit code.
- For parallel jobs, store PIDs in an array and `wait` each to collect statuses (§3).
- `set -e` won't catch background failures — check `wait`.
- Use `( )` for isolation, `{ }` to avoid forking; never `read` into a var across a pipe.
- `trap cleanup EXIT` for cleanup; forward `TERM`/`INT` to supervised children.
- `SIGKILL`/`SIGSTOP` are uncatchable — `TERM` first, escalate only if needed.
- Detach with `setsid` (+ redirect stdio); for real services use `systemd`.
- `exec "$@"` in container entrypoints so the app is PID 1 and gets signals.

---

> Next: [10 — Advanced & Enterprise](10_advanced_enterprise.md) — scaling this up: `xargs -P` and GNU `parallel` for managed concurrency, writing production `systemd` units, and the patterns that take your scripts from "works on my box" to fleet-grade.
