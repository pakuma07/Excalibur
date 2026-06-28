# 08 — Gotchas & Quirks Catalog

> **Audience:** all levels — keep this open while debugging. Batch fails in
> consistent, *learnable* ways. This is the catalog of traps that cause the "it
> randomly broke" tickets, grouped so you can pattern-match a symptom to a cause.

---

## 1. Expansion timing (the #1 cause of bugs)

```bat
:: BUG: %var% inside a block is the value from BEFORE the block ran.
set "x=1"
if "%x%"=="1" ( set "x=2" & echo %x% )     :: prints 1, not 2
```

- **Symptom:** a variable changed in a loop/`if` block "doesn't update" / is always
  its initial value.
- **Cause:** `%var%` is expanded at *parse* time, once, for the whole block (ch 02).
- **Fix:** `setlocal enabledelayedexpansion` and read with `!var!`.

---

## 2. Delayed expansion eats `!` (and `^`)

```bat
setlocal enabledelayedexpansion
set "pw=p@ss!word"
echo %pw%      :: p@ss!word  (fine)
echo !pw!      :: p@ssword   (the !word! was treated as a variable -> mangled!)
```

- **Symptom:** values containing `!` lose characters when delayed expansion is on.
- **Fix:** toggle delayed expansion **off** around code handling `!`-containing data,
  or read such values with `%var%` (parse-time) only, or use the `call set` trick.
  This is the dark side of the §1 fix — they conflict.

---

## 3. Trailing spaces in `set`

```bat
set X=value     :: a trailing space before the line end becomes part of X!
if "%X%"=="value" echo match   :: FAILS — X is actually "value "
```

- **Fix:** always `set "X=value"` (quote the whole assignment). The quotes aren't
  stored; they pin the boundaries.

---

## 4. `if errorlevel` is "greater-or-equal"

```bat
if errorlevel 0 echo always    :: TRUE for every exit code (>= 0)
```

- **Fix:** `%errorlevel% equ 0` for exact; `if not errorlevel 1` for "is zero"; and
  `!errorlevel!` inside blocks (§1 again).

---

## 5. `::` comments break `for`/`if` blocks

```bat
for %%i in (1 2 3) do (
    :: this comment breaks the block parsing
    echo %%i
)
```

- **Symptom:** "the system cannot find the batch label" or silent breakage inside a
  block.
- **Fix:** use `REM` (a real command) inside `( )` blocks; reserve `::` for standalone
  lines (ch 01).

---

## 6. Pipes run in a child shell

```bat
echo hi | set "GOT=%%a"       :: GOT is NOT set in the parent
dir | find /c /v "" > tmp & set /p N=<tmp   :: workaround via a temp file or for /f
```

- **Symptom:** a variable set on either side of `|` is empty afterward.
- **Cause:** each side of a pipe is its own `cmd.exe`.
- **Fix:** capture with `for /f` (escape the inner pipe as `^|`).

---

## 7. Quoting, spaces, and special characters

```bat
del %file%              :: BREAKS if %file% has a space -> "del C:\my" "file.txt"
del "%file%"            :: correct
echo a & b              :: '&' starts a NEW command -> runs 'b'! Use: echo a ^& b
copy a.txt b.txt & echo done   :: ' & ' chains commands (often intended)
```

- **The dangerous set:** `& | < > ( ) ^ % ! "` and **space**.
- **Fixes:** quote `%var%` that may contain spaces/`&`; escape a literal special with
  `^` (`^&`, `^|`, `^>`); double `%` to `%%`. Unquoted **untrusted** input here is a
  command-injection vulnerability, not just a bug.

---

## 8. Locale-dependent `%DATE%` / `%TIME%`

```bat
echo %DATE%    :: "Mon 06/23/2026" on one box, "23/06/2026" on another, "2026-06-23"...
```

- **Symptom:** date parsing works on your machine, fails on another (or in another
  user's regional settings).
- **Fix:** use `wmic os get localdatetime /value` (YYYYMMDD..., locale-independent) or
  PowerShell `Get-Date -Format s`. Never parse `%DATE%`/`%TIME%` for logic.

---

## 9. `setlocal` swallows your return value

```bat
:get
setlocal
set "RESULT=42"
endlocal          :: RESULT is discarded here!
goto :eof
```

- **Fix:** `endlocal & set "%~1=%RESULT%"` (the out-parameter idiom, ch 05).

---

## 10. Forgetting `call` for sub-scripts

```bat
other.cmd         :: control transfers and NEVER returns (rest of this script is dead)
call other.cmd    :: runs and returns
```

- **Symptom:** "the lines after invoking another script never ran."
- **Fix:** `call` it (ch 01/05).

---

## 11. `exit` vs `exit /b`

```bat
exit 1            :: kills the WHOLE cmd.exe (closes the window / kills the caller)
exit /b 1         :: exits just this script/subroutine
```

- **Fix:** almost always `exit /b`. Bare `exit` is for "close this shell entirely."

---

## 12. Unicode and codepages

```bat
chcp                 :: show current codepage (often 437/850 on US, 1252...)
chcp 65001           :: switch to UTF-8 (needed to handle/echo non-ASCII correctly)
type utf8file.txt    :: may show mojibake unless the console codepage matches
```

- **Symptom:** accented/CJK characters become garbage; redirected files have a BOM or
  wrong encoding.
- **Fix:** `chcp 65001` for UTF-8 (and use a font that supports the glyphs); be aware
  that `>` redirection writes in the console codepage. For real Unicode/encoding work,
  PowerShell handles it far better.

---

## 13. Long paths and the 260-char limit

- Classic Win32 APIs cap paths at **MAX_PATH = 260**; deep trees (e.g.
  `node_modules`) blow past it and tools fail with "path too long." `robocopy` handles
  long paths; many built-ins don't. Modern Windows can opt in to long paths via a
  registry/Group-Policy setting (`LongPathsEnabled`) — but don't assume it.

---

## 14. Scheduled-task environment differs

- A `schtasks` job runs **non-interactively**: different `PATH`, current directory
  often `C:\Windows\System32`, no mapped drives, possibly a different account
  (`SYSTEM`). "Works when I run it, fails on schedule" is almost always this — use
  **absolute paths**, set your own `cd /d "%~dp0"`, and don't rely on user env (ch 07).

---

## 15. UAC / elevation

```bat
net session >nul 2>&1 || ( echo Run as Administrator. 1>&2 & exit /b 1 )
```

- Many operations (HKLM registry, services, `Program Files`, system config) require
  **elevation**. A batch file can't elevate itself cleanly; detect non-elevation with
  the `net session` check above and bail with a clear message (or relaunch via a
  PowerShell `Start-Process -Verb RunAs` shim).

---

## 16. `.bat` vs `.cmd` errorlevel difference

- A handful of built-ins (`set`, `path`, `assoc`, `prompt`, …) reset `errorlevel` to 0
  on success in **`.cmd`** but may leave it unchanged in **`.bat`**. Prefer `.cmd` for
  predictable error handling (ch 01).

---

## Quick symptom → cause table

| Symptom | Likely cause | Section |
|---|---|---|
| Variable "doesn't update" in a loop | parse-time `%var%` expansion | §1 |
| Value with `!` loses characters | delayed expansion | §2 |
| `if` comparison fails unexpectedly | trailing space in `set` | §3 |
| `if errorlevel 0` always true | `errorlevel` is ≥ | §4 |
| "Cannot find batch label" in a block | `::` inside `( )` | §5 |
| Var empty after a pipe | pipe runs in child shell | §6 |
| Breaks on a path with a space | unquoted `%var%` | §7 |
| Date parsing fails on another PC | locale `%DATE%`/`%TIME%` | §8 |
| Subroutine's result is empty | `endlocal` discarded it | §9 |
| Script stops after calling another | missing `call` | §10 |
| Window closes / parent dies | bare `exit` | §11 |
| Non-ASCII is garbled | codepage | §12 |
| "Path too long" | MAX_PATH 260 | §13 |
| Works manually, fails scheduled | task env differs | §14 |
| "Access denied" on system change | needs elevation | §15 |

> Next: [09 — PowerShell Bridge](09_powershell_bridge.md) — when to stop fighting
> batch, and how the two interoperate.
