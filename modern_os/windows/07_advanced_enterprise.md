# 07 — Advanced & Enterprise

> **Audience:** intermediate to staff. This is the toolkit batch is *actually used
> for* in Windows enterprises: bulk file sync (`robocopy`), scheduled jobs
> (`schtasks`), services (`sc`), the registry (`reg`), system queries (`wmic`/
> `where`/`tasklist`), networking, and the bootstrap/deploy scripts that wrap them.
> These are the commands a staff Windows engineer reaches for daily.

---

## 1. robocopy — the industrial file copier

`robocopy` (Robust File Copy) is the right tool for any non-trivial copy/sync/mirror
— resumable, multithreaded, attribute-aware. It replaces `copy`/`xcopy` for real
work.

```bat
robocopy "C:\src" "D:\dst" /E              :: copy incl. empty subdirs
robocopy "C:\src" "D:\dst" /MIR           :: MIRROR (delete extras in dst — careful!)
robocopy "C:\src" "D:\dst" *.log /S /R:2 /W:5   :: pattern, recurse, 2 retries, 5s wait
robocopy "\\server\share" "C:\local" /MT:16 /Z   :: 16 threads, restartable mode
robocopy "C:\src" "D:\dst" /MIR /XD node_modules .git /XF *.tmp   :: exclude dirs/files
robocopy "C:\src" "D:\dst" /MIR /LOG:sync.log /NP /TEE   :: log to file + console
```

> **The exit-code trap (critical):** robocopy uses **bit-coded exit codes 0–7 for
> SUCCESS** (0 = nothing to do, 1 = files copied, 2 = extras, 3 = both, etc.) and
> **≥ 8 for failure**. Naively treating any non-zero as failure breaks every CI step
> that uses it. Check correctly:

```bat
robocopy "%SRC%" "%DST%" /MIR
if %errorlevel% geq 8 ( echo robocopy FAILED & exit /b 1 )
exit /b 0
```

---

## 2. schtasks — scheduled tasks

The batch-era way to schedule recurring jobs (the Windows equivalent of cron;
launchd on Mac, systemd timers on Linux).

```bat
:: Create a daily 2am task running as SYSTEM:
schtasks /create /tn "Nightly Backup" /tr "C:\scripts\backup.cmd" ^
    /sc daily /st 02:00 /ru SYSTEM /rl HIGHEST /f

:: Other schedules: /sc minute /mo 15  |  /sc weekly /d MON,WED  |  /sc onstart  |  /sc onlogon
schtasks /run    /tn "Nightly Backup"     :: run now
schtasks /query  /tn "Nightly Backup" /v /fo LIST   :: inspect
schtasks /change /tn "Nightly Backup" /disable
schtasks /delete /tn "Nightly Backup" /f
```

- **`/ru SYSTEM`** runs without a logged-in user (services-style); `/rl HIGHEST` runs
  elevated. Store the script path absolutely (`%~dp0` won't help inside the task — it
  runs detached).
- Tasks run in a **non-interactive session** with a different environment and current
  directory (often `C:\Windows\System32`) — always use absolute paths and don't
  assume a `PATH` or mapped drives. This causes most "works when I run it, fails on
  schedule" incidents.

---

## 3. sc — services

```bat
sc query MyService                    :: state (RUNNING/STOPPED)
sc start MyService                    :: start ; sc stop MyService
sc config MyService start= auto       :: NOTE the space after 'start=' (required!)
sc create MyService binPath= "C:\app\svc.exe" start= auto obj= LocalSystem
sc failure MyService reset= 86400 actions= restart/5000/restart/5000/restart/5000
sc delete MyService
```

- **The `key= value` syntax requires a space *after* the `=`** and none before
  (`start= auto`, not `start=auto`) — a notorious `sc` quirk.
- `sc failure` configures auto-restart on crash (the recovery tab) — important for
  resilient services. For querying/controlling, modern fleets often prefer PowerShell
  (`Get-Service`/`Restart-Service`) which returns objects.

---

## 4. reg — the registry

```bat
:: Read a value:
reg query "HKLM\SOFTWARE\MyApp" /v Version
:: Capture it into a variable:
for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\MyApp" /v Version ^| findstr Version') do set "VER=%%b"

:: Write / create:
reg add "HKLM\SOFTWARE\MyApp" /v Version /t REG_SZ /d "1.2.3" /f
reg add "HKLM\SOFTWARE\MyApp" /v Port /t REG_DWORD /d 8080 /f
reg delete "HKLM\SOFTWARE\MyApp" /v OldKey /f
reg export "HKLM\SOFTWARE\MyApp" backup.reg       :: backup a subtree
```

- `/f` forces (no prompt) — needed in non-interactive scripts. `/t` sets the type
  (`REG_SZ`, `REG_DWORD`, `REG_EXPAND_SZ`, `REG_MULTI_SZ`).
- **HKLM writes need elevation** (Administrator). Registry edits are a top cause of
  "it worked locally" failures — back up with `reg export` before changing (the
  rollback pattern, chapter 06).

---

## 5. System queries — wmic, where, tasklist, systeminfo

```bat
where python                          :: locate an executable on PATH (like `which`)
tasklist | findstr /i node            :: running processes
taskkill /im chrome.exe /f            :: kill by image name (/f force)
taskkill /pid 1234 /t /f              :: kill a tree (/t)

systeminfo | findstr /b /c:"OS Name" /c:"OS Version"

:: WMIC (deprecated but still common — query almost anything):
wmic os get Caption,Version,LastBootUpTime /value
wmic cpu get NumberOfCores,NumberOfLogicalProcessors /value
wmic logicaldisk get DeviceID,FreeSpace,Size /value
wmic process where "name='java.exe'" get ProcessId,WorkingSetSize /value
for /f "tokens=2 delims==" %%t in ('wmic os get localdatetime /value ^| find "="') do set "DT=%%t"
:: %DT% is YYYYMMDDhhmmss... — a LOCALE-INDEPENDENT timestamp (chapter 02)
```

- **`wmic` is deprecated** (removed from default installs on recent Windows) — its
  replacement is PowerShell `Get-CimInstance`. But it's still the batch-era way to get
  a parseable, locale-independent timestamp and hardware/process info. Know both.
- **`where`** is `which`; **`tasklist`/`taskkill`** manage processes; **`systeminfo`**
  dumps host facts.

---

## 6. Networking

```bat
ipconfig /all                         :: interfaces, IPs, DNS, MAC
ipconfig /flushdns                    :: clear the DNS cache
ping -n 4 host                        :: -n count (not -c like Unix!)
nslookup example.com 1.1.1.1          :: DNS query against a specific resolver
netstat -ano | findstr :443           :: connections + owning PID on a port
netstat -ano | findstr LISTENING
curl -fsSL https://example.com/health :: curl ships with Windows 10+ (use it)
net use Z: \\server\share /user:DOMAIN\me   :: map a network drive
net session                            :: who's connected
```

- **`ping -n`** for count (Windows), not `-c` (Unix) — a frequent cross-platform slip.
- **`curl` is built in** since Windows 10 1803 — prefer it (and its exit codes,
  `-f` fails on HTTP errors) over legacy download hacks.
- `netstat -ano` + the PID, then `tasklist | findstr <pid>`, is the "what's holding
  this port?" combo.

---

## 7. A worked example — a service deploy/bootstrap script

```bat
@echo off
setlocal enabledelayedexpansion
:: --- must run elevated ---
net session >nul 2>&1 || ( echo Run as Administrator. 1>&2 & exit /b 1 )

set "SVC=MyApp"
set "SRC=%~dp0dist"
set "DST=C:\Program Files\MyApp"
set "BK=%DST%.bak"

call :log "stopping %SVC%"
sc query "%SVC%" >nul 2>&1 && sc stop "%SVC%" >nul

call :log "backing up current install"
if exist "%DST%" robocopy "%DST%" "%BK%" /MIR /NJH /NJS >nul

call :log "deploying new build"
robocopy "%SRC%" "%DST%" /MIR /R:2 /W:3 /NJH /NJS >nul
if !errorlevel! geq 8 ( call :log "deploy FAILED, rolling back" & goto :rollback )

call :log "starting %SVC%"
sc start "%SVC%" || ( call :log "start FAILED, rolling back" & goto :rollback )

:: health check with retries
set /a tries=0
:health
curl -fsS http://localhost:8080/health >nul 2>&1 && goto :ok
set /a tries+=1
if !tries! lss 10 ( timeout /t 2 >nul & goto :health )
call :log "health check FAILED, rolling back" & goto :rollback

:ok
call :log "deploy OK"
exit /b 0

:rollback
sc stop "%SVC%" >nul 2>&1
if exist "%BK%" robocopy "%BK%" "%DST%" /MIR /NJH /NJS >nul
sc start "%SVC%" >nul 2>&1
exit /b 1

:log
echo [%~1]
goto :eof
```

This is a representative staff-level batch script: elevation check, stop → backup →
deploy (robocopy with correct exit-code handling) → start → **health check with
retries** → **rollback on any failure**. It's idempotent (re-runnable) and
transactional.

---

## 8. Key takeaways

1. **`robocopy`** is the real copier — and its **exit codes 0–7 are success, ≥ 8 is
   failure** (the most important enterprise-batch gotcha).
2. **`schtasks`** schedules jobs; scheduled tasks run **non-interactively** with a
   different env/CWD — use absolute paths and `/ru SYSTEM`.
3. **`sc`** manages services (mind the **`key= value`** space); configure
   auto-restart with `sc failure`.
4. **`reg`** reads/writes the registry (`/f`, `/t`); HKLM needs elevation — `reg
   export` to back up before changing.
5. **`where`/`tasklist`/`taskkill`/`wmic`/`systeminfo`** query the system; `wmic` is
   deprecated (→ PowerShell CIM) but still the batch way to a locale-independent
   timestamp.
6. Networking: **`ping -n`** (not `-c`), built-in **`curl`**, `netstat -ano` + PID.
7. A production deploy script = **elevation check → stop → backup → deploy → start →
   health check → rollback on failure**.

> Next: [08 — Gotchas & Quirks Catalog](08_gotchas_quirks.md) — the definitive trap
> list before you ship.
