# 05 — Functions & Arrays

> **Audience:** Scripters who can already branch and loop ([04 — Control Flow](04_control_flow.md)) and want to package logic into reusable functions and manipulate collections of data. By the end you will know how to scope variables, return both *status* and *data*, and build command lines from arrays so you never reach for `eval`.

This is where shell scripts stop being top-to-bottom recipes and start being programs. The two features that get this most wrong in the wild are **variable scope** (everything is global by default) and **array quoting** (one missing `"` silently corrupts your data). Both are covered in depth below.

---

## 1. Defining functions

Two equivalent syntaxes. Prefer the first — it is POSIX-portable and unambiguous.

```bash
# Preferred: name() { ... }
greet() {
  echo "hello, $1"
}

# Bash-only keyword form (no parens). Works, but offers nothing extra.
function greet {
  echo "hello, $1"
}

greet world          # call it — no parentheses, args are space-separated
```

A function must be **defined before it is called** — the parser reads top to bottom. Put functions near the top of the file (or in a sourced library) and your `main` logic last.

```bash
main() {
  greet "$@"
}
# ... all other functions ...
main "$@"            # single entry point on the LAST line
```

---

## 2. Positional parameters inside a function

Inside a function, `$1 $2 …` are the function's arguments — **not** the script's. The script's own arguments are shadowed for the duration of the call. See [02 — Variables, Quoting & Expansion](02_variables_quoting_expansion.md) for the full quoting rationale.

| Token       | Meaning inside a function                                  |
|-------------|------------------------------------------------------------|
| `$1`,`$2`…  | First, second, … argument                                  |
| `$#`        | Number of arguments                                        |
| `"$@"`      | All args, **each as a separate quoted word** (almost always what you want) |
| `"$*"`      | All args joined into **one** word by the first char of `IFS` |
| `$0`        | Script name (NOT the function name — a common surprise)    |
| `${FUNCNAME[0]}` | The current function's name                           |

```bash
show() {
  echo "got $# args"
  for a in "$@"; do        # ALWAYS quote: preserves words with spaces
    echo "  [$a]"
  done
}
show "a b" c               # got 2 args / [a b] / [c]
```

```bash
# WRONG — unquoted $@ word-splits "a b" into two
for a in $@; do ... ;  done     # sees: a / b / c  → THREE iterations

# RIGHT
for a in "$@"; do ... ; done    # sees: "a b" / c  → TWO iterations
```

Validate arity early:

```bash
deploy() {
  (( $# == 2 )) || { echo "usage: deploy <env> <tag>" >&2; return 2; }
  local env=$1 tag=$2
  ...
}
```

---

## 3. `local` scope — always use it

Bash variables are **global by default**, even when assigned inside a function. This leaks state across the whole script and causes maddening action-at-a-distance bugs.

- **Symptom:** A loop variable or temp inside `helper()` mysteriously changes a variable in `main()`.
- **Cause:** The assignment was global; both functions touched the same name.
- **Fix:** Declare every function-internal variable with `local`.

```bash
# WRONG — i leaks; if the caller also uses i, both break
count() {
  for i in 1 2 3; do total=$((total + i)); done
}

# RIGHT — locals are scoped to this call (and dynamically to its callees)
count() {
  local i total=0
  for i in 1 2 3; do total=$((total + i)); done
  echo "$total"            # return data via stdout (see §4)
}
```

> **Gotcha — `local` masks exit status.** `local x=$(cmd)` always succeeds (the `local` builtin returns 0), hiding `cmd`'s failure. Split it: declare `local x` first, then `x=$(cmd) || return 1` on the next line. Pairs with `set -e` discussion in [04 — Control Flow](04_control_flow.md).

---

## 4. Returning status vs returning data

Bash has two distinct channels. Confusing them is the single most common functional bug.

| You want to return…       | Mechanism                          | Read it with            |
|---------------------------|------------------------------------|-------------------------|
| Success/failure (0–255)   | `return N` (exit status)           | `if func; then` / `$?`  |
| A string / number / list  | `echo`/`printf` to stdout          | `result=$(func)`        |
| Mutate a caller variable  | nameref out-param (`local -n`)     | caller reads its var    |

### 4a. Exit status — `return` (0–255 only)

```bash
is_even() {
  (( $1 % 2 == 0 )) && return 0 || return 1
}
if is_even 4; then echo "even"; fi      # 0 = success/true
```

`return` only carries an integer 0–255. `return 300` wraps to `300 % 256 = 44`. Never try to `return "$some_string"` — it errors.

### 4b. Returning data — stdout + command substitution

```bash
upper() {
  local s=$1
  printf '%s' "${s^^}"      # write the "value" to stdout, no trailing newline
}
name=$(upper "ada")         # capture it; $? is upper's exit status
echo "$name"                # ADA
```

```bash
# WRONG — tries to send a string through the status channel
get_path() { return "/etc/hosts"; }     # error: not a number

# RIGHT
get_path() { echo "/etc/hosts"; }
p=$(get_path)
```

### 4c. Out-parameter via nameref (`local -n`)

Returning large data through `$(...)` forks a subshell and copies text. For arrays or hot paths, hand the function the **name** of the caller's variable; the nameref makes the local an alias for it.

```bash
fill_list() {
  local -n out=$1           # out is now an alias for the caller's variable
  out=(alpha beta gamma)    # mutates the caller's array directly, no subshell
}

declare -a items
fill_list items             # pass the NAME, not "$items"
echo "${items[1]}"          # beta
```

Avoid naming the nameref the same as the caller's variable (`local -n x=x` is a circular-reference error). A common convention is a trailing underscore: `local -n out_=$1`.

---

## 5. Recursion caveats

Recursion works, but the shell is not built for it.

- No tail-call optimisation — each level adds a frame.
- `FUNCNEST` (default unset/large) and ultimately the process stack cap depth; deep recursion crashes with *"maximum function nesting level exceeded."*
- Every level needs its own `local` state, or recursion will clobber shared globals.

```bash
factorial() {
  local n=$1
  (( n <= 1 )) && { echo 1; return; }
  local sub; sub=$(factorial $((n - 1)))   # capture child's stdout
  echo $(( n * sub ))
}
factorial 5    # 120
```

For anything deep (tree walks, large fan-out) prefer an explicit stack array or an iterative loop.

---

## 6. Indexed arrays

```bash
declare -a fruits=(apple banana cherry)   # explicit; declare -a is optional
fruits[5]=fig                             # arrays are sparse — gaps are fine

echo "${fruits[0]}"        # apple   (always brace-index; $fruits[0] is wrong)
echo "${#fruits[@]}"       # 4       (COUNT of elements, not highest index)
echo "${!fruits[@]}"       # 0 1 2 5 (the INDEX keys — note the gap)
```

```bash
fruits+=(date elderberry)  # append (NOT fruits=fruits+... )
unset 'fruits[1]'          # delete one element (keeps it sparse; quote the index)
```

Slicing — `${arr[@]:start:count}`:

```bash
nums=(10 20 30 40 50)
echo "${nums[@]:1:2}"      # 20 30
echo "${nums[@]: -2}"      # 40 50   (mind the space before -2)
```

---

## 7. `[@]` vs `[*]` vs unquoted — the correctness table

This is the array equivalent of the quoting rules in [02 — Variables, Quoting & Expansion](02_variables_quoting_expansion.md). Get it wrong and elements split or merge silently.

| Form          | Expands to                                            | Use when                          |
|---------------|-------------------------------------------------------|-----------------------------------|
| `"${arr[@]}"` | Each element as its **own** quoted word               | **Default.** Looping, passing args |
| `"${arr[*]}"` | All elements as **one** word, joined by `IFS[0]`      | Building a single display string  |
| `${arr[@]}`   | Each element, then **word-split + glob-expanded**     | Almost never — a bug magnet       |
| `${arr[*]}`   | One word, then split + globbed                         | Almost never                      |

```bash
files=("my report.txt" "*.log")

# WRONG — unquoted: "my report.txt" splits into 2; "*.log" glob-expands to real files!
for f in ${files[@]}; do echo "[$f]"; done

# RIGHT — quoted [@]: exactly 2 iterations, literal values preserved
for f in "${files[@]}"; do echo "[$f]"; done

# Joining for display only
IFS=, ; echo "csv: ${files[*]}"; unset IFS   # csv: my report.txt,*.log
```

---

## 8. Build argv arrays for safe command construction (no `eval`)

When a command line is assembled dynamically, **store the pieces in an array and expand `"${cmd[@]}"`**. Each element becomes exactly one argument — spaces, quotes and globs in the data stay inert. This is the structural defence against command injection covered further in [10 — Advanced & Enterprise](10_advanced_enterprise.md#building-argv-arrays-to-avoid-injection).

```bash
# WRONG — string + eval: a filename like "; rm -rf ~" executes as code
cmd="rsync -a $src $dst"
eval "$cmd"

# RIGHT — array: data can never break out of its argument slot
cmd=(rsync -a)
[[ -n $exclude ]] && cmd+=(--exclude "$exclude")   # conditional flags append cleanly
cmd+=("$src" "$dst")
"${cmd[@]}"                                         # one element == one argv slot
```

- **Symptom:** Filenames with spaces split, or shell metacharacters in user input get executed.
- **Cause:** The command was a single string passed through `eval` (or unquoted expansion).
- **Fix:** Keep arguments as array elements and invoke `"${cmd[@]}"`. Never `eval` data.

---

## 9. Associative arrays (maps)

Require `declare -A` **before** first use (Bash 4+). Without it, Bash treats string keys as arithmetic and silently writes to index 0.

```bash
declare -A color            # MUST declare -A first
color[apple]=red
color[lime]=green
color+=([plum]=purple)      # append entries

echo "${color[apple]}"      # red
echo "${!color[@]}"         # apple lime plum   (the KEYS — unordered)
echo "${color[@]}"          # red green purple  (the VALUES)
echo "${#color[@]}"         # 3                  (entry count)
```

Existence check — distinguish "missing key" from "empty value" with the `-v` test:

```bash
if [[ -v color[apple] ]]; then echo "have apple"; fi   # key exists?
[[ -v color[mango] ]] || echo "no mango"               # absent

# Iterate keys safely (quote the expansion)
for k in "${!color[@]}"; do
  printf '%s=%s\n' "$k" "${color[$k]}"
done
```

> Associative-array keys are **unordered** — never rely on insertion order. Sort the keys (`printf '%s\n' "${!color[@]}" | sort`) if you need determinism.

---

## 10. Passing arrays to functions

You **cannot** pass an array as a single positional argument — `func "$arr"` sends only element 0. Two correct techniques:

### 10a. Expand into positional args (read-only, simple)

```bash
sum() {
  local n=0 x
  for x in "$@"; do (( n += x )); done   # the array arrives as separate args
  echo "$n"
}
nums=(3 5 8)
sum "${nums[@]}"          # 16  — expand at the call site
```

### 10b. Pass by name with a nameref (large data or need to mutate)

```bash
# Works for indexed AND associative arrays — pass the bare NAME
print_map() {
  local -n m=$1            # alias to the caller's array
  local k
  for k in "${!m[@]}"; do printf '%s -> %s\n' "$k" "${m[$k]}"; done
}

declare -A env=([HOST]=db1 [PORT]=5432)
print_map env             # NAME only, no $ and no quotes
```

- **Symptom:** A function "receives an array" but only sees the first element.
- **Cause:** `func "$arr"` expands to `${arr[0]}`; arrays are not first-class values.
- **Fix:** Either expand with `"${arr[@]}"` (10a) or pass the name and use `local -n` (10b).

---

## Recap

- `name() { ... }`; define before calling; one `main "$@"` entry point on the last line.
- Inside functions: `"$@"` (quoted!) for args, `$#` for count, `$0` is the script.
- **`local` everything** — scope leaks are the #1 function bug; `local x=$(cmd)` hides failures.
- Return **status** with `return 0–255`; return **data** via `echo` + `$(func)`; mutate caller vars via `local -n`.
- Indexed arrays: `arr+=(x)`, `${#arr[@]}` count, `${!arr[@]}` keys, `${arr[@]:1:2}` slice.
- **Always `"${arr[@]}"`** when looping or passing args; `"${arr[*]}"` only to join for display.
- Build command lines as **argv arrays** and run `"${cmd[@]}"` — never `eval` data.
- Associative arrays need `declare -A`; check keys with `[[ -v map[k] ]]`; keys are unordered.
- Pass arrays to functions by expanding `"${arr[@]}"` or by name via `local -n`.

> Next: [06 — I/O, Redirection & Here-Docs](06_io_redirection.md) — now that functions can return data on stdout, we will master where that data flows: file descriptors, `>` vs `>>` vs `2>&1`, pipes, process substitution, and feeding multi-line input with here-docs and here-strings.
