# 01 — The macOS Shell Landscape

> **Audience:** You know Bash and Linux cold, and now you've been handed a Mac to automate. Stop. The shell you think you're typing into is *not* the one Linux gave you — Apple froze Bash at a 2007 release, made zsh the default, and locked the system volume read-only. This chapter maps the terrain so your muscle memory doesn't quietly produce broken scripts. We cover the *differences*, not Bash itself.

---

## 1. zsh is the default login shell (since Catalina)

Apple switched the default interactive shell from bash to **zsh** in macOS Catalina (10.15, 2019). New user accounts get zsh; pre-existing accounts upgraded from older macOS may still have bash until reset.

```bash
# What am I actually running?
dscl . -read /Users/$USER UserShell   # UserShell: /bin/zsh   <- the login default
echo "$SHELL"                          # /bin/zsh
echo "$0"                              # -zsh (login)  or  zsh (interactive)

# Change a user's login shell (the supported way):
chsh -s /bin/zsh                       # must be listed in /etc/shells
```

Why the switch? Apple's `/bin/bash` is GPLv2 and stuck (see §2). zsh ships under a permissive MIT-like license, so Apple can keep it current. The practical impact: scripts you author *interactively* run under zsh, but `#!/bin/bash` scripts still invoke Apple's ancient bash. The shebang wins, not your login shell.

| Behavior | bash | zsh |
|---|---|---|
| Arrays are 1-indexed | no (0-indexed) | **yes (1-indexed)** by default |
| Word-splitting on unquoted `$var` | yes | **no** (no split by default) |
| Globs that don't match | passed literally | **error** (`no matches found`) |
| `${array[@]}` to expand all | yes | `${array[@]}` *or* `$array` |
| Associative arrays | 4.0+ only | yes |
| Startup files | `.bashrc`/`.bash_profile` | `.zshrc`/`.zprofile` |

> The zsh no-split / glob-error differences silently break ported bash scripts. The fix is to keep `#!/usr/bin/env bash` shebangs for scripts and reserve zsh for your interactive prompt.

---

## 2. The ancient bash Apple ships: 3.2.57

```bash
/bin/bash --version
# GNU bash, version 3.2.57(1)-release (x86_64-apple-darwin...)
```

That's a **2007** release. Apple won't ship anything newer because bash 4.0+ is **GPLv3**, and Apple refuses to ship GPLv3 in the base OS. So `/bin/bash` is forever 3.2.

### What bash 3.2 LACKS (vs bash 4+/5)

```bash
# WRONG on macOS /bin/bash — these all fail or misbehave:
declare -A map                 # bash: declare: -A: invalid option   (no associative arrays)
echo "${name^^}"               # bad substitution                    (no ^^ / ,, case mod)
echo "${name,,}"               # bad substitution
mapfile -t lines < file.txt    # command not found                   (no mapfile)
readarray -t lines < file.txt  # command not found                   (no readarray)
```

| Feature | Needs | 3.2.57? |
|---|---|---|
| Associative arrays (`declare -A`) | 4.0 | NO |
| `${v^^}` / `${v,,}` case modify | 4.0 | NO |
| `mapfile` / `readarray` | 4.0 | NO |
| `&` for stderr+stdout redirect (`&>>` append) | 4.0 | NO (use `>>file 2>&1`) |
| `;;&` / `;&` in `case` | 4.0 | NO |
| Negative array indices | 4.3 | NO |
| `printf -v var` | 3.1 | **YES** (works!) |
| `[[ ... ]]`, `$( )`, `local`, indexed arrays | 3.x | YES |

```bash
# RIGHT on bash 3.2 — portable workarounds:
upper=$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')   # instead of ${name^^}
printf -v padded '%05d' "$n"                                # printf -v is fine
while IFS= read -r line; do lines+=("$line"); done < file   # instead of mapfile
cmd >>log 2>&1                                              # instead of cmd &>>log
```

- **Symptom:** A CI script using `declare -A` or `${x^^}` works on Linux, errors only on Mac.
- **Cause:** `#!/bin/bash` resolved to Apple's 3.2.57.
- **Fix:** Install modern bash (§3) and use `#!/usr/bin/env bash`, *or* write 3.2-portable code.

---

## 3. Getting a modern bash

Install via Homebrew (see §7 for the prefix story), register it, and use an env shebang so the *first bash on PATH* wins.

```bash
brew install bash
bash --version                 # GNU bash, version 5.2.x

# Where did it land? Depends on architecture:
which -a bash
# /opt/homebrew/bin/bash       <- Apple Silicon
# /usr/local/bin/bash          <- Intel
# /bin/bash                    <- Apple's 3.2.57 (still there, immovable)
```

```bash
# Register it as a legal login shell (needed for chsh):
echo /opt/homebrew/bin/bash | sudo tee -a /etc/shells   # Apple Silicon
echo /usr/local/bin/bash    | sudo tee -a /etc/shells   # Intel
chsh -s /opt/homebrew/bin/bash                          # optional: make it your login shell
```

```bash
#!/usr/bin/env bash
# ^ RIGHT: resolves to Homebrew bash if it's earlier on PATH than /bin
declare -A counts            # now works
echo "${greeting^^}"         # now works
```

```bash
#!/bin/bash
# ^ WRONG (if you need modern features): always Apple's 3.2.57, ignores PATH
```

> `/usr/bin/env bash` only finds modern bash if `/opt/homebrew/bin` (or `/usr/local/bin`) precedes `/bin` on PATH — which is exactly what PATH ordering (§6) determines. Verify with `env bash --version` inside your runtime environment, not just your terminal.

---

## 4. /bin/sh on macOS

`/bin/sh` on macOS is **not** dash (as on Debian/Ubuntu) and **not** a separate binary. It is the *same* `/bin/bash` 3.2.57 binary running in **POSIX mode**.

```bash
ls -l /bin/sh /bin/bash
# -r-xr-xr-x  /bin/sh      (separate file, but same bash codebase)
# -r-xr-xr-x  /bin/bash

/bin/sh -c 'echo $BASH_VERSION'   # 3.2.57(1)-release  <- it IS bash
```

When invoked as `sh`, bash disables bashisms and follows POSIX more strictly. Consequences for porting from Linux:

- **Symptom:** A `#!/bin/sh` script using `[[ ]]` or arrays works on Mac but fails on a Debian box (or vice-versa).
- **Cause:** Debian `/bin/sh` is dash (no bashisms); macOS `/bin/sh` is bash-in-POSIX-mode (tolerates *some* bashisms but not all).
- **Fix:** If you need bash features, say so: `#!/usr/bin/env bash`. If you want true portability, test under dash too, don't trust macOS `/bin/sh` to catch bashisms.

---

## 5. Shell startup files (zsh vs bash)

Login vs interactive determines which files load — and on macOS, **every new Terminal.app window is a login shell**, unlike most Linux terminal emulators. That single fact trips up nearly everyone.

### zsh load order

```bash
# Always:           /etc/zshenv  -> ~/.zshenv
# Login shells:     /etc/zprofile -> ~/.zprofile  (then below)
# Interactive:      /etc/zshrc   -> ~/.zshrc
# Login (last):     /etc/zlogin  -> ~/.zlogin
```

| File | When it runs | Put here |
|---|---|---|
| [~/.zshenv](file:///dev/null) | every shell (even scripts) | rarely — env for non-interactive too |
| [~/.zprofile](file:///dev/null) | login shells | PATH, env exports, one-time setup |
| [~/.zshrc](file:///dev/null) | interactive shells | aliases, prompt, completion, keybinds |
| [~/.zlogin](file:///dev/null) | login, after zshrc | final login-only actions |

> macOS quirk: Apple's `/etc/zprofile` calls [path_helper](file:///dev/null) (§6), which can **reorder** your PATH *after* your `~/.zprofile` runs in some setups. If your PATH edits get clobbered, that's why — see §6.

### bash load order

```bash
# Login:        /etc/profile -> first of ~/.bash_profile, ~/.bash_login, ~/.profile
# Interactive:  ~/.bashrc
```

Because Terminal opens *login* shells, your `~/.bashrc` is **not** sourced automatically. The standard fix:

```bash
# in ~/.bash_profile
[ -f ~/.bashrc ] && . ~/.bashrc   # bridge login -> interactive config
```

- **Symptom:** Aliases/functions in `~/.bashrc` don't exist in a fresh Terminal window.
- **Cause:** macOS Terminal launches a login shell, which reads `~/.bash_profile`, not `~/.bashrc`.
- **Fix:** Source `~/.bashrc` from `~/.bash_profile` (above).

---

## 6. PATH, path_helper, /etc/paths and /etc/paths.d

macOS builds your initial PATH from files, not just exports, via [/usr/libexec/path_helper](file:///dev/null). It reads [/etc/paths](file:///dev/null) (one dir per line) and every file in [/etc/paths.d/](file:///dev/null), concatenating them in order.

```bash
cat /etc/paths
# /usr/local/bin
# /usr/bin
# /bin
# /usr/sbin
# /sbin

ls /etc/paths.d                    # e.g. a file dropped by an installer
/usr/libexec/path_helper -s        # shows the PATH it would build (sh syntax)
```

The trap: `/etc/zprofile` (and `/etc/profile` for bash) run `path_helper`, which **prepends** the file-based dirs. If you set PATH in `~/.zshenv` (runs *before* zprofile), path_helper can shove system dirs in front of yours.

```bash
# WRONG: PATH set in ~/.zshenv gets reordered by path_helper later
export PATH="/opt/homebrew/bin:$PATH"   # in ~/.zshenv  -> may end up AFTER /usr/bin

# RIGHT: set PATH in ~/.zprofile (runs AFTER path_helper), or re-prepend there
export PATH="/opt/homebrew/bin:$PATH"   # in ~/.zprofile -> wins
```

- **Symptom:** `which python3` shows `/usr/bin/python3` even after installing via Homebrew.
- **Cause:** path_helper put `/usr/bin` ahead of the Homebrew prefix because of *when* you set PATH.
- **Fix:** Prepend the Homebrew prefix in `~/.zprofile`; `brew shellenv` does this correctly (§7).

---

## 7. SIP, the read-only system volume, and Homebrew prefix

### SIP and the read-only system volume

Since macOS Catalina, the OS lives on a **read-only system volume**. **SIP** (System Integrity Protection) enforces it — even `root` cannot write to [/System](file:///dev/null) or [/usr](file:///dev/null) (except [/usr/local](file:///dev/null)).

```bash
csrutil status                     # System Integrity Protection status: enabled.
sudo touch /usr/bin/foo            # touch: /usr/bin/foo: Read-only file system
```

- **Symptom:** `sudo cp mytool /usr/bin/` fails with *Read-only file system*, despite being root.
- **Cause:** SIP + read-only system volume. `/usr/bin`, `/System`, `/bin` are immutable.
- **Fix:** Put user tools in **/usr/local** (Intel) or **/opt/homebrew** (Apple Silicon) — both writable. Never disable SIP for this.

### Apple Silicon vs Intel: the Homebrew prefix differs

This is the single biggest cross-Mac portability gotcha. Homebrew installs to a *different prefix* per architecture:

| | Intel (x86_64) | Apple Silicon (arm64) |
|---|---|---|
| Homebrew prefix | `/usr/local` | `/opt/homebrew` |
| Binaries | `/usr/local/bin` | `/opt/homebrew/bin` |
| `brew` location | `/usr/local/bin/brew` | `/opt/homebrew/bin/brew` |
| `arch` reports | `i386` | `arm64` |

```bash
# Don't hardcode the prefix — ask brew:
eval "$(/opt/homebrew/bin/brew shellenv)"   # or /usr/local/bin/brew on Intel
echo "$(brew --prefix)"                      # /opt/homebrew  or  /usr/local

# Architecture-aware, no hardcoding:
BREW="$(command -v brew)"
[ -x "$BREW" ] && eval "$("$BREW" shellenv)"
```

```bash
# WRONG — breaks on Apple Silicon:
export PATH="/usr/local/bin:$PATH"

# RIGHT — works on both:
eval "$($(command -v brew) shellenv)"
```

### arch and Rosetta

Apple Silicon Macs can run x86_64 binaries under **Rosetta 2**. The `arch` command picks which slice runs:

```bash
arch                       # arm64  (native on Apple Silicon)
arch -x86_64 zsh           # launch an Intel (Rosetta) shell
arch -arm64  zsh           # force native arm64
uname -m                   # arm64  or  x86_64

# A shell launched under Rosetta sees the INTEL Homebrew prefix:
arch -x86_64 brew --prefix # /usr/local   <- different from native!
```

- **Symptom:** `brew` commands behave inconsistently — sometimes `/opt/homebrew`, sometimes `/usr/local`.
- **Cause:** You have *two* Homebrews because your terminal sometimes runs under Rosetta.
- **Fix:** Check `arch`/`uname -m`; standardize on native `arm64` and one prefix.

---

## See also

- [02 — BSD vs GNU Userland](02_bsd_vs_gnu.md) — why `sed -i`, `date -d`, and `readlink -f` betray you.
- [03 — launchd & Scheduling](03_launchd_scheduling.md) — there is no `cron` you should trust.
- Linux fundamentals refresher: [../linux/01_fundamentals.md](../linux/01_fundamentals.md)

---

> Next: [02 — BSD vs GNU Userland](02_bsd_vs_gnu.md) — your `sed`, `awk`, `date`, `grep`, and `ls` are BSD here, not GNU. The flags you've typed for a decade silently mean different things — or nothing at all.
