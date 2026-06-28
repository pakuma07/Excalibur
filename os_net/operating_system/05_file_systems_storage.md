# 05 — File Systems & Storage

> **Audience:** staff/principal. You know how to open a file. This doc is about *what happens underneath* — from the `int fd` an application holds, down through the VFS, the page cache, the journal, the block layer, and onto spinning rust or NAND flash — and the durability and amplification trade-offs that decide whether your data actually survives a power loss.
>
> **Primary sources:** Kerrisk, *The Linux Programming Interface* (TLPI), ch. 4, 5, 13, 14, 18; Tanenbaum, *Modern Operating Systems* ch. 4; Silberschatz, *Operating System Concepts* ch. 11–14; Arpaci-Dusseau, *Operating Systems: Three Easy Pieces* (OSTEP) — "Persistence" chapters 36–44; the ext4, XFS, and ZFS documentation; the PostgreSQL "fsyncgate" thread (2018).

---

## 1. Why this matters at scale

Persistence is the one subsystem where a bug is *unrecoverable*. A miscomputed cache value is annoying; a write that the application believes is durable but that evaporates on power loss is a silent data-corruption incident — the most expensive class of bug in any data system.

Three questions decide whether your storage layer is correct and fast:

1. **What is the abstraction?** A process holds a file descriptor — a small integer. Underneath, the kernel maintains three layers of indirection (descriptor table → open file table → inode). Understanding those layers explains `dup2`, why `fork` shares file offsets, and why `O_APPEND` is atomic but a `write()` of 1 MiB may not be.
2. **When is a write durable?** `write()` returning success means *the kernel has the bytes*, not *the disk has the bytes*. Durability requires `fsync`/`fdatasync`, and even that has been a graveyard of bugs (fsyncgate 2018). At scale, durability is a property of the **entire storage stack**, not just your code.
3. **What does the I/O cost?** Random vs sequential, buffered vs `O_DIRECT`, the I/O scheduler, the media (HDD seek vs SSD erase-block vs NVMe queue depth) — these set your throughput and tail-latency envelope and your flash wear (write amplification).

Everything a database, message broker, or object store does sits on top of these answers.

```
   Application:   fd = open("/data/log", O_WRONLY|O_APPEND);  write(fd, buf, n);
        |
        v
   ┌──────────────────────────────────────────────────────────┐
   │  System call layer  (open/read/write/fsync)                │
   ├──────────────────────────────────────────────────────────┤
   │  VFS  — generic inode / dentry / file objects              │
   ├──────────────────────────────────────────────────────────┤
   │  Page cache  — dirty pages, write-back, readahead          │
   ├──────────────────────────────────────────────────────────┤
   │  Filesystem  — ext4 / XFS / Btrfs / ZFS  (+ journal/CoW)   │
   ├──────────────────────────────────────────────────────────┤
   │  Block layer  — bio, request queue, I/O scheduler          │
   ├──────────────────────────────────────────────────────────┤
   │  Device driver  — NVMe / SCSI / SATA                       │
   ├──────────────────────────────────────────────────────────┤
   │  Storage media  — HDD platter / SSD NAND / NVMe            │
   └──────────────────────────────────────────────────────────┘
```

---

## 2. The file abstraction & file descriptors

A **file descriptor (fd)** is a per-process, non-negative integer index. By convention 0/1/2 are stdin/stdout/stderr. `open()` returns the **lowest-numbered unused** descriptor — a guarantee that makes the classic `close(1); dup(fd)` redirection trick work.

### 2.1 The three tables

The integer is just the top of a chain of three kernel data structures (TLPI §5.4):

```
  Process A                         Open file table              Inode table
  fd table (per-process)            (system-wide)                (system-wide)
  ┌────┬──────────┐                 ┌─────────────────┐          ┌──────────────┐
  │ fd │ flags    │   ──────────►   │ offset          │  ──────► │ inode (file  │
  │  3 │ (FD_CLOEXEC)               │ status flags    │          │  metadata +  │
  └────┴──────────┘                 │ (O_APPEND,...)  │          │  block map)  │
                                    │ inode pointer   │          └──────────────┘
  Process B                         └─────────────────┘                ▲
  ┌────┬──────────┐                 ┌─────────────────┐                │
  │  4 │          │  ──────────►    │ offset          │  ──────────────┘
  └────┴──────────┘                 └─────────────────┘
```

- **Descriptor table** (one per process): maps fd → open file table entry. Holds the `close-on-exec` flag.
- **Open file description (OFD)** (system-wide): holds the **file offset** and the **status flags** (`O_APPEND`, `O_NONBLOCK`, …). This is the entry `dup()`/`fork()` *share*.
- **Inode** (system-wide): the file's metadata and the map from logical offset to physical disk blocks.

This layering explains real behaviour:

| Scenario | What is shared | Consequence |
|---|---|---|
| `fork()` | Child & parent share the **same OFD** | They share the offset — interleaved writes advance one cursor (intentional for shell pipelines). |
| `dup()` / `dup2(fd, 1)` | New fd → **same OFD** | Shared offset; this is how shells do `>` redirection. |
| Two separate `open()`s of the same file | Distinct OFDs, **same inode** | Independent offsets — they will clobber each other's data. |
| `O_APPEND` | Flag on the OFD | The seek-to-end-and-write is **atomic** per `write()` — the basis of safe concurrent log appends. |

> Staff-level gotcha: `O_APPEND` guarantees atomicity of a *single* `write()` against the file size on a local filesystem, but **not over NFS** (no atomic append primitive) and **not for a partial write** if the buffer exceeds what the kernel writes in one go. For an append-only log you must check the return value and loop, or keep records ≤ `PIPE_BUF`/page-sized.

---

## 3. The VFS layer

Linux supports dozens of filesystems behind one API. The **Virtual File System (VFS)** is the abstract, object-oriented indirection layer that makes `read()` work identically on ext4, XFS, NFS, or `/proc` (Tanenbaum §4.3.4; the VFS is modeled on Sun's vnode/VFS architecture).

The four key VFS objects:

| Object | Represents | Key methods |
|---|---|---|
| **superblock** | a mounted filesystem | `alloc_inode`, `sync_fs`, `statfs` |
| **inode** | a file (data is irrelevant to the *name*) | `create`, `lookup`, `link`, `unlink`, `mkdir` |
| **dentry** | a directory entry — a name→inode binding, cached | `d_compare`, `d_hash` |
| **file** | an open file (an OFD, basically) | `read`, `write`, `mmap`, `fsync` |

When you call `read(fd, …)`, the VFS dispatches through the `file_operations` table the filesystem registered. The filesystem fills pages from disk (or returns them from the page cache). The **dentry cache (dcache)** caches path-component lookups so that resolving `/usr/lib/x/y` doesn't hit the disk inode for every component on every access.

```
  open("/var/log/app.log")
     → path walk, one component at a time, via dcache:
        "/"      → root inode (cached)
        "var"    → dentry lookup → inode
        "log"    → dentry lookup → inode
        "app.log"→ dentry lookup → inode  → allocate file object + fd
```

The VFS is why `cat /proc/cpuinfo` works: `procfs` implements the same `file_operations` interface but generates content on the fly. *Everything is a file* is enforced here.

---

## 4. Inodes & directory entries

The **inode** (index node) holds *everything about a file except its name and its data*: type & permission bits, owner/group, size, timestamps (atime/mtime/ctime), link count, and the pointers to the data blocks. The name lives in a **directory**, which is just a special file whose contents are a list of `(name, inode number)` pairs — the directory *entries*.

```
  Directory "/home/p" data blocks            Inode table
  ┌───────────────┬──────────┐               ┌────────────────────────────┐
  │  name         │ inode #  │               │ inode 8231:                │
  ├───────────────┼──────────┤               │   mode=0644 type=regular   │
  │  "."          │ 8230     │               │   uid=1000 size=4096       │
  │  ".."         │ 12       │   ──────────► │   nlink=2                  │
  │  "notes.txt"  │ 8231     │               │   blocks=[4012, 4013, ...] │
  │  "report.pdf" │ 8244     │               │   atime/mtime/ctime        │
  └───────────────┴──────────┘               └────────────────────────────┘
```

Crucial consequence: **the filename is not in the inode**. A file is identified on disk by its inode number, not its path. This is the foundation for the next section.

You can see the indirection from userspace:

```c
/* statx_demo.c — show that name and inode are separate.
   Build:  cc -O2 -o statx_demo statx_demo.c
   Run:    ./statx_demo /etc/hostname  */
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s PATH\n", argv[0]); return 2; }
    struct stat st;
    if (lstat(argv[1], &st) == -1) { perror("lstat"); return 1; }
    printf("path        : %s\n", argv[1]);
    printf("inode       : %lu\n", (unsigned long) st.st_ino);
    printf("link count  : %lu\n", (unsigned long) st.st_nlink);
    printf("size        : %lld bytes\n", (long long) st.st_size);
    printf("blocks (512): %lld\n", (long long) st.st_blocks);
    printf("is symlink  : %s\n", S_ISLNK(st.st_mode) ? "yes" : "no");
    return 0;
}
```

---

## 5. Hard vs symbolic links

Because names and inodes are decoupled, a single inode can have **many names**. That is a hard link.

| | **Hard link** | **Symbolic (soft) link** |
|---|---|---|
| What it is | A second directory entry pointing at the **same inode** | A tiny file whose *contents* are a pathname |
| Inode | Shared; `nlink` counts the names | Separate inode (type = symlink) |
| Cross-filesystem? | **No** (inode numbers are per-fs) | **Yes** |
| Link to a directory? | No (would create cycles) | Yes |
| Survives target rename/delete? | Yes — data lives until `nlink` hits 0 | **No** — becomes a dangling link |
| Cost to follow | Free (it *is* the inode) | Extra path resolution per access |

```
  HARD LINK                              SYMLINK
  "a.txt" ─┐                             "link" ── inode(symlink)
           ├─► inode 8231 (nlink=2)              contents: "a.txt"
  "b.txt" ─┘    [data blocks]                          │ resolved at access time
                                                       ▼
                                          "a.txt" ──► inode 8231 [data]
```

This is why **`unlink()` is named that way**: deleting a file *decrements the link count*; the data is only freed when `nlink == 0` **and** no process holds the file open. The latter clause is the classic "deleted but disk still full" situation — a process holds an fd to an unlinked file; `df` shows space used, `du` does not, until the process closes the fd. It is also the basis of the **secure temp file** idiom: `open()` then immediately `unlink()` — the file is invisible in the namespace but usable via the fd, and reaped automatically on process exit.

```bash
# Demonstrate hard-link semantics
echo "data" > a.txt
ln a.txt b.txt          # hard link; nlink becomes 2
stat -c '%h %i %n' a.txt b.txt   # same inode, link count 2
rm a.txt                # data survives — b.txt still readable
cat b.txt               # -> "data"
```

---

## 6. On-disk layout: superblock, inode table, data blocks, extents

A traditional Unix filesystem (ext2/3/4 lineage; OSTEP ch. 40) divides the device into fixed regions, replicated into **block groups** for locality and resilience:

```
  ext-family on-disk layout (per block group)
  ┌──────────┬───────────┬─────────────┬──────────────┬─────────────────────────┐
  │Super-    │ Group     │ Block       │ Inode        │  Data blocks            │
  │block     │ descriptor│ bitmap      │ bitmap +     │  (file contents +       │
  │(copy)    │ table     │             │ inode table  │   directory entries)    │
  └──────────┴───────────┴─────────────┴──────────────┴─────────────────────────┘
```

- **Superblock**: the filesystem's master record — block size, total inode/block counts, free counts, the fs state (clean/dirty), feature flags. So critical it is **replicated** across block groups; `fsck` can recover from a backup superblock.
- **Block / inode bitmaps**: one bit per block/inode, marking free vs used. Allocation = find a clear bit.
- **Inode table**: the fixed array of inodes. **The inode count is set at `mkfs` time** — you can run out of inodes (millions of tiny files) while having free space, and vice versa.
- **Data blocks**: file contents and directory entries.

### 6.1 Block maps vs extents

How does an inode map logical file offset → physical block?

- **Indirect block scheme (ext2/3, classic Unix):** the inode has ~12 *direct* pointers, then a *single-indirect* block (a block full of pointers), a *double-indirect*, and a *triple-indirect*. Elegant, but a large file needs many pointer blocks and metadata I/O scales poorly.

```
  inode ──► [d0][d1]...[d11]  direct (first 12 blocks)
        ──► [single indirect] ──► [ptr][ptr]... ──► data
        ──► [double indirect] ──► [ptr] ──► [ptr]... ──► data
        ──► [triple indirect] ──► ... (huge files)
```

- **Extents (ext4, XFS, Btrfs):** instead of per-block pointers, store `(start_block, length)` ranges. One extent can describe 128 MiB of contiguous data with a single record. Extents drastically reduce metadata for large, contiguous files and improve sequential I/O. ext4's `extent tree` is a B-tree of extents; XFS was extent-based from day one and is the choice for very large filesystems.

| Filesystem | Mapping | Max file/fs | Notable |
|---|---|---|---|
| ext4 | extents (B-tree) | 16 TiB / 1 EiB | journaling, default on many distros |
| XFS | extents (B+tree) | 8 EiB / 8 EiB | parallel allocation groups, great for large/streaming I/O |
| Btrfs | CoW extents | 16 EiB | snapshots, checksums, CoW |
| ZFS | CoW, variable block | 256 ZiB (theoretical) | checksums, RAID-Z, end-to-end integrity |

---

## 7. Journaling: write-ahead for metadata (and data)

A crash mid-update can leave the filesystem **inconsistent**: e.g., a block is marked used in the bitmap but no inode references it (leak), or worse, two files point at the same block (corruption). The pre-journaling fix was `fsck` scanning the *entire* disk on boot — minutes to hours on large volumes.

**Journaling** applies the write-ahead-log idea (OSTEP ch. 42): record what you're *about* to do in a circular **journal** and `fsync` that, then perform the real update. After a crash, replay the journal — bounded, fast recovery, no full scan.

The transaction sequence (ext4/jbd2):

```
  1. TxBegin     ── write journal descriptor + the blocks to be changed → journal
  2. Journal commit (barrier/FLUSH) ── make the journal record durable
  3. Checkpoint  ── write the blocks to their FINAL on-disk locations
  4. Free        ── reclaim the journal space
        crash before (2):  transaction lost, fs consistent (old state)
        crash after  (2):  replay journal → fs consistent (new state)
```

### 7.1 ext4 journaling modes (`data=`)

| Mode | Journals | Guarantee | Trade-off |
|---|---|---|---|
| `data=journal` | **metadata + data** | strongest: data and metadata both crash-consistent | every byte written **twice** (journal + final) → ~½ write throughput |
| `data=ordered` (default) | metadata only, but **forces data blocks to disk before** committing the metadata that references them | metadata consistent; no stale-data exposure (you won't read another file's old data) | a crash can lose the *last* writes but never expose garbage |
| `data=writeback` | metadata only, **no ordering** | metadata consistent, but a freshly-extended file may show **stale/garbage** data after a crash | fastest; weakest data guarantee |

> Why `ordered` is the default: it closes the security/correctness hole where a crash after a metadata commit but before the data write would let a file's new blocks contain *whatever was there before* (possibly another user's deleted data) — while avoiding the double-write cost of full data journaling.

Journaling protects the *filesystem structure*. It does **not** by itself make *your application's* writes durable — that is what `fsync` is for (§9). And a journal commit only helps if the device's `FLUSH`/`FUA` actually push past its volatile cache (§9.2).

---

## 8. Copy-on-write filesystems (ZFS / Btrfs)

Journaling writes data twice. **Copy-on-write (CoW)** filesystems take a different route: **never overwrite a live block.** A modification is written to a *new* block, and then pointers up the tree are rewritten to point at the new block — recursively to the root (the "uberblock" in ZFS). The root switch is the **atomic commit**: either you see the old tree or the new one, never a torn mix.

```
  CoW update of one leaf:
        root'                      (new root, published atomically last)
       /     \
   internal   internal'           (only the path to the change is copied)
   /    \      /     \
 leaf  leaf  leaf   leaf'         (new leaf; old leaf still referenced by snapshots)
```

This buys, almost for free:

- **Snapshots & clones** — a snapshot is just *keeping the old root*. O(1) to create. (Used for backups, time-travel, DB-style "create dev copy of 10 TB volume instantly".)
- **Atomic, consistent on-disk state** — no journal needed; there is never a half-written tree.
- **End-to-end checksums** (ZFS/Btrfs checksum every block and verify on read) — detects silent bit rot the drive's ECC missed; with redundancy (mirror/RAID-Z) it **self-heals** by reading the good copy.

Costs and caveats:

- **Fragmentation** — logically sequential files scatter physically as they're updated; bad for HDD random reads, less so on SSD.
- **Write amplification on small writes** — rewriting the pointer path. Mitigated by batching (ZFS transaction groups).
- **The RAID-5 "write hole"** is *eliminated* by CoW (ZFS RAID-Z), unlike traditional parity RAID (§12).
- ZFS combines volume manager + filesystem + RAID; it discourages putting a hardware RAID controller or a volatile-cache layer beneath it precisely because it wants to own integrity end-to-end.

> Btrfs offers similar features and is in the mainline kernel; ZFS has a more mature integrity/RAID-Z story but ships out-of-tree (licensing). For new large-scale storage where integrity matters (think backing a database fleet), CoW + checksums is increasingly the default reasoning.

---

## 9. The page cache, write-back, fsync & durability

### 9.1 The page cache and write-back

`read()`/`write()` on a normal file go through the **page cache** — RAM holding file pages. A `write()`:

1. copies your bytes into a page in the cache,
2. marks the page **dirty**,
3. **returns success immediately** — the disk has *not* been touched.

A kernel **write-back** thread (`pdflush`/`bdi-writeback`) later flushes dirty pages, governed by `vm.dirty_ratio`, `vm.dirty_background_ratio`, and `dirty_expire_centisecs`. This is what makes buffered I/O fast (writes coalesce, reads get readahead) — and what makes it **non-durable on crash**.

```
  write(fd, buf, n)        fsync(fd)
       │                      │
       ▼                      ▼
  ┌──────────┐  write-back  ┌──────────┐  FLUSH  ┌──────────┐
  │  dirty   │ ───(later)──►│ in-flight│ ───────►│  on disk │
  │  page    │              │ to device│         │ (durable)│
  └──────────┘              └──────────┘         └──────────┘
   returns now             not durable until fsync + device flush completes
```

### 9.2 fsync / fdatasync — the durability point

| Call | Forces | Skips |
|---|---|---|
| `fsync(fd)` | file data **and** all metadata (size, mtime, …) to stable storage | nothing |
| `fdatasync(fd)` | file data + only metadata **needed to read it back** (e.g. size if it grew) | non-essential metadata (mtime) → fewer metadata I/Os |
| `sync()` | schedules flush of *all* filesystems | does not wait (historically) |

For a database appending to a preallocated file where the size doesn't change, `fdatasync` avoids an inode write per commit — a measurable win.

**Durability also requires the device to honor a cache flush.** `fsync` issues a `FLUSH`/`FUA` to the block layer; if a drive (or a cheap RAID card, or a virtualization layer) has a *volatile* write cache and lies about flushing, data acknowledged as durable is lost on power failure. Enterprise practice: disable volatile write caches or use power-loss-protected (PLP) SSDs; for RAID, battery/flash-backed write cache (BBWC/FBWC).

### 9.3 The fsync gate (fsyncgate, 2018)

A landmark incident every staff engineer should know. On Linux, when an asynchronous write-back **fails** (e.g., a transient I/O error or a thin-provisioned volume hitting ENOSPC), the kernel historically **cleared the dirty page flag and reported the error only once** — to whichever fd happened to call `fsync` next. A program that:

1. `write()`s data,
2. gets an error on `fsync()`,
3. **retries `fsync()`**

would see the *second* `fsync` return **success** — because the dirty bit was already cleared — even though the data was never written. PostgreSQL (and others) had assumed "retry fsync on failure". The data was gone, but the database thought the checkpoint succeeded → silent corruption.

The fix/learnings:

- **An `fsync` error is unrecoverable from userspace.** You cannot "retry" it; the dirty data may already be discarded.
- The correct response to `fsync` failure is to treat it as fatal: crash, and recover from the WAL — do **not** assume a later successful fsync means the earlier data is safe.
- Kernels were patched to keep the error sticky per-inode and report it more reliably (errseq_t), but the application-side lesson stands: **handle fsync errors as data-loss events, not transient retries.**

---

## 10. O_DIRECT — bypassing the page cache

`O_DIRECT` opens a file for **unbuffered** I/O: DMA straight between the device and your buffer, skipping the page cache. Databases that maintain their own buffer pool (InnoDB, Oracle) use it to avoid **double caching** (once in their pool, once in the OS cache) and to control exactly when data hits the device.

Constraints (TLPI §13.6): the buffer address, the offset, and the length must all be aligned to the device's logical block size (typically 512 B or 4 KiB) — you must `posix_memalign` your buffer.

```
  Buffered (default):   app buf ── copy ──► page cache ── DMA ──► device
  O_DIRECT:             app buf ───────────── DMA ───────────────► device   (aligned!)
```

| | Buffered | O_DIRECT |
|---|---|---|
| Cache | OS page cache | none (app must cache) |
| Best for | general workloads, repeated reads | apps with their own cache; avoiding cache pollution from huge scans |
| Durability | still needs `fsync` | still needs `fsync` for metadata; data bypasses cache but a flush may still be needed for the device's volatile cache |
| Alignment | none | mandatory block alignment |

> `O_DIRECT` is **not** a durability guarantee. It avoids the page cache, but the device's own volatile write cache still requires a flush. The robust pattern for databases is `O_DIRECT | O_DSYNC` or `O_DIRECT` + explicit `fdatasync`.

---

## 11. Block layer & I/O schedulers

Below the filesystem, requests become **`bio` structures** merged into a request queue. The **I/O scheduler** decides the order in which requests reach the device — a decision that mattered enormously for HDDs (minimize seek) and far less for SSD/NVMe.

| Scheduler | Idea | Good for | Status |
|---|---|---|---|
| **CFQ** (Completely Fair Queuing) | per-process time slices, fairness | legacy HDD, desktops | removed; replaced by BFQ |
| **deadline** / **mq-deadline** | each request gets a deadline; bound worst-case latency, batch by sector | HDD, latency-sensitive | current (multi-queue) |
| **BFQ** (Budget Fair Queuing) | fair-share with low-latency heuristics | interactive HDD/SATA SSD | current |
| **none** (noop) | FIFO, no reordering | **NVMe / fast SSD** | **default for NVMe** |

For **NVMe**, `none` is the right answer: the device has deep hardware queues (thousands of entries, many submission/completion queues), reorders internally, and has *no seek penalty* — so a software scheduler only adds CPU overhead and latency. The kernel moved to **blk-mq** (multi-queue block layer) precisely because the single-queue model with one lock could not feed millions of IOPS across many cores.

```
  blk-mq:  per-CPU software queues  ──►  hardware submission queues  ──►  NVMe device
           (no global lock; scales       (one per core)                  (deep parallelism)
            with core count)
```

Tuning lever: `echo none > /sys/block/nvme0n1/queue/scheduler` (it usually already is). For HDD-backed throughput jobs, `mq-deadline`; for desktop responsiveness, `bfq`.

---

## 12. RAID levels

RAID (Redundant Array of Independent Disks) combines drives for **performance**, **capacity**, and/or **redundancy** (Silberschatz §11.8).

| Level | Layout | Redundancy | Read | Write | Usable capacity | Use |
|---|---|---|---|---|---|---|
| **0** | striping | **none** | fast | fast | 100% | scratch/perf only; one disk dies → all data lost |
| **1** | mirroring | survives 1 of 2 | fast (read either) | write both | 50% | databases, boot volumes |
| **5** | striping + 1 parity | survives **1** disk | fast | slow (read-modify-write parity) | (N−1)/N | general; **write hole** risk |
| **6** | striping + 2 parity | survives **2** disks | fast | slower (2 parity) | (N−2)/N | large arrays where rebuild time is long |
| **10** (1+0) | mirrored pairs, striped | survives 1 per mirror | fast | fast | 50% | high-perf databases |

Key staff concerns:

- **The write hole (RAID 5/6):** a power loss between writing the data stripe and its parity leaves them inconsistent; a later disk failure then reconstructs *wrong* data silently. Mitigations: BBWC, journaled RAID, or **CoW RAID (ZFS RAID-Z) which closes the hole by design**.
- **Rebuild time & URE:** with multi-TB drives, rebuilding a RAID 5 array reads every other disk in full; the probability of hitting an unrecoverable read error (URE) during rebuild is non-trivial → **RAID 6 or mirroring** for large modern disks.
- **RAID is not a backup** — it protects against *disk failure*, not `rm -rf`, ransomware, or correlated failures (bad batch, controller, fire).

---

## 13. LVM — the logical volume manager

LVM virtualizes block storage so volumes aren't bound to physical disk boundaries:

```
  Physical Volumes (PV):   /dev/sda1   /dev/sdb1   /dev/nvme0n1p2
                              └──────────┴──────────┘
  Volume Group (VG):                 vg_data        (a pool of extents)
                              ┌──────────┬──────────┐
  Logical Volumes (LV):     lv_db (200G)  lv_logs (50G)  ── mkfs, mount these
```

What it buys: **online resize** (grow an LV and the fs on top), **snapshots** (CoW point-in-time copies for consistent backups), **thin provisioning** (allocate-on-write — but beware: a thin pool hitting full mid-write is exactly the ENOSPC condition that triggered fsyncgate-class data loss), and **migration** (`pvmove` data off a failing disk live). The cost is a thin layer of indirection and operational complexity; ZFS/Btrfs fold volume management *into* the filesystem instead.

---

## 14. Storage media: HDD vs SSD vs NVMe

| | **HDD** | **SATA SSD** | **NVMe SSD** |
|---|---|---|---|
| Mechanism | spinning platter + head | NAND flash, SATA/AHCI | NAND flash, PCIe |
| Random read latency | ~5–10 ms (seek+rotation) | ~100 µs | ~10–80 µs |
| IOPS | ~100–200 | ~10⁴–10⁵ | ~10⁵–10⁶+ |
| Queue depth | 1 (one head) | 32 (AHCI) | **64K queues × 64K depth** |
| Cost/GB | lowest | mid | higher |
| Sequential vs random | **random is catastrophic** | random ~ fine | random ~ fine |

### 14.1 Flash specifics: erase blocks, write amplification, TRIM

NAND flash has a critical asymmetry: you can **read** and **program (write)** at *page* granularity (4–16 KiB), but you can only **erase** at *block* granularity (hundreds of pages), and a cell wears out after a limited number of program/erase (P/E) cycles.

- **The Flash Translation Layer (FTL)** maps logical block addresses to physical pages, does wear leveling, and runs **garbage collection**: to reuse a partially-stale erase block it must copy the still-valid pages elsewhere, then erase the block.
- **Write amplification (WA):** because of GC and the erase-block granularity, writing 1 logical byte can cause *several* physical bytes written. `WA = physical writes / logical writes`. High WA wears the drive faster and lowers sustained throughput.
- **TRIM / `discard`:** the filesystem tells the SSD "these LBAs are now free" (after a delete). Without TRIM the FTL thinks stale data is still live and copies it during GC — inflating WA and degrading performance over time. Enable via `fstrim` (periodic, preferred) or the `discard` mount option (inline, can add latency).

> Design implication: log-structured / append-heavy workloads (LSM trees, journals) align beautifully with flash — sequential large writes minimize WA. Random small in-place updates are the worst case for both HDD seeks and SSD wear.

---

## 15. Distributed & networked storage

| | **NFS** (file) | **Object storage** (S3-style) | **Block (iSCSI/SAN/EBS)** |
|---|---|---|---|
| Abstraction | POSIX-ish files over the network | `PUT`/`GET` of immutable blobs by key | a remote raw block device |
| Consistency | weak (caching, attribute timeouts); **no atomic append**, `fsync` semantics differ | per-object atomic `PUT`; strong read-after-write (modern S3) but **no partial in-place update** | depends on filesystem on top |
| Scale | departmental | effectively unbounded | per-volume |
| Use | shared home dirs, build artifacts | backups, data lakes, media, immutable logs | databases needing a "local" disk in cloud |

Staff-level pitfalls:

- **Never assume POSIX semantics over NFS.** Locking (`flock`/`fcntl`) is advisory and historically flaky; `fsync` may not mean what it means locally; `O_APPEND` is not atomic. Do not run a write-heavy database on NFS unless the vendor explicitly certifies it.
- **Object storage is not a filesystem.** No append, no rename-is-cheap (rename = copy+delete), eventual list consistency historically. The "rename to commit" trick (§16) does **not** translate to S3 — you commit by writing the final object once.
- **The end-to-end argument:** the more layers (VM → network block device → remote storage), the more places a "flush" can be quietly dropped. Verify durability empirically (power-pull tests, `diskchecker`-style tools) for any critical store.

---

## 16. Enterprise working example: a crash-safe append-only log

The canonical durable-write pattern, combining everything above: **fsync the data, then atomically publish via rename, then fsync the directory.** `rename()` is atomic on POSIX filesystems — a reader sees either the old file or the fully-written new one, never a torn mix. The directory `fsync` is the step everyone forgets: without it, the *rename itself* (a directory metadata change) may not be durable.

```python
"""
durable_log.py — a crash-safe append-only log + atomic-publish snapshot.

Demonstrates the four durability primitives every storage system needs:
  1. write-ahead append with per-record fsync (the durability point)
  2. fdatasync vs fsync (avoid the inode write when size is preallocated)
  3. the atomic "write temp -> fsync -> rename -> fsync(dir)" publish pattern
  4. crash recovery by replaying the log

Runs on Linux/macOS. (Windows lacks os.fsync-on-dir; guarded below.)
Run:  python durable_log.py
"""
from __future__ import annotations
import json
import os
import struct
import tempfile

MAGIC = b"LOG1"

# fdatasync is POSIX-only; fall back to fsync where it is unavailable (Windows).
_fdatasync = getattr(os, "fdatasync", os.fsync)
# O_BINARY is 0 on POSIX; on Windows it disables text-mode newline translation
# that would otherwise corrupt the binary length-prefix framing.
_O_BINARY = getattr(os, "O_BINARY", 0)


class DurableLog:
    """Append-only log. Each record: [u32 length][payload bytes].
    Every append is fsync'd before append() returns -> durable on success."""

    def __init__(self, path: str):
        self.path = path
        # O_APPEND makes each write atomic w.r.t. the end-of-file on local fs.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | _O_BINARY
        self.fd = os.open(path, flags, 0o644)

    def append(self, payload: bytes) -> None:
        record = struct.pack(">I", len(payload)) + payload
        # Loop to handle partial writes (write() may write fewer bytes).
        view = memoryview(record)
        while view:
            n = os.write(self.fd, view)
            view = view[n:]
        # fdatasync: data + size, skip mtime metadata. THE durability point.
        # An error here is a data-loss event (fsyncgate) -> treat as fatal.
        try:
            _fdatasync(self.fd)
        except OSError as e:
            raise SystemExit(f"FATAL: fdatasync failed, data not durable: {e}")

    def close(self) -> None:
        os.close(self.fd)

    @staticmethod
    def replay(path: str):
        """Recover by reading whole records; stop at the first torn tail."""
        records = []
        if not os.path.exists(path):
            return records
        with open(path, "rb") as f:
            data = f.read()
        off = 0
        while off + 4 <= len(data):
            (length,) = struct.unpack(">I", data[off:off + 4])
            if off + 4 + length > len(data):
                # Torn write: a crash left a partial record. Discard the tail.
                break
            records.append(data[off + 4: off + 4 + length])
            off += 4 + length
        return records


def atomic_publish(dirpath: str, name: str, content: bytes) -> None:
    """Write `content` to dirpath/name such that a crash can never leave a
    partially written or corrupt file at the final name.

    Pattern: temp in same dir -> write -> fsync(file) -> rename -> fsync(dir).
    rename() within a filesystem is atomic on POSIX.
    """
    final = os.path.join(dirpath, name)
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".tmp-" + name + "-")
    try:
        os.write(fd, content)
        os.fsync(fd)              # data of the temp file is durable
        os.close(fd)
        os.replace(tmp, final)    # atomic publish: readers see old or new, never torn
        # Crucial & commonly forgotten: fsync the DIRECTORY so the rename
        # (a directory entry change) is itself durable. POSIX-only; Windows
        # cannot open a directory as a normal fd, so this is best-effort.
        try:
            dfd = os.open(dirpath, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except (PermissionError, OSError):
            pass              # not supported on this platform (e.g. Windows)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


if __name__ == "__main__":
    d = tempfile.mkdtemp(prefix="durlog_")
    try:
        logpath = os.path.join(d, "wal.log")
        log = DurableLog(logpath)
        for i in range(1000):
            log.append(json.dumps({"seq": i, "op": "set", "k": f"k{i}"}).encode())
        log.close()

        # Simulate a crash + restart: a fresh process replays the log.
        recovered = DurableLog.replay(logpath)
        assert len(recovered) == 1000, f"expected 1000, got {len(recovered)}"
        assert json.loads(recovered[0])["seq"] == 0
        assert json.loads(recovered[-1])["seq"] == 999

        # Atomic snapshot publish (e.g. a checkpoint file).
        atomic_publish(d, "checkpoint.json",
                       json.dumps({"last_seq": 999}).encode())
        with open(os.path.join(d, "checkpoint.json")) as f:
            assert json.load(f)["last_seq"] == 999

        print(f"OK: appended+recovered 1000 records; checkpoint published atomically")
        print(f"    log size = {os.path.getsize(logpath)} bytes")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
```

**What each piece teaches:**
- `os.fdatasync` after every append is the durability boundary; the append is durable the instant it returns.
- The fsyncgate lesson is encoded: an `fdatasync` failure is **fatal**, not retried.
- `replay()` stops at the **first torn record** — a crash mid-append leaves a partial `[len][partial payload]`; we detect it by checking `off + 4 + length > len(data)` and discard the incomplete tail. This is exactly how a real WAL handles a crash during the final write.
- `atomic_publish` is the **write-temp → fsync → rename → fsync(dir)** pattern used by SQLite, Git (loose objects), and virtually every config-file writer that cares about crashes.

---

## 17. Enterprise working example: measuring buffered vs O_DIRECT vs fsync

The single most useful storage benchmark a staff engineer can run is *"what does durability actually cost on this box?"* This C program writes the same data three ways and reports throughput. The gap between buffered and fsync-per-write is your durability tax; the gap to O_DIRECT shows page-cache overhead.

```c
/* iobench.c — buffered vs fsync-per-write vs O_DIRECT throughput.
   Build:  cc -O2 -D_GNU_SOURCE -o iobench iobench.c
   Run:    ./iobench /data/testfile
   (Writes ~64 MiB three ways; deletes the file afterward.)            */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>

#define BLK   (4096)
#define COUNT (16384)            /* 16384 * 4 KiB = 64 MiB */

static double now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void report(const char *name, double secs) {
    double mib = (double)COUNT * BLK / (1024.0 * 1024.0);
    printf("%-22s %8.3f s   %8.1f MiB/s   %9.0f IOPS\n",
           name, secs, mib / secs, COUNT / secs);
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s PATH\n", argv[0]); return 2; }
    const char *path = argv[1];

    /* aligned buffer required for O_DIRECT */
    void *buf;
    if (posix_memalign(&buf, BLK, BLK) != 0) { perror("posix_memalign"); return 1; }
    memset(buf, 'x', BLK);

    /* 1) buffered writes, single fsync at the end */
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { perror("open"); return 1; }
    double t0 = now();
    for (int i = 0; i < COUNT; i++)
        if (write(fd, buf, BLK) != BLK) { perror("write"); return 1; }
    fsync(fd);
    report("buffered + 1 fsync", now() - t0);
    close(fd);

    /* 2) fsync after EVERY write (the durability tax) */
    fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    t0 = now();
    for (int i = 0; i < COUNT; i++) {
        if (write(fd, buf, BLK) != BLK) { perror("write"); return 1; }
        if (fdatasync(fd) != 0) { perror("fdatasync"); return 1; }
    }
    report("fdatasync per write", now() - t0);
    close(fd);

    /* 3) O_DIRECT (bypass page cache); fall back if unsupported (e.g. tmpfs) */
    fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_DIRECT, 0644);
    if (fd < 0 && errno == EINVAL) {
        printf("O_DIRECT not supported on this filesystem; skipping\n");
    } else if (fd < 0) {
        perror("open O_DIRECT"); return 1;
    } else {
        t0 = now();
        for (int i = 0; i < COUNT; i++)
            if (write(fd, buf, BLK) != BLK) { perror("write O_DIRECT"); return 1; }
        fsync(fd);   /* still need flush for the device's volatile cache */
        report("O_DIRECT + 1 fsync", now() - t0);
        close(fd);
    }

    free(buf);
    unlink(path);
    return 0;
}
```

Typical shape of results on an NVMe SSD (illustrative): buffered ≈ multiple GiB/s (you're measuring memcpy into the page cache), fdatasync-per-write drops to thousands of IOPS (each commit waits for the device), and O_DIRECT sits between — no page-cache copy, but every write goes to the device. The lesson: **batch your fsyncs** (group commit) — durability cost is per-fsync, not per-byte.

---

## 18. Detecting & handling partial / torn writes

A **torn write** is a write that the device applied only partially across a power loss — e.g., a 16 KiB database page where only the first 4 KiB sector made it. The filesystem journal protects *its* metadata; it does **not** protect *your* multi-sector page from being half-old/half-new.

Defenses staff engineers use:

| Technique | How | Used by |
|---|---|---|
| **Per-record/page checksum** | store a checksum; on read, recompute and reject a torn page | ZFS, Btrfs, modern DBs |
| **Double-write buffer** | write the page to a scratch area + fsync, *then* to its final home; on crash, the scratch copy is intact | InnoDB ("doublewrite buffer") |
| **Atomic-write hardware** | NVMe `AWUPF` advertises an atomic-write unit; trust pages ≤ that size | high-end NVMe |
| **Torn-tail discard** | the WAL pattern from §16 — detect an incomplete final record by length/checksum and drop it | every WAL |

```python
"""
torn_write.py — detect a torn record using a length+CRC framing.
Frame layout:  [u32 length][u32 crc32(payload)][payload]
A crash can leave a truncated frame; verification rejects it.
Run:  python torn_write.py
"""
import struct, zlib, os, tempfile

HDR = struct.Struct(">II")  # length, crc32


def write_frame(fd, payload: bytes) -> None:
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    os.write(fd, HDR.pack(len(payload), crc) + payload)


def read_frames(path: str):
    """Yield only intact frames; stop at the first torn/corrupt one."""
    with open(path, "rb") as f:
        data = f.read()
    off = 0
    while off + HDR.size <= len(data):
        length, crc = HDR.unpack(data[off:off + HDR.size])
        start = off + HDR.size
        end = start + length
        if end > len(data):
            return                       # truncated tail -> stop (torn write)
        payload = data[start:end]
        if (zlib.crc32(payload) & 0xFFFFFFFF) != crc:
            return                       # corrupt payload (bit rot / torn) -> stop
        yield payload
        off = end


if __name__ == "__main__":
    d = tempfile.mkdtemp(prefix="torn_")
    try:
        p = os.path.join(d, "frames.bin")
        # O_BINARY is 0 on POSIX; on Windows it prevents text-mode newline
        # translation from corrupting the binary length/CRC header bytes.
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
        for i in range(100):
            write_frame(fd, f"record-{i}".encode())
        os.fsync(fd)
        os.close(fd)

        # Simulate a torn write: append a partial frame header + truncated body.
        with open(p, "ab") as f:
            f.write(HDR.pack(50, 0xDEADBEEF))   # claims 50 bytes...
            f.write(b"only-ten!!")              # ...but only 10 present

        good = list(read_frames(p))
        assert len(good) == 100, f"torn tail must be ignored; got {len(good)}"
        assert good[0] == b"record-0" and good[-1] == b"record-99"
        print(f"OK: recovered {len(good)} intact frames, torn tail discarded")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
```

---

## 19. Advanced: the durability ladder, write barriers, and filesystem choice

### The durability ladder — exactly what "saved" means

"Did the write survive a power loss?" has many answers, each a rung with a cost:

```
   write()            -> in the kernel PAGE CACHE (lost on power loss; fast)
   fsync()/fdatasync()-> flushed to the DEVICE (durable IF the device honors flush)
   + FUA / flush      -> past the device's volatile cache to stable media
   + power-loss prot. -> enterprise SSD capacitors guarantee the device cache survives
```

The subtle trap: a device with a **volatile write cache** can ACK an fsync while data
sits in the drive's RAM — a power loss then loses "durable" data. The kernel issues a
**cache-flush / FUA (Force Unit Access)** to push past it; consumer drives that lie
about flush completion are a classic data-loss source. Enterprise drives with
**power-loss protection** (capacitors) make the device cache effectively non-volatile,
which is why databases demand them. `fdatasync` skips metadata (faster) when only data
durability is needed; `fsync` flushes metadata too.

### Write barriers and ordering

Durability is not just "is it flushed" but "in what order." A journaling filesystem
([§7](#7-journaling-write-ahead-for-metadata-and-data)) must ensure the journal commit
lands **before** the data it describes — enforced by **write barriers** (flush+FUA at
commit points). Disabling barriers (`nobarrier`/`barrier=0`) speeds writes but can
corrupt the filesystem on power loss; only safe with battery-backed cache. The same
ordering logic is why a database's WAL fsync must complete before the data-page write
is allowed to reach disk.

### Filesystem choice & mount tuning (ext4 vs XFS vs ZFS)

| FS | Strengths | Choose when |
|---|---|---|
| **ext4** | mature, predictable, low overhead | general purpose, the safe default |
| **XFS** | excellent parallel/large-file I/O, scales to many cores | databases, big files, high-concurrency writes |
| **ZFS/Btrfs** | CoW, checksums, snapshots ([§8](#8-copy-on-write-filesystems-zfs--btrfs)) | data integrity, snapshots — at higher overhead |

High-leverage mount options: **`noatime`** (stop updating access time on every read —
a free win, eliminates a write per read); `nodiratime`; tuning the journal mode
(`data=ordered` default vs `data=writeback` faster/less safe). For sparse files and
fast space management, **`fallocate`** (preallocate without writing) and `FALLOC_FL_PUNCH_HOLE`
(free ranges) beat truncate/rewrite.

### io_uring for storage

Beyond networking ([06 §10](06_io_models_async.md)), io_uring gives true async file
I/O — including async `fsync`, `O_DIRECT` reads, and registered buffers — letting a
database issue thousands of overlapping I/Os from one thread without a thread pool.
It's becoming the high-performance storage path (used by modern databases and
object stores).

---

## Key Takeaways

1. **A file descriptor is the top of a three-layer indirection** (fd table → open file description → inode). The OFD holds the offset and status flags — which is exactly what `fork`/`dup` share and why `O_APPEND` is atomic.
2. **Names and data are decoupled.** The inode holds everything but the name; a directory maps names to inode numbers. Hard links share an inode (`nlink`), symlinks store a path. `unlink` just decrements `nlink`.
3. **`write()` returning is not durability.** Bytes sit dirty in the page cache until write-back; durability requires `fsync`/`fdatasync` **and** the device honoring a cache flush. `fdatasync` skips non-essential metadata for a cheaper commit.
4. **fsyncgate (2018):** an `fsync` error is an unrecoverable data-loss event, not a retry. Crash and recover from the WAL; never assume a later successful fsync rescued earlier failed data.
5. **Journaling protects filesystem structure** (ext4 `ordered` is the safe default); **CoW filesystems (ZFS/Btrfs)** give atomic commits, snapshots, and end-to-end checksums that detect and self-heal bit rot.
6. **Match the I/O scheduler to the media:** `none` for NVMe (deep hardware queues, no seek), `mq-deadline`/`bfq` for HDD/SATA. The block layer is multi-queue (blk-mq) to scale across cores.
7. **Flash rewards sequential, append-heavy I/O.** Erase-block granularity + GC create write amplification; TRIM keeps the FTL honest. LSM/log workloads are a natural fit.
8. **RAID is availability, not backup.** Beware the RAID 5/6 write hole (use BBWC or CoW RAID-Z) and long rebuild times with multi-TB disks (prefer RAID 6 / mirroring).
9. **The durable-write pattern is universal:** append + `fdatasync`, and publish atomically via **temp → fsync → rename → fsync(directory)**. Detect torn writes with length+checksum framing and discard the partial tail.
10. **Don't trust POSIX semantics over the network.** NFS append/locking/fsync differ; object storage has no in-place update or cheap rename. Verify durability empirically for any critical store.

> Read next: [06 — I/O, Interrupts & Async I/O](06_io_models_async.md) for how the bytes actually move — the syscall cost, DMA, epoll, io_uring, and zero-copy.
