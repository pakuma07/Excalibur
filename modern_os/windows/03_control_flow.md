# 03 — Control Flow

> **Audience:** zero to staff. Batch control flow is `if`, `for`, `goto`, and
> `call`. There's no `while` and no `switch` — you build them. The star of the show
> is **`for`**, which has five modes and is how batch does iteration, file reading,
> and command-output capture. Master `for /f` and you've mastered most real batch.

---

## 1. if / else

```bat
if "%x%"=="yes" echo matched                 :: string equality (quote both sides!)
if /i "%x%"=="YES" echo case-insensitive     :: /i = ignore case
if not "%x%"=="" echo x is non-empty

if "%x%"=="yes" (
    echo block form
) else (
    echo the ) and else MUST be placed like this — cmd is picky
)

if exist "C:\file.txt" echo file is there    :: file/dir existence
if defined VARNAME echo the variable is set  :: tests definition (no % needed)
```

Numeric and comparison operators (for `errorlevel` / `set /a` results):

```bat
if %n% lss 10 echo less than           :: lss leq equ neq geq gtr
if %n% geq 1 if %n% leq 100 echo in range 1..100    :: chain for AND
```

- **Quote both sides** of `==` so an empty/spaced value doesn't cause a syntax error
  (`if "%x%"=="y"` is safe; `if %x%==y` breaks when `x` is empty or has spaces).
- The block-form punctuation is unforgiving: the `)` of the `if` and the `else` must
  be on the same line (`) else (`). A newline before `else` is a syntax error.
- **There is no `&&`/`||` inside `if` the way Unix has** — you chain `if`s for AND
  and use `goto` for complex logic. (`&&`/`||` between *commands* do exist — ch 06.)

---

## 2. errorlevel comparisons (a trap)

```bat
if errorlevel 1 echo failed       :: TRUE when errorlevel is 1 OR MORE (>=), not ==1!
if %errorlevel% equ 1 echo exactly one
if %errorlevel% neq 0 echo any failure
```

`if errorlevel N` means **"≥ N"**, by historical design. To test an exact code use
`%errorlevel% equ N`. And `%errorlevel%` can be **stale inside a block** (parse-time
expansion, ch 02) — use `!errorlevel!` with delayed expansion, or restructure. Full
treatment in chapter 06.

---

## 3. for — the workhorse, in five modes

### 3.1 Plain set iteration

```bat
for %%i in (a b c *.txt) do echo %%i     :: iterate literals and GLOB matches
```

- In a **file** use **`%%i`** (doubled); at the **command line** use `%i` (single).
  This is the #1 "works at the prompt, fails in the script" surprise.
- The loop variable is one letter, case-sensitive (`%%i` ≠ `%%I`), and consumes
  letters for tokens (§3.5).

### 3.2 `/l` — numeric ranges (the closest thing to a counted loop)

```bat
for /l %%n in (1,1,10) do echo %%n        :: start, step, end -> 1..10
for /l %%n in (10,-2,0) do echo %%n       :: 10 8 6 4 2 0 (negative step)
```

### 3.3 `/d` — directories only

```bat
for /d %%g in (C:\proj\*) do echo dir: %%g    :: only directories matching the glob
```

### 3.4 `/r` — recursive walk

```bat
for /r "C:\logs" %%f in (*.log) do echo %%f   :: every *.log under the tree
for /r %%f in (*.tmp) do del "%%f"             :: recursive delete by pattern
```

### 3.5 `/f` — parse files, strings, and command output (the powerful one)

This is how batch reads files line-by-line and captures command output.

```bat
:: Read a file line by line:
for /f "usebackq delims=" %%L in ("config.txt") do echo LINE: %%L

:: Capture command OUTPUT (note the backquotes / 'usebackq'):
for /f "usebackq tokens=*" %%v in (`hostname`) do set "HOST=%%v"

:: Parse columns: split on ',' take fields 1 and 3 into %%a and %%c
for /f "tokens=1,3 delims=," %%a in ("alice,30,nyc") do echo name=%%a city=%%c

:: Skip a header and parse whitespace-delimited output:
for /f "skip=1 tokens=1,2" %%a in ('tasklist') do echo %%a %%b
```

The `for /f` options (in the quoted string):

| Option | Meaning |
|---|---|
| `delims=xyz` | characters to split on (default: space+tab). `delims=` (empty) = whole line |
| `tokens=2,4*` | which split fields to assign; `*` = "rest of line" into the next var |
| `skip=N` | skip the first N lines (e.g. a header) |
| `eol=;` | lines starting with this char are treated as comments (default `;`) |
| `usebackq` | use **`** `` ` `` ** for commands and `"..."` for filenames** (needed when paths have spaces) |

- **`tokens=1,3`** assigns field 1 to `%%a` and field 3 to the *next letter* `%%b`
  (it advances through the alphabet, not by your numbers) — a constant source of
  confusion. `tokens=2*` puts field 2 in `%%a` and the remainder in `%%b`.
- **`usebackq`** lets you quote a filename with spaces (`"C:\my file.txt"`) and run a
  command with backquotes `` `cmd` `` — use it whenever paths or commands are
  involved.
- Reading a file with `for /f` **skips blank lines** by default and stops at a
  Ctrl-Z. For exact line-by-line including blanks, prefix a counter trick or use
  PowerShell.

---

## 4. goto and labels

```bat
@echo off
goto :main                  :: jump to a label (the ':' is optional in goto)

:helper
echo in helper
goto :eof                   :: :eof is a BUILT-IN label = end of file (return/exit)

:main
echo in main
call :helper                :: see chapter 05 for call :label subroutines
goto :end

:end
echo done
```

- **Labels** are lines beginning with `:`. **`goto :eof`** is a magic built-in label
  meaning "end of file" — the standard way to return from a subroutine or end a
  script (don't define your own `:eof`).
- `goto` is how you build loops and complex branching that `for`/`if` can't express.

---

## 5. Emulating while / until / switch

```bat
:: WHILE loop via goto:
set /a i=0
:loop
if %i% geq 5 goto :after
echo iteration %i%
set /a i+=1
goto :loop
:after

:: SWITCH via goto + labels (or chained if):
goto :case_%action%        :: computed goto — jump based on a variable's value
:case_start
echo starting & goto :endsw
:case_stop
echo stopping & goto :endsw
:endsw
```

- **Computed goto** (`goto :case_%action%`) is the idiomatic batch "switch" — jump to
  a label named after the variable. Add a default by testing `defined`/existence
  first, or a `:case_` fallthrough.
- The `&` chains commands on one line (`cmd1 & cmd2`) — useful for compact branches.

---

## 6. A worked example — process a CSV

```bat
@echo off
setlocal enabledelayedexpansion

:: data.csv:  name,age,city  (with a header line to skip)
set /a n=0
for /f "usebackq skip=1 tokens=1-3 delims=," %%a in ("data.csv") do (
    set /a n+=1
    echo [!n!] name=%%a age=%%b city=%%c       :: !n! needs delayed expansion (ch 02)
)
echo Processed !n! rows.
endlocal
```

This combines: `usebackq` (quoted filename), `skip=1` (header), `tokens=1-3` (a
range), `delims=,` (CSV), and `!n!` delayed expansion for the running counter — a
representative real batch task.

---

## 7. Key takeaways

1. **`if`**: quote both sides of `==`, use `/i` for case-insensitive, `if defined`
   to test a variable, `if exist` for files; block punctuation (`) else (`) is
   strict.
2. **`if errorlevel N` means ≥ N** — use `%errorlevel% equ N` for an exact match.
3. **`for`** has five modes: plain/glob, **`/l`** (counted ranges), **`/d`** (dirs),
   **`/r`** (recursive), and **`/f`** (parse files/strings/**command output**).
4. In a script use **`%%i`** (doubled); `for /f` options are
   `delims`/`tokens`/`skip`/`eol`/`usebackq`, and `tokens` advances through letters,
   not your numbers.
5. **`goto :eof`** is the built-in "return/end"; build `while`/`switch` from `goto`
   and **computed goto** (`goto :case_%x%`).

> Next: [04 — Strings & Substitution](04_strings.md) — substrings, search-and-replace,
> and the quoting rules that bite.
