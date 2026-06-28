# 01 — Fundamentals

> **Audience:** Engineers who write shell scripts that run in CI, containers, cron, and on production hosts — and are tired of "works on my machine." This chapter is the load-bearing foundation: get the shebang, the interpreter, and the execution model wrong and every later chapter is built on sand. We are opinionated here because in ops the difference between `sh` and `bash`, or between sourcing and executing, is the difference between a clean deploy and a 2 a.m. page.

---

## 1. What a shell actually is

A shell is a program that reads text, expands it, and executes commands. It is simultaneously a programming language and the thing that launched your terminal. The two roles cause most confusion.

```bash
ps -p $$ -o comm=   # which shell is running THIS process? e.g. bash, zsh, dash
echo "$SHELL"       # your LOGIN shell from /etc/passwd — NOT necessarily the current one
```

Symptom/Cause/Fix for the most common beginner trap:

- **Symptom:** A script "works" when you paste it into your terminal but fails under cron or CI.
- **Cause:** Your interactive shell is `bash` (or `zsh`), but the script ran under `/bin/sh` (often `dash`).
- **Fix:** Pin the interpreter with a correct shebang (Section 3) and never assume the ambient shell.

---

## 2. `sh` vs `bash` vs POSIX/dash

These names get used interchangeably and they are not the same thing.

| Name | What it is | Notes |
|------|------------|-------|
| `sh` | A *contract* (the POSIX shell spec) | On Debian/Ubuntu it's `dash`; on Alpine it's `busybox sh`; on macOS it's `bash` in POSIX mode |
| `bash` | The Bourne-Again Shell | Superset of POSIX; has arrays, `[[ ]]`, `local`, process substitution |
| `dash` | Debian Almquist Shell | Tiny, fast, strictly POSIX-ish; `/bin/sh` on Debian/Ubuntu |
| `zsh` | Z shell | Default *interactive* shell on macOS; not POSIX, see [../mac/01_shell_landscape.md](../mac/README.md) |

The trap: bash-only syntax under a `#!/bin/sh` shebang.

```bash
# WRONG — runs fine under bash, breaks under dash
#!/bin/sh
arr=(a b c)            # dash: "Syntax error: ( unexpected"
[[ $x == foo* ]]       # dash: "[[: not found"

# RIGHT — either declare bash, or stay strictly POSIX
#!/usr/bin/env bash
arr=(a b c)
[[ $x == foo* ]]
```

Rule of thumb: if you use arrays, `[[ ]]`, `local`, `+=`, or `${var,,}`, you are writing **bash**, so say so in the shebang. "It's just a shell script" is how you ship a dash bug.

---

## 3. The shebang

The shebang (`#!`) is the first line; the kernel reads it to pick an interpreter. It must be byte 0 of the file.

```bash
#!/usr/bin/env bash    # PREFERRED — finds bash via PATH, portable across distros/macOS
#!/bin/bash            # OK on Linux; on macOS this is ancient bash 3.2
#!/bin/sh              # POSIX only — opt into this deliberately, not by accident
```

Symptom/Cause/Fix:

- **Symptom:** `./script.sh: bad interpreter: No such file or directory`.
- **Cause:** Hard-coded path (`/bin/bash`) that doesn't exist on this host, or CRLF line endings making the path `"/bin/bash\r"`.
- **Fix:** Use `#!/usr/bin/env bash`; run `file script.sh` and `sed -i 's/\r$//' script.sh` to strip Windows CRLF.

```bash
file script.sh         # "...with CRLF line terminators" => your shebang is broken
```

Why `env`: the kernel does no PATH lookup on the shebang, so `#!/usr/bin/env bash` delegates the lookup to `env`, finding whichever bash is first on PATH (e.g. a Homebrew bash 5 over macOS's 3.2). One caveat: the kernel passes only a single argument after the interpreter, so portable shebangs can't reliably stack flags like `#!/usr/bin/env bash -eu` — set options inside the script instead (Section 6).

---

## 4. Running a script: five ways, three meanings

```bash
chmod +x script.sh        # make it executable (once)
./script.sh               # NEW process, uses the shebang
bash script.sh            # NEW process, IGNORES the shebang — bash is chosen explicitly
sh script.sh              # NEW process under sh — your bashisms may now break
source script.sh          # SAME shell — runs in your current environment
. script.sh               # identical to `source` (POSIX spelling)
```

The distinction that bites people: **subshell vs current shell.**

```bash
# setenv.sh
export DEPLOY_ENV=prod
cd /srv/app

./setenv.sh    # WRONG if you wanted the vars — they died with the child process
echo "$DEPLOY_ENV"   # empty; you're still in your old dir

source setenv.sh   # RIGHT — exports and cd take effect in YOUR shell
echo "$DEPLOY_ENV"   # prod
```

Symptom/Cause/Fix:

- **Symptom:** `nvm`, `conda activate`, or a "load these env vars" script does nothing.
- **Cause:** You executed it (`./`) in a child process; the changes vanished on exit.
- **Fix:** `source` it. This is exactly why activation scripts tell you to `source`, never `./`.

Also note `chmod +x` is irrelevant when you call the interpreter explicitly: `bash script.sh` works on a non-executable file; `./script.sh` requires the execute bit.

---

## 5. The execution model

The shell processes input in stages, and knowing the order explains 90% of quoting surprises (deepened in [02 — Variables, Quoting & Expansion](02_variables_quoting_expansion.md)):

1. **Read** a line (or here-doc/continuation).
2. **Tokenize / parse** into words and operators.
3. **Expand** — brace, tilde, parameter (`$x`), command (`$(...)`), arithmetic, then **word splitting** and **globbing**.
4. **Execute** — builtin, function, or external program; perform redirections.

The killer detail: expansion happens *before* execution, and word splitting happens *after* variable expansion. That's why unquoted variables explode.

```bash
f="my file.txt"
rm $f          # WRONG — runs: rm my file.txt  (two args!)
rm "$f"        # RIGHT — one argument
```

### Interactive vs non-interactive, login vs non-login

This controls **which startup files run**, which controls whether your `PATH`/aliases/functions exist.

| Invocation | Login? | Interactive? | Files read (bash) |
|------------|--------|--------------|-------------------|
| SSH / console login | yes | yes | `/etc/profile`, then first of `~/.bash_profile`, `~/.bash_login`, `~/.profile` |
| New terminal tab / `bash` | no | yes | `~/.bashrc` |
| `bash script.sh` / cron / CI | no | no | **none** (only `$BASH_ENV` if set) |

```bash
case $- in *i*) echo interactive;; *) echo non-interactive;; esac
shopt -q login_shell && echo login || echo non-login
```

Symptom/Cause/Fix — the classic cron failure:

- **Symptom:** Script runs fine in your terminal, fails in cron with "command not found".
- **Cause:** Cron is non-interactive, non-login: **no rc files**, so a minimal `PATH` (often `/usr/bin:/bin`). Your `~/.bashrc` PATH additions never loaded.
- **Fix:** Set `PATH` explicitly at the top of the script, or use absolute paths. Don't rely on the ambient environment.

```bash
#!/usr/bin/env bash
export PATH="/usr/local/bin:/usr/bin:/bin"   # be explicit in cron/CI
```

A common pattern is to have `~/.bash_profile` source `~/.bashrc` so login shells also get your interactive setup — but never put PATH-critical logic only in rc files that batch contexts skip.

---

## 6. A preview of `set` (strict mode)

These flags change how the shell reacts to errors. We introduce them here and deepen them in [08 — Error Handling, Strict Mode & Debugging](08_error_handling_debugging.md).

```bash
set -e            # exit immediately if a command exits non-zero
set -u            # error on use of an UNSET variable (catches typos)
set -o pipefail   # a pipeline fails if ANY stage fails, not just the last
set -x            # xtrace: print each command before running it (debugging)
```

The idiomatic strict-mode header:

```bash
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'       # safer word splitting; see chapter 02
```

Why each matters in production:

```bash
# Without -e: this script "succeeds" even though the build failed
make build        # exits 1, but...
deploy            # ...this runs anyway. Disaster.

# Without pipefail: the pipe hides the failure
curl -f bad-url | tar xz   # curl fails, tar gets empty input, exit code is tar's 0

# Without -u: a typo silently nukes the wrong path
rm -rf "$prefx/data"   # $prefx is unset -> "rm -rf /data"
```

Caveat (covered fully in chapter 08): `set -e` has surprising exceptions — it does **not** trigger inside `if`/`while` conditions, `&&`/`||` chains, or for the non-last command of a pipe (hence `pipefail`). It is a sharp tool, not a safety net; treat it as a tripwire and still check exit codes where it matters.

---

## 7. Exit codes & `$?`

Every command returns an integer 0–255. **0 means success**; anything else is failure. This is the inverse of most programming languages, where 0 is falsy — in the shell, "zero problems" is true.

```bash
ls /tmp;       echo $?   # 0
ls /nonexistent; echo $?  # 2 (error)
```

`$?` holds the exit status of the **most recent** command — it's volatile, so capture it immediately.

```bash
backup.sh
rc=$?                     # capture NOW
if (( rc != 0 )); then
  echo "backup failed: $rc" >&2
  exit "$rc"
fi
```

Symptom/Cause/Fix:

- **Symptom:** `if [ $? -eq 0 ]` always looks like success.
- **Cause:** A command (even `echo`) ran between the one you cared about and the `$?` check, overwriting it.
- **Fix:** Test the command directly — `if backup.sh; then ...` — or capture `$?` on the very next line.

Useful conventions:

```bash
exit 0      # success
exit 1      # generic failure
exit 2      # CLI misuse (bad args) — convention, mirrors many tools
# 126 = found but not executable; 127 = command not found
# 128+N = killed by signal N (e.g. 130 = Ctrl-C / SIGINT)
true; echo $?    # 0  — the `true` builtin exists only to return 0
false; echo $?   # 1  — and `false` only to return 1
```

The last command's exit code becomes the script's exit code if you don't `exit` explicitly — so a script ending in a failing command exits non-zero whether you meant it to or not.

```bash
# WRONG — script's exit code is grep's: 1 if "ok" not found, surprising callers
grep -q ok log.txt

# RIGHT — be explicit about what the script reports
if grep -q ok log.txt; then exit 0; else exit 1; fi
```

---

## 8. Putting it together — a minimal correct script

```bash
#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

export PATH="/usr/local/bin:/usr/bin:/bin"   # don't trust the ambient env

main() {
  local target="${1:?usage: deploy.sh <target>}"   # -u-friendly arg check
  echo "deploying to ${target}" >&2
  # ... real work ...
}

main "$@"
```

This single file demonstrates every concept in this chapter: a portable shebang, strict mode, an explicit PATH for batch contexts, exit-code-aware argument handling, and a `main "$@"` pattern that keeps definitions above execution.

---

> Next: [02 — Variables, Quoting & Expansion](02_variables_quoting_expansion.md) — why `"$@"` and `"$var"` need quotes, how word splitting and globbing turn one variable into three arguments, and the parameter-expansion toolkit (`${x:-default}`, `${x%.txt}`, `${x,,}`) that replaces most of your `sed`/`awk` reflexes.
