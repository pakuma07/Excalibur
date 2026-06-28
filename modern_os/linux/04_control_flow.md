# 04 — Control Flow

> **Audience:** Anyone who has stopped fearing variables (see [02 — Variables, Quoting & Expansion](02_variables_quoting_expansion.md)) and now wants their scripts to *decide* and *repeat* — without the silent bugs that haunt `if [ ... ]`. We go from the basics of `test` to exit-status-driven design that a principal engineer would sign off on.

Control flow in Bash is built on one idea: **every command returns an exit status, and `0` means success.** `if`, `while`, `&&`, `||` — none of them test "truth"; they test *exit status*. Internalize that and the rest falls into place.

---

## 1. `test`, `[ ]`, and `[[ ]]`

`[` is not syntax — it is a **command** (often `/usr/bin/[`, also a Bash builtin). That is why it needs a closing `]` as an argument and why spaces matter.

```bash
# WRONG — no spaces: [ becomes the command "[x"
if [$x = 1]; then ...        # bash: [x: command not found

# RIGHT — [ is a command, its operands are space-separated words
if [ "$x" = 1 ]; then ...    # note the quotes; see pitfall below
```

`[[ ]]` is a Bash **keyword**, not a command. The shell parses it specially, so word-splitting and glob expansion do **not** happen inside it. This single fact eliminates a whole class of bugs.

```bash
file="my report.txt"

# WRONG — unquoted $file splits into 3 words: [ -f my report.txt ]
if [ -f $file ]; then ...    # bash: [: my: binary operator expected

# RIGHT in [ ] — you MUST quote
if [ -f "$file" ]; then ...

# RIGHT in [[ ]] — no splitting, quotes optional (still a good habit)
if [[ -f $file ]]; then ...
```

- **Symptom:** `[: too many arguments` or `binary operator expected`.
- **Cause:** An unquoted variable inside `[ ]` expanded to zero or multiple words, or was empty.
- **Fix:** Quote every expansion in `[ ]`, or switch to `[[ ]]` where splitting cannot happen.

### `[ ]` vs `[[ ]]`

| Feature                         | `[ ]` (test)            | `[[ ]]` (keyword)              |
|---------------------------------|-------------------------|--------------------------------|
| Word-splitting on unquoted vars | Yes (bug source)        | No                             |
| Glob/pathname expansion         | Yes                     | No (RHS of `==` is a pattern)  |
| `&&` / `\|\|` inside             | No (use `-a`/`-o`, buggy) | Yes                          |
| Regex match `=~`                | No                      | Yes                            |
| Pattern match `==`/`!=`         | No (literal only)       | Yes (`[[ $f == *.txt ]]`)      |
| POSIX portable (`sh`, `dash`)   | Yes                     | No (Bash/Ksh/Zsh only)         |

**Rule of thumb:** writing for `#!/usr/bin/env bash`? Use `[[ ]]`. Writing strict POSIX `#!/bin/sh`? Use `[ ]` and quote religiously.

```bash
# [[ ]] superpowers
[[ $name == J* ]]              # glob pattern: starts with J
[[ $name == "J*" ]]           # quoted RHS = literal match, no glob
[[ $email =~ ^[^@]+@[^@]+$ ]]  # regex; captures land in ${BASH_REMATCH[@]}
[[ -n $a && -z $b ]]          # logical operators inside one test
```

> Quote the RHS of `=~` and you turn it into a *literal* string, not a regex. Keep the pattern unquoted, or store it in a variable: `re='^[0-9]+$'; [[ $x =~ $re ]]`.

---

## 2. String vs numeric comparison

Bash uses **different operators** for strings and integers. Mixing them is the most common beginner bug.

```bash
# Numeric: -eq -ne -lt -le -gt -ge
[[ $count -gt 10 ]]

# String:  =  ==  !=  <  >  (lexical)
[[ $status == "ready" ]]

# WRONG — "10" vs "9" compared as strings: "10" < "9" is TRUE!
[[ "10" > "9" ]] && echo "bigger"   # prints "bigger" — lexical, not numeric

# RIGHT — numeric intent needs numeric operators
[[ 10 -gt 9 ]] && echo "bigger"     # correct
(( 10 > 9 )) && echo "bigger"       # arithmetic context, also correct
```

- **Symptom:** `9` reported as greater than `10`; sorting/threshold logic backwards.
- **Cause:** `>` / `<` inside `[[ ]]` are *lexical* string comparisons.
- **Fix:** Use `-gt`/`-lt`/`-eq` for numbers, or do the comparison in `(( ))`.

> Inside `[ ]`, `<` and `>` must be escaped (`\<`) or they are read as redirections — yet another reason to prefer `[[ ]]` or `(( ))` for comparisons.

---

## 3. File test operators

```bash
[[ -e $p ]]   # exists (any type)
[[ -f $p ]]   # regular file
[[ -d $p ]]   # directory
[[ -r $p ]]   # readable by us
[[ -w $p ]]   # writable by us
[[ -x $p ]]   # executable by us (or searchable, for dirs)
[[ -s $p ]]   # exists AND size > 0 (non-empty)
[[ -L $p ]]   # symbolic link
[[ a -nt b ]] # a is newer than b (modification time)
[[ a -ot b ]] # a is older than b
```

```bash
# Practical guard before reading a config
config="${HOME}/.app/config.yml"      # ${VAR} form: see 03 — Parameter Expansion
if [[ -r $config && -s $config ]]; then
  load_config "$config"
else
  echo "No readable, non-empty config at $config" >&2
  exit 1
fi
```

- **Pitfall:** `-e` only says it exists; `-f` excludes directories, sockets, devices. Pick the most specific test.
- **TOCTOU:** a test followed by an action is a race. For critical paths, just *attempt* the operation and check its exit status instead of pre-testing.

See [03 — Parameter Expansion](03_parameter_expansion.md) for safely building the paths you feed these tests.

---

## 4. `if` / `elif` / `else`

`if` runs a *command* and branches on its exit status. The `[[ ]]` is just one such command — any command works.

```bash
if grep -q "^ERROR" "$logfile"; then        # branch on grep's exit status
  notify_oncall
elif [[ $(wc -l < "$logfile") -gt 100000 ]]; then
  rotate_log "$logfile"
else
  echo "log healthy"
fi
```

```bash
# WRONG — comparing the OUTPUT to 0 by hand is redundant and fragile
if [[ "$(systemctl is-active nginx)" == "active" ]]; then ...

# RIGHT — the command already returns 0/non-zero; just test it
if systemctl is-active --quiet nginx; then ...
```

> The single most important habit in this chapter: **let exit status drive the branch.** `if mycmd; then` is cleaner and more correct than capturing output and string-matching it.

---

## 5. `case` — multi-way branching on patterns

`case` matches a value against **glob patterns** (not regex), top to bottom, first match wins.

```bash
case "$1" in
  start)          do_start ;;
  stop|halt)      do_stop ;;          # alternation with |
  re*)            do_restart ;;       # glob: matches restart, reload, ...
  [0-9]*)         echo "numeric arg" ;;
  *)              echo "usage: $0 {start|stop|restart}" >&2; exit 2 ;;
esac
```

Terminators control fall-through:

```bash
case "$level" in
  debug) echo "verbose"  ;&    # ;&  fall THROUGH to next clause's body
  info)  echo "normal"   ;;&   # ;;& keep TESTING remaining patterns
  *)     echo "always"   ;;    # ;;  stop (the default)
esac
```

- `;;` — stop after this clause (the usual case).
- `;&` — execute the **next** clause's body unconditionally (C-style fall-through).
- `;;&` — continue testing subsequent patterns against the same value (useful for tagging).

---

## 6. `for` loops

```bash
# List form — iterate over words
for svc in nginx redis postgres; do
  systemctl restart "$svc"
done

# Over arguments — ALWAYS use "$@" (quoted) to preserve each arg intact
for arg in "$@"; do            # "$*" would join into ONE word — wrong
  echo "got: $arg"
done

# Glob form — let the shell expand filenames (handles spaces correctly)
for f in /var/log/*.log; do
  [[ -e $f ]] || continue      # guard: glob yields the literal pattern if no match
  gzip "$f"
done

# C-style — when you need an index
for (( i = 0; i < 10; i++ )); do
  echo "iteration $i"
done
```

```bash
# WRONG — looping over command output via word-splitting; breaks on spaces
for f in $(ls *.log); do ...   # filename "two words.log" splits into two

# RIGHT — glob directly (section above), or use a while-read loop (section 8)
for f in *.log; do ...
```

- **Symptom:** filenames with spaces processed as separate items.
- **Cause:** `for x in $(command)` splits output on `$IFS`.
- **Fix:** glob directly, or pipe into `while IFS= read -r`.

---

## 7. `while` and `until`

```bash
# while: loop WHILE the command succeeds (exit 0)
count=0
while (( count < 5 )); do
  echo "$count"
  (( count++ ))
done

# until: loop UNTIL the command succeeds — handy for polling
until curl -sf http://localhost:8080/health >/dev/null; do
  echo "waiting for service..."
  sleep 2
done
echo "service is up"
```

---

## 8. Reading lines safely

The canonical, bug-free file/stream reader:

```bash
while IFS= read -r line; do
  process "$line"
done < "$input_file"
```

Why each piece matters:

- `IFS=` — prevents leading/trailing whitespace from being stripped.
- `-r` — prevents backslashes (`\`) from being interpreted as escapes.
- redirect `< file` — feeds the loop without a subshell (see pitfall below).

```bash
# WRONG — pipe creates a SUBSHELL; variables set inside are lost
count=0
cat file | while read -r line; do (( count++ )); done
echo "$count"        # prints 0 — the subshell's $count vanished

# RIGHT — redirect instead of pipe; loop runs in the current shell
count=0
while IFS= read -r line; do (( count++ )); done < file
echo "$count"        # correct
```

> **`while read` + `set -e` interaction:** `read` returns non-zero at EOF (it has no more input), and `(( count++ ))` returns non-zero when the result is `0`. Under `set -e` these can abort your loop or script unexpectedly. Use `(( count++ )) || true`, or `(( ++count ))` (pre-increment never yields the pre-`0` result first). See [08 — Error Handling, Strict Mode & Debugging](08_error_handling_debugging.md) for the full strict-mode picture.

To read a missing-final-newline line too, test the buffer after the loop:

```bash
while IFS= read -r line || [[ -n $line ]]; do
  process "$line"
done < "$file"
```

---

## 9. `&&` / `||` short-circuit logic

`&&` runs the right side only if the left **succeeded** (exit 0); `||` only if it **failed**.

```bash
mkdir -p /opt/app && cd /opt/app          # cd only if mkdir succeeded
command -v jq >/dev/null || { echo "jq required" >&2; exit 1; }
```

### The if/else trap

A tempting one-liner — `A && B || C` — is **not** a reliable if/else.

```bash
# WRONG — looks like "if A then B else C", but isn't
[[ -d /opt ]] && setup_dir || fallback
# If setup_dir itself FAILS (exit non-zero), fallback ALSO runs.
```

- **Symptom:** the `||` branch executes even when the condition was true.
- **Cause:** `||` triggers on the failure of *whatever ran last* — including `B`, not just `A`.
- **Fix:** use a real `if`/`else` when `B` can fail:

```bash
if [[ -d /opt ]]; then
  setup_dir
else
  fallback
fi
```

> `A && B || C` is safe **only** when `B` cannot fail (e.g. a bare `echo`). Anything with real exit-status risk needs `if`.

---

## 10. Arithmetic: `(( ))` and `let`

`(( ))` is an arithmetic context. Variables need no `$`, C operators work, and **its exit status is `0` when the result is non-zero** (and `1` when the result is `0`).

```bash
(( total = price * qty ))        # assignment; no $ needed inside
(( i++, j-- ))                   # comma operator
result=$(( (a + b) * 2 ))        # $(( )) substitutes the VALUE

# Exit-status gotcha
(( 0 ))   ; echo $?              # 1  — value 0 means "false"
(( 1 ))   ; echo $?              # 0  — non-zero value means "true"
```

```bash
# WRONG — under set -e, this aborts the script when n is 0
(( n = 0 ))                      # value 0 -> exit 1 -> script dies

# RIGHT — guard arithmetic that may evaluate to 0
(( n = 0 )) || true
```

`let` is the older equivalent; prefer `(( ))` for readability. Use `$(( ))` whenever you want the computed *value* rather than a true/false test.

---

## 11. `break` and `continue`

```bash
for f in *.csv; do
  [[ -s $f ]] || continue        # skip empty files
  if ! validate "$f"; then
    echo "bad file: $f, aborting" >&2
    break                        # leave the loop entirely
  fi
  import "$f"
done

# Multi-level: break N / continue N exits N enclosing loops
for d in */; do
  for f in "$d"*.tmp; do
    [[ -e $f ]] || continue 2    # no match -> skip to next directory
    rm -f "$f"
  done
done
```

- `continue` — skip to the next iteration of the current loop.
- `break` — exit the current loop.
- `break N` / `continue N` — act on the *N*th enclosing loop (rarely needed; if you reach `N>2`, consider a function instead).

---

## Mental model recap

1. **Exit status is the currency.** `0` = success = "true". Design branches around commands, not string comparisons of their output.
2. **`[[ ]]` for Bash, `[ ]` (quoted) for POSIX.** `[[ ]]` removes word-splitting and adds `=~`, `&&`, glob `==`.
3. **Numbers use `-eq`/`-gt`; strings use `==`/`<`.** Never cross them.
4. **`while IFS= read -r line; do ...; done < file`** — memorize it; use redirect, never a pipe.
5. **`A && B || C` is a trap** once `B` can fail — reach for real `if`/`else`.
6. **`(( ))` returns false on a `0` result** — guard it under `set -e`.

---

> Next: [05 — Functions & Arrays](05_functions_arrays.md) — package this control flow into reusable functions with proper return codes and local scope, then drive your loops with indexed and associative arrays instead of fragile space-delimited strings.
