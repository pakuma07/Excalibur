# 05 — Subroutines & I/O

> **Audience:** zero to staff. Batch has no functions — it has **labels you `call`**
> and parameters via `%1..%9`. This chapter covers subroutines, the powerful `%~`
> argument modifiers, the trick to **return a value past `endlocal`**, and the I/O
> model: redirection, pipes, and reading files.

---

## 1. Subroutines via `call :label`

```bat
@echo off
setlocal
call :greet World
call :add 2 3
echo (back in main)
exit /b 0

:greet
echo Hello, %~1!            :: %1 is the first argument; %~1 strips surrounding quotes
goto :eof                   :: return to the caller

:add
set /a sum=%~1 + %~2
echo %~1 + %~2 = %sum%
goto :eof
```

- **`call :label arg1 arg2`** runs the code at `:label` as a subroutine; **`goto :eof`**
  returns. Arguments arrive as `%1`, `%2`, … (and `%0` is the label).
- **`call`** is also how you run *another batch file* and come back (`call other.cmd`);
  without `call`, control transfers and never returns (chapter 01).
- The whole script's arguments are also `%1..%9`; **`shift`** shifts them left (so you
  can process more than 9 / iterate). `%*` is all arguments as one string.

---

## 2. The `%~` argument modifiers (extremely useful)

Applied to `%0..%9` (and `for` variables `%%i`), these decompose a path argument:

```bat
:: Suppose %1 = "C:\proj\src\app.exe"
echo %~1        :: C:\proj\src\app.exe   (quotes removed)
echo %~f1       :: C:\proj\src\app.exe   (full/absolute path)
echo %~d1       :: C:                    (drive)
echo %~p1       :: \proj\src\            (path, no drive)
echo %~n1       :: app                   (name, no extension)
echo %~x1       :: .exe                  (extension)
echo %~dp1      :: C:\proj\src\          (drive+path — combine modifiers)
echo %~nx1      :: app.exe               (name+ext)
echo %~s1       :: short 8.3 path
echo %~a1       :: file attributes
echo %~t1       :: timestamp
echo %~z1       :: size in bytes
echo %~$PATH:1  :: search PATH for the file named in %1, return full path
```

- **`%~dp0`** is the single most important one: drive+path of `%0` = the directory of
  the *running script*. Use it to locate sibling files regardless of the current
  directory: `call "%~dp0lib.cmd"`.
- Modifiers combine in a fixed order (`%~dpnx1`). They also work on `for` variables:
  `for %%f in (*.log) do echo %%~nf` (basename without extension).

---

## 3. Returning values (the `endlocal` problem)

`endlocal` discards everything `set` since `setlocal` (chapter 02) — so a subroutine
that uses `setlocal` can't just leave a variable for the caller. The idiom is the
**`endlocal & set` one-liner**, which evaluates the value *before* the scope is torn
down:

```bat
:get_count
setlocal enabledelayedexpansion
set /a n=0
for %%f in (*.txt) do set /a n+=1
:: hand the value back to the caller's scope: ' & ' runs after endlocal, and
:: %n% here was expanded at PARSE time (before endlocal) -> survives.
endlocal & set "%~1=%n%"
goto :eof

:: caller:
call :get_count RESULT
echo there are %RESULT% txt files
```

The pattern is `endlocal & set "%~1=%localvalue%"` — by convention the subroutine
takes the *name* of an output variable as an argument (`%~1`) and assigns into it.
This is batch's equivalent of an out-parameter / return value.

---

## 4. Redirection — stdout, stderr, files

```bat
command > out.txt            :: redirect stdout to a file (overwrite)
command >> out.txt           :: append stdout
command 2> err.txt           :: redirect stderr (handle 2)
command > out.txt 2>&1        :: stdout to file, stderr to the SAME place (order matters)
command > nul                 :: discard stdout (the 'null device')
command 2>nul                 :: discard stderr only
echo message 1>&2             :: write to stderr (for errors/diagnostics)
command < input.txt           :: feed a file as stdin
```

- **Handles:** `1` = stdout, `2` = stderr. `2>&1` means "send handle 2 to wherever
  handle 1 currently points" — so **`> file 2>&1`** (in that order) captures both;
  `2>&1 > file` does *not* (it binds 2 to the console first). Order matters.
- **`>nul`** discards output (Windows' `/dev/null` is `nul`). `1>&2` emits to stderr,
  which CI and log capture treat separately from stdout — use it for diagnostics.
- A redirect with a **`%var%` containing trailing spaces** can create oddly-named
  files; quote and trim.

---

## 5. Pipes

```bat
tasklist | findstr /i chrome           :: pipe stdout into another command's stdin
dir /b | sort | more                    :: chain filters
type big.log | findstr "ERROR" > errors.txt
```

- Each side of a `|` runs in its **own `cmd.exe` child** — so variables set in a
  piped command **do not survive** to the parent (a classic surprise). Capture with
  `for /f` instead if you need the value:
  `for /f %%c in ('dir /b ^| find /c /v ""') do set "lines=%%c"` (the `^|` escapes the
  pipe inside the `for` command).

---

## 6. Reading files

```bat
:: First line only:
set /p first=<config.txt

:: Every line (for /f, chapter 03) — note: skips blank lines by default:
for /f "usebackq delims=" %%L in ("data.txt") do echo LINE: %%L

:: Line-by-line INCLUDING blanks (number the lines via findstr, then strip):
for /f "usebackq delims=" %%L in (`type "data.txt" ^| findstr /n "^"`) do (
    set "line=%%L"
    setlocal enabledelayedexpansion
    echo !line:*:=!            :: strip the 'N:' prefix findstr added
    endlocal
)
```

- `for /f` is the file reader, but it **skips empty lines** and treats `;` (the
  default `eol`) as comments. The `findstr /n "^"` trick numbers every line (forcing
  blanks to be non-empty) and you strip the prefix — the standard "read all lines
  faithfully" workaround.
- For anything structured (CSV with quoted fields, JSON), this is the point where you
  switch to PowerShell (chapter 09).

---

## 7. Here-doc emulation (multi-line output to a file)

Batch has no here-doc; you emit lines or use parentheses block redirection:

```bat
(
echo line 1
echo line 2
echo line 3
) > output.txt               :: redirect a whole block at once

:: Or append a config:
>>app.conf echo key=value
```

The parenthesized-block redirect `( ... ) > file` is the closest thing to a here-doc
and avoids reopening the file per line.

---

## 8. A worked example — a small library pattern

```bat
@echo off
setlocal enabledelayedexpansion

call :log INFO "starting up"
call :require_file "%~dp0config.ini" || exit /b 1
call :log INFO "all good"
exit /b 0

:log <level> <message>
echo [%~1] %DATE% %TIME% %~2
goto :eof

:require_file <path>
if not exist "%~1" (
    call :log ERROR "missing file: %~1" 1>&2
    exit /b 1
)
goto :eof
```

`call :require_file ... || exit /b 1` uses the subroutine's exit code with `||`
(chapter 06) — a clean, composable pattern. `%~dp0config.ini` finds the config next
to the script.

---

## 9. Key takeaways

1. Subroutines are **`call :label args`** returning via **`goto :eof`**; arguments are
   `%1..%9`, `%*` (all), `shift` to advance.
2. The **`%~` modifiers** decompose path args — **`%~dp0`** (script's own dir) and
   `%~nx1`, `%~f1` are essential; they work on `for` variables too.
3. Return a value past `setlocal` with the **`endlocal & set "%~1=%val%"`** idiom
   (out-parameter by name).
4. Redirection: `1`=stdout `2`=stderr; **`> file 2>&1`** (order matters) captures
   both; `>nul` discards; `1>&2` writes diagnostics to stderr.
5. **Piped commands run in child `cmd`s** — their variables don't survive; capture via
   `for /f`.
6. `for /f` reads files but **skips blanks**; use the `findstr /n "^"` trick to read
   every line; structured data → PowerShell.

> Next: [06 — Error Handling](06_error_handling.md) — `errorlevel`, `&&`/`||`,
> `exit /b`, and robust, transactional scripts.
