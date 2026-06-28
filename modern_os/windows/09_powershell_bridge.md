# 09 — PowerShell Bridge

> **Audience:** anyone who writes batch today and operates a Windows fleet
> tomorrow. Batch is for *reaching* PowerShell, not replacing it. This chapter is
> the migration map: **when** to leave batch, **how** the two interoperate (each
> calling the other, passing data, exit codes), and a translation table so you can
> rewrite a script you already understand.

The honest summary: **write new automation in PowerShell.** Keep batch for tiny
wrappers, bootstrap-before-PowerShell (PXE/WinPE, MSI custom actions, login
scripts), and editing the legacy that already exists. A staff Windows engineer
*reads* batch fluently and *writes* PowerShell by default.

---

## 1. When to stop using batch

| Signal | Why batch hurts | Reach for |
|---|---|---|
| Structured data (JSON/CSV/XML/registry as objects) | batch only has strings; parsing is `for /f` torture | PowerShell objects, `ConvertFrom-Json` |
| Real error handling | `errorlevel` lies (ch 06); no exceptions | `try/catch`, `$ErrorActionPreference` |
| Anything > ~50 lines | expansion/quoting traps compound | PowerShell |
| Remoting / managing many hosts | none built in | `Invoke-Command`, PS Remoting / WinRM |
| Math beyond integers, dates, sorting | `set /a` is integer-only; `%DATE%` is locale junk | `[math]`, `Get-Date`, `Sort-Object` |
| Calling REST APIs | not feasible | `Invoke-RestMethod` |

**When batch still wins:** it runs *before* a PowerShell profile/policy is
available, it's universally present (no execution-policy gate), and it's the right
size for a 5-line wrapper. That's the whole list.

---

## 2. The execution-policy gate (the #1 "PowerShell won't run" issue)

```powershell
Get-ExecutionPolicy -List          # see policy per scope
```

PowerShell refuses to run *script files* under a restrictive policy — but **this is
not a security boundary**, it's a guardrail. From batch you bypass it for one
invocation without changing machine state:

```bat
:: run a .ps1 from a .bat — the canonical launcher line
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
```

- `-NoProfile` — skip the user profile (faster, deterministic; profiles inject env).
- `-ExecutionPolicy Bypass` — for *this process only*; doesn't persist.
- `-File "%~dp0..."` — `%~dp0` is this .bat's folder, so the path is absolute (ch 07/08).
- `%*` — forward all batch args through to the script.
- Use **`pwsh.exe`** for PowerShell 7+ (cross-platform, the modern default);
  **`powershell.exe`** is Windows PowerShell 5.1 (in-box, frozen, still everywhere).

---

## 3. Batch → PowerShell: calling PS and getting data back

```bat
@echo off
:: 3a. run inline PS and capture one line of output into a batch var
for /f "delims=" %%v in ('powershell -NoProfile -Command "(Get-Date).ToString('s')"') do set "NOW=%%v"
echo Started at %NOW%

:: 3b. check the script's exit code (PS 'exit N' -> batch errorlevel)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check.ps1"
if errorlevel 1 ( echo check failed & exit /b 1 )
```

Quoting across the boundary is the trap: batch parses first, then PowerShell.
Doubled-up quotes are common. Safer for anything non-trivial: **put the logic in a
`.ps1`** and pass arguments, rather than cramming it into `-Command "..."`.

```bat
:: pass args positionally / named; PS receives them as $args or param()
powershell -NoProfile -File "%~dp0rotate.ps1" -Path "C:\logs" -KeepDays 7
```

---

## 4. PowerShell → Batch: calling cmd tools and legacy .bat

```powershell
# 4a. run a legacy .bat and capture output + exit code
$out  = & cmd.exe /c "C:\legacy\build.bat" 2>&1
$code = $LASTEXITCODE                       # native-exe exit code lands HERE, not $?
if ($code -ne 0) { throw "build.bat failed ($code): $out" }

# 4b. one-off cmd builtin (e.g. an env-expansion only cmd does)
cmd /c "echo %PROCESSOR_ARCHITECTURE%"
```

- **`$LASTEXITCODE`** holds the last *native* process exit code; **`$?`** is just a
  boolean "did the last thing succeed." Check `$LASTEXITCODE` for external tools.
- `&` is the **call operator** — needed to run a command stored in a variable or a
  quoted path: `& $exe @args`.
- `2>&1` merges stderr into the stream so you capture both.

---

## 5. The translation table (idioms side by side)

| Task | Batch (`cmd`) | PowerShell |
|---|---|---|
| Print | `echo hi` | `Write-Output hi` / `"hi"` |
| Set var | `set "x=1"` | `$x = 1` |
| Read var | `%x%` / `!x!` | `$x` (no expansion phases!) |
| Comment | `REM` / `::` | `#` |
| If | `if "%x%"=="1" (...)` | `if ($x -eq 1) {...}` |
| Compare | `equ gtr lss` | `-eq -gt -lt -ne -ge -le` |
| For each file | `for %%f in (*.log) do ...` | `foreach ($f in Get-ChildItem *.log) {...}` |
| Count to N | `for /l %%i in (1,1,10)` | `1..10 \| % { ... }` |
| Read file lines | `for /f "delims=" %%l in (f.txt)` | `Get-Content f.txt` |
| Function | `call :sub` + `:sub`/`goto :eof` | `function Sub { ... }` |
| Exit script | `exit /b 1` | `exit 1` |
| Exists? | `if exist "f"` | `Test-Path f` |
| Env-independent date | `wmic os get localdatetime` | `Get-Date -Format s` |
| Substring | `%v:~0,3%` | `$v.Substring(0,3)` |
| Replace | `%v:a=b%` | `$v -replace 'a','b'` |
| Pipe to var (hard in batch) | temp file / `for /f` | `$x = cmd \| ...` (objects flow natively) |

The conceptual leap: **batch pipes text; PowerShell pipes objects.** No more
parsing `dir` output — `Get-ChildItem` hands you `.Length`, `.LastWriteTime` as
typed properties. That single difference is why PowerShell wins for data work.

---

## 6. A real migration — log rotation, both ways

**Batch (the legacy you'll find):**

```bat
@echo off
setlocal enabledelayedexpansion
set "DIR=C:\logs"
for %%f in ("%DIR%\*.log") do (
    forfiles /p "%DIR%" /m "%%~nxf" /d -7 >nul 2>&1 && del "%%f"
)
```

**PowerShell (what you write instead):**

```powershell
Get-ChildItem 'C:\logs\*.log' |
    Where-Object LastWriteTime -lt (Get-Date).AddDays(-7) |
    Remove-Item -WhatIf      # -WhatIf: dry-run; drop it to actually delete
```

Shorter, locale-safe dates, built-in dry-run (`-WhatIf`), real errors. This is the
argument for migrating in one example.

---

## 7. Interop gotchas

- **Quoting is doubled across the boundary** — batch strips one layer, PowerShell
  another. Prefer `-File script.ps1` over `-Command "long string"` to avoid quoting hell.
- **Exit codes:** PowerShell `exit N` → batch `errorlevel N`. But an *uncaught
  exception* may exit `1` regardless — set `$ErrorActionPreference='Stop'` and use
  `try/catch { exit 2 }` for explicit codes.
- **`$LASTEXITCODE` vs `$?`** — see §4; the classic "my error check never fires" bug.
- **5.1 vs 7+:** `powershell.exe` (Windows PowerShell 5.1, in-box, default `Out-File`
  encoding is UTF-16!) vs `pwsh.exe` (PS 7+, UTF-8 default, cross-platform). Don't
  assume which is installed; check, or ship `pwsh`.
- **Encoding:** 5.1 writes UTF-16 with a BOM by default — pipe-to-file then read in
  another tool and you get mojibake. Use `-Encoding utf8` explicitly.
- **Execution policy** blocks *files*, not `-Command`; `Bypass` is per-process and
  not a security control (§2).

---

## 8. The decision rule

> **New automation → PowerShell (`pwsh` if you can choose).** Batch only for: a
> wrapper that launches PowerShell (§2), code that must run before PowerShell is
> available (WinPE/PXE, MSI custom actions, early login scripts), or a one-screen
> glue script. Everything structured, networked, or error-sensitive is PowerShell's
> job — and beyond ~a few hundred lines or any real data model, leave the shell
> entirely for **Python** ([`../../python_book/`](../../python_book/README.md)) or
> **C#** ([`../../csharp_book/`](../../csharp_book/README.md)).

You've now seen the batch language end to end (ch 01–08) and the bridge off it.
The skill that lasts isn't batch trivia — it's knowing the four traps
([README](README.md)), reading legacy without fear, and writing the modern tool.

> Related: [`../linux/`](../linux/README.md) for the Bash counterpart and the same
> "when to leave the shell for Python" line, [`../../os_net/`](../../os_net/README.md)
> for the OS internals these scripts drive.
