# 03 — Parameter Expansion

> **Audience:** Anyone who still pipes a variable through `sed`, `cut`, `basename`, or `awk` just to chop off an extension or strip a prefix. Stop. The shell already does string surgery in-process — no fork, no subshell, no quoting nightmare. This chapter is the operator-by-operator reference for `${...}`, the single most underused feature in Bash. Master it and your scripts get faster, shorter, and immune to filenames with spaces.

Builds on [02 — Variables, Quoting & Expansion](02_variables_quoting_expansion.md). For when these operators *aren't* enough (multi-line, regex groups, field math), jump to [07 — Text Processing Toolkit](07_text_processing.md). Array-aware forms are previewed here and finished in [05 — Functions & Arrays](05_functions_arrays.md).

---

## 1. Why parameter expansion beats forking

```bash
file=/var/log/nginx/access.log.2026

# WRONG — three forks, breaks on spaces, slow in loops
ext=$(echo "$file" | sed 's/.*\.//')
base=$(basename "$file")

# RIGHT — pure shell, zero subprocesses
ext=${file##*.}        # 2026
base=${file##*/}       # access.log.2026
```

- **Symptom:** A loop over 10,000 files takes minutes.
- **Cause:** Each `$(basename ...)` / `$(sed ...)` forks a process. Forks dominate runtime.
- **Fix:** Use `${var##*/}` and friends. In-process expansion is effectively free.

Every operator below works on the *value* of a variable. None mutate the variable unless noted (only `:=` does).

---

## 2. Defaults and presence tests

The `:` distinguishes **unset OR empty** from **unset only**.

```bash
unset x; y=""

echo "${x:-fallback}"   # fallback   (unset  -> default)
echo "${y:-fallback}"   # fallback   (empty  -> default, because of ':')
echo "${y-fallback}"    #            (empty stays empty; no ':')

# := also ASSIGNS the default back into the variable
echo "${cfg:=/etc/app.conf}"   # /etc/app.conf  AND cfg is now set
echo "$cfg"                    # /etc/app.conf

# :? aborts the script with a message if unset/empty (great for required args)
deploy_env=${DEPLOY_ENV:?must set DEPLOY_ENV before deploying}

# :+ is the inverse — substitute ONLY if the variable IS set
opts=${VERBOSE:+--verbose}     # empty unless VERBOSE is set
```

| Form | If set & non-empty | If empty | If unset |
|------|--------------------|----------|----------|
| `${v:-d}` | `$v` | `d` | `d` |
| `${v-d}`  | `$v` | `""` | `d` |
| `${v:=d}` | `$v` | assign+`d` | assign+`d` |
| `${v:?m}` | `$v` | error `m` | error `m` |
| `${v:+x}` | `x` | `""` | `""` |
| `${v+x}`  | `x` | `x` | `""` |

> **Mnemonic:** `-` gives a *fallback*, `=` *sticks*, `?` *complains*, `+` is *opposite day*. The colon adds "...or empty" to all of them.

Real-world config layering:

```bash
port=${PORT:-${DEFAULT_PORT:-8080}}     # nested defaults
: "${LOG_DIR:=/var/log/myapp}"          # set-if-missing idiom (the ':' no-op runs the expansion only for its side effect)
mkdir -p "$LOG_DIR"
```

---

## 3. Prefix and suffix stripping (the basename/dirname killers)

`#` chops from the **front**, `%` from the **back**. Doubling (`##`, `%%`) makes the match **greedy** (longest). The pattern is a *glob*, not a regex.

```bash
path=/home/parveen/reports/q2.final.csv

${path##*/}     # q2.final.csv     basename  (greedy strip up to last /)
${path%/*}      # /home/parveen/reports   dirname (non-greedy strip from last /)
${path%.*}      # /home/parveen/reports/q2.final  drop last extension
${path%%.*}     # /home/parveen/reports/q2        drop ALL extensions (greedy)

name=${path##*/}     # q2.final.csv
${name%.*}           # q2.final     stem
${name##*.}          # csv          extension only
```

Pure-shell `basename` + `dirname`:

```bash
basename() { local p=${1%/}; printf '%s\n' "${p##*/}"; }
dirname()  { local p=${1%/*}; printf '%s\n' "${p:-/}"; }
```

- **Symptom:** `${file%.*}` on `archive.tar.gz` gives `archive.tar`, not `archive`.
- **Cause:** Single `%` is non-greedy — strips only the *shortest* trailing `.*`.
- **Fix:** Use `%%` for greedy (`${file%%.*}` -> `archive`), but beware: `2026.backup` -> `2026`. Choose based on whether dots appear in the stem.

```bash
url=https://api.example.com/v2/users?id=42
${url#*://}        # api.example.com/v2/users?id=42   strip scheme
${url##*/}         # users?id=42                       last path segment
${url%%\?*}        # https://api.example.com/v2/users  drop query string (? escaped — it's a glob metachar)
```

---

## 4. Search and replace

`${v/pat/repl}` replaces the **first** match; `${v//pat/repl}` replaces **all**. Anchor with `#` (front) or `%` (back). `pat` is a glob.

```bash
s="foo bar foo baz"
${s/foo/X}        # X bar foo baz     first only
${s//foo/X}       # X bar X baz       all
${s// /_}         # foo_bar_foo_baz   spaces -> underscores
${s//foo/}        # " bar  baz"       delete all matches (empty repl)

f=report-2026-draft.txt
${f/#report/final}    # final-2026-draft.txt   anchored at START
${f/%.txt/.md}        # report-2026-draft.md   anchored at END
```

Useful one-liners:

```bash
csv="a,b,,c,"
${csv//,/ }              # a b  c    commas -> spaces
path=$PATH
${path//:/$'\n'}         # PATH one entry per line

# Trim whitespace (combine with extglob — see below)
shopt -s extglob
trim=${var##+([[:space:]])}      # strip leading whitespace
trim=${trim%%+([[:space:]])}     # strip trailing whitespace
```

> For real regex (capture groups, alternation, backreferences) or multi-line edits, this is your signal to reach for [07 — Text Processing Toolkit](07_text_processing.md). Parameter-expansion patterns are globs only.

---

## 5. Substring extraction

`${v:offset}` and `${v:offset:length}`. Negative offsets count from the end (the **space before `-` is required**, or it parses as a default).

```bash
s=abcdefgh
${s:2}        # cdefgh     from index 2 to end
${s:2:3}      # cde        3 chars from index 2
${s: -3}      # fgh        last 3 chars  (note the space!)
${s: -3:2}    # fg
${s:2:-1}     # cdefg      from 2, stop 1 from end (negative length)
```

- **Symptom:** `${s:-3}` returns the whole string, not the tail.
- **Cause:** `:-` is the *default* operator. `${s:-3}` means "use `3` if `s` is empty."
- **Fix:** Insert a space: `${s: -3}` or parenthesize: `${s:$((-3))}`.

---

## 6. Length

```bash
v=hello
echo ${#v}          # 5     character count (bytes if not UTF-8 locale)

arr=(a b c d)
echo ${#arr[@]}     # 4     number of elements
echo ${#arr[2]}     # 1     length of element 2's value
```

Guard against oversized input without `wc`:

```bash
[[ ${#password} -ge 12 ]] || { echo "too short"; exit 1; }
```

---

## 7. Case conversion

`^` upper, `,` lower; doubled affects all characters, single affects only the first match.

```bash
s="hELLo WoRLD"
${s^^}        # HELLO WORLD    all upper
${s,,}        # hello world    all lower
${s^}         # HELLo WoRLD    first char upper
${s,}         # hELLo WoRLD    first char lower
${s^^[aeiou]} # hELLo WoRLD -> uppercases only matching chars: hELLO WORLD
```

Normalize user input:

```bash
read -r answer
case ${answer,,} in
  y|yes) echo "proceeding" ;;
  *)     echo "aborting" ;;
esac
```

(For Bash older than 4.0 — e.g. stock macOS — `^^`/`,,` don't exist; use `tr '[:lower:]' '[:upper:]'`.)

---

## 8. Indirection and name listing

`${!ref}` expands to the value of the variable *named by* `ref` — a pointer dereference.

```bash
target=PATH
echo "${!target}"        # prints the value of $PATH

# Dynamic config selection
env=prod
prod_url=https://prod.example.com
key="${env}_url"
echo "${!key}"           # https://prod.example.com
```

List variable/array names by prefix or index:

```bash
APP_HOST=db1 APP_PORT=5432 APP_USER=admin
echo "${!APP_@}"         # APP_HOST APP_PORT APP_USER   names with prefix APP_

arr=(x y z)
echo "${!arr[@]}"        # 0 1 2      array indices (keys)
```

- **Symptom:** `${!key}` errors or behaves oddly in `sh`.
- **Cause:** Indirection is a Bash extension, not POSIX.
- **Fix:** Ensure the script runs under `bash` (`#!/usr/bin/env bash`), or use `declare -n` namerefs (Bash 4.3+) for cleaner aliasing — covered in [05 — Functions & Arrays](05_functions_arrays.md).

---

## 9. Array slicing (preview)

All the operators above broadcast across arrays, and `${arr:off:len}` slices:

```bash
nums=(10 20 30 40 50)
echo "${nums[@]:1:2}"      # 20 30        slice (offset 1, length 2)
echo "${nums[@]: -2}"      # 40 50        last two
echo "${nums[@]^^}"        # broadcast case (on string arrays)
echo "${nums[@]/#/item-}"  # item-10 item-20 ...   prefix every element

# Positional params slice the same way
echo "${@:2}"              # all args from $2 onward
echo "${@:2:3}"            # 3 args starting at $2
```

Full array mechanics — associative arrays, `declare -n`, append `+=`, safe iteration — live in [05 — Functions & Arrays](05_functions_arrays.md).

---

## 10. Putting it together — a real filename pipeline

```bash
# Rename "IMG_2026-06-23_vacation.JPEG" -> "vacation_20260623.jpg"
for f in *.JPEG; do
  stem=${f%.*}                 # IMG_2026-06-23_vacation
  ext=${f##*.}                 # JPEG
  date=${stem#IMG_}            # 2026-06-23_vacation
  date=${date%%_*}             # 2026-06-23
  date=${date//-/}             # 20260623
  label=${stem##*_}            # vacation
  mv -- "$f" "${label}_${date}.${ext,,}"
done
```

No `sed`, no `awk`, no `basename`, no subshells — and it survives spaces in filenames because every expansion is quoted.

---

## Cheat-sheet — every operator

| Operator | Result |
|----------|--------|
| `${v:-d}` / `${v-d}` | Default if empty-or-unset / unset |
| `${v:=d}` / `${v=d}` | Assign default if empty-or-unset / unset |
| `${v:?m}` / `${v?m}` | Error `m` if empty-or-unset / unset |
| `${v:+x}` / `${v+x}` | Use `x` if set (else empty) |
| `${v#p}` / `${v##p}` | Strip shortest / longest **prefix** glob |
| `${v%p}` / `${v%%p}` | Strip shortest / longest **suffix** glob |
| `${v/a/b}` / `${v//a/b}` | Replace first / all matches of `a` |
| `${v/#a/b}` / `${v/%a/b}` | Replace `a` anchored at start / end |
| `${v:off}` / `${v:off:len}` | Substring (negative offset needs leading space) |
| `${#v}` / `${#arr[@]}` | Length of value / element count |
| `${v^^}` / `${v,,}` | Upper / lower all |
| `${v^}` / `${v,}` | Upper / lower first char |
| `${!ref}` | Indirect — value of variable named by `ref` |
| `${!pre@}` / `${!pre*}` | Names of variables with prefix `pre` |
| `${!arr[@]}` | Array keys/indices |
| `${arr[@]:off:len}` | Array slice |

---

> Next: [04 — Control Flow](04_control_flow.md) — `if`, `case`, `[[ ]]` vs `[ ]`, arithmetic conditionals, loops that don't choke on spaces, and the trap-and-cleanup patterns that separate fragile scripts from production ones.
