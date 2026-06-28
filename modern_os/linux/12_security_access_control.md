# 12 — Linux Security & Access Control

> **Audience:** Staff/principal engineers securing Linux hosts at FAANG scale. This is operational craft — the permission model, capabilities, sudo, MAC, and SSH as you actually wield them on production fleets — not kernel internals. The throughline: *least privilege, enforced and auditable, everywhere.*

---

## 1. Users, Groups, Identity

Identity on Linux is three flat files plus a name-resolution layer (NSS).

```bash
# /etc/passwd — one line per account (world-readable)
#   name:x:UID:GID:GECOS:home:shell      (x = password lives in shadow)
root:x:0:0:root:/root:/bin/bash
nginx:x:101:101:nginx:/var/nonexistent:/usr/sbin/nologin

# /etc/shadow — hashes (root-only, 0640). $6$ = sha512crypt, $y$ = yescrypt
# /etc/group — name:x:GID:supplementary,members
```

UID 0 is root. By convention UID < 1000 are **system accounts** (services, daemons); UID >= 1000 are **human accounts**. `/etc/login.defs` sets the ranges.

```bash
id alice                       # uid, gid, all groups — your first identity check
getent passwd alice            # resolves via NSS (files, then LDAP/SSSD, ...)

# Create a HUMAN: shell + home
useradd -m -s /bin/bash -G docker,sudo alice   # -G = supplementary groups
usermod -aG wheel alice        # -aG APPENDS. Forgetting -a NUKES other groups.
groupadd -r deploy             # -r = system group (GID < 1000)

# Create a SERVICE ACCOUNT: no login, no home, no password
useradd -r -s /usr/sbin/nologin -M appsvc      # -r system, -M no home
```

**Primary** group (the GID field in passwd) owns files you create; **supplementary** groups grant additional access. `getent` is the truth source because it consults NSS — on a fleet, accounts usually live in **LDAP/SSSD**, not `/etc/passwd`. If `id` finds a user but the file doesn't, that's NSS doing its job.

> **The principle:** every service runs as a *dedicated unprivileged user* with a `nologin` shell. A compromised `nginx` worker should own nothing but its own runtime files — never root, never a shared `daemon` account.

---

## 2. File Permissions — Deep

Every file has an **owner**, a **group**, and three permission triads: owner / group / other, each `rwx`.

```bash
# -rwxr-x---  =  0750   owner: rwx(7)  group: r-x(5)  other: ---(0)
chmod 0750 deploy.sh           # octal — explicit, scriptable, preferred
chmod u+x,go-w deploy.sh       # symbolic — incremental
chown app:app /srv/app         # owner:group
chgrp deploy /srv/shared
chmod -R g+rX /srv/shared      # CAPITAL X = +x only on dirs / already-exec files
```

On a **directory**, `x` means *traverse* (enter), `r` means *list names*, `w` means *create/delete entries*. You can `x` into a dir without `r` — useful for "drop-box" paths.

### Special bits (setuid / setgid / sticky)

| Bit | Octal | On file | On directory |
|-----|-------|---------|--------------|
| setuid | 4 | runs as **file owner** (`-rwsr-xr-x`) | (ignored) |
| setgid | 2 | runs as **file group** (`-rwxr-sr-x`) | new files **inherit dir's group** |
| sticky | 1 | (ignored) | only **owner** can delete their files (`drwxrwxrwt`) |

```bash
chmod 2775 /srv/shared         # setgid dir: every file created here joins the group
chmod 1777 /tmp                # sticky: anyone writes, only owner deletes their own
ls -l /usr/bin/passwd          # -rwsr-xr-x  setuid root — runs as root for any caller
find / -perm -4000 -type f 2>/dev/null   # AUDIT every setuid-root binary. Each is attack surface.
```

> **The setgid-on-directory trick** is the canonical fix for "team can't edit each other's files in a shared dir." **setuid root** is the canonical *liability*: a bug in that binary = instant root. Prefer capabilities (§4) or a small `sudo` rule.

### umask

`umask` is the *subtracted* default. Final perms = base (`666` files / `777` dirs) **AND NOT** umask.

```bash
umask                          # 0022  -> files 644, dirs 755
umask 0077                     # private by default -> files 600, dirs 700
# Set per-service umask in the unit, not globally: UMask=0027  (see ch 11)
```

### Symptom / Cause / Fix — "Permission denied"

- **Symptom:** `cat /srv/app/conf/x.yaml` → Permission denied, but `x.yaml` is `0644`.
- **Cause:** a **parent directory** lacks the traverse (`x`) bit for you. The file perms are irrelevant if you can't walk the path.
- **Fix:** trace every component's perms with `namei -l`:

```bash
namei -l /srv/app/conf/x.yaml   # shows perms+owner of EACH path component
# drwxr-x---  app  app  conf   <- here: 'other' has no x, you aren't in group app
sudo chmod o+x /srv/app/conf    # or add yourself to the group
```

---

## 3. ACLs — When Octal Isn't Enough

Three triads can't express "user *bob* gets `rw`, group *audit* gets `r`, everyone else nothing." That's a POSIX ACL. The `+` after the mode in `ls -l` (`-rw-rw----+`) signals one is present.

```bash
getfacl /srv/data              # show the full ACL
setfacl -m u:bob:rw /srv/data           # grant bob rw — no group juggling
setfacl -m g:audit:r /srv/data
setfacl -x u:bob /srv/data              # remove bob's entry
setfacl -b /srv/data                    # strip ALL ACLs

# DEFAULT ACLs — inherited by everything created inside a dir (the powerful one)
setfacl -d -m u:deploy:rwx /srv/incoming   # 'd' = default; applies to future children
```

ACLs are the right tool for fine-grained, per-user grants that change often. Keep them documented — they're invisible in a plain `ls -l` beyond the lone `+`, and that's how access drifts.

---

## 4. Linux Capabilities — Breaking Up Root

Root is ~40 distinct privileges fused into UID 0. **Capabilities** split them so a process can do *one* privileged thing without being root.

Each process has capability **sets**:

| Set | Meaning |
|-----|---------|
| **Permitted (P)** | the caps the process *may* use |
| **Effective (E)** | the caps *currently active* (checked at syscall time) |
| **Inheritable (I)** | preserved across `execve()` (legacy, rarely used) |
| **Bounding (B)** | the ceiling — caps can never exceed this set |
| **Ambient (A)** | inherited by non-setuid `execve` — how you grant caps to a plain binary |

```bash
# WRONG: run the whole web server as root just to bind :80
sudo ./webserver --port 80

# RIGHT: grant ONLY the bind-low-port capability to the binary
sudo setcap 'cap_net_bind_service=+ep' /usr/local/bin/webserver
getcap /usr/local/bin/webserver         # cap_net_bind_service=ep
./webserver --port 80                   # runs as YOU, binds :80

capsh --print                           # current shell's cap sets + bounding set
grep Cap /proc/$$/status                # CapPrm/CapEff/CapBnd as hex bitmasks
capsh --decode=0000000000003000         # decode a hex mask to cap names
```

Tie-in: in systemd you don't `setcap` files — you declare `AmbientCapabilities=CAP_NET_BIND_SERVICE` plus `CapabilityBoundingSet=` in the unit (see [11 — systemd: Service Authoring & Operations](11_systemd_services.md)). Containers do the same via the runtime's cap drop/add — a default Docker container already drops most caps. For seccomp/namespaces, see [../../os_net/operating_system/07_virtualization_containers.md](../../os_net/operating_system/07_virtualization_containers.md).

> Audit `getcap -r / 2>/dev/null` like you audit setuid bits — a stray `cap_sys_admin` on a binary is nearly root.

---

## 5. sudo — Least-Privilege Privilege Escalation

`sudo` runs commands as another user (default root) under policy. **Always edit with `visudo`** — it syntax-checks before saving; a broken sudoers can lock everyone out.

```bash
visudo                              # edits /etc/sudoers (validated)
visudo -f /etc/sudoers.d/deploy     # drop-in files — preferred for fleet config mgmt

# Rule syntax:  user  host=(runas:rungroup)  TAGS:  commands
alice   ALL=(ALL:ALL) ALL                          # full root — minimize these
deploy  ALL=(root) NOPASSWD: /usr/bin/systemctl restart app.service
%oncall web-*=(root) /usr/bin/journalctl           # %group, host pattern
```

```bash
sudo -l                             # WHAT can I run? Run this on every host you touch.
```

### Command restriction & its pitfalls

Restricting commands is *hard* because shells and wildcards leak:

```bash
# WRONG: looks restricted, is actually root-equivalent
deploy ALL=(root) NOPASSWD: /usr/bin/vim     # !! :!sh inside vim => root shell
deploy ALL=(root) /usr/bin/find              # !! find ... -exec sh => root shell
deploy ALL=(root) /bin/systemctl *           # !! wildcard matches `systemctl ... ; rm -rf`

# RIGHT: exact, argument-pinned, no shell-escapable tools
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart app.service
```

Any program that can spawn a shell, edit arbitrary files, or take a `--exec`-style flag is **not** safely restrictable. `ALL` is dangerous precisely because it includes all of those.

### Defaults — set these fleet-wide

```bash
Defaults    env_reset                       # scrub the caller's environment
Defaults    secure_path="/usr/sbin:/usr/bin:/sbin:/bin"   # no PATH hijack
Defaults    timestamp_timeout=5             # re-auth after 5 min (0 = every time)
Defaults    logfile="/var/log/sudo.log"     # log every invocation
Defaults    use_pty                         # capture pty — defeats some escapes
```

> **Least-privilege sudo:** grant the *narrowest exact command* a role needs, prefer per-role drop-in files managed by config management, and audit `sudo -l` output across the fleet. Never hand out `(ALL) ALL` as the default.

---

## 6. SELinux & AppArmor — Mandatory Access Control

DAC (the perms above) lets the *owner* decide. **MAC** enforces a system-wide policy that even root can't override. Two implementations dominate:

| | **SELinux** (RHEL/Fedora) | **AppArmor** (Ubuntu/SUSE) |
|---|---|---|
| Model | **Type enforcement** — label every object | **Path-based** — rules per file path |
| Granularity | Very fine, very complex | Coarser, far simpler |
| Unit | contexts/labels (`ls -Z`) | per-binary profiles |
| Modes | enforcing / permissive / disabled | enforce / complain (per profile) |

### SELinux operations

```bash
getenforce                      # Enforcing | Permissive | Disabled
setenforce 0                    # -> Permissive (logs, doesn't block) — TEMPORARY only
ls -Z /var/www/html             # show contexts: unconfined_u:object_r:httpd_sys_content_t:s0
ps -Z                           # process contexts
```

**The #1 workflow — a denial:**

```bash
# Symptom: nginx 403s a file that has perfect 0644 perms.
# Cause:   wrong SELinux TYPE on the file (e.g. default_t, not httpd_sys_content_t).
ausearch -m avc -ts recent           # find the AVC denial in the audit log
ausearch -m avc -ts recent | audit2allow -M mypol   # GENERATE candidate policy module
# ^ READ the .te file first. audit2allow blindly papers over the denial.

# Usually the RIGHT fix is relabeling, not a new policy:
semanage fcontext -a -t httpd_sys_content_t "/srv/web(/.*)?"   # persist the rule
restorecon -Rv /srv/web                                        # apply it now

# Or flip a boolean instead of writing policy:
getsebool -a | grep httpd
setsebool -P httpd_can_network_connect on    # -P = persist across reboot
```

### AppArmor operations

```bash
aa-status                       # loaded profiles, which are enforce vs complain
aa-complain /etc/apparmor.d/usr.sbin.nginx   # log-only while you tune
aa-enforce  /etc/apparmor.d/usr.sbin.nginx   # back to enforcing
# Profiles live in /etc/apparmor.d/ ; logs land in dmesg/audit as DENIED
```

> **The honest message:** `setenforce 0` (or disabling AppArmor) makes the symptom vanish and ships a hole to prod. Use *permissive/complain* to **collect** denials, then fix the label/boolean/profile. Disabling MAC fleet-wide is a finding, not a fix.

---

## 7. SSH at Depth

The principal's daily tool. Get the key story right and the rest follows.

```bash
# Prefer ed25519 — small, fast, no curve-trust questions. RSA only if forced (>=3072).
ssh-keygen -t ed25519 -C "alice@laptop"
ssh-keygen -t rsa -b 4096 -C "legacy-only"      # fallback
```

### `~/.ssh/config` — stop typing flags

```sshconfig
Host bastion
    HostName bastion.prod.example.com
    User alice
    IdentityFile ~/.ssh/id_ed25519

Host app-*
    User deploy
    ProxyJump bastion           # modern bastion hop — replaces ProxyCommand
    IdentitiesOnly yes
```

```bash
ssh app-07                      # transparently hops through bastion
```

**ProxyJump (`-J`)** is the correct bastion pattern: each hop is a separate SSH connection; your key never touches the intermediate host.

### Agent — and the forwarding trap

```bash
eval "$(ssh-agent)"; ssh-add ~/.ssh/id_ed25519    # keys held in-memory, unlocked once
ssh-add -l                                         # list loaded keys

# WRONG: agent forwarding — exposes your agent socket on the remote host
ssh -A bastion        # root (or anyone) on bastion can hijack your agent -> auth anywhere
# RIGHT: ProxyJump — your private key stays on YOUR machine, no socket on the hop
ssh -J bastion app-07
```

### `authorized_keys` options — constrain a key

```
from="10.0.0.0/8",no-port-forwarding,command="/usr/local/bin/backup" ssh-ed25519 AAAA... backup-bot
```

`command=` forces exactly one command (great for bots); `from=` pins source IPs; `no-port-forwarding` blocks tunneling.

### SSH Certificate Authorities — the fleet answer

Managing `authorized_keys` on every host and `known_hosts` on every laptop does not scale. The FAANG answer: an **SSH CA** signs short-lived **user certs** (host trusts the CA, no per-host `authorized_keys`) and **host certs** (clients trust the CA, killing TOFU prompts).

```bash
ssh-keygen -s ca_user_key -I alice -n deploy,oncall -V +8h user_key.pub   # sign a USER cert
ssh-keygen -s ca_host_key -I host -h -n app-07.prod  host_key.pub         # sign a HOST cert

# Hosts: trust the user CA once.   In sshd_config:
#   TrustedUserCAKeys /etc/ssh/ca_user.pub
# Clients: trust the host CA once. In known_hosts / ssh_known_hosts:
#   @cert-authority *.prod.example.com ssh-ed25519 AAAA...ca_host...
```

Short cert lifetimes (hours) replace key revocation — expired beats revoked.

### sshd hardening

```bash
# /etc/ssh/sshd_config.d/10-hardening.conf
PermitRootLogin no              # never log in as root directly
PasswordAuthentication no       # keys/certs only — kills brute force
PubkeyAuthentication yes
AllowGroups ssh-users           # only members of this group may connect
MaxAuthTries 3
KexAlgorithms curve25519-sha256
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com
```

```bash
sshd -t                         # VALIDATE before reloading — don't lock yourself out
systemctl reload sshd
```

> **known_hosts / TOFU:** the first connection blindly trusts the host key ("trust on first use") — a MITM window. Host certificates close it: clients trust the CA, every signed host verifies instantly. For network-layer controls and zero-trust, see [../../os_net/comp_networking/09_network_security.md](../../os_net/comp_networking/09_network_security.md).

---

## 8. auditd — Forensics & Compliance

The kernel audit framework records security-relevant events: file accesses, syscalls, logins. It's how you answer "who changed `/etc/sudoers` at 3am?" and how you pass SOC 2 / PCI.

```bash
# Watch a file for any write/attribute change, tag it:
auditctl -w /etc/sudoers -p wa -k sudoers_change
# Watch a syscall (here: every privilege-changing call):
auditctl -a always,exit -F arch=b64 -S setuid -S setgid -k privesc
auditctl -l                     # list active rules  (persist in /etc/audit/rules.d/)

ausearch -k sudoers_change      # query by your key
ausearch -m avc -ts today       # the SELinux denials from §6 live here too
aureport --summary              # high-level event report
```

auditd is append-only, kernel-level, and hard for an attacker to scrub — which is exactly why compliance frameworks mandate it and why it's your forensic ground truth.

---

## 9. Harden a New Host — Checklist

```bash
# IDENTITY & ACCESS
useradd -r -s /usr/sbin/nologin <svc>      # dedicated user per service (§1)
find / -perm -4000 -type f 2>/dev/null     # audit setuid-root; remove what you can (§2)
getcap -r / 2>/dev/null                     # audit file capabilities (§4)

# SUDO
visudo -c                                   # validate sudoers; set env_reset+secure_path (§5)
#   grant exact commands per role in /etc/sudoers.d/, no bare (ALL) ALL

# MAC
getenforce                                  # SELinux Enforcing (or AppArmor enforce) (§6)
#   fix denials via labels/booleans — never disable

# SSH
#   PermitRootLogin no, PasswordAuthentication no, AllowGroups, ed25519/cert auth (§7)
sshd -t && systemctl reload sshd

# AUDIT
systemctl enable --now auditd               # + rules for /etc/passwd,/etc/sudoers,privesc (§8)

# DEFAULTS
#   umask 027, lock unused accounts, patch cadence, central log shipping
```

Each line maps to a section above. Codify it in your config-management layer so every host is born hardened — drift, not the initial build, is where security dies.

---

> Next: [13 — Storage & Filesystem Operations](13_storage_filesystems.md) — block devices, LVM, filesystems, mount options (`noexec`/`nosuid`/`nodev` as security primitives), and keeping data alive under load.
