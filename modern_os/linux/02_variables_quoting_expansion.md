# 02 — Variables, Quoting & Expansion

> **Audience:** Engineers who already write Bash but keep getting bitten by spaces in filenames, mysterious `unbound variable` errors, and scripts that silently glob the wrong files. This chapter is the single highest-leverage thing you can master in Bash. Word-splitting and globbing cause the **#1 class of Bash bugs** and the **#1 injection vector**. If you internalize one rule from this entire series, make it: **quote everything** — `"$var"`, `"${arr[@]}"`, `"$(cmd)"`. The unquoted exceptions are rare and deliberate, never accidental.

See [01 — Fundamentals](01_fundamentals.md) for shells, processes, and exit codes. The Windows batch/PowerShell quoting horror show is catalogued in [../windows/08_gotchas_quirks.md](../windows/08_gotchas_quirks.md) if you need the parallel.

---

## 1. Variable assignment & reference

Assignment is **`name=value` with NO spaces around `=`**. This trips up everyone coming from other languages.

```bash
# WRONG — bash parses these as commands, not assignments
name = "Ada"      # runs command `name` with args `=` and `Ada`
name= "Ada"       # runs `Ada` with name set to empty in its env
name ="Ada"       # runs `name` with arg `=Ada`

# RIGHT
name="Ada"        # assignment, value is the string Ada
count=42
path="/var/log"
empty=            # valid: empty string
```

Reference with `$name` or, preferably, `${name}` when adjacent text could be ambiguous:

```bash
file="report"
echo "$file_2024"     # WRONG-ish: expands variable `file_2024` (likely unset → empty)
echo "${file}_2024"   # RIGHT: report_2024 — braces delimit the name
```

**Symptom:** `command not found` on an assignment line. **Cause:** stray space around `=`. **Fix:** remove the spaces.

Variable names: `[A-Za-z_][A-Za-z0-9_]*`. Convention: `lower_snake` for locals, `UPPER_SNAKE` for exported/environment and constants.

---

## 2. Single `'` vs double `"` vs no quotes

This is the core decision you make on every expansion. Get it wrong and you have a bug or a vulnerability.

| Form | Expansion happens? | Use when |
|------|-------------------|----------|
| `'single'` | **No** — everything literal, no `$`, no `\` | Literal strings: regex, sed scripts, JSON templates |
| `"double"` | **Yes** — `$var`, `$(cmd)`, `$(( ))`, `\` escapes | **Default.** Anything with a variable you want expanded |
| no quotes | Yes **+ word-splitting + globbing** | Almost never on a variable. Reserve for deliberate splitting/globbing |

```bash
name="Ada Lovelace"
echo '$name'      # $name        — literal, no expansion
echo "$name"      # Ada Lovelace — expanded, ONE argument
echo $name        # Ada Lovelace — expanded, but TWO arguments (split on space)
```

That last line is the trap. `echo` papers over it, but the moment you pass an unquoted variable to `rm`, `cp`, `[`, or a loop, splitting breaks you.

```bash
file="my report.txt"
rm $file          # WRONG: runs `rm my report.txt` → deletes `my` and `report.txt`
rm "$file"        # RIGHT: removes the single file `my report.txt`
```

Embed double quotes inside single, and vice versa, rather than escaping:

```bash
echo "She said \"hi\""        # works but noisy
echo 'She said "hi"'          # cleaner
greeting='it'\''s here'       # single quotes can't contain a single quote; close, escape, reopen
```

---

## 3. Word-splitting & globbing — the #1 bug and injection vector

After an **unquoted** expansion, Bash does two more things to the result:

1. **Word-splitting** — splits the text on every character in `$IFS` (default: space, tab, newline) into separate words/arguments.
2. **Globbing (filename expansion)** — if a word contains `*`, `?`, or `[...]`, it is replaced by matching filenames.

```bash
files="*.txt"
echo $files       # WRONG: glob expands → lists every .txt file in cwd
echo "$files"     # RIGHT: prints the literal string *.txt

user_input="; rm -rf /"        # imagine this came from a web form
eval "process $user_input"     # CATASTROPHE — never eval untrusted input
```

**Symptom:** script works in `/home/you` but explodes in a dir with spaces or `*` in names, or processes the wrong files. **Cause:** unquoted expansion underwent splitting/globbing. **Fix:** quote it — `"$var"`.

**Symptom:** attacker-controlled value runs arbitrary commands. **Cause:** unquoted expansion in a context that re-parses (`eval`, unquoted command building). **Fix:** quote, and never `eval` untrusted data; use arrays for building command lines (see §7).

The defensive rule, stated positively:

```bash
# Quote EVERY expansion unless you have a specific, documented reason not to.
cp "$src" "$dst"
for f in "$@"; do process "$f"; done
grep "$pattern" "$file"
```

Disable globbing entirely when you genuinely don't want it (e.g. before handling raw user globs): `set -f` (a.k.a. `set -o noglob`); re-enable with `set +f`.

---

## 4. The full expansion order

Bash processes a command line through expansions in a **fixed order**. Knowing it explains every "why did that happen" moment.

```
1. Brace expansion        {a,b}  {1..5}
2. Tilde expansion        ~  ~user
3. Parameter expansion    $var  ${var:-default}  (see ch. 03)
4. Command substitution   $(cmd)  `cmd`
5. Arithmetic expansion   $(( expr ))
   (3, 4, 5 happen left-to-right in a single pass)
6. Word-splitting         on $IFS — ONLY on unquoted results of 3/4/5
7. Filename expansion      *  ?  [...] globbing
   (then: quote removal)
```

Critical consequences:

```bash
# Brace expansion is FIRST and is purely textual — no variables yet.
n=3
echo {1..$n}      # WRONG-ish: prints `{1..3}` literally — $n not expanded in time
seq 1 "$n"        # RIGHT for a variable range

# Steps 6 & 7 happen AFTER substitution — so command output gets split/globbed too.
echo $(echo "*.md")    # WRONG: command output * gets globbed → file list
echo "$(echo '*.md')"  # RIGHT: literal *.md

# Brace expansion makes combinations
mkdir -p project/{src,test,docs}      # creates three dirs
cp file.txt{,.bak}                     # cp file.txt file.txt.bak — handy idiom
```

```bash
~        # → $HOME
~root    # → root's home dir
~/bin    # → $HOME/bin   (tilde only expands at the START of a word, unquoted)
echo "~"  # → literal ~  (quoted = no tilde expansion)
```

---

## 5. `$IFS` — the Internal Field Separator

`IFS` controls word-splitting (step 6) and how `read` carves input. Default is space + tab + newline.

```bash
# Read CSV-style data by changing IFS for ONE command
line="alice,30,nyc"
IFS=',' read -r name age city <<< "$line"
echo "$name $age $city"    # alice 30 nyc

# Loop over PATH safely by splitting on its real separator
IFS=':' read -ra dirs <<< "$PATH"
for d in "${dirs[@]}"; do echo "$d"; done
```

`IFS=',' read ...` sets IFS only for that single command — it does not leak. If you set it globally, save and restore:

```bash
old_ifs=$IFS
IFS=$'\n'
# ... do newline-split work ...
IFS=$old_ifs       # restore — or just `unset IFS` to reset to default
```

**Symptom:** loop iterations break on spaces, or trailing/empty fields vanish. **Cause:** unexpected IFS, or unquoted expansion under the default IFS. **Fix:** quote expansions; set IFS explicitly and scope it to one command.

The single most robust splitting idiom — read NUL-delimited data from `find`, immune to every special character including newlines:

```bash
while IFS= read -r -d '' f; do
  process "$f"                    # always quoted
done < <(find . -name '*.log' -print0)
```

`IFS=` (empty) on the `read` line stops it from trimming leading/trailing whitespace; `-r` stops backslash mangling. Memorize `while IFS= read -r line`.

---

## 6. Command substitution: `$(...)` vs backticks

Both capture a command's stdout, with trailing newlines stripped. **Always use `$(...)`.**

```bash
# WRONG (legacy) — backticks don't nest, and \ behaves inconsistently
files=`ls`
nested=`echo \`date\``    # painful, fragile

# RIGHT
files=$(ls)
nested=$(echo "$(date)")  # nests cleanly
count=$(grep -c ERROR "$log")
```

Why `$(...)` wins: it nests without backslash gymnastics, is visually unambiguous, and the parsing rules are saner. Backticks exist only for POSIX-sh portability you almost never need.

**Quote the substitution** to preserve its output as a single argument:

```bash
msg="$(cat note.txt)"          # preserves internal whitespace/newlines
echo "$msg"                    # quoted on use, too
if [[ "$(id -un)" == "root" ]]; then echo "running as root"; fi
```

Note command substitution runs in a **subshell** — variable assignments inside it do not survive. (Subshells and process scope are covered in [01 — Fundamentals](01_fundamentals.md).)

---

## 7. Arithmetic substitution: `$(( ))`

`$(( expr ))` evaluates integer arithmetic and substitutes the result. Inside, `$` on variables is optional and word-splitting doesn't apply.

```bash
i=5
echo "$(( i + 1 ))"        # 6  — no $ needed inside (( ))
echo "$(( i * 2 - 3 ))"    # 7
total=$(( price * qty ))

(( i++ ))                  # arithmetic COMMAND form — mutates, returns exit status
(( count > 0 )) && echo "non-empty"   # use as a condition

# Bases and operators
echo "$(( 0xff ))"         # 255   (hex)
echo "$(( 2#1010 ))"       # 10    (binary)
echo "$(( 10 / 3 ))"       # 3     — INTEGER ONLY, truncates
```

**Trap:** Bash arithmetic is integer-only. **Symptom:** `10/3` is `3`, decimals silently lost. **Fix:** use `awk`, `bc -l`, or `printf` for floating point:

```bash
result=$(awk 'BEGIN { printf "%.2f", 10/3 }')   # 3.33
```

**Trap:** `(( expr ))` returns exit status `1` (failure) when the result is `0`. **Symptom:** `set -e` kills your script on `(( i++ ))` when `i` was `0`. **Cause:** the arithmetic value `0` maps to a non-zero (false) exit. **Fix:** prefer `i=$(( i + 1 ))`, or use `(( i++ )) || true`. See [08 — Error Handling, Strict Mode & Debugging](08_error_handling_debugging.md).

---

## 8. Environment vars vs shell vars, and `export`

Two scopes, easy to conflate:

- **Shell variable** — lives in the current shell only. Invisible to child processes.
- **Environment variable** — copied into the environment of **child processes** you launch.

`export` promotes a shell variable into the environment.

```bash
greeting="hi"           # shell variable only
export greeting         # now exported to children
export region="us-east" # assign + export in one line

bash -c 'echo "$greeting $region"'   # child sees both
```

```bash
# Inspect
echo "$HOME"            # a single var
env                     # all environment vars
set                     # all shell vars + functions (huge)
declare -p greeting     # show how a specific var is declared

# Per-command env without exporting globally — leaks nothing
DEBUG=1 LOG_LEVEL=trace ./run.sh

# Remove
unset greeting
```

Quote on export too: `export PATH="$PATH:/opt/bin"` (unquoted, a `PATH` with spaces or globs would break).

### `local` — preview

Inside functions, **always** declare working variables `local` so they don't leak into or clobber the caller's scope:

```bash
process() {
  local f="$1"            # scoped to this function
  local count=0
  # ...
}
```

**Gotcha:** `local var=$(cmd)` masks the command's exit status (the `local` builtin succeeds), defeating `set -e`. Split it: `local var; var=$(cmd)`. Functions, scope, and return values are covered in depth in a later chapter; for now: default to `local`.

---

## 9. The cheat sheet

```bash
name="value"                       # assign, no spaces around =
echo "${name}"                     # reference, braces for safety
'literal'                          # no expansion at all
"$expanded"                        # expand, no splitting — THE DEFAULT
$unquoted                          # expand + split + glob — almost never
"$(cmd)"                           # command substitution, quoted
$(( a + b ))                       # integer arithmetic
"${arr[@]}"                        # all array elements, each one quoted
while IFS= read -r line; do        # the canonical safe read loop
export NAME="value"                # promote to environment
local x; x=$(cmd)                  # function-scoped, status-preserving
```

**The one rule:** if it has a `$`, it gets double quotes — until you can articulate exactly why this specific case must not. The exceptions (deliberate splitting in §5, intentional globbing in §3) are rare, named, and on purpose.

---

> Next: [03 — Parameter Expansion](03_parameter_expansion.md) — the full power of `${...}`: defaults `${x:-y}`, substring `${x:2:3}`, pattern stripping `${x#prefix}` / `${x%suffix}`, search-and-replace `${x//a/b}`, case conversion, and indirection. Where quoting meets surgical string manipulation.
