# 14 — Packages, Builds & Dynamic Linking

> **Audience:** Staff/principal engineers who build, package, and ship software on Linux at scale — and who get paged when a binary that ran fine in CI dies in prod with `error while loading shared libraries`. This is the operational craft of package managers, source builds, and the runtime loader. When you lose a day to "works on my box," it's almost always in here.

---

## 1. The two package-manager families

Linux distros split into two camps: Debian/Ubuntu (`.deb`, `dpkg`/`apt`) and RHEL/Fedora/Amazon Linux (`.rpm`, `rpm`/`dnf`). Learn both — your laptop and your prod fleet are rarely the same.

```bash
# --- Debian/Ubuntu ---
dpkg -i ./foo_1.2_amd64.deb     # install a local .deb (no dep resolution)
apt-get install -f              # ...then fix the deps it pulled in
apt-get update && apt-get install nginx   # update lists, then install w/ deps
dpkg -l | grep nginx            # is it installed? what version?
dpkg -L nginx                   # WHAT FILES does this package install?
dpkg -S /usr/sbin/nginx         # WHICH PACKAGE owns this file?  -> nginx-core
apt-cache policy nginx          # candidate version + which repo it comes from

# --- RHEL/Fedora ---
rpm -i ./foo-1.2.x86_64.rpm     # install local rpm (no dep resolution)
dnf install nginx               # install with dep resolution (use this)
rpm -qa | grep nginx            # all installed pkgs matching nginx
rpm -q nginx                    # exact installed version
rpm -ql nginx                   # WHAT FILES does it install? (= dpkg -L)
rpm -qf /usr/sbin/nginx         # WHICH PACKAGE owns this file? (= dpkg -S)
rpm -V nginx                    # VERIFY: which files changed since install?
dnf history                     # transaction log; `dnf history undo <id>` rolls back
```

The two reverse lookups — **"what does this install?"** and **"who owns this file?"** — are the bread and butter of debugging a misbehaving system. Memorize the pairs.

### apt vs dnf cheat-sheet

| Task | Debian/Ubuntu | RHEL/Fedora |
|---|---|---|
| Install | `apt-get install X` | `dnf install X` |
| Remove | `apt-get remove X` | `dnf remove X` |
| Refresh metadata | `apt-get update` | (auto; `dnf makecache`) |
| Upgrade all | `apt-get upgrade` | `dnf upgrade` |
| Search | `apt-cache search X` | `dnf search X` |
| List installed | `dpkg -l` | `rpm -qa` |
| Files in pkg | `dpkg -L X` | `rpm -ql X` |
| Owner of file | `dpkg -S /path` | `rpm -qf /path` |
| Verify integrity | `debsums X` | `rpm -V X` |
| Local cache | `/var/cache/apt/archives` | `/var/cache/dnf` |

### Holds, pinning, and not getting surprised

```bash
# Pin a package so `apt upgrade` won't touch it (e.g. a kernel that boots):
apt-mark hold linux-image-generic
apt-mark showhold
apt-mark unhold linux-image-generic

# dnf equivalent:
dnf install python3-dnf-plugin-versionlock
dnf versionlock add nginx-1.24.0
```

apt repo priority lives in `/etc/apt/preferences.d/` (Pin-Priority); dnf priorities come from the `priority=` line per-repo in `/etc/yum.repos.d/*.repo`.

---

## 2. Repos, GPG signing & reproducibility

Packages are served from repos whose metadata is GPG-signed; the client verifies signatures against trusted keys before installing. **Never** disable signature checks to "make it work."

```bash
# Modern apt: keys live as files, referenced per-repo (no more apt-key)
curl -fsSL https://example.com/key.gpg | gpg --dearmor \
  | sudo tee /etc/apt/keyrings/example.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/example.gpg] https://repo.example.com stable main" \
  | sudo tee /etc/apt/sources.list.d/example.list

# dnf: gpgcheck=1 and a gpgkey= URL per repo in /etc/yum.repos.d/example.repo
rpm --import https://repo.example.com/RPM-GPG-KEY-example
```

**Why reproducibility matters.** "Install the latest nginx" is not reproducible — the result depends on the day you ran it. At scale you want:

- **Pinned versions** in your provisioning (`nginx=1.24.0-1ubuntu1`), not floating ranges.
- **A private mirror / pull-through cache** so an upstream repo going down (or mutating a version in place) can't break a deploy.
- **Lockfiles** at the language layer (see §8) and image digests at the container layer (see §7).

The failure mode this prevents: a rebuild six months later that silently pulls newer transitive deps and behaves differently. Pin everything that can be pinned.

---

## 3. Building from source

The classic autotools dance, and the one mistake that fights your package manager.

```bash
# Pull build dependencies declaratively (don't guess -dev packages):
sudo apt-get build-dep ./        # Debian, from debian/control
sudo dnf builddep foo.spec       # Fedora, from a spec file

./configure --prefix=/usr/local  # where it WILL live at runtime
make -j"$(nproc)"
sudo make install                # copies into --prefix
```

`pkg-config` is how `./configure` and Makefiles discover library flags — query it directly when a build can't find a lib:

```bash
pkg-config --cflags --libs libssl   # -I.../  -lssl -lcrypto
pkg-config --modversion libssl      # 3.0.2
# It reads .pc files; set PKG_CONFIG_PATH if yours live in a nonstandard prefix.
```

### DESTDIR staging vs install prefix

`--prefix` is baked into the binary (it's where the program looks for its data at runtime). `DESTDIR` is a *staging* relocation used at `make install` time only — perfect for building a package:

```bash
./configure --prefix=/usr        # runtime prefix = /usr
make
make DESTDIR=/tmp/stage install  # installs to /tmp/stage/usr/... but binary still thinks /usr
```

### `make install` into /usr is the trap

```bash
# WRONG — overwrites package-manager-owned files; `dpkg -S` won't know about them,
# upgrades collide, removal leaves orphans:
./configure --prefix=/usr && sudo make install

# RIGHT (quick & local) — /usr/local is reserved for exactly this and is on PATH:
./configure --prefix=/usr/local && sudo make install

# RIGHT (proper) — build a REAL package so the PM tracks every file:
sudo checkinstall                # wraps `make install`, emits a .deb/.rpm, installs it
# ...or fpm, or a debian/ dir + dpkg-buildpackage, or an rpmbuild spec.
```

Rule of thumb: **anything that outlives a debugging session should be a tracked package.** If `dpkg -S`/`rpm -qf` can't tell you who owns a file in `/usr`, future-you is debugging blind.

---

## 4. Static vs dynamic linking — the model

A `.so` ("shared object") is a library loaded at runtime and shared across processes. Static linking copies the code into your binary at build time instead.

| | Static (`.a` → in binary) | Dynamic (`.so` at runtime) |
|---|---|---|
| Binary size | Large (everything bundled) | Small |
| RAM across procs | Each proc has its own copy | One copy, shared pages |
| Security patch to libssl | **Rebuild every binary** | Patch the `.so`, restart |
| Portability | Excellent (self-contained) | Fragile (needs matching `.so`s) |
| `dlopen`, NSS plugins | Quirky/broken with static glibc | Works |
| Startup | No symbol resolution | Loader does relocation work |

The tension this whole chapter is about: dynamic linking is great for *patching and memory*, terrible for *portability*. Static linking flips it.

### soname versioning

A library carries a **soname** that encodes ABI compatibility:

```
libfoo.so.1            <- SONAME (ABI version; bump only on breaking change)
libfoo.so.1.2.3        <- the real file (full version)
libfoo.so              <- dev symlink, used only at LINK time (-lfoo)
```

So `libfoo.so.1 -> libfoo.so.1.2.3` (created/refreshed by `ldconfig`) and `libfoo.so -> libfoo.so.1` (provided by the `-dev`/`-devel` package). Your binary records a `NEEDED` entry of `libfoo.so.1` — the soname, not the full filename — so any 1.x can satisfy it.

---

## 5. The runtime loader `ld.so`

When you `exec` a dynamically linked binary, the kernel hands control to the *interpreter* named in the ELF (`/lib64/ld-linux-x86-64.so.2`), which finds and maps every `NEEDED` library before `main` runs. (See [../../os_net/operating_system/01_processes_threads.md](../../os_net/operating_system/01_processes_threads.md) for exec/loader mechanics.)

### Search order (first match wins)

| # | Source | Set by | Notes |
|---|---|---|---|
| 1 | `DT_RPATH` | `-Wl,-rpath` (legacy) | **Deprecated**; can't be overridden by env |
| 2 | `LD_LIBRARY_PATH` | environment | Override knob — handy to debug, fragile to ship |
| 3 | `DT_RUNPATH` | `-Wl,-rpath` (new default) | Like RPATH but searched *after* LD_LIBRARY_PATH |
| 4 | `/etc/ld.so.cache` | `ldconfig` | The fast path for system libs |
| 5 | `/lib`, `/usr/lib` (+`64`) | hardcoded defaults | Last resort |

```bash
ldconfig                       # rebuild /etc/ld.so.cache after adding libs
cat /etc/ld.so.conf.d/*.conf   # extra dirs added to the cache search
ldconfig -p | grep libssl      # QUERY the cache: which libssl.so.N is registered, where
```

Add a new library dir the *right* way (not via `LD_LIBRARY_PATH`):

```bash
echo /opt/myapp/lib | sudo tee /etc/ld.so.conf.d/myapp.conf
sudo ldconfig                  # now it's in the cache for all processes
```

---

## 6. Inspecting binaries: the toolbox

```bash
# What libraries does it need, and do they resolve?
ldd /usr/bin/curl
#   libcurl.so.4 => /lib/x86_64-linux-gnu/libcurl.so.4 (0x00007f...)
#   libssl.so.3  => not found        <-- THIS is your problem

# SECURITY: ldd may *run* the target's loader logic. Never ldd an untrusted binary.
# Safe alternative — just read the headers, executes nothing:
readelf -d /usr/bin/curl | grep -E 'NEEDED|RPATH|RUNPATH|SONAME'
objdump -p /usr/bin/curl | grep -E 'NEEDED|PATH|SONAME'   # same info, objdump

# Symbols: does this .so actually export the symbol I'm missing?
nm -D --defined-only /lib/.../libfoo.so.1 | grep my_symbol
readelf -Ws /lib/.../libfoo.so.1 | grep my_symbol         # incl. version tags

# Trace the loader's decisions live — gold for "why did it pick THAT lib?":
LD_DEBUG=libs   ./myapp 2>&1 | head        # search paths tried per library
LD_DEBUG=help   ./myapp                    # list all LD_DEBUG categories
```

---

## 7. The classic failures (Symptom / Cause / Fix)

### "cannot open shared object file"

```
./myapp: error while loading shared libraries: libfoo.so.1:
cannot open shared object file: No such file or directory
```

- **Symptom:** Binary won't start; one `NEEDED` lib doesn't resolve (confirm with `ldd`).
- **Cause:** The `.so` isn't installed, or it's installed somewhere not in `ld.so.cache`.
- **Fix:** Install the providing package (`dnf provides '*/libfoo.so.1'` / `apt-file search libfoo.so.1`); **or** if it's in a custom dir, add it via `/etc/ld.so.conf.d/` + `ldconfig`; only as a last-resort temporary probe, `LD_LIBRARY_PATH=/opt/foo/lib ./myapp`.

### "version `GLIBC_2.34' not found" — the #1 portability disaster

```
./myapp: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.34' not found
(required by ./myapp)
```

- **Symptom:** Binary built on a newer distro fails on an older one.
- **Cause:** glibc is **backward** compatible, not **forward** compatible. You linked against symbols that only exist in a glibc newer than the target's. Building on Ubuntu 24.04 and running on RHEL 8 is the canonical trigger.
- **Fix (in order of preference):**
  1. **Build on the OLDEST distro you must support** (or in a container based on it). This is the single most reliable fix and the one principals should institutionalize in CI.
  2. Statically link, or build against **musl** (see §8) to drop the glibc dependency entirely.
  3. Ship the userland with the binary (container, §7-containers below) so the glibc you built against is the glibc that runs.

```bash
# Diagnose which glibc version your binary demands vs what the host has:
objdump -T ./myapp | grep -oE 'GLIBC_[0-9.]+' | sort -uV | tail -1   # max required
ldd --version | head -1                                              # host's glibc
```

### symbol versioning conflicts

- **Symptom:** `undefined symbol: foo, version BAR` at load time.
- **Cause:** A `.so` on the host is older/newer than the one you linked against; the versioned symbol your binary wants isn't present. Common with mixing distro libs and self-built libs in the same `LD_LIBRARY_PATH`.
- **Fix:** Stop mixing. Resolve to one consistent set of libraries; rebuild against the exact lib you'll deploy.

### The `LD_LIBRARY_PATH` anti-pattern

```bash
# WRONG — wrapper script that bakes in an env var:
LD_LIBRARY_PATH=/opt/app/lib exec /opt/app/bin/app   # leaks to children, masks
                                                      # system libs, breaks on path moves

# RIGHT — bake the path into the binary at LINK time as RUNPATH:
gcc main.c -o app -L/opt/app/lib -lfoo \
    -Wl,-rpath,'$ORIGIN/../lib'      # relative to the binary; relocatable!
readelf -d ./app | grep RUNPATH      # verify: RUNPATH  [$ORIGIN/../lib]
```

`$ORIGIN` resolves to the binary's own directory at runtime — so the app finds its bundled libs no matter where the install tree is moved. Far more robust than any env var.

---

## 8. `patchelf` — fix a prebuilt binary

When you can't rebuild (vendor blob, prebuilt wheel), edit the ELF in place:

```bash
patchelf --print-rpath ./app                       # inspect
patchelf --set-rpath '$ORIGIN/../lib' ./app        # fix where it looks for .so's
patchelf --set-interpreter /lib64/ld-linux-x86-64.so.2 ./app  # fix the loader path
patchelf --replace-needed libold.so.1 libnew.so.1 ./app
```

This is the standard tool behind relocatable Python wheels (`auditwheel`) and Nix/Conda binary patching. Indispensable when "I have the binary but not the source."

---

## 9. glibc vs musl, and the static escape hatch

glibc-version hell (§7) exists because nearly every dynamically linked binary depends on a specific glibc. Two ways out:

- **musl libc** (Alpine Linux): small, static-friendly libc. A musl-static binary has *no* libc version dependency — it runs on any kernel new enough. The cost: glibc-specific behavior differs, and **static glibc `dlopen`/NSS is broken** (name resolution via `/etc/nsswitch.conf` plugins needs dynamic loading), so static *glibc* binaries can silently fail DNS/user lookups. Static *musl* sidesteps most of this.
- **Go and Rust static binaries** are the modern escape hatch. `CGO_ENABLED=0 go build` produces a fully static binary with no libc at all — copy it to a `scratch` container or a 5-year-old host and it just runs.

```bash
CGO_ENABLED=0 go build -o app .         # pure-Go static binary, zero .so deps
file app                                # ... statically linked
ldd  app                                # "not a dynamic executable"

# Rust against musl for a static binary:
rustup target add x86_64-unknown-linux-musl
cargo build --release --target x86_64-unknown-linux-musl
```

| Strategy | glibc-version risk | dlopen/NSS | Patchable libssl |
|---|---|---|---|
| Dynamic glibc | High | Works | Yes (best) |
| Static glibc | None | **Broken** | No (rebuild) |
| Static musl | None | OK | No (rebuild) |
| Go/Rust static | None | N/A | No (rebuild) |

The trade-off is always the same: static buys portability and pays in patch-velocity (a libssl CVE means rebuilding and redeploying every static binary, not patching one `.so`).

---

## 10. Containers as the packaging answer

A container image pins the *entire userland* — your binary **and** the exact glibc, libssl, and config it was built against — into one immutable, content-addressed artifact. That makes "version `GLIBC_2.34' not found" structurally impossible: the glibc you built on is the glibc that runs.

```bash
# Pin by DIGEST, not tag — `:latest` and even `:1.24` are mutable:
FROM ubuntu:24.04@sha256:abc123...      # reproducible base
# Multi-stage: build with toolchain, ship only the artifact + its runtime libs
```

Containers don't repeal linking rules — they pin the inputs so the rules stop biting you. The build-time disciplines from this chapter (oldest-target builds, RUNPATH, static linking) still apply *inside* the image. See [../../os_net/operating_system/07_virtualization_containers.md](../../os_net/operating_system/07_virtualization_containers.md) for the namespace/cgroup mechanics.

**Language package managers live above the OS one.** `pip`, `npm`, `cargo`, `go mod`, `maven` resolve *application* deps; `apt`/`dnf` resolve *system* deps (including the compilers and `-dev` headers the language tools need to build native extensions). They are complementary layers, and each needs its own lockfile (`requirements.txt`/`poetry.lock`, `package-lock.json`, `Cargo.lock`, `go.sum`) for reproducibility.

---

## 11. Runbook: "runs on my box, fails on prod with a .so error"

Work top to bottom; stop when you find the break.

```bash
# 1. What exactly is missing/wrong? (run ON PROD, on the failing binary)
ldd ./app | grep -E 'not found'                  # missing NEEDED libs
./app 2>&1 | grep -E 'GLIBC_|error while loading|undefined symbol'

# 2. If "GLIBC_x.yy not found" -> version skew. Compare demand vs host:
objdump -T ./app | grep -oE 'GLIBC_[0-9.]+' | sort -uV | tail -1
ldd --version | head -1
#    Demand > host?  -> rebuild on the oldest target distro, or go static (§9).

# 3. If "cannot open shared object" -> the lib exists somewhere, or doesn't:
ldconfig -p | grep libfoo                        # is it in the cache?
apt-file search libfoo.so.1 ; dnf provides '*/libfoo.so.1'   # who provides it?
#    Present but uncached -> add /etc/ld.so.conf.d + ldconfig.
#    Absent -> install the package (pin the version!).

# 4. If "undefined symbol ... version" -> ABI skew between linked & installed lib:
readelf -d ./app | grep NEEDED                   # what soname does it want?
ldconfig -p | grep <that-soname>                 # what version is actually present?
#    Reconcile to ONE consistent lib set; rebuild if needed.

# 5. Still mysterious? Watch the loader make every decision:
LD_DEBUG=libs ./app 2>&1 | grep -E 'trying|found|needed'

# 6. Confirm the box itself is sane (prod vs your box differ how?):
dpkg -l | sort > /tmp/prod.pkgs        # diff against your dev box's list
```

The meta-lesson: **the bug is almost never in your code — it's in the gap between your build environment and prod.** Close that gap (oldest-target builds, RUNPATH not env vars, pinned/static/containerized userland) and the .so pages stop coming.

---

> Next: [15 — User-Space Debugging](15_debugging_gdb_coredumps.md) — when the binary *loads* fine but misbehaves: attaching `gdb`, reading core dumps, decoding stack traces with symbols, and the debuginfo packages that make a backtrace readable. Cross-refs: [13 — Storage & Filesystem Operations](13_storage_filesystems.md) for where `/usr`, `/var/cache`, and overlay images actually live.
