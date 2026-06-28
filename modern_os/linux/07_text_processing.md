# 07 — Text Processing Toolkit

> **Audience:** Engineers who can write loops and functions but still reach for a 20-line Python script when a one-line pipeline would do. This chapter teaches the Unix power tools — `grep`, `sed`, `awk`, and friends — and the *pipeline mindset*: stream text through small composable filters, each doing one job well. Master this and you'll debug production incidents from a log file faster than you can open an editor.

---

## 1. The Pipeline Mindset

Every tool here reads lines from stdin, transforms them, writes to stdout. Chain them with `|`. Data flows left to right; each stage is a filter.

```bash
# Anatomy: source | filter | filter | sink
cat access.log | grep ' 500 ' | awk '{print $7}' | sort | uniq -c | sort -rn | head

# ...but that leading `cat` is the classic "Useless Use of Cat" (UUOC).
# Tools take a filename argument directly — no need to fork a cat:
grep ' 500 ' access.log | awk '{print $7}' | sort | uniq -c | sort -rn | head
```

- **Tip:** UUOC isn't just style. `cat file | grep x` spawns an extra process and discards the file's seekability. `grep x file` is faster and clearer. (Exception: `cat a b c | ...` to concatenate multiple files is fine.)
- **Tip:** Before forking out to `sed`/`awk` for trivial string surgery, check whether Bash can do it in-process — see [03 — Parameter Expansion](03_parameter_expansion.md). `${var##*/}` beats `echo "$var" | sed 's|.*/||'` every time.

---

## 2. Regex Flavors: BRE vs ERE vs PCRE

The single biggest source of "but it worked on the other machine" pain.

| Flavor | Where | `+ ? { } ( ) |` | Notes |
|---|---|---|---|
| **BRE** (Basic) | `grep`, `sed` default | must be escaped: `\+ \? \{ \} \( \) \|` | the awkward legacy default |
| **ERE** (Extended) | `grep -E`, `sed -E`, `awk` | bare metacharacters | what you usually want |
| **PCRE** (Perl) | `grep -P` (GNU only) | bare + lookahead, `\d`, `\b`, non-greedy `*?` | most powerful, least portable |

```bash
echo "foo123" | grep    'o\+'      # BRE: escape the +
echo "foo123" | grep -E 'o+'       # ERE: bare +
echo "foo123" | grep -P '\d+'      # PCRE: \d shorthand -> 123
```

- **Symptom:** `grep 'colou?r'` matches literally `colou?r`, not `color`/`colour`.
- **Cause:** Default BRE treats `?` as a literal.
- **Fix:** Use `grep -E 'colou?r'`. Get in the habit of always reaching for `-E`.

---

## 3. `grep` — Find Lines

```bash
grep -i error app.log          # -i  case-insensitive
grep -v DEBUG app.log          # -v  invert: lines NOT matching
grep -c ERROR app.log          # -c  count matching lines (not occurrences!)
grep -n TODO src.py            # -n  prefix line numbers
grep -o '[0-9]\+' data.txt     # -o  print only the matched part, one per line
grep -r 'apiKey' ./src         # -r  recurse into directories
grep -rl 'TODO' ./src          # -l  list filenames with a match (l = list)
grep -F '1.2.3.4' hosts        # -F  fixed string: dots are literal, not regex
grep -E '4[0-9]{2}' access.log # -E  ERE for {n} quantifier
```

- **Tip:** `-F` (fixed-string, aka `fgrep`) is faster *and* safer when your needle contains regex metacharacters like `.` `*` `[`. Searching for an IP or a version number? Use `-F`.
- **Tip:** Combine flags: `grep -rin TODO .` = recursive, case-insensitive, with line numbers.
- **Symptom:** `grep -c` returns a smaller number than expected.
- **Cause:** `-c` counts *lines* with at least one match, not total matches. For total occurrences: `grep -o pattern file | wc -l`.

---

## 4. `sed` — Stream Editor

The substitution workhorse. Operates line by line.

```bash
sed 's/foo/bar/'    file       # replace FIRST foo on each line
sed 's/foo/bar/g'   file       # g = global: every foo on the line
sed 's/foo/bar/gi'  file       # i = case-insensitive
sed '3s/foo/bar/'   file       # address: only line 3
sed '/^#/d'         file       # delete comment lines (d = delete)
sed -n '10,20p'     file       # -n quiet + p print: just lines 10-20
sed 's#/usr#/opt#g' file       # any char as delimiter — handy for paths
```

### In-place editing (`-i`) — the portability trap

```bash
# GNU sed (Linux): bare -i, optional suffix for backup
sed -i        's/old/new/g' config.ini      # edit in place
sed -i.bak    's/old/new/g' config.ini      # also write config.ini.bak

# BSD sed (macOS): -i REQUIRES an argument (use '' for no backup)
sed -i '' 's/old/new/g' config.ini          # macOS: empty string mandatory
```

- **Symptom:** A script with `sed -i 's/.../.../'` works on CI (Linux) but errors `command c expects \ followed by text` on a Mac.
- **Cause:** GNU and BSD `sed` disagree on `-i` syntax. See the full breakdown in [../mac/02_bsd_vs_gnu.md](../mac/README.md).
- **Fix:** For portable scripts, use `sed -i.bak ... && rm -f *.bak`, or install GNU sed (`gsed`) on macOS, or generate to a temp file and `mv`.

---

## 5. `awk` — The Workhorse

`awk` splits each line into fields and runs `pattern { action }` blocks. It's a whole language; you'll use 5% of it constantly.

```bash
# Fields: $1 first, $NF last, $0 whole line; NR=record#, NF=field count
awk '{print $1, $NF}'  access.log        # first and last field
awk -F: '{print $1}'   /etc/passwd       # -F sets field separator to ':'
awk 'NR==1'            data.csv          # just the header row
awk 'NF'               file              # print only non-blank lines
awk '$3 > 100'         metrics.tsv       # pattern: rows where col 3 > 100
awk '/ERROR/ {print $0}' app.log         # /regex/ pattern + action
```

### BEGIN / END and aggregation

```bash
# Sum a column (bytes = field 10 in a combined access log)
awk '{ sum += $10 } END { print sum }' access.log

# Average, with a BEGIN banner
awk 'BEGIN { print "computing..." }
     { sum += $1; n++ }
     END { printf "avg = %.2f\n", sum/n }' nums.txt

# Count occurrences by field — the associative-array trick
awk '{ count[$1]++ } END { for (k in count) print count[k], k }' access.log
```

- **Tip:** `awk` already understands ERE and arithmetic, so it often replaces a whole `grep | cut | sed` chain by itself: `awk -F, '$2=="US" {print $1}'` filters *and* projects in one pass.
- **Tip:** `printf` in awk works like C: `printf "%-20s %5d\n", name, count` for aligned columns.

---

## 6. `cut`, `tr`, `wc`, `head`/`tail`

```bash
cut -d: -f1     /etc/passwd     # -d delimiter, -f field(s): usernames
cut -d, -f1,3   data.csv        # multiple fields
cut -c1-10      file            # character ranges

tr 'a-z' 'A-Z'  < file          # translate: lowercase -> uppercase
tr -d '\r'      < dos.txt       # -d delete (strip carriage returns)
tr -s ' '       < file          # -s squeeze repeats into one

wc -l file                      # line count
wc -lwc file                    # lines, words, bytes

head -n 20 file                 # first 20 lines
tail -n 20 file                 # last 20 lines
tail -f app.log                 # -f follow: stream new lines as they arrive
tail -F app.log                 # -F also survives log rotation
```

- **Symptom:** `cut -d' '` on a log file with multiple spaces returns empty fields.
- **Cause:** `cut` treats every delimiter literally — two spaces = an empty field between them. It does **not** collapse runs.
- **Fix:** Use `awk` (which splits on runs of whitespace by default) or pre-squeeze with `tr -s ' '`.

---

## 7. `sort` + `uniq` — Counting Things

`uniq` only collapses *adjacent* duplicates, so you almost always `sort` first.

```bash
sort file                       # lexical ascending
sort -n file                    # -n numeric (so 10 sorts after 9, not before)
sort -r file                    # -r reverse
sort -u file                    # -u unique (sort + dedupe in one)
sort -t, -k2 -n data.csv        # -t field sep, -k2 sort by 2nd field, numeric
sort -k3,3nr -k1,1 data.txt     # multi-key: col3 numeric desc, then col1

uniq -c sorted.txt              # -c prefix each line with its count
uniq -d sorted.txt              # -d only show lines that were duplicated
```

- **Symptom:** `uniq` leaves duplicates in the output.
- **Cause:** `uniq` compares only neighboring lines; unsorted input hides duplicates.
- **Fix:** `sort file | uniq -c`. This is the canonical "count by value" idiom.
- **Tip:** `-k2` means "from field 2 to end of line." To sort on *just* field 2, write `-k2,2`. Forgetting the end-field is a subtle bug in multi-key sorts.

---

## 8. Real Pipelines

```bash
# Top 10 URLs by request count (field 7 = path in combined log format)
awk '{print $7}' access.log | sort | uniq -c | sort -rn | head

# Top 10 client IPs hitting 5xx errors
awk '$9 ~ /^5/ {print $1}' access.log | sort | uniq -c | sort -rn | head

# Total bytes transferred (sum field 10)
awk '{ s += $10 } END { print s }' access.log

# Count log lines per hour: [23/Jun/2026:14:.. -> "14"
awk '{print substr($4, 14, 2)}' access.log | sort | uniq -c

# Unique error messages, most frequent first
grep -F 'ERROR' app.log | sed 's/^[0-9T:.-]* //' | sort | uniq -c | sort -rn
```

- See [06 — I/O, Redirection & Here-Docs](06_io_redirection.md) for feeding these pipelines from process substitution and capturing both stdout and stderr.

---

## 9. `jq` — JSON Is Not Line-Oriented

`grep`/`awk` choke on JSON because structure spans lines and quoting matters. `jq` is the right tool.

```bash
jq '.name'              data.json        # extract a field
jq '.items[].id'        data.json        # iterate an array, pull a key
jq -r '.users[].email'  data.json        # -r raw output (no quotes) for piping
jq '.[] | select(.age > 30)' people.json # filter
jq -c '.results[]'      api.json         # -c compact: one JSON object per line

# Pipeline: pull all IPs from a JSON log, count them
jq -r '.client_ip' events.jsonl | sort | uniq -c | sort -rn
```

- **Tip:** `-r` (raw) is essential when the output feeds `sort`/`uniq`/`awk` — without it you get quoted strings.
- **Tip:** For newline-delimited JSON (`.jsonl`), `jq` processes each line as a separate document automatically.

---

## 10. `xargs` — Turn Output Into Arguments

Some commands (`rm`, `mv`, `kill`) take arguments, not stdin. `xargs` bridges the gap.

```bash
grep -rl 'TODO' . | xargs wc -l            # count lines in matching files
find . -name '*.tmp' | xargs rm            # delete found files
echo "1 2 3" | xargs -n1 echo             # -n1: one arg per invocation

# SAFE version: handle spaces/newlines in filenames with NUL delimiters
find . -name '*.log' -print0 | xargs -0 rm
```

- **Symptom:** `find ... | xargs rm` deletes the wrong files when names contain spaces.
- **Cause:** `xargs` splits on whitespace by default; `my file.log` becomes two args.
- **Fix:** Pair `find -print0` with `xargs -0` (NUL-separated). Always, for file lists.
- **Tip:** `xargs -P4` runs 4 jobs in parallel — a poor-man's `make -j` for batch work.

---

## Which Tool For Which Job

| Need | Reach for |
|---|---|
| Find/filter lines by pattern | `grep` (`-F` for literals, `-P` for `\d`/lookahead) |
| Substitute / delete text in a stream | `sed` |
| Fields, columns, math, aggregation | `awk` |
| Grab fixed columns by delimiter | `cut` (no whitespace collapsing — else `awk`) |
| Order / dedupe | `sort` (`-u`) + `uniq -c` |
| Char-level translate/strip/squeeze | `tr` |
| Count lines/words/bytes | `wc` |
| Peek at start/end / follow live | `head` / `tail -f` |
| Parse structured JSON | `jq` |
| Output → command arguments | `xargs -0` |
| Trivial string slicing in-script | **don't fork** — [03 — Parameter Expansion](03_parameter_expansion.md) |

---

> Next: [08 — Error Handling, Strict Mode & Debugging](08_error_handling_debugging.md) — when these pipelines fail silently in the middle of a `set -e` script, `pipefail`, traps, and `set -x` are how you find out why before production does.
