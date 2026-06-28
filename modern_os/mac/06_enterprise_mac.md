# 06 — Enterprise Mac

> **Audience:** Linux engineers automating and managing a **fleet** of Macs — CI
> build agents, MDM-enrolled laptops, signed-tool distribution. This capstone covers
> the Apple-specific machinery with no Linux equivalent: **Homebrew**, **MDM**/config
> profiles, **codesigning + notarization**, **Gatekeeper**/quarantine, the **keychain**
> (`security`), and **TCC** privacy. On Linux you `apt install`, drop a binary, and run
> it. On macOS, distributing or running code involves Apple's signing chain and a
> privacy database that *no CLI can override*. That asymmetry is the whole chapter.

See also: [01 — The macOS Shell Landscape](01_shell_landscape.md) (PATH/SIP),
[03 — launchd & Scheduling](03_launchd_scheduling.md) (the headless-session problem),
[05 — AppleScript, osascript & JXA](05_applescript_jxa.md) (TCC/Automation).

---

## 1. Homebrew in automation

Homebrew is the package manager every Mac dev box and CI agent relies on. The single
biggest portability trap is the **install prefix**, which differs by CPU architecture.

| Arch | Prefix | `brew` binary |
|------|--------|---------------|
| Apple Silicon (arm64) | `/opt/homebrew` | `/opt/homebrew/bin/brew` |
| Intel (x86_64) | `/usr/local` | `/usr/local/bin/brew` |

On Intel, `/usr/local/bin` is already on PATH. On Apple Silicon it is **not** — you
must initialise the environment or scripts won't find `brew` or anything it installed
(PATH/`path_helper` in [01](01_shell_landscape.md)).

```bash
# WRONG — assumes brew is on PATH; silently fails on Apple Silicon CI agents
brew install jq

# RIGHT — make the prefix explicit, then load brew's shellenv
eval "$(/opt/homebrew/bin/brew shellenv)"   # arm64
# eval "$(/usr/local/bin/brew shellenv)"    # Intel
brew install jq
```

To be arch-agnostic in a script, probe for the binary:

```bash
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$b" ] && eval "$("$b" shellenv)" && break
done
```

### Non-interactive installs

The bootstrap installer and `brew` prompt by default — fatal in CI. Set
`NONINTERACTIVE=1` (and disable analytics/auto-update for fast, reproducible runs):

```bash
export NONINTERACTIVE=1
export HOMEBREW_NO_ANALYTICS=1
export HOMEBREW_NO_AUTO_UPDATE=1        # pin versions; don't update mid-build
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Reproducible setup with `brew bundle` / Brewfile

A `Brewfile` is the macOS analogue of a `requirements.txt` / `Dockerfile` — declarative,
checked into the repo:

```ruby
# Brewfile
brew "jq"
brew "coreutils"        # GNU tools — see ch 02
cask "google-chrome"    # GUI apps install as casks
cask "temurin"          # JDK
mas  "Xcode", id: 497799835
```

```bash
brew bundle install --file=Brewfile     # install everything
brew bundle check  --file=Brewfile      # verify; nonzero exit if missing
brew bundle cleanup --file=Brewfile     # remove anything NOT in the file
```

**`--cask` for GUI apps:** formulae are CLI tools poured into the prefix; **casks**
wrap pre-built `.app`/`.pkg` GUI apps into `/Applications`:

```bash
brew install --cask visual-studio-code firefox
```

### Why brew must not run as root

- **Symptom:** `brew install` as root prints *"Don't run this as root!"* and aborts,
  or leaves the prefix owned by root and breaks future non-root installs.
- **Cause:** Homebrew owns its prefix as a normal user so it never needs `sudo`.
  Running it as root poisons file ownership.
- **Fix:** run brew as the build/login user (the prefix owner). In a launchd daemon or
  `sudo` context, drop to that user: `sudo -u builder brew install …`.

---

## 2. MDM & configuration profiles

**MDM** (Mobile Device Management) manages fleets centrally — **Jamf Pro**, **Microsoft
Intune**, **Kandji**. The device checks in with an MDM server that pushes
**configuration profiles** (`.mobileconfig`, an XML/plist payload) controlling settings,
certs, restrictions, and **PPPC** privacy grants (§6).

```bash
profiles list                       # list installed profiles (use sudo for system scope)
profiles show -type enrollment      # is this Mac MDM-enrolled, and how
profiles status -type enrollment    # MDM enrollment + DEP status
```

> **Modern macOS restricts manual profile installation.** You can no longer silently
> `profiles install -path x.mobileconfig` for most payloads — install requires **MDM
> delivery** or explicit **user approval** in System Settings > Profiles. The old
> `profiles -I`/`-R` install/remove flags are deprecated/blocked outside MDM. Deliver
> config *through* your MDM, not via scripts.

**Managed preferences override `defaults`.** A profile that sets a key writes a
*managed* (forced) value that beats anything a user or script writes with
`defaults write` ([04](04_system_config_tooling.md) covers `defaults`).

- **Symptom:** `defaults write` "succeeds" but the setting never takes effect / reverts.
- **Cause:** an MDM-managed (forced) preference is overriding it.
- **Fix:** change it in the profile/MDM. Inspect the forced values under
  `/Library/Managed Preferences/`.

**DEP / ADE zero-touch:** Automated Device Enrollment ties a Mac's serial (bought via
Apple Business Manager) to your MDM. On first boot during Setup Assistant the Mac
auto-enrolls and applies profiles — no human touches it. This is how you stand up a
build agent or laptop fleet "zero-touch."

---

## 3. Codesigning & notarization

To distribute **any** binary, script-app, `.app`, or `.pkg` so it runs on *other*
Macs, it must be **signed with a Developer ID** and **notarized** by Apple. Otherwise
Gatekeeper blocks it (§4). Three stages:

| Stage | Tool | What it does |
|-------|------|--------------|
| 1. Sign | `codesign` (apps) / `productsign` (pkgs) | attach your Developer ID signature + hardened runtime |
| 2. Notarize | `xcrun notarytool` | Apple scans the artifact for malware, returns a ticket |
| 3. Staple | `xcrun stapler` | attach the notarization ticket so it validates offline |

### Sign

```bash
# Sign an .app with hardened runtime + secure timestamp (both REQUIRED for notarization)
codesign --sign "Developer ID Application: Acme Inc (TEAMID1234)" \
         --options runtime \
         --timestamp \
         --entitlements MyApp.entitlements \
         --deep MyApp.app
```

- `--options runtime` enables the **hardened runtime** (required to notarize).
- `--timestamp` adds a trusted timestamp so the signature survives cert expiry.
- **Entitlements** are an XML plist of capabilities the hardened runtime otherwise blocks
  (e.g. `com.apple.security.cs.allow-jit`).

```bash
# Verify deeply and strictly — what Gatekeeper effectively checks
codesign --verify --deep --strict -vvv MyApp.app
codesign --display --verbose=4 MyApp.app    # inspect identity, entitlements, hash
```

### Notarize with `notarytool`

`altool` is gone — use `notarytool`. First store credentials once in a **keychain
profile** so the API key/Apple-ID isn't in your scripts or shell history:

```bash
# Store creds once (interactive); 'fleet-notary' is the profile name you reference later
xcrun notarytool store-credentials "fleet-notary" \
      --apple-id "ci@acme.com" --team-id "TEAMID1234" --password "app-specific-pw"

# Submit a ZIP/DMG/PKG and block until Apple finishes
xcrun notarytool submit MyApp.zip --keychain-profile "fleet-notary" --wait
# inline creds also work (--apple-id/--team-id/--password) but leak into the process list
```

> You notarize an **archive** (`.zip`/`.dmg`/`.pkg`), not a bare `.app`. If it fails,
> `xcrun notarytool log <submission-id> --keychain-profile fleet-notary` shows exactly
> which nested binary was unsigned/un-hardened.

### Staple

```bash
xcrun stapler staple MyApp.app      # attach the ticket (staple the .app, not the zip)
xcrun stapler validate MyApp.app    # confirm the ticket is attached
```

Stapling lets the artifact pass Gatekeeper **offline** — without it, the first-run
check needs a network round-trip to Apple.

---

## 4. Gatekeeper & quarantine

When a file is downloaded by a browser/curl/`open`-aware app, macOS tags it with the
**`com.apple.quarantine`** extended attribute. Gatekeeper then checks signature +
notarization before the *first* launch.

```bash
xattr -l ./Downloaded.app                 # list xattrs; look for com.apple.quarantine
xattr -p com.apple.quarantine ./file      # print just that attribute
spctl --assess --type exec -vv ./MyApp.app   # what Gatekeeper decides (accepted/rejected + source)
```

- **Symptom:** *"can't be opened because Apple cannot check it for malicious software"*
  or *"is damaged and can't be opened."*
- **Cause:** the artifact carries `com.apple.quarantine` and is **unsigned/un-notarized**
  (or the signature/staple is broken). Common with downloaded scripts and CI artifacts.
- **Fix (distribution):** sign + notarize + staple (§3) — the *only* fix that works for
  end users.
- **Fix (local only):** strip the attribute on your own machine —

```bash
xattr -dr com.apple.quarantine ./MyApp.app   # LOCAL workaround, NOT a distribution strategy
xattr -cr ./MyApp.app                         # nuke ALL xattrs (heavier hammer)
```

> Stripping quarantine only fixes *that one Mac*. Every other machine re-quarantines on
> download — don't ship a "run this `xattr`" README, sign and notarize instead.
> `curl`-downloaded files usually carry no quarantine bit, which is why a script that
> "works in my terminal" is blocked when a user double-clicks it.

---

## 5. Keychain & secrets (`security` CLI)

The keychain is macOS's encrypted secret store. Use it instead of hardcoding tokens in
scripts or env files. The CLI is `security`.

| Keychain | Path | Use |
|----------|------|-----|
| login | `~/Library/Keychains/login.keychain-db` | per-user secrets; unlocked at GUI login |
| System | `/Library/Keychains/System.keychain` | machine-wide; for daemons/launchd |
| iCloud | (synced) | personal, not for automation |

```bash
# WRONG — secret in the script, the repo, and shell history
TOKEN="ghp_hardcodedInSource"

# RIGHT — store once, fetch at runtime. -w prints the password only, to stdout.
security add-generic-password -a "ci" -s "deploy-token" -w "s3cr3t" \
         ~/Library/Keychains/login.keychain-db
TOKEN="$(security find-generic-password -a ci -s deploy-token -w)"
curl -H "Authorization: Bearer $TOKEN" https://api.example.com/deploy
```

### The headless / launchd problem

- **Symptom:** works interactively but a launchd daemon/SSH/CI run fails with *"The user
  interaction is not allowed"* / `errSecInteractionNotAllowed`.
- **Cause:** the **login keychain is locked** in a non-GUI session — it only auto-unlocks
  at GUI login ([03](03_launchd_scheduling.md) covers daemons vs agents).
- **Fix:** unlock it explicitly, or store the secret in the **System keychain**
  (machine-wide, accessible to daemons) with an access ACL.

```bash
# CI pattern: create a dedicated, pre-unlocked keychain for the build session
security create-keychain -p "$KP" build.keychain
security set-keychain-settings -lut 21600 build.keychain   # don't auto-lock mid-build
security unlock-keychain -p "$KP" build.keychain
security list-keychains -d user -s build.keychain login.keychain-db
```

This is also how you import a signing identity (`.p12`) for the §3 codesign step on CI.

---

## 6. TCC privacy in the fleet

**TCC** (Transparency, Consent & Control) gates access to protected resources —
**Full Disk Access**, **Automation** (controlling other apps — see [05](05_applescript_jxa.md)),
Accessibility, Camera, Mic, Screen Recording. It is a SQLite database guarded by SIP.

```bash
tccutil reset All                          # reset ALL privacy grants (forces re-prompt)
tccutil reset AppleEvents                  # reset just Automation grants
tccutil reset SystemPolicyAllFiles com.acme.agent   # reset FDA for one bundle id
```

- **Symptom:** a CI script that reads `~/Library`, drives an app, or records the screen
  fails silently or returns empty — no error, just nothing.
- **Cause:** the controlling process lacks a TCC grant. There is **no CLI to grant TCC**
  — `tccutil` can only *reset*, never *add*.
- **Fix on a fleet:** push a **PPPC** (Privacy Preferences Policy Control)
  configuration profile via **MDM** (§2) that pre-authorizes your CI agent's binary
  (by bundle id / code-signing requirement) for the resources it needs. A headless CI
  agent can't click the GUI consent dialog, so MDM-provisioned PPPC is the *only* way
  to grant Full Disk Access / Automation non-interactively.

> Because TCC grants are keyed to the **code-signing identity**, your agent binary must
> be signed (§3) for a stable PPPC grant — re-signing with a different identity
> invalidates the grant.

---

## 7. Checklist — what a managed Mac CI build agent needs

- [ ] **MDM-enrolled** via DEP/ADE for zero-touch + remote management (§2).
- [ ] **Homebrew** under the right prefix, owned by the **build user** (not root), with
      `brew shellenv` loaded; setup driven by a checked-in **Brewfile** (§1).
- [ ] **PPPC profile** via MDM granting **Full Disk Access / Automation** — no CLI
      alternative (§6).
- [ ] **Signing identity** (`Developer ID Application`) imported into a dedicated,
      pre-unlocked **CI keychain**; **`notarytool` keychain profile** stored once (§3, §5).
- [ ] Sign/**notarize**/**staple** pipeline wired in, verified with `codesign --verify`
      + `spctl --assess` (§3, §4).
- [ ] Secrets in the **keychain**, fetched with `security find-generic-password -w` —
      never hardcoded; unlock handled for the headless session (§5).
- [ ] Scheduled via **launchd** (not cron), aware of GUI-vs-daemon limits
      ([03](03_launchd_scheduling.md)).

---

> Related: [`../linux/`](../linux/README.md) (the Bash foundation this series builds on),
> [`../windows/`](../windows/README.md) (the same fleet problems on Windows), and
> [`../../os_net/`](../../os_net/README.md) (OS & networking fundamentals).
