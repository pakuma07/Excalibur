# 04 — Strings & Substitution

> **Audience:** zero to staff. Batch has no string library — but it has a compact set
> of **variable-expansion modifiers** that do substrings and search-and-replace
> inline, plus `findstr` for regex. This chapter covers string surgery and the
> quoting/special-character rules that cause most "weird character" bugs.

---

## 1. Substrings — `%var:~start,length%`

```bat
set "s=Hello, World"
echo %s:~0,5%       :: Hello        (from index 0, length 5)
echo %s:~7%         :: World        (from index 7 to the end)
echo %s:~-5%        :: World        (last 5 characters — negative start)
echo %s:~0,-1%      :: Hello, Worl  (all but the last char — negative length)
echo %s:~3,-2%      :: lo, Wor      (from index 3, stop 2 from the end)
```

Indexing is 0-based; negative start counts from the end; negative length means "stop
N from the end." This single feature replaces most `cut`/`substr` needs.

---

## 2. Search and replace — `%var:find=replace%`

```bat
set "path=C:\a\b\c"
echo %path:\=/%           :: C:/a/b/c     (replace ALL backslashes with forward)
echo %path:c=X%           :: C:\a\b\X     (case-INSENSITIVE match, replaces 'c')
set "s=one two two three"
echo %s:two=2%            :: one 2 2 three (replaces every occurrence)
echo %s: =_%              :: one_two_two_three  (replace spaces)
set "s=%s:two=%"          :: DELETE: replace 'two' with nothing
```

- Replacement is **global** (all matches) and **case-insensitive** on the search.
- There is no regex here — for patterns use `findstr` (§5). For "replace first only"
  or case-sensitive replace, you need `findstr`/PowerShell.

Special form — **replace a leading substring** with `:*`:

```bat
set "url=https://example.com/path"
echo %url:*//=%          :: example.com/path  (delete everything up to and incl '//')
```

`%var:*X=Y%` replaces everything from the start *through the first X* with Y — handy
for stripping prefixes.

---

## 3. Building strings & appending

```bat
set "out="
for %%w in (alpha beta gamma) do set "out=!out! %%w"   :: needs delayed expansion (ch 02)
echo %out%               :: ' alpha beta gamma'  (note leading space — trim if needed)

set "csv=a"
set "csv=%csv%,b"        :: a,b
set "csv=%csv%,c"        :: a,b,c
```

String concatenation is just re-assignment. Inside a loop, append with `!var!`
(delayed expansion) or the value won't update (chapter 02).

---

## 4. Length, case, and trimming

Batch has **no built-in length, no upper/lower, no trim.** The idioms:

```bat
:: String length via a subroutine (loop, or binary-search for speed):
call :strlen result "Hello, World"
echo length=%result%

:: Trim surrounding quotes from a value:  %~1 (ch 05) or:
set "q="quoted value""
set "unq=%q:"=%"          :: crude: removes ALL quotes

:: Upper/lower: there is no built-in. Either a per-letter replace table, or:
for /f "usebackq delims=" %%u in (`powershell -nop -c "'%s%'.ToUpper()"`) do set "U=%%u"
goto :eof

:strlen <resultVar> <string>
setlocal enabledelayedexpansion
set "str=%~2" & set "n=0"
:strlen_loop
if defined str ( set "str=!str:~1!" & set /a n+=1 & goto :strlen_loop )
endlocal & set "%~1=%n%"
goto :eof
```

The fact that **case conversion requires shelling out to PowerShell** (or a 26-line
replace table) is a representative example of why non-trivial string work belongs in
PowerShell (chapter 09).

---

## 5. findstr — search and (limited) regex

```bat
findstr "ERROR" app.log                 :: lines containing ERROR (literal)
findstr /i /c:"out of memory" app.log   :: /i case-insensitive, /c: literal phrase
findstr /r "^ERROR.*timeout$" app.log   :: /r = regex (limited POSIX-ish dialect)
findstr /v "DEBUG" app.log              :: /v = invert (lines NOT matching)
findstr /n "WARN" app.log               :: /n = prefix line numbers
type app.log | findstr /c:"503"         :: as a pipe filter (like grep)

:: Use the exit code: findstr sets errorlevel 0 if found, 1 if not
findstr /i /c:"healthy" status.txt >nul && echo OK || echo NOT HEALTHY
```

`findstr` is batch's `grep`. Its regex is **limited** (no `+`, no `\d`, quirky
alternation) — for real regex, PowerShell's `Select-String` or `-match`. But for
"does this line/word appear, set an exit code," `findstr` is the tool.

---

## 6. The quoting & special-character rules

The characters `& | < > ( ) ^ % ! "` and **space** are where batch string handling
goes wrong. Rules of thumb:

```bat
echo a ^& b              :: ^ ESCAPES the next special char -> prints  a & b
echo 50%%               :: % is doubled in a file
echo "value with & | special"   :: quotes protect & | < > (but not % or !)
set "p=C:\Program Files\App"      :: store WITH quotes off, USE with quotes:
dir "%p%"                          :: always quote %var% when it may contain spaces
```

- **`^`** escapes one following special char on the command line (`^&`, `^|`, `^<`,
  `^>`, `^^`). It does **not** survive inside quotes the same way — escaping is
  context-dependent and genuinely confusing (full catalog in chapter 08).
- **`%`** → `%%` in a file. **`!`** is special only under delayed expansion (then
  escape as `^^!` in some contexts — ugh).
- **Always quote a `%var%` that may contain spaces or `&`** when passing it to a
  command (`del "%file%"`, not `del %file%`). An unquoted path with a space splits
  into multiple arguments — a top bug *and* a command-injection vector if the value
  is untrusted.

---

## 7. A worked example — parse a version string

```bat
@echo off
setlocal enabledelayedexpansion
set "ver=v12.4.7-rc2"

set "ver=!ver:v=!"                 :: strip leading 'v'  -> 12.4.7-rc2
for /f "tokens=1-3 delims=.-" %%a in ("!ver!") do (
    set "major=%%a" & set "minor=%%b" & set "patch=%%c"
)
echo major=!major! minor=!minor! patch=!patch!     :: 12 / 4 / 7

if !major! geq 12 ( echo supported ) else ( echo too old )
endlocal
```

Combines substitution (strip `v`), `for /f` tokenizing with multi-char `delims=.-`,
and a numeric comparison — a realistic parse task done entirely in batch.

---

## 8. Key takeaways

1. **Substrings:** `%var:~start,len%` with 0-based and negative indices replaces
   `cut`/`substr`.
2. **Replace:** `%var:find=replace%` is global + case-insensitive; `%var:*X=Y%`
   strips a prefix through the first `X`; replace-with-nothing deletes.
3. **No built-in length/case/trim** — they require subroutines or shelling out to
   PowerShell (a sign you've outgrown batch).
4. **`findstr`** is batch's grep: `/i` `/c:` `/r` `/v` `/n`, and its exit code drives
   `&&`/`||` — but its regex is limited.
5. **Quote `%var%` whenever it may contain spaces or specials**; `^` escapes one
   special char; `%`→`%%`; unquoted untrusted values are an injection risk.

> Next: [05 — Subroutines & I/O](05_functions_io.md) — `call :label`, returning
> values, redirection, and reading files.
