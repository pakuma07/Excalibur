# 07 — Virtualization & Containers

> **Audience:** staff/principal. You know how to run a VM and `docker run`. This doc is about *what a virtual machine and a container actually are* at the hardware/kernel level — the privilege rings, the trap-and-emulate loop, the namespaces and cgroups — so you can reason about isolation strength, performance overhead, and the blast radius of a container escape from first principles.
>
> **Primary sources:** Popek & Goldberg, *Formal Requirements for Virtualizable Third Generation Architectures* (1974); Barham et al., *Xen and the Art of Virtualization* (SOSP 2003); Kivity et al., *kvm: the Linux Virtual Machine Monitor* (OLS 2007); the OCI Image/Runtime specs and runc/containerd docs; Brendan Gregg, *BPF Performance Tools* (2019) and *Systems Performance* (2nd ed., 2020); the Linux `cgroups(7)`, `namespaces(7)`, `capabilities(7)`, and `seccomp(2)` man pages; the Firecracker (NSDI 2020) and gVisor design docs.

---

## 1. Why this matters at scale

Virtualization and containerization are the two ways we get **multitenancy**: running many independent workloads on one physical machine while pretending each owns the box. The entire cloud economy is built on this. The two technologies sit at different points on one axis:

```
   stronger isolation                                weaker isolation
   higher overhead                                   lower overhead
   slower start                                      faster start
        |                                                 |
   [ bare-metal VM ]---[ microVM ]---[ gVisor ]---[ container ]---[ process ]
     full HW emul.     Firecracker    userspace      ns+cgroups    nothing
     (VT-x + EPT)      (KVM, minimal)  kernel reimpl  shared kernel
```

Two decisions dominate the cost/security envelope:

1. **What is the isolation boundary?** A VM puts a *hardware-virtualized boundary* (the hypervisor + CPU virtualization extensions) between tenants — a guest kernel compromise does not directly own the host. A container shares the **host kernel**; the boundary is namespaces + cgroups + the syscall surface. A single kernel LPE bug is a container escape. This is the whole reason microVMs (Firecracker) and userspace kernels (gVisor) exist.
2. **What is the overhead?** A VM boots a whole guest OS (seconds, hundreds of MB of RAM, a vCPU exit on privileged ops). A container is *just a Linux process* with extra metadata (milliseconds, near-zero memory tax, native syscall speed). At the density of a serverless fleet, that difference is the business model.

Staff engineers are expected to choose the right boundary for a given trust model and to debug the layer below the abstraction when it leaks.

---

## 2. Popek & Goldberg — the formal requirements (1974)

Before VT-x, before Xen, Popek and Goldberg gave the **definition of a virtualizable architecture**. A control program (the VMM/hypervisor) is "efficient" if it satisfies three properties:

| Property | Meaning |
|---|---|
| **Equivalence / Fidelity** | A program running under the VMM behaves *identically* to running on bare metal (except timing). |
| **Resource control / Safety** | The VMM has *complete control* of resources — the guest cannot touch resources not allocated to it. |
| **Efficiency** | A *statistically dominant* fraction of instructions execute *directly* on the real CPU with no VMM intervention. (This is what rules out pure emulation/interpretation like QEMU-TCG: correct, but not "efficient" by this definition.) |

### 2.1 The instruction taxonomy and the theorem

Classify instructions by behavior:

- **Privileged instructions** — trap (cause a fault) when executed in user mode, run fine in supervisor mode.
- **Sensitive instructions** — those that touch or depend on the privileged machine state:
  - *Control-sensitive*: change the configuration of resources (e.g., load a page table base, change interrupt flags).
  - *Behavior-sensitive*: behave differently depending on privilege/configuration (e.g., an instruction that reads the current privilege level and returns a different result).

> **Popek-Goldberg theorem:** A machine is *classically virtualizable* (a VMM via **trap-and-emulate** can be built) **iff the set of sensitive instructions is a subset of the set of privileged instructions.** I.e., *every instruction that could subvert the VMM must trap when the guest (running deprivileged) executes it.*

### 2.2 Why x86 was NOT classically virtualizable

The original x86 (pre-2005) **violated** the theorem: it had ~17 sensitive-but-unprivileged instructions. The canonical example is `POPF` (and `SGDT`, `SIDT`, `SMSW`, `PUSHF`):

- `POPF` pops flags including the interrupt-enable flag `IF`. In **ring 0** it changes `IF`. In **ring 3** (where a deprivileged guest kernel runs) it **silently ignores** the `IF` change — *no trap*. So a guest kernel trying to disable interrupts just gets a no-op and never knows. The VMM cannot intercept what never traps.

This single fact shaped a decade of virtualization engineering. Three escapes were invented:

1. **Binary translation** (VMware, 1999) — scan guest kernel code at runtime, *rewrite* the offending instructions into safe sequences that trap. Software-only, clever, but complex and with overhead.
2. **Paravirtualization** (Xen, 2003) — *modify the guest OS* to replace sensitive instructions with explicit calls (**hypercalls**) into the hypervisor. Fast, but needs a ported kernel.
3. **Hardware-assisted virtualization** (Intel VT-x 2005 / AMD-V) — add a new CPU mode so the theorem holds again. This won.

---

## 3. Trap-and-emulate and ring deprivileging

### 3.1 Protection rings

x86 has four privilege rings; conventionally the OS uses only two:

```
   Ring 0  — kernel (supervisor): full instruction set, all of memory
   Ring 1  — (unused by Linux/Windows)
   Ring 2  — (unused)
   Ring 3  — user space: restricted; privileged instr. trap to ring 0
```

### 3.2 The trap-and-emulate loop

The classical VMM technique: run the guest **deprivileged** (guest kernel moved out of ring 0), and emulate whatever traps.

```text
  guest runs directly on CPU (fast path)
        |
        | guest executes a privileged/sensitive instruction
        v
   CPU TRAPS  ---->  control transfers to the VMM
        |
        v
   VMM decodes the faulting instruction, EMULATES its effect
   against the *virtual* machine state (virtual CPU regs, virtual
   devices), advances the guest PC
        |
        v
   resume guest direct execution
```

- Most instructions (arithmetic, loads, branches) run **natively** → satisfies efficiency.
- Only the rare sensitive ones trap → satisfies safety + fidelity.
- This *requires* the Popek-Goldberg property. On classic x86 it fails (the `POPF` problem), which is why pure trap-and-emulate was impossible there until hardware help arrived.

### 3.3 Ring deprivileging and "ring compression"

To deprivilege the guest kernel, early Xen on 32-bit x86 ran the guest kernel in **ring 1** and guest user space in ring 3 — the hypervisor kept ring 0. This is **ring deprivileging**. On x86-64 the ring 1/2 segmentation tricks broke, which (combined with VT-x arriving) pushed everyone to hardware virtualization.

---

## 4. Hardware-assisted virtualization (VT-x / AMD-V, EPT/NPT)

Intel **VT-x** (VMX) and AMD **AMD-V** (SVM) re-enable classical virtualization by adding a new orthogonal CPU mode:

```
            VMX root mode                 VMX non-root mode
          (hypervisor: KVM)              (guest: kernel + user)
   ring0  hypervisor             VMRESUME    guest kernel  ring0
     ^         |               ----------->        |
     |         |   VM entry                         | privileged/configured exit
     |         v                                    v
     +----  VM EXIT  <-----------------------  guest causes a "VM exit"
            (reason code in VMCS)
```

- **VMCS / VMCB** (VM Control Structure / Block) — a per-vCPU in-memory structure that holds guest state, host state, and an **execution-control bitmap** that says *which events cause a VM exit* (e.g., specific instructions, I/O ports, interrupts). The hypervisor programs this; the CPU enforces it.
- The guest kernel now runs in **ring 0 of non-root mode** — so legacy OSes run unmodified. The dangerous instructions (or the events you opted into) cause a **VM exit** to root mode, where KVM emulates and `VMRESUME`s. No binary translation, no paravirt required.
- **VM exits are the cost.** Each exit is a few thousand cycles of save/restore. Virtualization tuning is largely about *eliminating exits* (e.g., posted interrupts, APICv, avoiding `CPUID`/`RDTSC` exits).

### 4.1 The MMU problem — EPT / NPT (two-dimensional paging)

The hardest part is memory. The guest maintains its own page tables mapping **Guest Virtual → Guest Physical**. But guest-physical is not real. Without hardware help, the VMM had to maintain **shadow page tables** (intercept every guest page-table write, maintain a synced GVA→HPA table) — correct but exit-heavy and complex.

Hardware fixed this with a *second* set of page tables:

- Intel **EPT** (Extended Page Tables) / AMD **NPT** (Nested Page Tables) / **RVI**.
- The CPU now does **two-dimensional page walks**: GVA → GPA (guest page tables) → HPA (EPT, managed by the hypervisor). The guest manages its own tables freely with no exits; the hypervisor only manages GPA→HPA.

```
   Guest Virtual Addr --[guest page tables]--> Guest Physical Addr
                                                     |
                                              [EPT / NPT, host-managed]
                                                     v
                                               Host Physical Addr
```

Cost: a TLB miss is more expensive (a 2D walk can be up to ~24 memory accesses for 4-level × 4-level), mitigated by larger TLBs and huge pages. Benefit: page-table-heavy workloads stop generating VM exits — a huge win over shadow paging.

### 4.2 Device I/O — virtio and SR-IOV

A naive VMM emulates real hardware (an e1000 NIC) instruction-by-instruction — every register poke is a VM exit. **Paravirtualized I/O** fixes this:

- **virtio** (Russell, 2008) — a standard set of paravirtual device interfaces (`virtio-net`, `virtio-blk`, `virtio-scsi`) based on **virtqueues**: shared-memory ring buffers between guest and host. The guest batches requests into the ring and rings a doorbell *once*, amortizing exits over many operations. This is why a `virtio-net` NIC is far faster than an emulated e1000.
- **vhost** — moves the virtio backend (the host side of the data plane) into the **host kernel** (`vhost-net`) or a userspace process (`vhost-user`, e.g., DPDK), so the data path skips the userspace VMM (QEMU) entirely.
- **SR-IOV** — the physical NIC exposes multiple **virtual functions (VFs)**; a VF is assigned (via the **IOMMU**, Intel VT-d) directly to a guest, giving near-bare-metal network performance with DMA isolation. The trade-off is loss of live-migration flexibility.

---

## 5. Type-1 vs Type-2 hypervisors

```
   TYPE 1 (bare-metal)                  TYPE 2 (hosted)
  +---------------------+              +---------------------+
  | guest | guest | gst |              | guest | guest |     |
  +---------------------+              +---------------------+
  |     hypervisor      |              |  VMM (e.g. VBox)    |
  +---------------------+              +---------------------+
  |      hardware       |              |   host OS (Linux)   |
  +---------------------+              +---------------------+
                                       |      hardware       |
   Xen, ESXi, Hyper-V                  +---------------------+
                                        VirtualBox, VMware Workstation
```

- **Type-1 (bare-metal)**: the hypervisor runs *directly on hardware*; guests run on top. Lower overhead, the production standard. Examples: **VMware ESXi**, **Microsoft Hyper-V**, **Xen**.
- **Type-2 (hosted)**: the VMM runs as an *application on a host OS*. Convenient for desktops/dev. Examples: VirtualBox, VMware Workstation, QEMU (pure userspace).

**KVM is the interesting hybrid.** KVM is a Linux *kernel module* that turns the Linux kernel itself into a type-1 hypervisor (it uses VT-x/AMD-V directly), but because the host *is* a full Linux, it has type-2 ergonomics. QEMU runs in userspace as the device-model/management process and calls KVM via `/dev/kvm` ioctls for the CPU/MMU virtualization. This is the basis of essentially all Linux cloud IaaS (and of Firecracker — §10).

| | Xen | KVM | ESXi | Hyper-V |
|---|---|---|---|---|
| Type | 1 | 1 (in-kernel) | 1 | 1 |
| Origin | Cambridge, paravirt pioneer | Linux kernel module | VMware proprietary | Microsoft |
| Dom0/host | privileged **Dom0** Linux for I/O | the host Linux kernel | proprietary VMkernel | parent partition (Windows) |
| HW virt | PV historically, now HVM (VT-x) | VT-x/AMD-V + EPT/NPT | VT-x/AMD-V | VT-x/AMD-V |
| Notable user | AWS EC2 (originally), Citrix | GCP, most OpenStack, Firecracker | enterprise on-prem | Azure, Windows |

### 5.1 The KVM ioctl skeleton (working C)

This is a *real, compiling* minimal KVM client: it creates a VM, loads 16 bits of guest real-mode code that writes a byte to an I/O port, and runs it. It demonstrates the `/dev/kvm` → `KVM_CREATE_VM` → `KVM_CREATE_VCPU` → `KVM_RUN` loop and how a `KVM_EXIT_IO` (a VM exit) surfaces to the userspace VMM. Requires a Linux host with `/dev/kvm` (Intel/AMD virt enabled).

```c
/* tiny_kvm.c — minimal KVM "hypervisor" demonstrating the VM-exit loop.
 * Build:  cc -O2 -o tiny_kvm tiny_kvm.c
 * Run:    ./tiny_kvm        (needs read/write on /dev/kvm)
 * Guest is 16-bit real-mode code that does: out 0x10, 'K'; out 0x10,'V'; hlt
 */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/kvm.h>
#include <unistd.h>

int main(void) {
    int kvm = open("/dev/kvm", O_RDWR | O_CLOEXEC);
    if (kvm < 0) { perror("open /dev/kvm"); return 1; }

    int vmfd = ioctl(kvm, KVM_CREATE_VM, 0);

    /* Guest physical memory: one 4 KiB page mapped at GPA 0x1000. */
    uint8_t *mem = mmap(NULL, 0x1000, PROT_READ | PROT_WRITE,
                        MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    struct kvm_userspace_memory_region region = {
        .slot = 0, .guest_phys_addr = 0x1000,
        .memory_size = 0x1000, .userspace_addr = (uint64_t)mem,
    };
    ioctl(vmfd, KVM_SET_USER_MEMORY_REGION, &region);

    /* 16-bit code: mov al,'K'; out 0x10,al; mov al,'V'; out 0x10,al; hlt */
    const uint8_t code[] = {
        0xB0, 'K', 0xE6, 0x10,   /* mov al,'K' ; out 0x10,al */
        0xB0, 'V', 0xE6, 0x10,   /* mov al,'V' ; out 0x10,al */
        0xF4                      /* hlt */
    };
    memcpy(mem, code, sizeof(code));

    int vcpu = ioctl(vmfd, KVM_CREATE_VCPU, 0);
    int mmap_size = ioctl(kvm, KVM_GET_VCPU_MMAP_SIZE, 0);
    struct kvm_run *run = mmap(NULL, mmap_size, PROT_READ | PROT_WRITE,
                               MAP_SHARED, vcpu, 0);

    /* Start in real mode with CS:IP -> 0x1000 (segment base 0x1000, ip 0). */
    struct kvm_sregs sregs;
    ioctl(vcpu, KVM_GET_SREGS, &sregs);
    sregs.cs.base = 0x1000; sregs.cs.selector = 0x100;
    ioctl(vcpu, KVM_SET_SREGS, &sregs);
    struct kvm_regs regs = { .rip = 0, .rflags = 0x2 };
    ioctl(vcpu, KVM_SET_REGS, &regs);

    for (;;) {
        ioctl(vcpu, KVM_RUN, 0);          /* VM entry; returns on VM exit */
        if (run->exit_reason == KVM_EXIT_IO &&
            run->io.direction == KVM_EXIT_IO_OUT && run->io.port == 0x10) {
            /* This is the VM EXIT being emulated by our userspace VMM. */
            char c = *((char *)run + run->io.data_offset);
            putchar(c); fflush(stdout);
        } else if (run->exit_reason == KVM_EXIT_HLT) {
            printf("  <guest halted>\n");
            return 0;
        } else {
            fprintf(stderr, "unexpected exit_reason=%d\n", run->exit_reason);
            return 1;
        }
    }
}
```

Run it and you see `KV  <guest halted>`. Each `out` instruction is a **VM exit** (`KVM_EXIT_IO`) that our 30-line "hypervisor" emulates — exactly the trap-and-emulate loop from §3, now hardware-accelerated.

---

## 6. VMs vs containers — the fundamental difference

```
   VIRTUAL MACHINES                          CONTAINERS
  +------+ +------+ +------+                +------+ +------+ +------+
  | app  | | app  | | app  |                | app  | | app  | | app  |
  | libs | | libs | | libs |                | libs | | libs | | libs |
  +------+ +------+ +------+                +------+ +------+ +------+
  |guestK| |guestK| |guestK|  <- full OS    |  ns + cgroups (metadata) |
  +------+ +------+ +------+      each       +--------------------------+
  |     hypervisor       |                   |   ONE shared host kernel  |
  +----------------------+                   +--------------------------+
  |      hardware        |                   |        hardware          |
  +----------------------+                   +--------------------------+
```

| | Virtual machine | Container |
|---|---|---|
| Kernel | own guest kernel per VM | **shared host kernel** |
| Isolation boundary | hardware (VT-x/EPT) + hypervisor | namespaces + cgroups + seccomp + caps |
| What it *is* | an emulated machine | a **process** with restricted views |
| Boot/start | seconds | milliseconds |
| Memory tax | hundreds of MB (whole OS) | ~the process working set |
| Density | tens per host | hundreds–thousands per host |
| Escape requires | hypervisor/CPU bug (rare, severe) | **kernel LPE bug** (more surface) |
| Run a different OS kernel? | yes (Windows on Linux host) | no (Linux containers need a Linux host kernel) |

The headline: **a container is not a lightweight VM. It is a normal Linux process that the kernel has been told to show a restricted view of the system.** There is no "container" object in the kernel — the word is shorthand for a *bundle* of namespaces, cgroups, a root filesystem (via `pivot_root`/`chroot`), a capability set, and a seccomp profile, all applied to a process. The next sections build one from those primitives.

---

## 7. Linux namespaces — virtualizing the kernel's global resources

A **namespace** wraps a global system resource so that processes inside the namespace see their own isolated instance of it (`namespaces(7)`). Creating/joining namespaces uses three syscalls: `clone(2)` (new process in new namespaces), `unshare(2)` (move *current* process into new namespaces), `setns(2)` (join an existing namespace, e.g., `nsenter`).

| Namespace | `clone` flag | Isolates | "Hello world" effect |
|---|---|---|---|
| **PID** | `CLONE_NEWPID` | process ID number space | first process becomes **PID 1**; can't see host PIDs |
| **NET** | `CLONE_NEWNET` | network stack: interfaces, routes, ports, iptables | starts with only `lo` (down); needs a veth pair |
| **MNT** | `CLONE_NEWNS` | mount table | private mounts; basis of per-container rootfs |
| **UTS** | `CLONE_NEWUTS` | hostname & domainname | `hostname` change doesn't affect host |
| **IPC** | `CLONE_NEWIPC` | SysV IPC, POSIX msg queues | private shared-memory segments |
| **USER** | `CLONE_NEWUSER` | UID/GID mappings, capabilities | **root inside, unprivileged outside** — the key to rootless |
| **CGROUP** | `CLONE_NEWCGROUP` | the cgroup root the process sees | hides the host cgroup hierarchy from the container |
| **TIME** | `CLONE_NEWTIME` | `CLOCK_MONOTONIC`/`BOOTTIME` offsets | per-namespace boot time (used in checkpoint/restore) |

Each is exposed as a magic symlink under `/proc/<pid>/ns/`. Two processes share a namespace iff those symlinks point at the same inode:

```bash
# Inspect a process's namespaces and compare to your own:
ls -l /proc/$$/ns/
# net:[4026531992]  <- the inode in brackets identifies the namespace

# The USER namespace lets an unprivileged user be "root" inside it:
unshare --user --map-root-user id
# uid=0(root) gid=0(root) ...  -- root *inside*, but mapped to your real uid outside

# A PID + mount namespace gives the classic "I am PID 1" view:
sudo unshare --pid --mount --fork --mount-proc bash -c 'echo "I am PID $$"; ps -ef'
# I am PID 1   -- and ps shows only this namespace's processes
```

### 7.1 The USER namespace and rootless containers

The user namespace is the security keystone. It maps a UID range: UID 0 *inside* maps to some unprivileged UID *outside* (via `/proc/<pid>/uid_map`). So a process can be fully "root" within its container — able to mount, set caps, own files — while the host kernel treats it as a harmless unprivileged user. This is what makes **rootless containers** (Podman, rootless Docker) possible: the whole container runs without ever needing real host root, dramatically shrinking the escape blast radius. (Historically the user namespace was also a *source* of CVEs because it exposed privileged kernel code paths to unprivileged users — a reminder that more flexible isolation can mean more attack surface.)

---

## 8. cgroups — accounting and limiting resources

Namespaces control **what a process can see**; **control groups (cgroups)** control **how much it can use** (`cgroups(7)`). A cgroup is a collection of processes bound to a set of resource **controllers**.

### 8.1 v1 vs v2

| | cgroups v1 | cgroups v2 |
|---|---|---|
| Hierarchy | **multiple** independent hierarchies (one per controller) | **single unified** hierarchy |
| Mount | `/sys/fs/cgroup/<controller>/...` | `/sys/fs/cgroup/...` (one tree) |
| Process placement | a task could be in different cgroups per controller (confusing) | a process is in exactly **one** cgroup, all controllers apply |
| Controllers enabled | per-hierarchy | via `cgroup.subtree_control` (delegation-friendly) |
| Key rule | — | **"no internal processes"**: only leaf cgroups hold processes |
| Status | legacy | the default on modern distros; required for systemd unified mode |

Both expose resources as a **pseudo-filesystem** you read/write. Core v2 controllers:

| Controller | Key files (v2) | Limits |
|---|---|---|
| **cpu** | `cpu.max` (`quota period`), `cpu.weight` | hard cap (e.g. `50000 100000` = 0.5 CPU) + proportional share |
| **memory** | `memory.max`, `memory.high`, `memory.current`, `memory.events` | hard OOM limit + soft throttle |
| **io** | `io.max`, `io.weight`, `io.stat` | per-device IOPS/bps caps + proportional share |
| **pids** | `pids.max`, `pids.current` | cap process count (fork-bomb defense) |

### 8.2 Setting and observing a limit (working bash)

```bash
# --- cgroups v2: cap a workload at 0.5 CPU and 100 MiB, then watch enforcement ---
# (run as root on a cgroups-v2 host: `stat -fc %T /sys/fs/cgroup` should say "cgroup2fs")
set -euo pipefail
CG=/sys/fs/cgroup/demo
sudo mkdir -p "$CG"

# Make sure the parent delegates the controllers we need to children:
echo "+cpu +memory +pids" | sudo tee /sys/fs/cgroup/cgroup.subtree_control

# CPU: 50ms of runtime per 100ms period == 0.5 of one core
echo "50000 100000" | sudo tee "$CG/cpu.max"
# Memory: hard cap 100 MiB (allocation past this triggers reclaim, then OOM)
echo $((100 * 1024 * 1024)) | sudo tee "$CG/memory.max"
# PIDs: at most 50 tasks (cheap fork-bomb protection)
echo 50 | sudo tee "$CG/pids.max"

# Launch a CPU hog INTO the cgroup by writing its PID to cgroup.procs:
( echo $BASHPID | sudo tee "$CG/cgroup.procs" >/dev/null
  exec yes > /dev/null ) &
HOG=$!

sleep 2
# Observe: despite a busy-loop, CPU usage is throttled to ~50%.
echo "== cpu.stat (note throttled_usec climbing) =="
cat "$CG/cpu.stat"
echo "== top view =="
top -b -n1 -p "$HOG" | tail -2     # %CPU should hover near 50, not 100

kill "$HOG" 2>/dev/null || true
sudo rmdir "$CG"
```

To watch the **memory** limit bite, run a small allocator inside the cgroup and watch `memory.events`:

```bash
# Inside the same $CG, a Python balloon that the cgroup will OOM-kill at 100 MiB:
( echo $BASHPID | sudo tee "$CG/cgroup.procs" >/dev/null
  exec python3 -c 'b=[]
import time
while True:
    b.append(bytearray(10*1024*1024)); time.sleep(0.1)' ) &
sleep 3
cat "$CG/memory.events"   # "oom_kill 1" appears once the cap is hit
dmesg | tail -3           # kernel logs the cgroup OOM kill
```

`memory.events` shows `oom_kill 1` and `dmesg` records `Memory cgroup out of memory: Killed process ...` — the cgroup OOM killer enforcing the limit *locally* without touching the rest of the system. This local containment is exactly what Kubernetes relies on when it sets `resources.limits.memory`.

---

## 9. Building a container from scratch

A container = **namespaces + cgroups + a root filesystem (pivot_root) + a dropped capability set + seccomp**. Here is a working minimal container that uses `unshare` for the namespaces, an **overlayfs** for a layered root filesystem, a cgroup for limits, and `pivot_root` to swap the root. No Docker involved.

### 9.1 The overlayfs layered root filesystem

OverlayFS is how container images get **copy-on-write layers**: read-only **lower** layers (image layers) + a writable **upper** layer (the container's changes) merged into one view. Writes go to `upper`; reads fall through to `lower`; deletions create "whiteout" files.

```
   merged (what the container sees)
        |
   +----+------------------------------+
   | upper (rw)  container's writes     |   <- copy-on-write happens here
   +-----------------------------------+
   | lower2 (ro) image layer N          |
   | lower1 (ro) image layer 1 (base)   |   <- shared, read-only, dedup'd
   +-----------------------------------+
   (work = overlayfs's internal scratch dir)
```

```bash
# --- build a layered rootfs with overlayfs (run as root) ---
set -euo pipefail
ROOT=/tmp/ctr
mkdir -p "$ROOT"/{lower,upper,work,merged}

# Populate a tiny "base image" lower layer. Easiest: a debootstrap or a busybox.
# Here we copy a static busybox + make the standard dirs (a real image would be
# an unpacked OCI layer tarball).
mkdir -p "$ROOT/lower"/{bin,proc,sys,dev,etc}
cp "$(command -v busybox)" "$ROOT/lower/bin/busybox"
for applet in sh ls cat mount ps echo id hostname; do
    ln -sf busybox "$ROOT/lower/bin/$applet"
done

# Merge lower (ro) + upper (rw) into 'merged' — this is the container rootfs.
mount -t overlay overlay \
  -o lowerdir="$ROOT/lower",upperdir="$ROOT/upper",workdir="$ROOT/work" \
  "$ROOT/merged"
echo "rootfs assembled at $ROOT/merged"
```

### 9.2 The full from-scratch container (working bash)

```bash
#!/usr/bin/env bash
# minicontainer.sh — a container from scratch: namespaces + cgroup + overlayfs
#                     + pivot_root + capability/seccomp hardening.
# Usage:  sudo ./minicontainer.sh /tmp/ctr/merged
set -euo pipefail
ROOTFS="${1:?usage: minicontainer.sh <rootfs-dir>}"

# 1) cgroup v2: cap the container at 0.5 CPU / 128 MiB / 64 pids.
CG=/sys/fs/cgroup/minicontainer
mkdir -p "$CG"
echo "+cpu +memory +pids" > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
echo "50000 100000"          > "$CG/cpu.max"
echo $((128*1024*1024))      > "$CG/memory.max"
echo 64                      > "$CG/pids.max"

# 2) unshare new namespaces and run the container init.
#    --fork + --pid + --mount-proc => child is PID 1 with a fresh /proc.
#    --user --map-root-user        => rootless-style UID mapping (optional).
CGROUP_DIR="$CG" ROOTFS="$ROOTFS" \
unshare --pid --mount --uts --ipc --net --cgroup --fork --mount-proc \
  /bin/bash -c '
    set -e
    # Join the cgroup created by the parent (PID 1 of this namespace = $$).
    echo $$ > "$CGROUP_DIR/cgroup.procs"

    hostname container               # UTS namespace: safe to rename
    mount --make-rprivate /          # do not propagate mounts back to host

    # 3) pivot_root into the overlay rootfs (proper container root swap).
    cd "$ROOTFS"
    mkdir -p oldroot
    mount --bind "$ROOTFS" "$ROOTFS" # pivot_root needs rootfs to be a mountpoint
    pivot_root . oldroot
    cd /
    mount -t proc proc /proc
    umount -l /oldroot               # detach the old host root: no escape via ..
    rmdir /oldroot 2>/dev/null || true

    echo "=== inside container: PID=$$ ($(hostname)) ==="
    id
    ps -ef                           # only container processes visible
    exec /bin/sh
  '
echo "container exited; cleaning cgroup"
rmdir "$CG" 2>/dev/null || true
```

Run it after building the overlay (`sudo ./minicontainer.sh /tmp/ctr/merged`) and you land in a shell where `hostname` is `container`, `ps -ef` shows only the namespace's processes, the root filesystem is the overlay, and resource use is capped by the cgroup. That is, modulo polish and image management, **everything Docker does**.

### 9.3 What's left to be "Docker"

- **Capabilities** — drop everything you don't need. A container should not run with full root caps. `capsh --drop=cap_sys_admin,cap_net_raw,... --` or, in code, `prctl(PR_CAPBSET_DROP, ...)`. The Docker default drops ~half of all caps.
- **seccomp** — restrict the syscall surface (§11).
- **veth + bridge** — wire the net namespace to the host (`ip link add veth0 type veth peer name veth1`, move one end into the namespace, attach the other to a bridge, NAT out).
- **Image management** — pulling, layer dedup, content-addressing (§10).

---

## 10. OCI images, the runtime stack, and microVMs

### 10.1 OCI image format and content addressing

An **OCI image** (the de-facto standard, generalized from Docker's format) is just:

```text
image = manifest + config + layers
  manifest.json   -> lists the config digest + ordered layer digests + media types
  config.json     -> rootfs (ordered layer DIFF-IDs), env, entrypoint, history
  layer blobs     -> gzipped tarballs of filesystem changesets
all referenced by sha256 CONTENT DIGEST (content-addressable => dedup + integrity)
```

- Each layer is a **diff** (changeset) over the previous; pulling an image fetches only layers you don't already have (dedup by digest). The image config lists layers in order; the runtime stacks them as overlayfs lowerdirs (§9.1).
- **Content addressing** (`sha256:...`) gives integrity (a tampered layer changes its digest) and dedup (identical base layers are stored once). This is why a thin app layer on a shared base is cheap to ship.

### 10.2 The runtime layering

```
   kubectl / Kubernetes
        |  (CRI: Container Runtime Interface, gRPC)
        v
   containerd   ----CRI plugin----  (or CRI-O)
        |  (image pull, snapshotter/overlayfs, lifecycle)
        v
   runc   <-- the OCI *runtime*: reads config.json, does the
        |       clone/unshare + cgroups + pivot_root + caps + seccomp dance
        v
   [ your container process ]
```

- **runc** — the low-level OCI runtime. It does *exactly* what §9 did, driven by an OCI `config.json` "runtime spec". Donated from Docker's `libcontainer`.
- **containerd** — the daemon that manages image pull, storage (snapshotters), and the lifecycle of many containers, invoking `runc` per container. Docker and Kubernetes both sit on it.
- **Docker (dockerd)** — adds the build system (Dockerfile/BuildKit), networking, the friendly CLI/API on top of containerd.
- **Kubernetes** — talks to the node's runtime via the **CRI** (to containerd via the CRI plugin, or to **CRI-O**). Docker-shim was removed in 1.24; k8s no longer needs Docker.

### 10.3 microVMs and userspace kernels — getting VM isolation at container speed

The shared-kernel weakness of containers (§6) is unacceptable for hard multitenancy (running *untrusted* customer code). Two answers:

- **Firecracker** (AWS, powers Lambda and Fargate) — a **microVM** monitor built on KVM. It strips the device model to the minimum (a few virtio devices, no BIOS, no PCI), so a microVM boots in **~125 ms** with **<5 MiB** of memory overhead, yet each workload gets a *real guest kernel behind a hardware VT-x/EPT boundary*. You get VM-grade isolation at near-container density. It is a small, memory-safe (Rust) VMM precisely to shrink the host attack surface.
- **gVisor** (Google) — a **userspace kernel**. Instead of a hardware VM, gVisor's `runsc` interposes a sandbox process (the "Sentry") that **reimplements the Linux syscall API in userspace** and intercepts the container's syscalls (via `ptrace` or KVM). The container never talks to the host kernel directly — only to gVisor, which talks to the host through a tiny, audited syscall set. Trade-off: a syscall-heavy workload pays an interception tax, and not every syscall is implemented.

```text
            container             microVM (Firecracker)        gVisor
           +---------+              +---------+               +---------+
   syscall | app     |     syscall  | app     |        intercepted syscall
           +----v----+              +----v----+               +----v----+
           | HOST    |              | guest   |               | Sentry   | userspace
           | kernel  |              | kernel  |               | (reimpl) | "kernel"
           +---------+              +----v----+               +----v----+
        full shared kernel          | KVM/VT-x|               | small host|
        surface (most risk)         +---------+               | syscall   |
                                  HW boundary, small VMM       | allowlist |
                                                               +-----------+
```

---

## 11. Container security — seccomp, capabilities, rootless

Defense-in-depth for the shared-kernel boundary, in order of importance:

1. **Don't run as root; use a user namespace (rootless).** UID 0 in the container = unprivileged UID on the host (§7.1). This neutralizes most escapes.
2. **Drop capabilities.** Linux split root into ~40 **capabilities** (`capabilities(7)`). Grant only what's needed (`CAP_NET_BIND_SERVICE` to bind <1024, not `CAP_SYS_ADMIN` — "the new root"). Docker drops ~2/3 by default.
3. **seccomp-bpf.** Filter the *syscall surface*. The kernel has ~400 syscalls; a typical app needs ~60. Every syscall you block is attack surface removed (a kernel LPE you can't reach). Docker's default profile blocks ~44 dangerous syscalls.
4. **Read-only rootfs, no new privileges (`PR_SET_NO_NEW_PRIVS`), drop SUID,** and **MAC** (SELinux/AppArmor) on top.

### 11.1 A working seccomp filter (compiling C)

This installs a seccomp-BPF allowlist that lets the program run normally but **kills it the moment it calls a forbidden syscall** (`mkdir`). It shows the exact mechanism Docker/runc use.

```c
/* seccomp_demo.c — install a seccomp-bpf allowlist and prove it blocks a syscall.
 * Build:  cc -O2 -o seccomp_demo seccomp_demo.c
 * Run:    ./seccomp_demo
 * Output: prints "wrote message", then is KILLED (SIGSYS) when it calls mkdir.
 */
#define _GNU_SOURCE
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

/* Helper macros to build BPF instructions for the seccomp classic-BPF VM. */
#define ALLOW    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
#define KILL     BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS)
#define LOAD_NR  BPF_STMT(BPF_LD | BPF_W | BPF_ABS, \
                          offsetof(struct seccomp_data, nr))
#define ALLOW_SYSCALL(name) \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_##name, 0, 1), ALLOW

int main(void) {
    struct sock_filter filter[] = {
        LOAD_NR,                       /* A = syscall number */
        ALLOW_SYSCALL(write),          /* allowlist the few we need */
        ALLOW_SYSCALL(exit_group),
        ALLOW_SYSCALL(rt_sigreturn),
        ALLOW_SYSCALL(brk),
        ALLOW_SYSCALL(newfstatat),
        ALLOW_SYSCALL(close),
        KILL,                          /* default: kill the process */
    };
    struct sock_fprog prog = {
        .len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
        .filter = filter,
    };

    /* NO_NEW_PRIVS is required before a non-privileged seccomp filter. */
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) { perror("no_new_privs"); return 1; }
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog)) {
        perror("seccomp"); return 1;
    }

    const char *msg = "wrote message (write was allowed)\n";
    write(STDOUT_FILENO, msg, strlen(msg));

    /* This forbidden syscall trips the filter -> process killed by SIGSYS. */
    mkdir("/tmp/should_be_blocked", 0755);

    write(STDOUT_FILENO, "this line never prints\n", 23); /* unreachable */
    return 0;
}
```

Running it prints `wrote message (write was allowed)` and then the shell reports the process was killed (`Bad system call`) — the kernel delivered `SIGSYS` the instant `mkdir` was attempted. This is precisely how a container runtime shrinks the syscall attack surface; runc loads a JSON seccomp profile that compiles down to a filter like this.

---

## 12. Performance & overhead reasoning

| Layer | CPU overhead | Memory overhead | Startup | Isolation |
|---|---|---|---|---|
| Bare process | 0 | 0 | µs | none |
| Container (ns+cgroups) | ~0 (native syscalls) | KBs of metadata | ms | shared kernel |
| gVisor | syscall-interception tax (can be 2–5× on syscall-heavy) | tens of MB | ~ms–tens of ms | userspace kernel |
| microVM (Firecracker) | near-native (VT-x), exits on I/O | ~5 MiB + guest kernel | ~125 ms | HW boundary |
| Full VM | near-native CPU, VM-exit tax on I/O/privileged | hundreds of MB (full OS) | seconds | HW boundary |

Reasoning rules:
- **Compute-bound, trusted workloads** → containers; the overhead is noise.
- **Untrusted/multitenant code** → microVM or gVisor; pay the isolation tax deliberately.
- **Legacy OS, kernel-version coupling, live migration** → full VM.
- **The hidden cost in VMs is VM exits**, not raw CPU. Tune by eliminating exits (virtio/vhost, SR-IOV, APICv, huge pages to cut EPT walk cost).

---

## 13. Advanced: KVM internals, cold-start economics, and confidential computing

### How KVM actually runs a guest (virtio & vhost)

A KVM VM is a normal Linux process (usually QEMU) that calls
`ioctl(KVM_RUN)`; the CPU enters guest mode (VT-x non-root,
[§4](#4-hardware-assisted-virtualization-vt-x--amd-v-eptnpt)) and runs guest code
natively until a **VM-exit** (a privileged/sensitive op, an interrupt, or I/O) hands
control back. The performance question is *how often you exit and how expensive each
exit is* — VM-exits cost hundreds-to-thousands of cycles.

I/O is where exits multiply, so virtualization uses **virtio** — a paravirtualized
device model where guest and host share ring buffers and **batch** notifications
(few exits per many I/Os) instead of emulating real hardware (an exit per register
access). **`vhost`** pushes the virtio backend *into the kernel* (vhost-net,
vhost-user/DPDK in userspace) to cut exits further. **Memory ballooning** lets the
host reclaim guest memory on demand; **posted interrupts** deliver interrupts without
an exit. This machinery is why a modern VM runs at ~95-98% of bare metal for
CPU-bound work but pays more for chatty I/O.

### Cold-start economics — the isolation/latency curve

The isolation boundary directly sets startup time, which dominates serverless and
autoscaling:

```
   process/container   ~ms           shared kernel, namespaces only
   gVisor              ~10s of ms     userspace kernel intercept
   Firecracker microVM ~125 ms        minimal device model, fast boot
   full VM (QEMU)      ~seconds       full firmware + device emulation
```

Firecracker exists precisely to put a **hardware isolation boundary** under a
function while keeping cold-start in the ~100 ms range (AWS Lambda/Fargate). Snapshot/
restore (and userfaultfd post-copy, [03 §16](03_memory_management.md)) pushes
cold-start lower by resuming a pre-booted memory image instead of booting. This is the
core trade a platform engineer makes: stronger isolation costs startup latency and
memory, and snapshotting buys some of it back.

### Confidential computing — encrypting memory from the host

The classic VM model trusts the hypervisor and host kernel. **Confidential computing**
removes that trust: **AMD SEV-SNP** and **Intel TDX** encrypt guest memory with a
key the host can't read and attest the guest's integrity, so a compromised hypervisor
(or cloud operator) can't read your data. The cost is some performance overhead and a
more complex attestation/boot chain. This is the frontier for regulated workloads in
shared clouds — and the reason "the cloud provider can read my RAM" is becoming a
solvable concern.

---

## 14. Trade-offs summary

- **Popek-Goldberg** says trap-and-emulate needs *sensitive ⊆ privileged*; x86 violated it, so we got binary translation → paravirt → **VT-x/AMD-V + EPT/NPT**, which restored efficient classical virtualization in hardware.
- **VMs isolate at the hardware boundary** (strong, heavy); **containers isolate via namespaces+cgroups over a shared kernel** (cheap, weaker). They are not competitors so much as different points on the isolation/overhead curve.
- **A container is a process** dressed in namespaces (what it sees), cgroups (what it can use), a pivoted rootfs (overlayfs layers), dropped capabilities, and a seccomp filter. There is no kernel "container" object.
- **OCI images** are content-addressed manifest+config+layer blobs; the stack is `runc` (does the syscalls) ← `containerd` (lifecycle/images) ← Docker/Kubernetes (build/orchestration via CRI).
- **For hard multitenancy of untrusted code**, reach for **Firecracker microVMs** or **gVisor** to regain a strong boundary at a fraction of full-VM cost.
- **Security is defense-in-depth**: rootless (user ns) > drop caps > seccomp > read-only/MAC. Every blocked syscall and dropped cap is kernel attack surface removed.

## 15. Key Takeaways

1. Virtualizability is a *formal* property (Popek-Goldberg): trap-and-emulate works iff every sensitive instruction traps. x86's failure to meet this drove the whole history of virtualization technology.
2. **VT-x/AMD-V** add a root/non-root mode + VMCS so unmodified guest kernels run in non-root ring 0 and only opted-in events cause **VM exits**; **EPT/NPT** make the MMU two-dimensional so guest paging no longer exits. The cost model is "count the VM exits."
3. **virtio/vhost/SR-IOV** are how VMs get fast I/O by replacing per-register-poke emulation with shared-memory rings or direct device assignment.
4. **Containers share the host kernel**; their isolation is the *sum* of namespaces + cgroups + pivot_root + capabilities + seccomp. You can build one from scratch with `unshare`, overlayfs, a cgroup, and `pivot_root` — and that is essentially what `runc` does.
5. **cgroups v2** (unified hierarchy, "no internal processes") expose CPU/memory/io/pids limits as files; writing `cpu.max`/`memory.max` gives the same enforcement Kubernetes `limits` rely on, including the local cgroup OOM killer.
6. **OCI images** are content-addressed layered tarballs stacked via overlayfs; the runtime stack is runc ← containerd ← Docker/Kubernetes (CRI).
7. For **untrusted multitenancy**, microVMs (Firecracker) and userspace kernels (gVisor) buy back a strong boundary; choose the isolation level from the *trust model*, and the overhead from the *VM-exit / syscall-interception cost*.

> Read next: [08 — Linux Internals & Performance Observability](08_linux_internals_observability.md) for how to *measure* what these abstractions actually cost on a running system.
