# Virtual Memory and Paging

Every process believes it owns a large, private, contiguous address space. The
hardware (MMU) and the kernel maintain that illusion by translating *virtual*
addresses to *physical* ones on every memory access.

## Why bother

- **Isolation**: one process cannot read or corrupt another's memory.
- **Overcommit**: the sum of all address spaces can exceed physical RAM.
- **Sharing**: the same physical page (libc code, a file) can appear in many
  address spaces.
- **Simplicity for programs**: the heap and stack can grow without the program
  caring where free physical memory happens to be.

## Pages and frames

Virtual memory is divided into fixed-size **pages** (4 KiB on x86-64 and most
arm64 systems; 16 KiB on Apple silicon). Physical memory is divided into
**frames** of the same size. Translation maps a virtual page number to a frame
number; the low bits (the offset inside the page) pass through unchanged.

```bash
# page size on this machine
getconf PAGESIZE

# resident vs. virtual size of a process
ps -o pid,rss,vsz,comm -p 1234
```

## Page tables

A single flat table for a 48-bit address space would need 2^36 entries per
process, so real systems use a **multi-level tree**. x86-64 uses four levels
(five with LA57); each level is indexed by 9 bits of the virtual address:

    47       39 38       30 29       21 20       12 11         0
    +----------+-----------+-----------+-----------+------------+
    |  PML4    |   PDPT    |    PD     |    PT     |   offset   |
    +----------+-----------+-----------+-----------+------------+

Only the parts of the tree that cover mapped regions are allocated, so a sparse
address space costs little. Each page-table entry (PTE) holds the frame number
plus flags: present, writable, user-accessible, accessed, dirty, no-execute.

## The TLB

Walking four levels means four extra memory reads per access, which would be
ruinous. The **Translation Lookaside Buffer** is a small associative cache of
recent virtual-to-physical translations inside the CPU. A hit costs nothing
extra; a miss triggers a hardware page-table walk.

### TLB shootdowns

When the kernel changes or removes a mapping (for example on `munmap`, or when
write-protecting a page), stale entries may still sit in the TLBs of *other*
cores running threads of the same process. The kernel must send an
inter-processor interrupt (IPI) to each of those cores and wait for them to
flush the entry. This is a **TLB shootdown**. It scales badly with core count,
and it is a well-known cost of `mmap`-heavy workloads on large machines.

Process-context identifiers (PCID on x86, ASID on arm) tag TLB entries with an
address-space id so that a context switch does not have to flush everything.

## Page faults

A page fault is the exception the CPU raises when a translation cannot be
completed: the PTE is missing, not present, or lacks the needed permission. The
kernel's handler looks at the faulting address and decides what it means.

| kind    | cause                                             | cost                 |
|---------|---------------------------------------------------|----------------------|
| minor   | page is in memory but not yet mapped for this process | microseconds     |
| major   | page must be read from disk (file or swap)        | milliseconds         |
| invalid | address not in any mapping, or permission violated | `SIGSEGV`           |

Faults are not errors in general. They are how the kernel implements laziness:

- **Demand paging.** `malloc` of 1 GiB returns immediately; physical frames are
  allocated only when each page is first touched (a minor fault that maps a
  zero-filled frame).
- **File mappings.** `mmap` of a file reads nothing up front; pages come in from
  the page cache or disk on first access.
- **Copy-on-write.** See below.

### Copy-on-write

`fork()` does not copy the parent's memory. Parent and child share every frame,
with the PTEs marked read-only. The first write by either side faults; the
kernel copies just that one page, maps the copy writable, and resumes. A child
that immediately calls `exec` therefore copies almost nothing.

## When memory runs out

### Page replacement

When free frames run low the kernel reclaims some. Clean file-backed pages can
simply be dropped (they can be re-read). Dirty pages must be written back first.
Anonymous pages (heap, stack) have no file behind them and must go to **swap**.

Choosing a victim is an approximation of least-recently-used. Linux keeps active
and inactive lists and uses the PTE *accessed* bit as a cheap recency signal,
which is a relative of the textbook **clock** (second-chance) algorithm. Newer
kernels can use multi-generational LRU.

### Thrashing

If the working sets of the running processes do not fit in RAM, the system
spends its time evicting pages that are needed again moments later. Every
access becomes a major fault, the disk is saturated, and CPU utilisation
*drops* because everything is waiting on I/O. Symptoms: the machine crawls, the
swap-in and swap-out columns of `vmstat` are both high, and load average climbs
while nothing gets done. The cure is less memory demand (kill something, lower
concurrency) or more RAM; faster swap only moves the cliff.

The OOM killer is the kernel's last resort: it picks a process with a high
badness score and kills it to free memory.

## Huge pages

With 4 KiB pages, a 64-entry L1 TLB covers only 256 KiB. **Huge pages** (2 MiB
or 1 GiB on x86-64) let one TLB entry cover far more, cutting miss rates for
databases and JVMs with large heaps. Costs: internal fragmentation, and the
kernel must find or compact contiguous physical memory. Transparent Huge Pages
does this automatically, sometimes with latency spikes from compaction.

## Quick reference

- Translation = page-table walk, cached by the TLB.
- Fault = "kernel, please finish this mapping", not necessarily a bug.
- RSS = frames actually resident; VSZ = address space reserved.
- Minor faults are cheap, major faults are disk I/O, thrashing is major faults
  in a loop.
