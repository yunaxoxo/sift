# Locks, Semaphores and Deadlock

Shared mutable state plus preemption equals race conditions. These notes cover
the primitives used to prevent them and the new failure modes those primitives
introduce.

## The problem: critical sections

`counter++` compiles to load, add, store. Two threads interleaving those three
steps can both read 41 and both write 42: one increment is lost. The sequence
that must not be interleaved is a **critical section**, and the property we want
is **mutual exclusion**: at most one thread inside at a time.

## Mutex

A mutex has two states, locked and unlocked, and an owner. `lock()` either
acquires it or **puts the calling thread to sleep** until it is released;
`unlock()` wakes a waiter.

- Sleeping costs a context switch (a few microseconds) but burns no CPU while
  waiting.
- Only the owner may unlock.
- Correct default for anything that might be held for more than a handful of
  instructions, and for any critical section that can block (I/O, allocation).

### Futex: the fast path

A naive mutex would make a system call on every lock and unlock. Linux mutexes
are built on the **futex** (fast userspace mutex): the lock word lives in user
memory, an uncontended acquire is a single atomic compare-and-swap with no
kernel involvement, and the `futex()` system call is made only to sleep or to
wake a waiter. The common case costs tens of nanoseconds.

## Spinlock

A spinlock waiter does not sleep. It loops, re-testing the lock word, until the
holder releases it (**busy waiting**).

- No context switch, so it wins when the hold time is shorter than the cost of
  sleeping and waking (well under a microsecond).
- It wastes a whole core while waiting, and is disastrous if the holder gets
  preempted: every waiter spins for the holder's entire time off-CPU.
- Used inside kernels (with preemption or interrupts disabled around the hold)
  and in a few carefully measured user-space hot paths.

### Mutex or spinlock?

| question                              | mutex            | spinlock               |
|---------------------------------------|------------------|------------------------|
| what a waiter does                    | sleeps           | spins (burns CPU)      |
| cost when uncontended                 | one atomic op    | one atomic op          |
| cost when contended                   | context switch   | wasted cycles          |
| may the holder block or sleep?        | yes              | never                  |
| sensible on a single core?            | yes              | no                     |

Rule of thumb: use a mutex. Many mutex implementations are *adaptive*: they spin
briefly in case the holder is about to release, then fall back to sleeping,
which captures most of the spinlock's benefit without its worst case.

## Semaphore

A semaphore is a non-negative counter with two atomic operations: `wait` (P)
decrements, blocking while the counter is zero; `signal` (V) increments and
wakes a waiter. A *binary* semaphore behaves like a lock without ownership. A
*counting* semaphore limits concurrency to N, for example a connection pool of
ten. Unlike a mutex, any thread may signal, which makes semaphores useful for
signalling between threads as well as for exclusion.

## Condition variable

A condition variable lets a thread sleep until some predicate over shared state
becomes true. It is always paired with a mutex:

```c
pthread_mutex_lock(&m);
while (queue_is_empty(q))          /* while, not if */
    pthread_cond_wait(&nonempty, &m);
item = dequeue(q);
pthread_mutex_unlock(&m);
```

`cond_wait` atomically releases the mutex and sleeps, then re-acquires the mutex
before returning. The loop is mandatory: wakeups can be **spurious**, and
another thread may have consumed the item between the signal and the wakeup.

## Deadlock

A set of threads is deadlocked when each one waits for a resource held by
another in the set, so none can ever proceed.

### The four Coffman conditions

Deadlock is possible only if all four hold at once:

1. **Mutual exclusion**: a resource can be held by only one thread at a time.
2. **Hold and wait**: a thread holds one resource while waiting for another.
3. **No preemption**: a resource cannot be taken away; it must be released
   voluntarily.
4. **Circular wait**: there is a cycle of threads, each waiting on the next.

Break any one and deadlock becomes impossible.

### Prevention in practice

- **Lock ordering** breaks circular wait: define a global order and always
  acquire in that order. This is the technique that actually gets used.
- **Try-lock with back-off** breaks hold-and-wait: if the second lock is busy,
  release the first and retry. Beware livelock.
- **Coarser locks**: one lock cannot deadlock with itself, at the price of
  scalability.
- **Detection** instead of prevention: build a wait-for graph, look for a cycle,
  and abort a victim. Databases do this for row locks, because transactions can
  be rolled back and retried; ordinary threads cannot.

## Other hazards

### Priority inversion

A low-priority thread holds a lock that a high-priority thread needs, and a
medium-priority thread preempts the low one, so the high-priority thread is
stuck behind work of medium importance. The Mars Pathfinder resets were this
bug. The fix is **priority inheritance**: the holder temporarily runs at the
priority of its highest-priority waiter.

### Reader–writer locks

Many readers *or* one writer. Worth it only when reads dominate and critical
sections are long; otherwise the bookkeeping costs more than a plain mutex.
Decide up front whether writers can starve.

### Lock-free alternatives

Atomic compare-and-swap (CAS) loops avoid locks entirely for simple structures
such as counters, stacks and queues. They cannot deadlock, but they bring their
own problems: the ABA problem, and memory reclamation without a garbage
collector. Reach for them only after measuring lock contention.
