# Windows Batch Scripting (cmd) — from scratch to expert 🪟

> **Audience:** from zero to staff/principal. Batch (`cmd.exe`) is old, quirky, and
> still **everywhere** in Windows enterprises — build steps, scheduled tasks,
> installer wrappers, CI glue, and the script that runs before PowerShell is even
> available (PXE/WinPE, login scripts, MSI custom actions). You cannot operate a
> Windows fleet without reading and writing it. This series teaches the language
> *completely* — including the genuinely bizarre parts (two expansion phases,
> `errorlevel` lies, `for /f` tokenizing) — then shows when to graduate to
> **PowerShell**.

The batch language is small but full of traps. Most "my batch script randomly
fails" bugs are one of four things: **expansion timing** (`%var%` vs `!var!`),
**quoting**, **`errorlevel` semantics**, or **`setlocal` scope**. Every chapter
hammers those.

---

## 📚 Chapters

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [Fundamentals](01_fundamentals.md) | `cmd.exe` model, `.bat`/`.cmd`, `echo`/`@echo off`, comments, running scripts, exit codes, `help`/`/?` |
| 02 | [Variables & Expansion](02_variables_expansion.md) | `set`, `%var%`, the **two expansion phases**, **delayed expansion** `!var!`, `set /a` arithmetic, `set /p` input, `setlocal`/`endlocal` scope |
| 03 | [Control Flow](03_control_flow.md) | `if`/`else`, `errorlevel` comparisons, **every `for` variant** (`/l` `/d` `/r` `/f` tokens & delims), `goto`, `call`, loop emulation |
| 04 | [Strings & Substitution](04_strings.md) | substrings `%v:~n,m%`, search-and-replace `%v:a=b%`, case, find/parse, the quoting rules that bite |
| 05 | [Subroutines & I/O](05_functions_io.md) | `call :label` subroutines, returning values, redirection `>`/`>>`/`2>&1`, pipes, reading files, here-doc emulation |
| 06 | [Error Handling](06_error_handling.md) | `errorlevel` pitfalls, `&&`/`\|\|`, `exit /b`, robust patterns, logging, transactional/rollback scripts |
| 07 | [Advanced & Enterprise](07_advanced_enterprise.md) | `robocopy`, `schtasks`, `sc`, `reg`, `wmic`/`where`, networking, CSV parsing, real deployment/bootstrap scripts |
| 08 | [Gotchas & Quirks Catalog](08_gotchas_quirks.md) | the definitive trap list: expansion, special chars, `.bat` vs `.cmd`, Unicode/codepages, paths with spaces, UAC |
| 09 | [PowerShell Bridge](09_powershell_bridge.md) | why/when to move off batch, batch↔PowerShell interop, a migration map, calling each from the other |

---

## 🚀 Running batch

```bat
:: save as hello.bat, then run from cmd.exe or double-click
@echo off
echo Hello from %COMPUTERNAME%
```

```cmd
hello.bat               REM run it
cmd /c hello.bat        REM run and return its exit code
hello.bat > out.log 2>&1   REM capture stdout+stderr
```

- **`.bat` vs `.cmd`:** behavior is nearly identical on modern Windows; `.cmd` is
  marginally preferred (it sets `errorlevel` more predictably for some built-ins).
  Both are interpreted by `cmd.exe`.
- **There is no "compile."** `cmd.exe` parses and expands **line by line** (or
  block by block) — which is the root of the expansion-timing quirks in [02](02_variables_expansion.md).

---

## 🎯 The four traps that cause most batch bugs

1. **Expansion timing** — `%var%` is expanded when the line is *parsed*; inside a
   loop or `if` block that means "before the loop ran." Use **delayed expansion**
   `!var!` (with `setlocal enabledelayedexpansion`) to read the *current* value
   ([02](02_variables_expansion.md)).
2. **`errorlevel` is not a variable** — `if errorlevel 1` means "≥ 1", and `%errorlevel%`
   can be stale inside blocks. ([06](06_error_handling.md)).
3. **Quoting & special characters** — `&`, `|`, `<`, `>`, `^`, `%`, `!`, and spaces
   in paths all need care; one unquoted path breaks everything ([04](04_strings.md), [08](08_gotchas_quirks.md)).
4. **`setlocal` scope** — variables set inside `setlocal` vanish at `endlocal`;
   forgetting this (or relying on it) causes "my variable disappeared" bugs ([02](02_variables_expansion.md)).

> **When to stop using batch:** anything involving structured data (JSON/CSV/XML),
> real error handling, objects, or remoting → **PowerShell** ([09](09_powershell_bridge.md)).
> Use batch for tiny wrappers, bootstrap-before-PowerShell, and editing legacy
> scripts. A staff Windows engineer fluently reads batch and writes PowerShell.

> Related: [`../linux/`](../linux/README.md) for the Unix counterpart,
> [`../../os_net/`](../../os_net/README.md) for OS internals.
