# 08 — Error Handling, Strict Mode & Debugging

> **Audience:** Engineers who can already write working Bash but whose scripts fail *silently*, *halfway*, or *unpredictably* in production. This is the chapter that separates a script you `chmod +x` and forget from one you bet a pager rotation on. By the end you will know exactly what `set -euo pipefail` does, where it *lies to you*, and how to debug the aftermath.

---

## 1. The default Bash failure model is broken

Bash, by default, runs like nothing matters. A command fails, prints an error, and the script *keeps going* with corrupt state.

```bash
#!/usr/bin/env bash
# WRONG — no strict mode
cd /var/data/$PROJECT      # $PROJECT is unset -> cd /var/data -> SUCCEEDS
rm -rf ./build             # deletes the WRONG build dir, exit 0
echo "done"                # cheerfully reports success
```

- **Symptom:** Script reports success while having deleted, deployed, or corrupted the wrong thing.
- **Cause:** Unset variables expand to empty strings; failed commands don't stop execution; pipeline exit status only reflects the *last* command.
- **Fix:** Opt into a stricter execution model and add explicit checks. The rest of this chapter is that model.

See [01 — Fundamentals](01_fundamentals.md) for variable expansion and [04 — Control Flow](04_control_flow.md) for exit-status semantics this chapter builds on.

---

## 2. `set -e` (errexit) — and why it lies to you

`set -e` exits the script the moment a command returns non-zero.

```bash
set -e
cp missing.txt /tmp/   # exits here, line never reached below
echo "unreachable"
```

That sounds like the whole solution. It is not. `set -e` has a long list of cases where it **does not fire**, and they are exactly the cases people assume it does.

### The gotcha table

| Construct | Does `set -e` trigger on failure? | Why |
|---|---|---|
| `cmd` (standalone) | ✅ Yes | The simple case |
| `if cmd; then …` | ❌ No | Command is a *condition*, failure is expected |
| `cmd && other` / `cmd \|\| other` | ❌ No (except last) | Only the final command of the list is checked |
| `while cmd; do …` | ❌ No | Loop condition |
| `! cmd` | ❌ No | Negation makes failure "expected" |
| `cmd \|\| true` | ❌ No (by design) | The explicit opt-out idiom |
| `foo \| bar` (bar succeeds) | ❌ No (without pipefail) | Only last pipe element counts |
| `func` whose body fails inside `if func` | ❌ No | errexit is *disabled* inside functions invoked in a condition |
| `local v=$(failing)` | ❌ No | `local` return status masks the substitution's |
| `(( count++ ))` when result is 0 | ✅ Yes (surprise!) | Arithmetic evaluating to 0 returns exit 1 |

That last two bite even experts.

```bash
# WRONG — set -e will NOT save you here
set -e
grep "pattern" file.log | wc -l   # grep finds nothing -> exit 1, but wc succeeds -> pipeline OK
count=0
(( count++ ))                     # post-increment returns the OLD value 0 -> exit 1 -> SCRIPT DIES

# RIGHT
set -e
if ! grep -q "pattern" file.log; then echo "not found"; fi
(( ++count )) || true             # pre-increment, or guard the arithmetic
```

- **Symptom:** A counter increment or a benign command kills the script; meanwhile a genuinely failed pipeline sails through.
- **Cause:** `set -e` checks the *last* command of a list/pipeline and arithmetic-evaluating-to-zero is "failure" to Bash.
- **Fix:** Use `pipefail` (next section) and guard arithmetic with `|| true` or pre-increment.

> **Be honest:** `set -e` is a *safety net for the cases you forgot*, not a substitute for explicit error checking. Anywhere correctness matters, write the `if`. Treat errexit as defense in depth, never as your primary strategy.

---

## 3. `set -u` (nounset) — fail on unset variables

```bash
set -u
echo "Deploying to $TARGET_ENV"   # unset -> "TARGET_ENV: unbound variable", exits
```

This catches the classic `rm -rf "$PREFIX/"` disaster where `$PREFIX` is empty. But it also breaks legitimate "maybe-unset" reads.

```bash
# WRONG — crashes under set -u if the var is optional
if [[ -n "$OPTIONAL_FLAG" ]]; then ...

# RIGHT — provide a default with :- so the expansion is always defined
if [[ -n "${OPTIONAL_FLAG:-}" ]]; then ...

# Other useful forms:
: "${REQUIRED_VAR:?must be set}"      # abort with a message if unset/empty
log_dir="${LOG_DIR:-/var/log/app}"   # default value
"${ARGS[@]:-}"                         # safe empty-array expansion (Bash 4.3-)
```

- **Symptom:** Script aborts with `unbound variable` on a parameter that is *optionally* set.
- **Cause:** `set -u` treats *any* reference to an unset variable as fatal.
- **Fix:** Use `"${VAR:-}"` for optional reads and `"${VAR:?msg}"` to assert required ones.

---

## 4. `set -o pipefail` — fix the pipeline blind spot

Without `pipefail`, a pipeline's exit status is that of the **last** command only.

```bash
# WRONG — failure of curl is invisible
set -e
curl -fsS https://api.example.com/data | jq '.items'   # curl 404s, jq gets empty, exit 0

# RIGHT
set -eo pipefail
curl -fsS https://api.example.com/data | jq '.items'   # now curl's non-zero propagates
```

`pipefail` makes the pipeline return the rightmost *non-zero* status. Combined with `set -e`, the whole script stops. Use `PIPESTATUS[@]` to inspect each stage when you need detail:

```bash
set -o pipefail
producer | consumer
echo "exit codes: ${PIPESTATUS[*]}"   # e.g. "1 0"
```

---

## 5. The strict-mode idiom: `set -euo pipefail` + `IFS`

The canonical header for a serious script:

```bash
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'        # split words only on newline/tab, NOT spaces
```

Why `IFS=$'\n\t'`? The default `IFS` includes the space, so unquoted expansions split on spaces and mangle filenames with spaces. Restricting `IFS` to newline+tab makes word-splitting in `for x in $list` far less surprising.

```bash
# With default IFS, this loops over 3 "words":
files="a file.txt"
for f in $files; do echo "[$f]"; done   # [a] [file.txt] -> WRONG

# With IFS=$'\n\t' and newline-separated input, it loops once correctly.
```

> **Caveat:** strict mode is a *strong default*, not a magic shield. `set -e`'s gotchas (section 2) still apply, and `IFS=$'\n\t'` can surprise code that *relies* on space-splitting. Always quote your expansions: `"$var"`, `"${arr[@]}"`. Strict mode plus quoting plus explicit checks is the real package.

---

## 6. `trap` — cleanup and error reporting

`trap` runs code on signals or pseudo-signals. The big four for scripting:

| Signal | Fires when | Typical use |
|---|---|---|
| `EXIT` | Script exits for *any* reason | Cleanup (temp files, locks) |
| `ERR` | A command fails (under `set -e` rules) | Error reporting |
| `INT` | Ctrl-C (SIGINT) | Graceful interrupt |
| `TERM` | `kill` (SIGTERM) | Graceful shutdown |

### Guaranteed cleanup with `EXIT`

```bash
set -euo pipefail

tmpdir="$(mktemp -d)"                          # never hardcode /tmp/foo.$$
cleanup() {
  rm -rf "$tmpdir"                             # runs on success, error, OR Ctrl-C
}
trap cleanup EXIT

work_in "$tmpdir"
# no explicit cleanup call needed — EXIT covers all paths
```

`EXIT` fires exactly once, on *every* exit path. This is the single most valuable trap: it makes leaks of temp files and stale lock files structurally impossible.

### An `err()` trap that tells you *where* it broke

```bash
set -euo pipefail

err() {
  local exit_code=$?
  echo "ERROR: '${BASH_COMMAND}' exited ${exit_code}" >&2
  echo "  at ${BASH_SOURCE[1]}:${LINENO} in ${FUNCNAME[1]:-main}()" >&2
}
trap err ERR
```

- `$?` — the exit code of the failed command (capture it *first*, before anything else clobbers it).
- `$BASH_COMMAND` — the command that was running.
- `$LINENO` / `${BASH_SOURCE[@]}` / `${FUNCNAME[@]}` — line, file, and call-stack arrays for a poor-man's backtrace.

> **Note:** by default `ERR` and other traps are *not* inherited by functions, subshells, or command substitutions. Add `set -E` (errtrace) so the `ERR` trap fires inside functions too — pair it with `-T` (functrace) for `DEBUG`/`RETURN` traps.

See [09 — Processes, Jobs & Signals](09_processes_signals.md) for the full signal model, signal masking, and why you should `trap '' SIGINT` only deliberately.

---

## 7. Tracing: `set -x`, `set -v`, and `PS4`

When a script misbehaves and you can't see why, *trace it*.

```bash
set -x      # print each command (after expansion) before running it
set -v      # print each line as read (before expansion) — rarer, shows the raw source
```

```bash
$ bash -x deploy.sh        # trace without editing the file
+ cd /srv/app
+ git pull
++ date +%F                # ++ = one level of nesting (command substitution)
+ tag=2026-06-23
```

### Make the trace readable with `PS4`

The default trace prefix is just `+ `. A richer `PS4` turns trace output into a usable log:

```bash
# Show file, line, and function on every traced line:
export PS4='+ ${BASH_SOURCE##*/}:${LINENO}:${FUNCNAME[0]:-main}() '
set -x
```

```
+ deploy.sh:42:deploy() git pull
+ deploy.sh:43:deploy() systemctl restart app
```

### `BASH_XTRACEFD` — keep trace out of your data

By default `set -x` writes to stderr, which pollutes program output and your error stream. Redirect the trace to its own file descriptor:

```bash
exec 5>/tmp/trace.$$.log     # open fd 5 to a log file
export BASH_XTRACEFD=5       # send xtrace there instead of stderr
set -x
# ...stderr stays clean for real errors; full trace lands in /tmp/trace.*.log
```

This is the difference between "trace floods my terminal" and "trace is a tidy log file I tail in another window."

---

## 8. `shellcheck` — the highest-leverage tool you're not running

If you adopt **one** thing from this chapter, make it ShellCheck. It is a static analyzer that catches the bugs above *before* they run: unquoted expansions, `set -e` foot-guns, useless `cat`, `[ ]` vs `[[ ]]` mistakes, masked exit codes from `local x=$(...)`, and hundreds more.

```bash
shellcheck deploy.sh
# In deploy.sh line 12:
#   rm -rf $tmpdir
#          ^------^ SC2086: Double quote to prevent globbing and word splitting.
```

### Use it as a CI gate (non-negotiable)

```yaml
# .github/workflows/lint.yml
name: shellcheck
on: [push, pull_request]
jobs:
  shellcheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run ShellCheck
        run: |
          shopt -s globstar
          shellcheck -S warning -x **/*.sh   # fail build on warnings+; -x follows sourced files
```

Suppress *intentional* findings explicitly and with a reason — never globally:

```bash
# shellcheck disable=SC2034  # var is exported for the sourced sub-script
unused_for_now="value"
```

A repo where ShellCheck is a required check has a *categorically* lower bug rate. It is free, fast, and finds things human reviewers miss every time.

---

## 9. Defensive patterns — fail fast, fail loud

Strict mode and traps are reactive. These patterns are *proactive*.

### Validate inputs at the top

```bash
main() {
  (( $# == 2 )) || { echo "usage: $0 SOURCE DEST" >&2; exit 64; }   # 64 = EX_USAGE
  local src=$1 dest=$2
  [[ -r $src ]]  || { echo "cannot read: $src"  >&2; exit 1; }
  [[ -d $dest ]] || { echo "no such dir: $dest" >&2; exit 1; }
  command -v rsync >/dev/null || { echo "rsync required" >&2; exit 127; }
  # ...only now do real work
}
```

### Always use `mktemp`, never `$$` or fixed names

```bash
# WRONG — predictable name, race condition, symlink-attack surface
tmp=/tmp/myapp.$$

# RIGHT — atomic, unique, race-free; pair with a trap (section 6)
tmp="$(mktemp)"           # file
tmpdir="$(mktemp -d)"     # directory
trap 'rm -rf "$tmp" "$tmpdir"' EXIT
```

### Fail loud, on stderr, with a non-zero code

```bash
die() { echo "FATAL: $*" >&2; exit 1; }
fetch_config || die "could not load config from $CONFIG_URL"
```

- Errors go to **stderr** (`>&2`) so pipelines and logs separate them from data.
- Use **meaningful exit codes** (see `sysexits.h`: 64 usage, 77 permission, 127 not-found) so callers can branch.
- Prefer **failing immediately** over limping forward with bad state.

---

## 10. Putting it together — a production-grade skeleton

```bash
#!/usr/bin/env bash
#
# deploy.sh — deploy the app. Strict, traced, self-cleaning.
set -Eeuo pipefail          # -E so the ERR trap fires inside functions
IFS=$'\n\t'

readonly SCRIPT_NAME=${BASH_SOURCE##*/}

err() {
  local code=$?
  echo "[${SCRIPT_NAME}] ERROR ${code}: '${BASH_COMMAND}'" >&2
  echo "  at line ${LINENO} in ${FUNCNAME[1]:-main}()"     >&2
  exit "$code"
}
trap err ERR

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT          # cleanup on every exit path

log() { echo "[${SCRIPT_NAME}] $*" >&2; }

main() {
  (( $# >= 1 )) || { echo "usage: $SCRIPT_NAME ENV" >&2; exit 64; }
  local env=${1}
  : "${ARTIFACT_URL:?ARTIFACT_URL must be set}"

  log "deploying to ${env}"
  curl -fsS "$ARTIFACT_URL" -o "$tmpdir/app.tar.gz"   # -f: fail on HTTP errors
  tar -xzf "$tmpdir/app.tar.gz" -C "$tmpdir"
  log "done"
}

main "$@"
```

Enable tracing on demand without editing the file:

```bash
DEBUG=1 bash -x deploy.sh staging     # or wrap: [[ ${DEBUG:-} ]] && set -x
```

---

## Summary

- **`set -euo pipefail` + `IFS=$'\n\t'`** is your default header — but understand each flag's gotchas; it is defense in depth, *not* a replacement for explicit checks.
- **`set -e` does not fire** in conditions, `&&`/`||` chains (except last element), function bodies called in conditions, or arithmetic-evaluating-to-zero. Know the table.
- **`pipefail`** rescues failures hidden mid-pipeline; **`set -u`** + `"${VAR:-}"` kills empty-variable disasters.
- **`trap … EXIT`** for guaranteed cleanup, **`trap … ERR`** with `$LINENO`/`BASH_SOURCE`/`FUNCNAME` for backtraces; add `set -E` so traps reach functions.
- **`set -x` + a rich `PS4` + `BASH_XTRACEFD`** make tracing readable and tidy.
- **ShellCheck in CI** is the single highest-leverage practice — adopt it first.
- **Validate inputs, use `mktemp`, fail fast and loud on stderr with meaningful exit codes.**

Cross-references: [01 — Fundamentals](01_fundamentals.md) · [04 — Control Flow](04_control_flow.md) · [09 — Processes, Jobs & Signals](09_processes_signals.md) (trap & signals) · [10 — Advanced & Enterprise](10_advanced_enterprise.md) (bats unit-testing your error paths). Windows counterpart: [../windows/06_error_handling.md](../windows/06_error_handling.md) (`$ErrorActionPreference`, `try/catch/finally`, `trap`).

> Next: [09 — Processes, Jobs & Signals](09_processes_signals.md) — now that your scripts fail cleanly, learn how Bash spawns, backgrounds, and reaps processes; how signals actually propagate to child and process groups; and how to write scripts that shut down gracefully instead of leaving orphans behind.
