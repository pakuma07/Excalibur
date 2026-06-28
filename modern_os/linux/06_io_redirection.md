# 06 — I/O, Redirection & Here-Docs

> **Audience:** Engineers who can write loops and conditionals but still `2>&1` by copy-paste and lose variables inside pipes. This chapter makes I/O plumbing first-class: file descriptors, redirection order, here-docs, process substitution, and the subshell traps that silently eat your data. By the end you should reach for `< <(cmd)` reflexively and never debug a `2>&1 >file` ordering bug again.

---

## 1. The three standard file descriptors

Every process starts with three open file descriptors (fds). A fd is just a small integer the kernel maps to an open file/pipe/terminal.

| fd | Name   | Default | Stream      | Redirect operators            |
|----|--------|---------|-------------|-------------------------------|
| 0  | stdin  | keyboard| input       | `<`, `<<`, `<<<`, `0<`        |
| 1  | stdout | terminal| normal out  | `>`, `>>`, `1>`, `1>>`       |
| 2  | stderr | terminal| errors      | `2>`, `2>>`                  |

```bash
echo "to stdout"          # fd 1
echo "to stderr" >&2      # fd 2 — manually send to stderr
ls /nope                  # writes its error to fd 2
```

Why two output streams? So you can capture results without capturing diagnostics. A well-behaved script sends data to stdout and progress/errors to stderr — that lets callers pipe the data cleanly.

---

## 2. Output redirection: `>`, `>>`, and truncation

```bash
echo "hi" > out.txt       # CREATE/TRUNCATE then write
echo "more" >> out.txt    # APPEND
```

`>` truncates the target to zero length *before* the command even runs. This is the classic self-clobber:

```bash
# WRONG — truncates file.txt to empty before sort reads it
sort file.txt > file.txt   # file.txt is now empty!

# RIGHT — use a temp file or a tool that supports in-place edits
sort file.txt > file.txt.tmp && mv file.txt.tmp file.txt
sort -o file.txt file.txt          # sort's own in-place flag
```

- **Symptom:** A file you tried to filter "in place" ends up empty.
- **Cause:** The shell opens and truncates the redirect target before the command starts; nothing is left to read.
- **Fix:** Redirect to a temp file then rename, or use a tool's native in-place option (`sort -o`, `sed -i`, `awk` to temp).

Guard against accidental clobber with `set -o noclobber` (or `set -C`); then `>` fails on an existing file and `>|` forces it.

---

## 3. stderr, `2>&1`, and the ORDER trap

`2>&1` means "make fd 2 point to wherever fd 1 currently points." The phrase *currently points* is the whole game.

```bash
# RIGHT — send stdout to file, then point stderr at the same place
cmd >file 2>&1
#   ^ fd1 -> file
#         ^ fd2 -> (copy of fd1, which is now file)  ✅ both in file
```

```bash
# WRONG — order reversed
cmd 2>&1 >file
#   ^ fd2 -> (copy of fd1, still the TERMINAL)
#        ^ fd1 -> file
# Result: stderr goes to the terminal, stdout goes to the file. ❌
```

- **Symptom:** Errors still spam your terminal even though you "redirected everything to a log."
- **Cause:** `2>&1` was placed *before* `>file`, so it copied the terminal target, not the file target.
- **Fix:** Put the data redirection first: `>file 2>&1`. Read redirections strictly left-to-right; each `&n` is a snapshot of where `n` points *at that instant*.

Bash shortcut for "both streams to the same place":

```bash
cmd &>file       # bash-only: stdout AND stderr to file (truncate)
cmd &>>file      # ... append
cmd >file 2>&1   # POSIX-portable equivalent — prefer this in /bin/sh scripts
```

Separating the streams instead:

```bash
cmd >out.log 2>err.log    # data and errors in different files
```

---

## 4. `/dev/null` — the bit bucket

```bash
cmd 2>/dev/null            # discard errors, keep output
cmd >/dev/null             # discard output, keep errors
cmd >/dev/null 2>&1         # discard everything (silence)
cmd &>/dev/null            # bash shortcut for the same

# Common pattern: test for existence quietly
if command -v jq >/dev/null 2>&1; then echo "jq present"; fi
```

`/dev/null` accepts and discards all writes and returns EOF on read. Reading from it is a handy "empty input": `cmd </dev/null` stops a program blocking on stdin.

---

## 5. Custom file descriptors with `exec`

`exec` *without a command* applies redirections to the current shell permanently (until changed). This lets you open extra fds.

```bash
exec 3>build.log          # open fd 3 writing to build.log
echo "step 1 done" >&3    # write to fd 3
echo "step 2 done" >&3
exec 3>&-                 # CLOSE fd 3 (release the file)
```

Open a fd for reading, or both:

```bash
exec 4<input.txt          # fd 4 reads from input.txt
read -r first_line <&4    # read one line from fd 4
exec 4<&-                 # close it

exec 5<>socketfile        # open fd 5 read+write (e.g. /dev/tcp, fifos)
```

- **Symptom:** "Too many open files" in a long-running script; or a log file that can't be deleted/rotated.
- **Cause:** fds opened with `exec 3>...` stay open for the life of the shell until you close them.
- **Fix:** Always close custom fds when done: `exec 3>&-` (write side) / `exec 3<&-` (read side).

### Redirecting an entire script

Put this near the top to tee or capture *all* subsequent output:

```bash
#!/usr/bin/env bash
exec >>/var/log/myjob.log 2>&1   # everything from here on appends to the log
echo "job started at $(date)"    # goes to the log, not the terminal
```

To keep terminal output *and* log simultaneously, combine with `tee` via process substitution (see §9):

```bash
exec > >(tee -a run.log) 2>&1    # stdout+stderr to terminal AND run.log
```

---

## 6. Here-documents (`<<EOF`)

A here-doc feeds an inline block to a command's stdin. The delimiter (`EOF` by convention) ends the block.

```bash
cat <<EOF
User: $USER
Host: $(hostname)
Path: $PATH
EOF
# Variables and $(...) ARE expanded
```

Quote the delimiter to disable all expansion — essential for emitting literal scripts, configs, or `$` characters:

```bash
cat <<'EOF'
This $USER is literal, $(date) is not run, \n stays as backslash-n.
EOF
# Nothing expanded — what you see is what you get
```

`<<-` strips *leading tabs* (tabs only, not spaces) so you can indent the body to match surrounding code:

```bash
generate_config() {
	cat <<-'EOF'
	server {
	    listen 80;
	}
	EOF
}
# The leading tabs before each line and before EOF are removed
```

- **Symptom:** `warning: here-document delimited by end-of-file (wanted 'EOF')`.
- **Cause:** Trailing whitespace after the opening `<<EOF`, or the closing `EOF` is indented with **spaces** (only `<<-` strips, and only tabs).
- **Fix:** Closing delimiter must be at column 0 (or tab-indented with `<<-`), alone on its line, exactly matching.

---

## 7. Here-strings (`<<<`)

A one-line stdin source. Cleaner than `echo ... |` and it avoids spawning a subshell (see §8).

```bash
# WRONG-ish — extra process, and the pipe creates a subshell
echo "$line" | read -r a b c

# RIGHT — here-string feeds read directly in the current shell
read -r a b c <<< "$line"
echo "$a / $b / $c"          # variables survive!

grep "error" <<< "$logtext"  # search a variable's contents
bc <<< "3 * 4"               # 12
```

---

## 8. The lost-variable-in-pipe trap

**Each side of a pipe runs in its own subshell.** Variables set in the right-hand side vanish when the subshell exits. This is the single most common Bash data bug.

```bash
# WRONG — count is updated in a subshell, then thrown away
count=0
grep -c . file | while read -r n; do
    count=$((count + n))
done
echo "$count"     # prints 0  ❌ — the loop ran in a subshell
```

```bash
# RIGHT — process substitution keeps the loop in the CURRENT shell
count=0
while read -r n; do
    count=$((count + n))
done < <(grep -c . file)
echo "$count"     # correct  ✅
```

- **Symptom:** A counter/array/flag built inside `... | while read` is empty after the loop.
- **Cause:** The `while` ran in a subshell on the right of the pipe; its variable mutations don't propagate to the parent.
- **Fix:** Replace the pipe with input redirection from **process substitution**: `while read ...; do ...; done < <(producer)`. (Bash ≥4.2 with `shopt -s lastpipe` + non-interactive is an alternative, but `< <(...)` is the portable, obvious idiom.)

See [04 — Control Flow](04_control_flow.md) for the canonical `while read -r` line-processing loop this pairs with.

---

## 9. Process substitution: `<(cmd)` and `>(cmd)`

Process substitution turns a command's I/O into a *filename* (`/dev/fd/63`) that other commands can open. It's the bridge between "tools want files" and "I have a pipeline."

```bash
# Diff the output of two commands — impossible with plain pipes
diff <(sort a.txt) <(sort b.txt)

# Compare live data: installed vs expected
diff <(rpm -qa | sort) <(sort expected_pkgs.txt)

# Feed multiple producers into one consumer
paste <(cut -f1 data) <(cut -f3 data)
```

`>(cmd)` is the output form — write *to* a process:

```bash
# tee to a compressor and a checksummer at once
some_command | tee >(gzip > out.gz) >(sha256sum > out.sha) > out.raw
```

Why it beats pipes: a pipe is a single linear stdin→stdout channel and forces the consumer into a subshell. Process substitution gives the consumer a real fd it can `open()`, keeps your loop in the current shell (§8), and lets a command take *several* streamed inputs.

> Note: process substitution is a bash/zsh/ksh feature, **not** POSIX `sh`. In a strict `#!/bin/sh` script use a temp file or `mkfifo` (§10).

---

## 10. Named pipes (FIFOs) with `mkfifo`

A FIFO is a pipe with a filesystem name, so unrelated processes can connect to it. Use it when process substitution isn't available or producer/consumer are started separately.

```bash
mkfifo /tmp/mypipe              # create the named pipe

# Reader (blocks until a writer appears)
grep "ERROR" < /tmp/mypipe > errors.log &

# Writer
tail -f app.log > /tmp/mypipe &

# ... later
rm /tmp/mypipe                  # clean up the FIFO node
```

- **Symptom:** A script using a FIFO hangs forever.
- **Cause:** `open()` on a FIFO blocks until *both* a reader and a writer are attached. One side never showed up.
- **Fix:** Start the reader in the background (`&`) before the writer, or open the fd non-blocking. Remove the FIFO when done. See [09 — Processes, Jobs & Signals](09_processes_signals.md) for managing the background jobs cleanly.

---

## 11. `read` — structured input

`read` splits a line of input into variables. Always use `-r` (raw) unless you specifically want backslash escapes processed.

```bash
read -r name                    # one var: whole line (minus leading/trailing IFS)
read -r user host port          # split on $IFS into three vars; extras spill into last
read -r -p "Continue? [y/N] " ans      # -p: prompt
read -r -t 5 -p "PIN: " pin || echo "timed out"   # -t: timeout seconds
read -r -s -p "Password: " pw; echo    # -s: silent (no echo) for secrets
read -r -a parts <<< "a b c"           # -a: split into an array; parts[0]=a ...
```

Control splitting with `IFS`. Set it *only* for that one `read` by prefixing the command:

```bash
# Parse /etc/passwd-style colon records without altering global IFS
while IFS=: read -r user _ uid gid _ home shell; do
    echo "$user has uid $uid, shell $shell"
done < /etc/passwd
```

```bash
# WRONG — default IFS collapses/strips, so leading spaces are lost
read -r line <<< "    indented text"   # line = "indented text"

# RIGHT — empty IFS preserves the full line verbatim
IFS= read -r line <<< "    indented text"   # line = "    indented text"
```

- **Symptom:** Leading/trailing whitespace or empty fields disappear; the last loop line is skipped.
- **Cause:** Default `IFS` (space/tab/newline) trims and merges; and `read` returns non-zero on a final line lacking a trailing newline, ending the loop early.
- **Fix:** Use `IFS= read -r line` for verbatim lines, and the standard `while IFS= read -r line || [[ -n $line ]]; do` guard to catch a last unterminated line.

---

## 12. `tee` — split a stream

`tee` copies stdin to one or more files *and* to stdout, so you can both see and save.

```bash
make 2>&1 | tee build.log              # watch + save (truncate)
make 2>&1 | tee -a build.log           # -a: append
echo 1 | sudo tee /sys/.../setting >/dev/null   # write to a root-owned file via sudo
```

That last pattern is the canonical fix for `echo x > /root/file` failing under `sudo` — the redirect is done by your shell (unprivileged), not by `sudo`. Piping into `sudo tee` runs the *write* as root.

---

## 13. Quick reference

| Goal                                   | Incantation                          |
|----------------------------------------|--------------------------------------|
| stdout to file (truncate / append)     | `> f` / `>> f`                       |
| stderr to file                         | `2> f`                               |
| both to same file                      | `> f 2>&1`  (order matters!) / `&> f`|
| discard everything                     | `>/dev/null 2>&1` / `&>/dev/null`    |
| keep loop vars after `while read`      | `done < <(producer)`                 |
| compare two command outputs            | `diff <(c1) <(c2)`                   |
| inline literal block                   | `<<'EOF' ... EOF`                    |
| feed a variable to a command           | `cmd <<< "$var"`                     |
| see + save a stream                    | `cmd \| tee log`                     |
| write to root-owned file               | `echo x \| sudo tee f`               |
| open / close custom fd                 | `exec 3>f` / `exec 3>&-`             |

The mental model: redirections are evaluated left-to-right, `&n` snapshots a target *at that moment*, pipes spawn subshells, and process substitution hands you a real filename. Keep those four facts straight and most "mysterious" I/O bugs evaporate. For turning the captured streams into reports — cutting, filtering, reshaping — continue to the next chapter.

---

> Next: [07 — Text Processing Toolkit](07_text_processing.md) — `grep`, `sed`, `awk`, `cut`, `sort`, `uniq` and friends: now that you can route any stream anywhere, learn to slice and transform what flows through it.
