# 05 — AppleScript, osascript & JXA

> **Audience:** Linux engineers automating a Mac. You know `bash`, `cron`, and piping CLIs together. macOS adds a second universe: GUI apps that expose no command line at all. The bridge into that universe is **Apple Events** — a 1990s IPC mechanism that's still how you tell Safari to open a tab, ask Music to play, or read the clipboard. This chapter shows how to drive it from the shell, when to reach for JavaScript instead, and — crucially — why these scripts mysteriously fail under `launchd`, SSH, and CI.

---

## 1. What AppleScript actually is

AppleScript is an English-like scripting language whose only real job is to send **Apple Events** (structured IPC messages) to applications. Apps publish a *scripting dictionary* (their "vocabulary") and AppleScript serializes your commands into events the app understands.

```applescript
-- This is AppleScript. It reads like a sentence on purpose.
tell application "Finder"
    set fileCount to count of files in (path to desktop)
end tell
return fileCount
```

Two things matter for a Linux engineer:

- **It is not a general-purpose language.** Use it as glue to apps, not for logic. Do the logic in `bash`/Python and call AppleScript for the one thing only an app can do.
- **You almost never write `.scpt` files by hand.** You drive AppleScript from the shell with `osascript`.

For the "no-GUI-app-needed" automation (defaults, `launchctl`, profiles), see [04 — System Config & Automation Tooling](04_system_config_tooling.md). This chapter is specifically for when you must talk to a running app.

---

## 2. Driving it from the shell: `osascript`

`osascript` is the CLI that compiles and runs AppleScript (or JXA). Four invocation styles:

```bash
# 1. One-liner
osascript -e 'tell application "Finder" to activate'

# 2. Multi-line: one -e per line (they concatenate with newlines)
osascript \
  -e 'tell application "Safari"' \
  -e '    make new document with properties {URL:"https://example.com"}' \
  -e 'end tell'

# 3. A compiled or plain-text script file
osascript /path/to/script.scpt
osascript /path/to/script.applescript

# 4. Heredoc — best for anything non-trivial (no -e quoting hell)
osascript <<'EOF'
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
end tell
return frontApp
EOF
```

> Use a **quoted heredoc** (`<<'EOF'`) so the shell does not expand `$VAR` or backticks inside your AppleScript. If you *want* shell interpolation, use unquoted `<<EOF` — but then escape every `$` and `"` you meant literally. Quoted + `on run argv` (section 4) is safer.

---

## 3. Reading results back into shell variables

`osascript` writes the script's return value to **stdout**. Capture it like any other command:

```bash
front=$(osascript -e 'tell application "System Events" to get name of first process whose frontmost is true')
echo "Frontmost app: $front"

# Numbers, lists, records all come back as text representations
count=$(osascript -e 'tell application "Finder" to count windows')
```

Caveats that bite Linux engineers expecting clean output:

- AppleScript **lists** print as `a, b, c` (comma-space separated) — not newline-separated. Re-split in the shell, or build the string yourself in-script.
- **Records** (`{name:"x", size:10}`) print in a non-JSON, hard-to-parse form. If you need structured data, use **JXA** and emit JSON (section 6).
- Errors go to **stderr** and set a non-zero exit code — so `set -e` and `$?` behave normally.

---

## 4. Passing shell → AppleScript arguments: `on run argv`

Don't string-concatenate shell values into your script (injection + quoting nightmare). Pass them as arguments to a `run` handler:

```bash
osascript - "Build done" "Deploy finished OK" <<'EOF'
on run argv
    set theTitle to item 1 of argv
    set theBody to item 2 of argv
    display notification theBody with title theTitle
end run
EOF
```

The `-` tells `osascript` to read the script from stdin; everything after it becomes `argv`. All `argv` items arrive as **text** — coerce with `(item 1 of argv) as integer` if you need a number.

```bash
# WRONG — shell value interpolated into the script body
msg="It's broken"          # the apostrophe will break the quoting
osascript -e "display dialog \"$msg\""

# RIGHT — value passed as an argument, no quoting games
osascript - "$msg" <<'EOF'
on run argv
    display dialog (item 1 of argv)
end run
EOF
```

---

## 5. Common recipes

```applescript
-- Notification (non-blocking, appears in Notification Center)
display notification "Backup complete" with title "Cron" subtitle "nightly"

-- Dialog (BLOCKS waiting for a click — never use in launchd/CI, see §9)
display dialog "Proceed?" buttons {"No", "Yes"} default button "Yes"

-- Clipboard get / set
set the clipboard to "hello"
set got to (the clipboard as text)

-- Finder: reveal a path, get selection
tell application "Finder" to reveal (POSIX file "/Users/me/report.pdf")

-- Safari: open a URL in the front window
tell application "Safari" to open location "https://takeda.com"

-- Music: transport control
tell application "Music" to playpause

-- Mail: send a message
tell application "Mail"
    set m to make new outgoing message with properties {subject:"Report", content:"see attached", visible:false}
    tell m to make new to recipient at end of to recipients with properties {address:"team@example.com"}
    send m
end tell

-- Frontmost app name (via System Events, works for any app)
tell application "System Events" to get name of first process whose frontmost is true
```

---

## 6. JXA — JavaScript for Automation

Since macOS 10.10, the same Apple Event bridge is reachable from JavaScript. Run it with `-l JavaScript`:

```bash
osascript -l JavaScript -e 'Application("Safari").activate()'
```

JXA's killer feature for shell pipelines is **real data structures and JSON**:

```javascript
// Emit clean JSON that jq can parse — impossible to do cleanly in AppleScript
const safari = Application("Safari");
const tabs = safari.windows[0].tabs();
const out = tabs.map(t => ({ name: t.name(), url: t.url() }));
JSON.stringify(out);   // becomes osascript's stdout
```

```bash
osascript -l JavaScript -e '...' | jq '.[].url'
```

### AppleScript vs JXA — which to reach for

| Concern | AppleScript | JXA |
|---|---|---|
| Readability of app commands | Excellent (English-like) | Awkward (`.whose()`, function-call syntax) |
| Data structures / arrays / maps | Painful | Native |
| Emitting JSON for the shell | No (manual string building) | `JSON.stringify()` — trivial |
| Documentation & examples online | Vast | Sparse |
| App dictionary quirks | Well-trodden | Often under-documented, surprising bugs |
| `Foundation` / Objective-C bridge | Via `use framework` | `ObjC` bridge, more ergonomic |

**Rule of thumb:** use **AppleScript** for simple "tell app to do X" commands (more examples exist), and **JXA** when you need to return structured data to the shell as JSON.

---

## 7. The `open` command — no Apple Events required

`open` is a plain CLI (no TCC prompt) and is often all you need. Prefer it over scripting an app to launch:

```bash
open report.pdf                      # open with the default app for that type
open -a "Google Chrome" report.pdf   # open with a specific app by name
open -b com.google.Chrome https://x  # ...or by bundle identifier
open https://takeda.com              # open a URL in the default browser
open .                               # open the current dir in Finder
open -R /Users/me/report.pdf         # REVEAL (select) the file in Finder
open -e notes.txt                    # open in TextEdit
```

Find a bundle id with `osascript -e 'id of app "Safari"'` or `mdls -name kMDItemCFBundleIdentifier /Applications/Safari.app`.

---

## 8. UI scripting via System Events (last resort)

When an app has **no scripting dictionary**, your only option is to drive its UI through the Accessibility API, exposed via `System Events`: synthetic keystrokes, clicks, and menu navigation.

```applescript
tell application "System Events"
    keystroke "s" using {command down}          -- Cmd+S
    key code 36                                  -- Return
    tell process "TextEdit"
        click menu item "Save" of menu "File" of menu bar 1
    end tell
end tell
```

> **This is brittle by nature.** It depends on exact menu titles, window layouts, and element hierarchies that change between OS versions, app versions, and even locales (a German menu has different titles). Treat UI scripting as a temporary hack, not infrastructure.

**Honest guidance:** before writing a single `keystroke`, look for — in order — (1) a real CLI for the task, (2) an HTTP API, (3) the app's AppleScript dictionary. UI scripting is the last resort and *will* break on the next macOS update.

---

## 9. Permissions: the TCC model (this is why it fails)

macOS gates Apple Events behind **TCC** (Transparency, Consent & Control). Two distinct permissions apply:

- **Automation** — app A controlling app B. The first time your script tells another app to do something, macOS shows *"Terminal wants to control Safari"* and the user must click **OK**. The grant is stored per (controlling-app, target-app) pair.
- **Accessibility** — required for **UI scripting** (`keystroke`, `click` via System Events). Granted in *System Settings → Privacy & Security → Accessibility* for the controlling app (e.g. Terminal, your agent binary, `osascript`).

These prompts can **only** be answered by a human at a logged-in GUI session.

### The `-1743` error

**Symptom:** `execution error: Not authorized to send Apple events to Safari. (-1743)` on stderr, non-zero exit.
**Cause:** The controlling process has no Automation grant for the target app — either the user denied it, or there's no GUI session/no one to approve the prompt.
**Fix:** Run once interactively in Terminal and click **OK** on the prompt; or pre-authorize via MDM (see [06 — Enterprise Mac](06_enterprise_mac.md)). Inspect/reset with `tccutil reset AppleEvents` (clears all Automation grants).

### Why it fails silently under launchd / SSH / CI

**Symptom:** A script that works perfectly when you run it in Terminal does nothing — or hangs — when launched by a `launchd` job, over SSH, or in CI.
**Cause:** Apple Events need a **GUI login (Aqua) session** and a TCC consent context. A `launchd` *daemon* (`/Library/LaunchDaemons`), an SSH session, and most CI runners have **no GUI session and no one to click the prompt**. The event is rejected (or a blocking `display dialog` hangs forever with nothing to display).
**Fix:**
- Run GUI automation from a **LaunchAgent** in the logged-in user's GUI context, never a daemon — see [03 — launchd & Scheduling](03_launchd_scheduling.md).
- Pre-grant Automation/Accessibility via an MDM **PPPC profile** for headless/fleet use — see [06 — Enterprise Mac](06_enterprise_mac.md).
- **Never** put a blocking `display dialog` in an unattended script. Use non-blocking `display notification`, or log to a file instead.

```bash
# WRONG — under a launchd daemon this hangs (no GUI) or errors -1743
osascript -e 'display dialog "Done"'

# RIGHT — non-blocking, and degrade gracefully if Apple Events are unavailable
osascript -e 'display notification "Done" with title "job"' 2>/dev/null \
    || logger "GUI notify unavailable; job finished"
```

---

## 10. Decision checklist

- Is there a **real CLI** (`open`, `defaults`, a vendor tool)? Use it — no TCC, works headless.
- Does the app have a **scripting dictionary**? Script it with AppleScript (commands) or JXA (data → JSON).
- Need **structured output** to the shell? JXA + `JSON.stringify` + `jq`.
- Only the UI is available? UI script via System Events — accept that it's brittle and version-fragile.
- Running **unattended** (launchd/SSH/CI)? Expect TCC failures; use a LaunchAgent in the GUI session and pre-grant via MDM. Never block on a dialog.

---

> Next: [06 — Enterprise Mac](06_enterprise_mac.md) — managing Macs at fleet scale: MDM, configuration profiles, and pre-authorizing the very TCC/Automation/Accessibility permissions that made this chapter's scripts fail in CI.
