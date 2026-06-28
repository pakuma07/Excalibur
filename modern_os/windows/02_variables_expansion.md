# 02 — Variables & Expansion

> **Audience:** zero to staff. Variables are simple; **expansion timing** is where
> batch earns its reputation. This chapter covers `set`, the two expansion phases,
> **delayed expansion** (`!var!`) — the fix for the #1 batch bug — arithmetic,
> input, and `setlocal` scope.

---

## 1. Setting and reading variables

```bat
set NAME=Ada                 :: sets NAME ... but a trailing space becomes part of it!
set "NAME=Ada"               :: PREFERRED: quotes delimit the value, no stray spaces
echo %NAME%                  :: read with %...%
set "EMPTY="                 :: unset / clear a variable
set NAME                     :: print all vars starting with NAME
set                          :: print ALL environment variables
```

- **Always use `set "VAR=value"`** (quotes around the whole `name=value`). Without
  quotes, `set X=5 ` (trailing space) stores `"5 "` — a maddening source of `if`
  comparison failures. The quotes are *not* stored; they just bound the assignment.
- Variable names are **case-insensitive** (`%name%` == `%NAME%`). Values are strings;
  batch has no real types.
- Reading an **undefined** variable expands to empty (after parse). This is why
  `if "%VAR%"=="x"` needs the quotes — if `VAR` is empty, `if ==x` is a syntax error,
  but `if ""=="x"` is valid.

---

## 2. The two expansion phases (the core concept)

From chapter 01: `cmd.exe` does **percent expansion at parse time**, then runs the
command. A `for` loop or a parenthesized `if`/block is parsed as **one unit**, so all
`%var%` inside it are substituted with the value *before the block ran*.

```bat
@echo off
set "count=0"
for %%f in (a b c) do (
    set /a count+=1
    echo %count%        :: ALWAYS prints 0 — %count% was expanded once, at parse time
)
echo final: %count%     :: prints 3 (correct, outside the block)
```

The loop body is parsed once; `%count%` became the literal `0` before the loop
executed even a single iteration. This is **not a bug in your logic** — it's the
parsing model.

---

## 3. Delayed expansion — the fix

Enable **delayed expansion** and read with `!var!` to get the value *at execution
time*:

```bat
@echo off
setlocal enabledelayedexpansion        :: turn it on (scoped by setlocal)
set "count=0"
for %%f in (a b c) do (
    set /a count+=1
    echo !count!        :: prints 1, 2, 3 — !...! is evaluated each iteration
)
echo final: !count!     :: 3
endlocal
```

The rule to memorize: **`%var%` = value at *parse* time; `!var!` = value at *run*
time.** Inside any loop or block where a variable changes, use `!var!`.

> **The catch:** delayed expansion makes `!` a special character, so a *value* that
> legitimately contains `!` gets mangled (e.g. a password `p@ss!word`). Toggle it on
> only around the code that needs it, or use the `call`-based read trick (chapter 05)
> for `!`-containing data. This is a real gotcha cataloged in chapter 08.

---

## 4. Arithmetic with `set /a`

```bat
set /a result=2+3*4          :: 14 — standard C-style precedence, INTEGER only
set /a result=10/3           :: 3  — integer division (no floats in batch, ever)
set /a result=10%%3          :: 1  — modulo (% doubled in a file)
set /a x+=5                  :: compound assignment works
set /a "y=(a+b)*c"           :: quote if using special chars like ( ) & | < >
set /a hex=0xFF              :: 255 — 0x hex and 0 octal literals are supported
set /a flags=1^<^<4          :: bit shift (^ escapes < > on the command line)
```

- **Integer only.** No floating point — for that you must shell out (PowerShell,
  `wmic`, or compute scaled integers). A frequent reason to leave batch.
- `set /a` does **not** require `%` to read variables: `set /a sum=a+b` works
  (it parses bare names). It also doesn't print unless you echo it.

---

## 5. User input with `set /p`

```bat
set /p "name=Enter your name: "      :: prompt and read a line into name
set /p "ans=Continue? [y/N] "
if /i "%ans%"=="y" echo proceeding   :: /i = case-insensitive compare (ch 03)

:: Read the FIRST LINE of a file into a variable (a handy idiom):
set /p firstline=<config.txt
```

- `set /p` reads one line (no trailing newline). If the user just presses Enter, the
  variable is **left unchanged** (not emptied) — initialize it first if that matters.
- The `<file` redirect form reads the first line of a file — useful for one-line
  config/version files. For multi-line file reading, use `for /f` (chapter 03).

---

## 6. setlocal / endlocal — variable scope

```bat
@echo off
set "GLOBAL=outer"
setlocal                      :: snapshot the environment; changes are local from here
set "GLOBAL=inner"
set "TEMP_VAR=scratch"
echo inside: %GLOBAL%         :: inner
endlocal                      :: RESTORE the environment to the snapshot
echo outside: %GLOBAL%        :: outer  (and TEMP_VAR is gone)
```

- **`setlocal`** begins a local scope: every `set` after it is undone by
  **`endlocal`** (or at script end). This keeps a script from polluting the caller's
  environment — a basic hygiene habit; start most scripts with `setlocal`.
- `endlocal` discards *all* changes since `setlocal`, so **returning a value past
  `endlocal`** needs a trick (chapter 05) — naively setting a var before `endlocal`
  loses it.
- `setlocal enabledelayedexpansion` combines scope + delayed expansion (most common
  form). `setlocal enableextensions` ensures command extensions are on (they are by
  default, but defensive scripts state it).

---

## 7. Variable types you get for free

```bat
echo %CD%              :: current directory
echo %~dp0            :: drive+path of THIS script (the most useful one — ch 05)
echo %DATE% %TIME%    :: locale-dependent! parsing these is painful (ch 04/08)
echo %RANDOM%         :: a pseudo-random 0-32767 each time it's read
echo %ERRORLEVEL%     :: last exit code (ch 06)
echo %COMPUTERNAME% %USERNAME% %USERPROFILE% %APPDATA% %TEMP%
echo %PATH%           :: the search path
```

- **`%~dp0`** (drive + path of arg 0 = the script itself) is essential for scripts
  that must reference files next to themselves regardless of the current directory.
  The `%~...` modifiers are detailed in chapter 05.
- **`%DATE%`/`%TIME%` are locale-formatted** and unreliable to parse across machines
  — for timestamps, prefer `wmic os get localdatetime` or PowerShell (chapter 07/08).

---

## 8. Key takeaways

1. Always assign with **`set "VAR=value"`** (quote the whole pair) to avoid
   trailing-space bugs.
2. **`%var%` expands at parse time; `!var!` at run time.** Inside any loop/block
   where a variable changes, you need **delayed expansion** (`setlocal
   enabledelayedexpansion` + `!var!`).
3. Delayed expansion makes `!` special — beware values containing `!`.
4. **`set /a`** is integer-only arithmetic (no floats — a reason to use PowerShell);
   **`set /p`** reads input (and `set /p x=<file` reads a file's first line).
5. **`setlocal`/`endlocal`** scope variable changes; `endlocal` discards them, so
   returning values needs care (chapter 05).
6. Useful built-ins: **`%~dp0`** (script's own path), `%CD%`, `%RANDOM%`,
   `%ERRORLEVEL%`; avoid parsing locale-dependent `%DATE%`/`%TIME%`.

> Next: [03 — Control Flow](03_control_flow.md) — `if`, the many faces of `for`,
> `goto`, and `call`.
