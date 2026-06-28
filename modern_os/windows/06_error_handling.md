# 06 — Error Handling

> **Audience:** intermediate to staff. A script that doesn't handle errors correctly
> is worse than no script — it fails silently and corrupts state. Batch error
> handling is built on `errorlevel`, and `errorlevel` has **sharp edges**. This
> chapter makes your scripts fail fast, fail loud, and clean up.

---

## 1. errorlevel — the two forms and their traps

```bat
some_command
if errorlevel 1 echo failed         :: form A: TRUE when errorlevel >= 1
if %errorlevel% equ 1 echo exactly  :: form B: exact comparison via the variable
```

- **`if errorlevel N` means "≥ N"**, not "== N" (historical). So `if errorlevel 1`
  catches *any* non-zero — usually what you want for "did it fail?" But
  `if errorlevel 0` is **always true** (everything is ≥ 0) — a classic mistake. To
  test "exactly zero / success," use `if %errorlevel% equ 0` or `if not errorlevel 1`.
- **`%errorlevel%` is parse-time expanded** (chapter 02). Inside a `for`/`if` block it
  holds the value from *before the block* — use **`!errorlevel!`** with delayed
  expansion, or check the command's result immediately on the next line.

```bat
:: WRONG inside a block — %errorlevel% is stale:
for %%f in (*.zip) do (
    unzip "%%f"
    if %errorlevel% neq 0 echo failed     :: checks the PRE-LOOP errorlevel!
)
:: RIGHT — delayed expansion:
setlocal enabledelayedexpansion
for %%f in (*.zip) do (
    unzip "%%f"
    if !errorlevel! neq 0 echo failed %%f
)
```

---

## 2. && and || — conditional chaining

```bat
build.cmd && deploy.cmd               :: run deploy ONLY if build succeeded (exit 0)
build.cmd || echo BUILD FAILED        :: run the right side ONLY on failure (exit != 0)
build.cmd && echo ok || echo failed   :: success/failure branches in one line
mkdir out 2>nul || echo (already exists)   :: tolerate an expected failure
```

`&&` / `||` test the previous command's exit code directly — cleaner than
`if errorlevel` for simple "do next only if this worked." They are the batch
equivalent of Unix short-circuit operators and the backbone of compact, correct
pipelines.

> **Caveat:** some built-ins and GUI apps don't set `errorlevel` reliably, and a few
> commands (notably older `findstr`, `del` on a missing file) have surprising codes.
> Test the actual code (`echo %errorlevel%`) when in doubt.

---

## 3. Setting your script's exit code

```bat
exit /b 0       :: success — return 0 to the caller (NOT exit the whole shell)
exit /b 1       :: failure
exit /b %errorlevel%   :: propagate the last command's code

:: Inside a subroutine, exit /b returns from the subroutine with that code:
:do_thing
... 
if not exist "%~1" exit /b 2
exit /b 0
```

- **Always `exit /b` (with `/b`)** — `exit` alone kills the entire `cmd.exe`,
  closing the window on double-click and killing a parent that `call`ed you.
- A script that ends with no explicit `exit /b` returns the errorlevel of its **last
  command** — often accidental success/failure. End scripts with an explicit
  `exit /b 0` (or the propagated code).

---

## 4. The "strict-ish" patterns (batch has no `set -e`)

Batch has **no equivalent of Bash's `set -e`** — it never auto-aborts on error. You
build robustness manually:

```bat
@echo off
setlocal enabledelayedexpansion

:: Pattern 1: check-and-bail after each critical step.
call :step "checkout"  git pull            || exit /b 1
call :step "build"     msbuild app.sln      || exit /b 1
call :step "test"      run_tests.cmd        || exit /b 1
echo PIPELINE OK
exit /b 0

:step <name> <command...>
echo === %~1 ===
shift                                       :: drop the name; %* would still hold it
%*                                          :: run the remaining command line  (note: see caveat)
if errorlevel 1 (
    echo STEP FAILED: %~1 1>&2
    exit /b 1
)
exit /b 0
```

> **Caveat:** `shift` does not affect `%*`; the simple, reliable version is to pass
> the command without a name and just run `%*`, or check `errorlevel` after each call
> inline. The takeaway is the *discipline*: every critical command is followed by a
> failure check; there is no automatic safety net.

---

## 5. Logging and diagnostics

```bat
@echo off
set "LOG=%~dp0run_%RANDOM%.log"
call :log "starting"
... work ...
call :log "done"
exit /b 0

:log <message>
echo [%DATE% %TIME%] %~1
echo [%DATE% %TIME%] %~1 >> "%LOG%"     :: tee: console + file
goto :eof
```

- Write **errors to stderr** with `1>&2` so callers/CI separate them from normal
  output.
- For a real timestamp (locale-independent), use
  `for /f %%t in ('powershell -nop -c "Get-Date -f s"') do set "TS=%%t"` — `%DATE%`/
  `%TIME%` are locale-formatted and unreliable across machines (chapter 02/08).

---

## 6. Cleanup / "finally" via a single exit point

Batch has no `try/finally`, so funnel all exits through one label that does cleanup:

```bat
@echo off
setlocal
set "TMP_DIR=%TEMP%\job_%RANDOM%"
mkdir "%TMP_DIR%" || exit /b 1

:: ... do work; on any failure, 'goto :cleanup' with an error code ...
copy data.bin "%TMP_DIR%\" || ( set "rc=1" & goto :cleanup )
process "%TMP_DIR%\data.bin" || ( set "rc=2" & goto :cleanup )
set "rc=0"

:cleanup
rmdir /s /q "%TMP_DIR%" 2>nul          :: always runs — the 'finally'
exit /b %rc%
```

The pattern: set an `rc` (return code) variable, `goto :cleanup` on any failure, and
do teardown once at `:cleanup` before `exit /b %rc%`. This guarantees the temp dir is
removed whether the job succeeds or fails — the batch version of `trap ... EXIT`.

---

## 7. Transactional / rollback scripts

For changes that must be all-or-nothing (deploys, config edits), record undo steps
and run them on failure:

```bat
@echo off
setlocal
set "BACKUP=%~dp0backup"
mkdir "%BACKUP%" 2>nul

copy "C:\app\config.ini" "%BACKUP%\" >nul         :: back up before changing
(echo new=value) > "C:\app\config.ini" || goto :rollback
sc stop MyService && sc start MyService || goto :rollback
echo deploy OK
exit /b 0

:rollback
echo ROLLING BACK 1>&2
copy "%BACKUP%\config.ini" "C:\app\config.ini" >nul
sc start MyService 2>nul
exit /b 1
```

Back up before mutating, attempt the change, and on failure restore from the backup
— idempotency and rollback are what make a deploy script safe to re-run.

---

## 8. Key takeaways

1. **`if errorlevel N` means ≥ N** (so `if errorlevel 0` is always true); use
   `%errorlevel% equ N` for exact, and **`!errorlevel!`** inside blocks (parse-time
   staleness).
2. **`&&`/`||`** chain on the previous exit code — the cleanest "do next only if this
   worked / handle failure."
3. **Always `exit /b N`** (never bare `exit`); end scripts with an explicit code.
4. Batch has **no `set -e`** — you must check after every critical step; build the
   discipline in.
5. Send errors to **stderr** (`1>&2`); for reliable timestamps shell to PowerShell,
   not `%DATE%`/`%TIME%`.
6. Emulate **`finally`** with a single `:cleanup` exit point and an `rc` variable; do
   **backup + rollback** for transactional changes.

> Next: [07 — Advanced & Enterprise](07_advanced_enterprise.md) — `robocopy`,
> `schtasks`, `sc`, `reg`, `wmic`, and real deployment scripts.
