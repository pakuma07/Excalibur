# 02 — BSD vs GNU Userland

> **Audience:** Linux engineers automating a Mac. If you have ever shipped a shell script that worked flawlessly on your CI runner and then exploded on a developer's laptop, this chapter is the reason. macOS inherits its command-line utilities from BSD (specifically FreeBSD and the older NetBSD lineage), **not** from GNU coreutils. The tools have the same names — `sed`, `date`, `grep`, `stat` — but different flags, different defaults, and occasionally different behavior on identical input. This is the single biggest source of cross-platform breakage. Master this chapter and 90% of your "works on Linux, breaks on the Mac" bugs disappear.

---

## 1. Why this happens at all

GNU coreutils is GPL-licensed. Apple has spent two decades migrating away from GPLv3 software, so the `/usr/bin` and `/bin` tools on macOS are BSD-licensed equivalents frozen at versions that predate GPLv3. The practical consequences:

- Flags you rely on (`sed -i` with no arg, `grep -P`, `stat -c`) either do something different or do not exist.
- macOS tools are often **older** than their Linux counterparts and lack newer features.
- Error messages differ, so your `2>&1 | grep "error string"` matching can silently fail.

You have two escape routes, covered in §12: write strictly portable scripts, or install the GNU tools via Homebrew. First, the offenders.

> See [01 — The macOS Shell Landscape](01_shell_landscape.md) for why the default shell is `zsh` and where these binaries live (`/usr/bin` vs `/opt/homebrew/bin`).

---

## 2. `sed -i` — the #1 break

GNU `sed` treats `-i` as taking an *optional* suffix attached to the flag. BSD `sed` treats `-i` as taking a *mandatory* suffix as the **next argument**. An empty string means "no backup."

```bash
# WRONG (GNU-ism) — on macOS this consumes 's/a/b/' as the backup
# suffix, then treats the filename as the script. Cryptic failure.
sed -i 's/foo/bar/' file.txt

# RIGHT (BSD/macOS) — empty '' is a SEPARATE argument = no backup
sed -i '' 's/foo/bar/' file.txt

# RIGHT (GNU/Linux) — suffix is glued to the flag, no space
sed -i 's/foo/bar/' file.txt      # in-place, no backup
sed -i.bak 's/foo/bar/' file.txt  # keep file.txt.bak
```

- **Symptom:** `sed: 1: "file.txt": invalid command code f` or a `.txt''`-named backup file appears.
- **Cause:** BSD `-i` ate the wrong argument because the suffix must be separate.
- **Fix:** pick a *portable* form below — there is **no** single `sed -i` invocation that works on both.

### Portable approaches

```bash
# (a) Temp file + move — works EVERYWHERE, no sed -i at all
sed 's/foo/bar/' file.txt > file.txt.tmp && mv file.txt.tmp file.txt

# (b) perl is GNU-compatible AND ships on every Mac
perl -i -pe 's/foo/bar/' file.txt
perl -i.bak -pe 's/foo/bar/' file.txt   # with backup

# (c) Use GNU sed explicitly (after: brew install gnu-sed)
gsed -i 's/foo/bar/' file.txt
```

The temp-file form (a) is the most robust and avoids any `sed` dialect entirely. `perl -i -pe` (b) is the pragmatic favorite: `perl` is preinstalled on macOS and Linux, and its regex is a superset of `sed`'s.

---

## 3. `date` — incompatible date math

GNU `date` does relative dates with `-d`. BSD `date` uses `-v` adjustments and `-r` for epochs. These flags **do not overlap at all**.

```bash
# --- Relative dates ---
# WRONG (GNU): -d does not exist on BSD date
date -d 'yesterday' +%Y-%m-%d
date -d '-1 day'    +%Y-%m-%d
# RIGHT (BSD/macOS): -v adjusts the current time, repeatable
date -v-1d +%Y-%m-%d          # yesterday
date -v+1m -v-3d +%Y-%m-%d    # +1 month then -3 days

# --- Epoch -> human ---
# WRONG (GNU): @ prefix means "this is an epoch"
date -d @1700000000 +%Y-%m-%d
# RIGHT (BSD/macOS): -r takes an epoch seconds value
date -r 1700000000 +%Y-%m-%d

# --- Human -> epoch (current time) ---
date +%s                       # SAME on both — always works

# --- Parse a fixed string -> epoch ---
# WRONG (GNU)
date -d '2026-01-01 00:00:00' +%s
# RIGHT (BSD/macOS): -j (don't set clock) -f (input format)
date -j -f '%Y-%m-%d %H:%M:%S' '2026-01-01 00:00:00' +%s
```

- **Symptom:** `date: illegal option -- d` (BSD) or `date: invalid date '@...'` (GNU).
- **Cause:** the flag vocabularies are disjoint.
- **Fix:** branch on platform, or `brew install coreutils` and call `gdate` (full GNU semantics). `date +%s` and most `+FORMAT` strings are identical, so only *math* and *parsing* need special handling.

```bash
# Portable "epoch N seconds ago"
now=$(date +%s)
ago=$((now - 86400))   # arithmetic is shell, not date — fully portable
```

---

## 4. `readlink -f` / `realpath` — canonical paths

GNU's `readlink -f` and `realpath` resolve a path to its absolute, symlink-free form. Stock macOS historically shipped **neither** with those semantics: BSD `readlink` has no `-f`, and `realpath` was absent on older releases.

> Note: macOS 12.3+ (and recent releases generally) *do* ship a `realpath` binary, but it is the BSD one and its flag set differs from GNU `realpath`. Do not assume GNU options like `--relative-to` exist.

```bash
# WRONG (relies on GNU readlink -f / GNU realpath flags)
readlink -f "$path"
realpath --relative-to="$base" "$path"

# RIGHT — pure-shell canonicalizer, works on any POSIX shell
canonicalize() {
  # resolves symlinks in the *directory* and returns abs path
  cd "$(dirname "$1")" || return 1
  printf '%s/%s\n' "$(pwd -P)" "$(basename "$1")"
}
canonicalize ./some/relative/file

# RIGHT — perl (preinstalled), full symlink resolution
perl -MCwd -e 'print Cwd::abs_path($ARGV[0]), "\n"' "$path"

# RIGHT — GNU tool after: brew install coreutils
grealpath "$path"
greadlink -f "$path"
```

- **Fix:** prefer the `perl -MCwd` one-liner for true canonicalization; use the `cd`/`pwd -P` function when you only need an absolute path and can tolerate not resolving a symlinked final component.

---

## 5. `grep` — no PCRE

BSD `grep` does **not** support `-P` (Perl-Compatible Regular Expressions). It supports BRE (`grep`), ERE (`grep -E`), and fixed strings (`grep -F`). `-o` (only-matching) **is** supported on macOS.

```bash
# WRONG (GNU): -P PCRE features (lookahead, \d, \K, lazy quantifiers)
grep -P '(?<=id=)\d+' file
grep -oP '\d{3}-\d{4}' file

# RIGHT — perl handles PCRE everywhere
perl -lne 'print $1 if /id=(\d+)/' file
perl -lne 'print $& if /\d{3}-\d{4}/' file

# RIGHT — ERE covers many cases without PCRE (no \d; use [0-9])
grep -oE '[0-9]{3}-[0-9]{4}' file

# RIGHT — GNU grep after: brew install grep  (installs ggrep)
ggrep -oP '(?<=id=)\d+' file
```

- **Symptom:** `grep: invalid option -- P` or `grep: unrecognized option`.
- **Cause:** BSD grep has no PCRE engine compiled in.
- **Fix:** rewrite in ERE if the pattern allows, otherwise use `perl` or `ggrep`. Watch for `\d`, `\w`, `\s`, lookarounds, and `\K` — none exist in BRE/ERE.

---

## 6. `stat` — totally different format strings

This one is brutal because both tools accept *a* format string, so they fail by producing garbage rather than erroring.

```bash
# WRONG (GNU): -c with % codes
stat -c '%s' file        # size in bytes
stat -c '%Y' file        # mtime epoch

# RIGHT (BSD/macOS): -f with DIFFERENT % codes
stat -f '%z' file        # size in bytes
stat -f '%m' file        # mtime epoch
```

| Want | GNU `stat -c` | BSD/macOS `stat -f` |
|------|---------------|---------------------|
| Size (bytes) | `%s` | `%z` |
| Mtime (epoch) | `%Y` | `%m` |
| Atime (epoch) | `%X` | `%a` |
| Ctime (epoch) | `%Z` | `%c` |
| Permission (octal) | `%a` | `%Lp` |
| Permission (symbolic) | `%A` | `%Sp` |
| Owner name | `%U` | `%Su` |
| Owner UID | `%u` | `%u` |
| File type | `%F` | `%HT` |
| Hard link count | `%h` | `%l` |

- **Fix:** `brew install coreutils` and call `gstat -c ...` for full GNU semantics, or branch:

```bash
filesize() {
  if stat --version >/dev/null 2>&1; then
    stat -c '%s' "$1"        # GNU
  else
    stat -f '%z' "$1"        # BSD/macOS
  fi
}
# Portable alternative with no stat at all:
wc -c < "$1"                 # byte count, works everywhere
```

---

## 7. `xargs` — the empty-input footgun

```bash
# WRONG (GNU): -r ("no-run-if-empty") not supported on BSD xargs
find . -name '*.log' | xargs -r rm

# DANGER: BSD xargs with NO input still runs the command ONCE
echo -n '' | xargs rm        # on macOS: runs `rm` with no args!
```

- **Symptom:** a command runs unexpectedly on empty input; or `xargs: illegal option -- r`.
- **Cause:** BSD `xargs` lacks `-r`; its default is to run once even with empty stdin (GNU's default also runs once, but you defended with `-r`).
- **Fix:** guard explicitly, or use `find -exec`, or use `-0` with NUL-delimited input (supported on both).

```bash
# RIGHT — let find run the command; no empty-input invocation
find . -name '*.log' -exec rm {} +

# RIGHT — NUL-safe and portable; both BSD and GNU support -0
find . -name '*.log' -print0 | xargs -0 rm

# -I replacement string: both support -I, but BSD -I implies one
# arg per line. GNU -I{} and BSD -I {} both work; quote the token.
find . -name '*.tmp' -print0 | xargs -0 -I{} mv {} /tmp/
```

Note: GNU's `-I` defaults to `-L1` (one line per command) on both, but BSD does not support the `-i` (lowercase, deprecated) alias the same way. Always use uppercase `-I` with an explicit replacement string.

---

## 8. `base64` — wrapping and decode flag

```bash
# --- No line wrapping when encoding ---
# WRONG (GNU): -w0 disables wrapping
base64 -w0 file
# RIGHT (BSD/macOS): -b 0 ... but BSD base64 does NOT wrap by default
base64 -i file               # macOS: single line already (no wrap)
base64 < file                # also fine on macOS

# --- Decoding ---
# WRONG (GNU): -d to decode
echo "aGk=" | base64 -d
# RIGHT (BSD/macOS): -D (capital D)
echo "aGk=" | base64 -D
```

- **Symptom:** `base64: invalid option -- w` or `base64: invalid option -- d`.
- **Cause:** GNU uses `-w`/`-d`; BSD uses `-b`/`-D` and does not wrap output by default.
- **Fix:** the most portable decode is to feed both flags through a wrapper, or use `openssl base64 -d` which is identical on both platforms:

```bash
# Portable encode (no newlines) and decode via openssl
openssl base64 -A -in file          # encode, -A = no line breaks
echo "aGk=" | openssl base64 -d -A  # decode
```

---

## 9. `find` — mostly compatible, two gaps

`find` is one of the more portable tools. `-name`, `-type`, `-print0`, `-exec ... +`, `-maxdepth`, `-mtime` all work on both.

```bash
# WRONG (GNU): -printf has NO BSD equivalent
find . -type f -printf '%s %p\n'

# RIGHT — use -exec with a tool that formats, e.g. stat (per §6)
find . -type f -exec stat -f '%z %N' {} +     # BSD/macOS
find . -type f -exec stat -c '%s %n' {} +     # GNU/Linux

# SAFE everywhere: -print0 for NUL-delimited output
find . -type f -name '*.sh' -print0 | xargs -0 chmod +x
```

- **Gap 1:** `-printf` is GNU-only; there is no BSD `find` equivalent — pipe through `stat` or a shell loop.
- **Gap 2:** GNU `-regextype` and `-regex` semantics differ from BSD `-E`/`-regex`. For regex matching on macOS, use `find -E . -regex '...'` (note `-E` comes *before* the path).

---

## 10. Quick-reference: the rest

| Tool | GNU-ism (WRONG on mac) | BSD/macOS (RIGHT) | Portable fix |
|------|------------------------|-------------------|--------------|
| `cp` | `cp -a src dst` (archive) | `cp -pR src dst` | use `cp -pR`, or `gcp -a` |
| `cp` | `cp --parents` | (no equivalent) | `rsync -R`, or build paths manually |
| `ls` | `ls --color=auto` | `ls -G` | set `CLICOLOR=1`; avoid `--color` |
| `ls` | `ls --time-style=...` | (no equivalent) | `stat`/`date -r` per file |
| `mktemp` | `mktemp` (template optional) | template-or-`-t` needed | `mktemp -t prefix` works on both |
| `sort` | `sort -h` (human sizes) | not on older BSD | `gsort -h`, or numeric `sort -n` |
| `tac` | `tac file` | **missing** | `tail -r file` (BSD), or `gtac` |
| `timeout` | `timeout 5 cmd` | **missing** | `gtimeout 5 cmd` (coreutils) |
| `seq` | `seq 1 10` | present, mostly same | OK; avoid `-w` edge cases |
| `head`/`tail` | `head -n -5` (all but last 5) | not on BSD | `ghead`, or awk |
| `wc` | `wc -l` | same | identical |
| `echo` | `echo -e` (interprets escapes) | varies by shell builtin | use `printf` always |

```bash
# mktemp — the portable form (works on both):
tmp=$(mktemp -t myscript)        # creates /tmp/myscript.XXXX or similar
trap 'rm -f "$tmp"' EXIT

# tac replacement on macOS:
tail -r file.txt                  # BSD reverse; GNU has no -r, use tac there
```

> `echo -e` and `echo -n` portability is a minefield (the shell builtin's behavior depends on `zsh`/`bash`/`sh` and `xpg_echo`). **Always use `printf`** for anything with escapes or no-trailing-newline. See [01 — The macOS Shell Landscape](01_shell_landscape.md) for the shell-builtin details.

---

## 11. Cross-toolkit reference

Everything in the Linux text-processing playbook — [../linux/07_text_processing.md](../linux/07_text_processing.md) — assumes GNU semantics. When porting those recipes to a Mac, the high-risk substitutions are: `sed -i` (§2), `grep -P` (§5), `date -d` (§3), and `stat -c` (§6). `awk` is comparatively safe because macOS ships a BWK `awk` that handles standard one-liners, but GNU `gawk` extensions (`gensub`, `asort`, `strftime`, `--re-interval` is on by default) require `brew install gawk` → `gawk`.

---

## 12. Two strategies for portable scripts

### Strategy A — Stay POSIX, feature-detect

Restrict yourself to POSIX-specified options and behavior, avoid GNU-only flags, and detect capabilities at runtime with `command -v`. This produces scripts that run on a bare Mac, a bare Linux box, and Alpine/BusyBox alike — no install step.

```bash
#!/usr/bin/env bash
set -euo pipefail

# --- Resolve GNU tool if present, else fall back ---
# Prefer g-prefixed coreutils, then plain name, then a shim.
SED=$(command -v gsed || command -v sed)
DATE=$(command -v gdate || command -v date)
STAT=$(command -v gstat || command -v stat)

# In-place edit that works regardless of which sed we found:
sed_inplace() {
  local expr=$1 file=$2
  if "$SED" --version >/dev/null 2>&1; then
    "$SED" -i    "$expr" "$file"   # GNU
  else
    "$SED" -i '' "$expr" "$file"   # BSD
  fi
}

# Detect GNU vs BSD generically:
is_gnu() { "$1" --version >/dev/null 2>&1; }   # GNU tools accept --version
```

The `--version` probe is the canonical detector: GNU tools respond to `--version`; BSD tools error out (exit non-zero / print usage). Capture that to branch.

### Strategy B — Install GNU coreutils via Homebrew

If your scripts only ever run on machines you control, just install the GNU userland and use it.

```bash
brew install coreutils gnu-sed gawk findutils grep gnu-tar grep
```

By default these install with a `g` prefix (`gsed`, `gdate`, `gstat`, `ggrep`, `gfind`, `grealpath`, `gtimeout`, `gtac`, `gsort`) so they don't shadow the system tools. Two ways to use them:

```bash
# (1) Call them by g-name explicitly — safest, no surprises:
gsed -i 's/a/b/' file
gdate -d 'yesterday' +%F

# (2) Prepend the gnubin dirs to PATH so `sed` == GNU sed.
#     Each formula prints its gnubin path on install; e.g.:
export PATH="/opt/homebrew/opt/coreutils/libexec/gnubin:$PATH"
export PATH="/opt/homebrew/opt/gnu-sed/libexec/gnubin:$PATH"
export PATH="/opt/homebrew/opt/grep/libexec/gnubin:$PATH"
export PATH="/opt/homebrew/opt/findutils/libexec/gnubin:$PATH"
# Now `sed`, `grep`, `find`, `date`, `stat` are the GNU versions.
```

> **Trade-off:** PATH-prepending (option 2) makes your *interactive* shell match Linux, which is convenient — but it can mask portability bugs, because a script that works on your machine will still break on a teammate's stock Mac. For CI and shipped scripts, prefer explicit `g`-names or Strategy A. (On Intel Macs the prefix is `/usr/local/opt/...` instead of `/opt/homebrew/opt/...` — see [01 — The macOS Shell Landscape](01_shell_landscape.md).)

### Drop-in portability detection snippet

```bash
#!/usr/bin/env bash
# portability.sh — source this at the top of cross-platform scripts.
set -euo pipefail

detect_platform() {
  case "$(uname -s)" in
    Darwin) PLATFORM=macos ;;
    Linux)  PLATFORM=linux ;;
    *)      PLATFORM=other ;;
  esac
}

# Pick the best available implementation of a tool, preferring GNU.
pick() {                       # pick VARNAME gname name [name...]
  local var=$1; shift
  local t
  for t in "$@"; do
    if command -v "$t" >/dev/null 2>&1; then
      printf -v "$var" '%s' "$(command -v "$t")"
      return 0
    fi
  done
  echo "FATAL: none of [$*] found in PATH" >&2
  return 1
}

detect_platform
pick SED      gsed sed
pick DATE     gdate date
pick STAT     gstat stat
pick GREP     ggrep grep
pick REALPATH grealpath realpath
pick FIND     gfind find

# Usage downstream:
#   "$STAT" -c '%s' file   # only if is_gnu "$STAT"; else -f '%z'
is_gnu() { "$1" --version >/dev/null 2>&1; }
```

This gives you a single sourced header that resolves to GNU tools when available and degrades to the stock BSD tools otherwise — the best of both strategies.

---

> Next: [03 — launchd & Scheduling](03_launchd_scheduling.md) — macOS replaced `cron` with `launchd`. We'll translate your crontab into LaunchAgents and LaunchDaemons, cover plist anatomy, `launchctl bootstrap`/`kickstart`, run-at-load vs intervals, and why your cron job silently never fired.
