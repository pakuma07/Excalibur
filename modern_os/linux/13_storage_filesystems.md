# 13 — Storage & Filesystem Operations

> **Audience:** Staff/principal engineers managing storage on Linux at FAANG scale. This is operational command-line craft: how to grow a volume online, triage a disk-full incident at 3 AM, and write an [fstab](https://man7.org/linux/man-pages/man5/fstab.5.html) that won't brick the box. The kernel/FS theory — journaling, fsync semantics, I/O schedulers, RAID levels — lives in the sibling [os_net file systems & storage reference](../../os_net/operating_system/05_file_systems_storage.md). We link to it and stay operational here.

---

## 1. The storage stack, top to bottom

Every block of data climbs a stack. Know which layer you're at before you touch anything.

```
physical disk (/dev/nvme0n1, /dev/sda)
  └─ partition        (/dev/nvme0n1p1)          ← parted/gdisk/fdisk
       └─ LVM PV → VG → LV  (/dev/vg0/data)     ← pvcreate/vgcreate/lvcreate
            └─ filesystem    (ext4, xfs)        ← mkfs.*
                 └─ mount     (/data)           ← mount, /etc/fstab
```

```bash
# The single most useful command: tree of block devs + FS info
lsblk -f
# NAME        FSTYPE      LABEL  UUID                                 MOUNTPOINT
# nvme0n1
# ├─nvme0n1p1 vfat        EFI    1234-ABCD                            /boot/efi
# └─nvme0n1p2 LVM2_member        kx7Yz9-...                           
#   ├─vg0-root xfs        root   a1b2c3d4-...                         /
#   └─vg0-data ext4       data   e5f6a7b8-...                         /data

blkid /dev/vg0/data        # UUID + type for ONE device (reads on-disk superblock)
findmnt /data              # what's mounted here, with options (parses /proc/self/mountinfo)
findmnt --verify           # sanity-check /etc/fstab BEFORE you trust it

df -h                      # space by mountpoint, human units
df -i                      # INODES by mountpoint — the forgotten half of "disk full"
cat /proc/mounts           # ground truth: what the kernel actually has mounted
```

`lsblk -f` first, always. It collapses the whole stack into one view so you can see whether `/dev/sdb` is raw, partitioned, an LVM PV, or already mounted.

---

## 2. Partitioning: GPT vs MBR

| | MBR (msdos) | GPT |
|---|---|---|
| Max disk | 2 TiB | ~9.4 ZB |
| Max partitions | 4 primary | 128 (typical) |
| Redundancy | none | dup header + CRC |
| Use when | legacy BIOS only | **everything modern** |

Use GPT unless something forces MBR. Use `parted` for scripting, `gdisk` for interactive GPT, `fdisk` for muscle memory.

```bash
# parted, non-interactive (note: parted writes IMMEDIATELY — no "w" to commit)
parted -s /dev/sdb mklabel gpt
parted -s /dev/sdb mkpart primary 0% 100%   # 0%/100% = let parted ALIGN to MiB boundary
parted /dev/sdb align-check optimal 1        # "1 aligned" — confirm 1 MiB / 4K alignment

# WRONG: starting at sector 34 or "1MB" hand-picked → misaligned, RMW penalty on SSD/RAID
# RIGHT: use 0%/100% (or 1MiB) and let the tool align to the physical block size

partprobe /dev/sdb          # tell the kernel to re-read the partition table…
# …if partprobe fails "device busy" (mounted partitions in use), the new table
# isn't visible until reboot. Avoid by partitioning before mounting.
```

For growing a partition in place (e.g. after expanding a cloud disk), `growpart` (from cloud-utils) is safer than hand-editing:

```bash
growpart /dev/nvme0n1 2      # grow partition 2 to fill the disk, alignment-safe
```

---

## 3. LVM — the daily workhorse

LVM decouples filesystems from physical disks. This is what makes **online growth** possible and is the layer you'll touch most. Three nested objects:

| Layer | Object | Create | Inspect |
|---|---|---|---|
| Physical | **PV** (physical volume) | `pvcreate /dev/sdb` | `pvs`, `pvdisplay` |
| Pool | **VG** (volume group) | `vgcreate vg0 /dev/sdb` | `vgs`, `vgdisplay` |
| Virtual disk | **LV** (logical volume) | `lvcreate -L 50G -n data vg0` | `lvs`, `lvdisplay` |

```bash
# Build a stack from a fresh disk
pvcreate /dev/sdb                       # mark /dev/sdb as an LVM PV
vgcreate vg0 /dev/sdb                    # create VG "vg0" on it
lvcreate -L 50G -n data vg0              # carve a 50G LV → /dev/vg0/data
mkfs.xfs /dev/vg0/data                   # put a filesystem on it
```

### 3.1 Extending storage online (the move you'll make most)

Two independent steps that people forget are **separate**: grow the LV, then grow the **filesystem** on top of it.

```bash
# 1. Add a new disk into the existing VG (more free extents)
pvcreate /dev/sdc
vgextend vg0 /dev/sdc

# 2. Grow the LV AND the filesystem in one shot with -r (--resizefs)
lvextend -r -L +50G /dev/vg0/data
#        ^^ -r runs resize2fs (ext4) or xfs_growfs (xfs) for you, online

# Without -r you do it manually AFTER lvextend:
lvextend -L +50G /dev/vg0/data
resize2fs /dev/vg0/data        # ext4
xfs_growfs /data               # xfs — takes the MOUNTPOINT, grows online only
```

> **Symptom:** "I added a disk but the volume is still full."
> **Cause:** You did one or two of the three steps. Adding a physical disk does nothing on its own.
> **Fix:** `vgextend` (disk → VG) → `lvextend` (VG → LV) → resize the FS (`-r`, or `resize2fs`/`xfs_growfs`). Verify each with `vgs`, `lvs`, `df -h`. See the runbook in §10.

### 3.2 Snapshots — consistent point-in-time backups

```bash
# Snapshot for a clean backup of a busy LV (writes diverge into the 5G CoW area)
lvcreate -s -L 5G -n data_snap /dev/vg0/data
mount -o ro,nouuid /dev/vg0/data_snap /mnt/snap   # nouuid: XFS refuses dup UUIDs
tar czf /backup/data.tgz -C /mnt/snap .
umount /mnt/snap && lvremove -f /dev/vg0/data_snap
# WARNING: a thick snapshot that fills its CoW space goes INVALID. Size it for
# expected write churn during the backup window, then delete it promptly.
```

### 3.3 Thin provisioning

```bash
lvcreate -L 100G --thinpool tp vg0          # a thin pool
lvcreate -V 500G -T vg0/tp -n bigvol        # 500G LV backed by a 100G pool (overcommit)
# Powerful but DANGEROUS: monitor pool usage (lvs -o data_percent). A full thin
# pool = I/O errors for every LV in it. Configure autoextend or alert at 80%.
```

---

## 4. Filesystems in practice — ext4 vs XFS

| | ext4 | XFS |
|---|---|---|
| Default on | Debian/Ubuntu | **RHEL/Fedora/Amazon Linux** |
| Grow online | yes (`resize2fs`) | yes (`xfs_growfs`) |
| **Shrink** | **yes, offline** (`resize2fs`) | **NEVER — cannot shrink** |
| Best at | general, small files | large files, high parallelism |
| Reserve blocks | 5% root-reserved (tunable) | minimal |

The shrink asymmetry bites people: **XFS cannot shrink, ever.** If you overprovision an XFS LV there is no undo short of backup → recreate smaller → restore. On ext4 you can shrink, but only **unmounted**.

```bash
mkfs.ext4 -L data /dev/vg0/data            # -L sets a label
mkfs.xfs  -L data /dev/vg0/data            # XFS, labeled

# Resize
xfs_growfs /data                           # XFS: grow only, online, by MOUNTPOINT
resize2fs /dev/vg0/data 80G                # ext4 grow online; SHRINK requires unmount + e2fsck:
umount /data && e2fsck -f /dev/vg0/data && resize2fs /dev/vg0/data 30G

# Labels & UUIDs (use these in fstab, NOT /dev/sdX)
xfs_admin -L data -U generate /dev/vg0/data   # set XFS label / new UUID
tune2fs -L data /dev/vg0/data                 # set ext4 label
tune2fs -U random /dev/vg0/data               # new UUID (after a clone, to break dup UUID)
tune2fs -m 1 /dev/vg0/data                    # drop root-reserve 5% → 1% on a big data vol
tune2fs -l /dev/vg0/data                      # dump ext4 superblock
```

**Btrfs / ZFS** add copy-on-write, built-in snapshots, checksums, and integrated volume management (no LVM needed). They're operationally different beasts — for the CoW/snapshot/checksum theory and when to choose them, see [os_net file systems & storage](../../os_net/operating_system/05_file_systems_storage.md).

---

## 5. Mounting & /etc/fstab

`/etc/fstab` fields, left to right:

```
# <device>            <mountpoint> <fstype> <options>                  <dump> <pass>
UUID=e5f6a7b8-...      /data        ext4     defaults,noatime,nofail    0      2
LABEL=root             /            xfs      defaults                   0      1
/dev/vg0/swap          none         swap     sw                         0      0
nfs.internal:/exports  /mnt/nfs     nfs      defaults,_netdev,nofail    0      0
```

> **Always mount by `UUID=` or `LABEL=`, never `/dev/sdX`.** Kernel device names reorder across reboots and disk additions — `/dev/sdb` today is `/dev/sdc` tomorrow, and your fstab now mounts the wrong disk (or fails to boot). UUID/LABEL are stable, tied to the filesystem itself.

Key mount options:

| Option | Effect |
|---|---|
| `defaults` | rw, suid, dev, exec, auto, nouser, async |
| `noatime` | don't write access timestamps — real I/O win on busy FS |
| `nodev` | ignore device files (defense in depth) |
| `nosuid` | ignore setuid bits — see [12 — Linux Security & Access Control](12_security_access_control.md) |
| `noexec` | forbid execution from this FS (e.g. `/tmp`, upload dirs) |
| `_netdev` | network FS — wait for network before mounting |
| `nofail` | **don't block boot if this device is missing** |

### Test BEFORE you reboot

```bash
mount -a                # mount everything in fstab not yet mounted — fstab smoke test
findmnt --verify        # static validation of fstab (bad UUIDs, dup mountpoints, etc.)
```

> **Symptom:** Box won't boot, drops to emergency shell after an fstab edit.
> **Cause:** A bad fstab line (typo'd UUID, missing device). By default systemd waits for every entry, and a failed mount blocks boot.
> **Fix:** Add `nofail` to non-critical mounts so a missing disk degrades instead of bricking. **Always** run `mount -a` and `findmnt --verify` after editing fstab, before rebooting. Recovery: boot to emergency, `mount -o remount,rw /`, fix the line.

### systemd .mount / .automount — the modern alternative

systemd parses fstab into transient `*.mount` units, but you can author them directly for ordering, dependencies, and on-demand mounting. See [11 — systemd: Service Authoring & Operations](11_systemd_services.md).

```ini
# /etc/systemd/system/data.mount  (filename MUST match the escaped mountpoint: data → /data)
[Unit]
Description=Data volume
[Mount]
What=/dev/disk/by-uuid/e5f6a7b8-...
Where=/data
Type=ext4
Options=noatime,nofail
[Install]
WantedBy=multi-user.target
```

```ini
# data.automount — mount on first access, unmount when idle (great for NFS/rarely-used FS)
[Automount]
Where=/data
TimeoutIdleSec=600
```

---

## 6. Disk-full & inode-exhaustion triage

The classic page. First question: **space or inodes?**

```bash
df -h /var          # bytes — "100% used"
df -i /var          # INODES — can be 100% even when df -h shows 40% free!
# Filesystem      Inodes  IUsed IFree IUse%
# /dev/vg0/var    6.5M    6.5M  0     100%   ← millions of tiny files (mail, sessions, caches)
```

> **Symptom:** "No space left on device" but `df -h` shows free space.
> **Cause:** Inode exhaustion — too many files, not too many bytes.
> **Fix:** Find the offender directory and clean it; ext4 inode count is fixed at `mkfs` time, so a permanent fix may mean recreating the FS with `mkfs.ext4 -N <count>` or `-i <bytes-per-inode>`. XFS allocates inodes dynamically — rarely an issue.

```bash
# Find the big directories (sorted, human)
du -sh /var/* 2>/dev/null | sort -h | tail
ncdu /var                              # interactive disk usage browser — best tool for this
find / -xdev -type f -size +1G -exec ls -lh {} +   # files >1G, stay on one FS (-xdev)
```

### The "df says full, du says not" gotcha — deleted-but-open files

When a process holds an open file descriptor to a file someone `rm`'d, the space is **not freed** until the holder closes it. `du` walks the directory tree and can't see the unlinked inode; `df` counts it.

```bash
lsof +L1            # files with link count < 1 = deleted but still open
# COMMAND  PID  USER  FD   SIZE/OFF  NLINK  NODE NAME
# java    9123  app   24   18G       0      ...  /var/log/app.log (deleted)

# Fix without killing: truncate via the proc fd (frees space immediately)
: > /proc/9123/fd/24
# Or restart the holder (java here) if it owns the deleted log.
```

Common culprits: log/journal bloat. `journalctl --disk-usage`, then cap it:

```bash
journalctl --vacuum-size=500M        # or --vacuum-time=7d
```

For the full incident decision tree (I/O latency, throughput saturation, noisy neighbors), see [os_net I/O & storage incidents](../../os_net/enterprise_scenarios/02_io_storage_incidents.md).

---

## 7. Swap

```bash
free -h                              # see mem + swap at a glance
# Swapfile (flexible, easy to resize/remove — preferred on cloud):
fallocate -l 4G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab    # persist

# Partition swap:
mkswap /dev/vg0/swap && swapon /dev/vg0/swap
swapon --show                        # what's active

# Tune reclaim aggressiveness (0 = avoid swap, 100 = swap eagerly)
sysctl vm.swappiness=10              # then persist in /etc/sysctl.d/
```

Swap **masks** memory pressure: a box that's constantly swapping is usually under-provisioned RAM or has a leak, not "needing more swap." Adding swap trades OOM kills for latency death. Diagnose the memory problem — see [os_net memory management](../../os_net/operating_system/05_file_systems_storage.md).

---

## 8. mdadm software RAID — quick ops

LVM and cloud volumes have displaced mdadm for most workloads (cloud block storage is already redundant; LVM gives flexibility without the resync cost). You'll still meet it on bare metal. Theory/RAID levels live in [os_net storage](../../os_net/operating_system/05_file_systems_storage.md).

```bash
mdadm --create /dev/md0 --level=1 --raid-devices=2 /dev/sdb /dev/sdc   # RAID1 mirror
cat /proc/mdstat                     # live status + resync progress bar
mdadm --detail /dev/md0              # full state, which disks, which failed
mdadm --detail --scan >> /etc/mdadm.conf    # persist array def

# Failed-disk replace workflow
mdadm /dev/md0 --fail /dev/sdc       # mark failed (if not already)
mdadm /dev/md0 --remove /dev/sdc     # remove from array
# …physically swap the disk…
mdadm /dev/md0 --add /dev/sdd        # add replacement → auto-resync (watch /proc/mdstat)
```

---

## 9. Maintenance: fsck, fstrim, quotas, I/O stats

```bash
# fsck — NEVER on a mounted filesystem (corrupts live FS). Unmount or boot to rescue.
umount /data && fsck -y /dev/vg0/data          # ext4
xfs_repair /dev/vg0/data                        # XFS uses xfs_repair, also unmounted

# TRIM/discard for SSDs — reclaim freed blocks so the drive stays fast
fstrim -av                                       # trim all mounted FS now
systemctl enable --now fstrim.timer              # weekly trim (preferred over mount -o discard)

# Quotas (brief)
mount -o remount,usrquota,grpquota /home
quotacheck -cum /home && quotaon /home
edquota -u alice                                 # edit alice's soft/hard limits

# I/O observability — pointers; full coverage in os_net observability
iostat -xz 1                                     # per-device util%, await, throughput
iotop -o                                         # per-process I/O (only active)
```

---

## 10. Runbook — grow a full root/data volume safely

Ties §3 + §4 together. Scenario: `/data` (on `/dev/vg0/data`, XFS) is at 100%, and you've attached a new cloud disk as `/dev/sdc`.

```bash
# 0. CONFIRM it's space, not inodes, not a deleted-open-file holding space
df -h /data && df -i /data && lsof +L1 | grep /data

# 1. Snapshot first if the data matters and the LV allows it (rollback safety)
lvcreate -s -L 10G -n data_snap /dev/vg0/data

# 2. Make the new disk available to the VG
pvcreate /dev/sdc
vgextend vg0 /dev/sdc
vgs                                  # confirm VFree increased

# 3. Grow LV + filesystem in one online step
lvextend -r -l +100%FREE /dev/vg0/data    # take all new free extents, -r grows the FS
#   (-r → xfs_growfs runs automatically for XFS; online, no unmount)

# 4. Verify, then drop the snapshot once you trust the result
df -h /data
lvremove -f /dev/vg0/data_snap
```

Notes that save you:
- **XFS can't shrink** — don't overshoot. ext4 can shrink but only unmounted (§4).
- Always re-check `df -h` *after* the resize; if it didn't grow, you skipped the FS step.
- For the root volume specifically, growth is online too — no reboot needed for LVM+XFS/ext4. The danger is only in the partition/`growpart` step under the PV.

---

> Next: [14 — Packages, Builds & Dynamic Linking](14_packages_linking.md) — from where bytes live to how binaries run: package managers, dependency resolution, building from source, and the dynamic linker that decides which `.so` your process actually loads.
