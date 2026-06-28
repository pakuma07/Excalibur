# 01 — Batch Fundamentals

> **Audience:** zero to staff. This chapter establishes the mental model that makes
> the rest of batch make sense: what `cmd.exe` actually does to your script, how
> scripts run, comments, echoing, and the exit-code contract. Get the **parsing
> model** here and the famous quirks in later chapters stop being mysterious.

---

## 1. What batch *is*

A batch file is a plain-text list of commands interpreted by **`cmd.exe`**, the
Windows command processor (the descendant of DOS `COMMAND.COM`). There is no
compiler and no runtime VM — `cmd.exe` reads your file, **expands** it, and runs each
command exactly as if you had typed it at the prompt.

Two extensions, both interpreted by `cmd.exe`:

- **`.bat`** — the classic DOS-era extension.
- **`.cmd`** — Windows NT+ extension; behaves almost identically. The one real
  difference: a few built-in commands (`append`, `dpath`, `ftype`, `set`, `path`,
  `assoc`, `prompt`) reset `errorlevel` differently — `.cmd` sets it to 0 on success
  where `.bat` may leave it unchanged. **Prefer `.cmd`** for new scripts; the
  `errorlevel` behavior is more predictable (chapter 06).

```bat
:: hello.cmd
@echo off
echo Hello from %COMPUTERNAME% as %USERNAME%
```

---

## 2. The parsing model (the key to everything)

This is the single most important concept in batch. `cmd.exe` processes the file in
**phases**, and the order explains every later quirk:

```
For each logical line (or parenthesized block, read as a unit):
  1. Read the line.
  2. Percent expansion:   %var%, %1, %%i           <- happens NOW, at parse time
  3. Special-char / token parsing:  &  |  <  >  ( ) , ; = (split into commands)
  4. Delayed expansion (IF enabled): !var!          <- happens at EXECUTION time
  5. Execute the resulting command.
```

The consequence that trips up everyone: **`%var%` is substituted before the line
runs.** Inside a `for` loop or an `if` block (which `cmd` reads as one unit), every
`%var%` is replaced with the value it had *before the block started* — not the
current value. The fix is **delayed expansion** `!var!` (chapter 02). Burn this in:
*percent = parse time, exclamation = run time.*

---

## 3. Running a script

```cmd
hello.cmd                  REM run in the current cmd window
cmd /c hello.cmd           REM run in a child cmd, then exit (propagates exit code)
cmd /k hello.cmd           REM run, then KEEP the window open (for debugging)
call hello.cmd             REM run another batch file and RETURN to this one (ch 05)
```

- **Double-clicking** a `.cmd` opens a console, runs it, and closes it on exit — so
  a script that errors flashes and vanishes. End interactive scripts with `pause`
  (waits for a keypress) while developing, or run from an already-open `cmd`.
- **`call` vs direct invocation:** running another batch file *directly* transfers
  control and never comes back (like `exec`); **`call`** runs it as a subroutine and
  returns. Forgetting `call` is a classic "the rest of my script didn't run" bug
  (chapter 05).

---

## 4. echo and @echo off

By default `cmd.exe` **prints each command before running it** (command echoing).
`echo off` turns that off; the leading **`@`** suppresses echoing of *that one line*
— so `@echo off` is "turn off echoing, and don't even show this line."

```bat
@echo off
echo This line's output is shown, but the 'echo' command itself is not.
echo.                       :: a single dot prints a BLANK line
echo Progress: 50%%         :: literal % must be DOUBLED in a batch file
@somecommand                :: @ hides just this command's echo (rare once echo is off)
```

- **`echo.`** (dot, no space) prints an empty line. `echo` alone prints the *current
  echo state* (`ECHO is on/off`), not a blank line — a common surprise.
- **`%` must be doubled** (`%%`) to print a literal percent in a `.bat`/`.cmd` file
  (at the prompt, a single `%` is fine). This is the same percent-expansion rule
  from §2.

---

## 5. Comments

```bat
REM This is the classic, safe comment (a real command that does nothing).
:: This is a label that looks like a comment — common, but with caveats.
```

- **`REM`** is a genuine no-op command — always safe, works anywhere, including
  inside `( )` blocks.
- **`::`** is actually a *label* (`:` + `:`), abused as a comment because it's
  terse. It's fine on its own line, but **`::` inside a `for` or `if ( )` block can
  break parsing** (a label in a block confuses `cmd`). Inside blocks, use `REM`.
- You cannot put a comment after `^` line continuation, and `REM` consumes the rest
  of the line including `&` separators.

---

## 6. The exit-code contract

Every command sets an exit code; in batch it surfaces as **`errorlevel`** (and the
`%errorlevel%` variable). `0` means success by convention; non-zero means failure.
This is how scripts and CI decide pass/fail — getting it right is chapter 06, but
the basics:

```bat
ping -n 1 example.com >nul
echo Exit code was %errorlevel%
if %errorlevel% neq 0 echo The ping FAILED

exit /b 0       :: exit THIS script with code 0 (use /b, see below)
exit 1          :: exit the entire cmd.exe process with code 1 (closes the window!)
```

- **`exit /b N`** exits the *script/subroutine* with code `N` and returns to the
  caller. **`exit N`** (no `/b`) terminates the whole `cmd.exe` — if run by
  double-click that closes your window; if `call`ed from another script it kills the
  parent too. Almost always use `exit /b`.
- A script with no explicit exit returns the errorlevel of its **last command** —
  which is often not what you want (chapter 06).

---

## 7. Getting help

```cmd
help                REM list built-in commands
help for            REM detailed help for a built-in (for, if, set, ...)
for /?              REM the /? switch works on almost every command — USE THIS
robocopy /?         REM external tools too
where cmd           REM find a command's path (like Unix `which`)
```

`command /?` is your reference for the dozens of switches — `for /?`, `set /?`, and
`if /?` in particular are dense and worth reading in full once.

---

## 8. A first real script

```bat
@echo off
setlocal                          :: scope variables to this script (chapter 02)

echo === Backup script ===
set "SRC=C:\data"                 :: quote the WHOLE assignment to avoid trailing-space bugs
set "DST=D:\backup"

if not exist "%SRC%" (
    echo ERROR: source "%SRC%" not found 1>&2
    exit /b 1
)

robocopy "%SRC%" "%DST%" /MIR /R:2 /W:5 >nul
:: robocopy uses exit codes 0-7 for success (chapter 07) — don't treat >0 as failure!
if %errorlevel% geq 8 (
    echo ERROR: robocopy failed with %errorlevel% 1>&2
    exit /b 1
)

echo Backup complete.
endlocal
exit /b 0
```

This already shows the staff-level habits: `setlocal`, quoted assignments, quoted
paths, explicit error checks to stderr (`1>&2`), and `exit /b` codes — all expanded
in later chapters.

---

## 9. Key takeaways

1. Batch is **interpreted line-by-line by `cmd.exe`**; there's no compile step, and
   the **parse-then-execute phase order** (percent expansion at parse time) explains
   every later quirk.
2. Prefer **`.cmd`** over `.bat` for more predictable `errorlevel`.
3. **`@echo off`** silences command echoing; **`%` doubles to `%%`** in a file;
   **`echo.`** prints a blank line.
4. Use **`REM`** for comments inside blocks (`::` can break `for`/`if` blocks).
5. **`exit /b N`** exits the script (not the whole shell); the exit code is the
   success contract — `0` = success.
6. **`command /?`** is your reference for every command's switches.

> Next: [02 — Variables & Expansion](02_variables_expansion.md) — `set`, the two
> expansion phases, and **delayed expansion**, the quirk behind most batch bugs.
