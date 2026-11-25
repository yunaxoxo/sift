# Process Scheduling in Linux

Notes from working through the kernel docs and OSTEP chapters 7–10. The scheduler
answers one question, thousands of times per second: *which runnable task gets
this CPU next, and for how long?*

## Goals that pull against each other

- **Throughput**: finish as much work as possible per second.
- **Responsiveness**: an interactive task that just woke up should run soon.
- **Fairness**: no runnable task starves.
- **Low overhead**: the scheduler itself must be cheap; it runs on every tick
  and every wakeup.

A batch job wants long uninterrupted slices (warm caches). A text editor wants
to run *right now* for 200 µs and then sleep again. One policy has to serve both.

## The Completely Fair Scheduler (CFS)

CFS was the default scheduler for normal (`SCHED_OTHER`) tasks from 2.6.23 until
6.6. Its core idea is to model an "ideal multitasking CPU" where every runnable
task runs simultaneously at `1/n` speed, and then approximate that on real
hardware.

### Virtual runtime

Every task carries a counter called `vruntime`: the amount of CPU time it has
received, scaled by its weight. When a task runs for `delta` nanoseconds its
`vruntime` advances by roughly

    vruntime += delta * (NICE_0_WEIGHT / task_weight)

A high-priority task has a large weight, so its `vruntime` grows slowly and it
is picked more often. A low-priority task's `vruntime` races ahead.

### Picking the next task

Runnable tasks sit in a **red-black tree ordered by `vruntime`**, one tree per
CPU run queue. The scheduler always picks the leftmost node, which is the task
that has received the least weighted CPU time so far. The leftmost pointer is
cached, so the pick itself is O(1); inserting a task back into the tree is
O(log n).

A task that sleeps does not accumulate `vruntime`, so when it wakes it tends to
be the leftmost node and preempts whatever is running. That is how CFS gets good
interactive latency without any explicit "interactive task" heuristic. To stop a
long sleeper from monopolising the CPU on wakeup, its `vruntime` is clamped to
around the tree's minimum rather than left arbitrarily far behind.

### Nice values and weights

`nice` ranges from −20 (highest priority) to +19 (lowest). Each step changes the
weight by about 1.25×, so a task at nice 0 gets roughly 10% more CPU than one at
nice 1 when both are CPU-bound.

| nice | weight | share vs. a nice-0 task |
|------|--------|-------------------------|
| −5   | 3121   | ~3.0×                   |
| 0    | 1024   | 1.0×                    |
| 5    | 335    | ~0.33×                  |
| 19   | 15     | ~0.015×                 |

### Time slices

CFS has no fixed quantum. It aims to run every runnable task once per
*scheduling period* (`sched_latency_ns`, 6 ms by default on small machines) and
divides that period by weight. With many runnable tasks the slice would become
tiny, so there is a floor (`sched_min_granularity_ns`) below which the period is
stretched instead.

### EEVDF

Since Linux 6.6 the fair class uses **EEVDF** (Earliest Eligible Virtual
Deadline First). It keeps the weighted-fairness accounting but adds a per-task
virtual deadline, which lets latency-sensitive tasks ask for shorter slices
without receiving more total CPU time. Most of the vocabulary above (weights,
virtual time, the red-black tree) carries over.

## Context switches

Switching tasks is not free. The kernel must:

1. save the outgoing task's registers and kernel stack pointer,
2. switch address spaces if the next task belongs to a different process
   (load a new page-table root; without PCIDs this flushes the TLB),
3. restore the incoming task's registers and return to user mode.

The direct cost is a few microseconds. The *indirect* cost is usually larger:
the incoming task finds cold L1/L2 caches and an empty TLB, and runs slowly
until they refill. This is why a scheduler prefers to keep a task on the CPU it
last ran on (cache affinity).

```bash
# voluntary vs. involuntary context switches for a PID
grep ctxt /proc/1234/status

# system-wide switches per second
vmstat 1

# pin a process to CPUs 0 and 1
taskset -cp 0,1 1234
```

## Preemption

A running task loses the CPU when:

- it blocks (I/O, a lock, `sleep`): a *voluntary* switch;
- a timer tick finds that it has exceeded its slice: *involuntary*;
- a task with a much smaller `vruntime` wakes up (wakeup preemption);
- a task in a higher scheduling class becomes runnable.

## Scheduling classes

Classes are consulted in strict priority order; a lower class runs only when
every higher class has nothing runnable.

| class      | policies                    | use                              |
|------------|-----------------------------|----------------------------------|
| deadline   | `SCHED_DEADLINE`            | hard periodic budgets (EDF)      |
| real-time  | `SCHED_FIFO`, `SCHED_RR`    | fixed priority 1–99              |
| fair       | `SCHED_OTHER`, `SCHED_BATCH`| everything else (CFS / EEVDF)    |
| idle       | `SCHED_IDLE`                | runs only when nothing else can  |

A runaway `SCHED_FIFO` task at priority 99 can freeze a machine, which is why
real-time throttling reserves 5% of each second for the fair class by default.

## Multi-core load balancing

Each CPU has its own run queue, which avoids a global lock but lets queues drift
out of balance. A periodic balancer, plus *idle balancing* when a CPU runs dry,
migrates tasks from busy queues to idle ones. Migration is weighed against cache
affinity: moving a task across NUMA nodes also moves it away from its memory.

## Textbook comparison: MLFQ

The multi-level feedback queue keeps several priority queues. A task starts at
the top; using its whole quantum demotes it, yielding early keeps it high, and a
periodic boost returns everything to the top to prevent starvation. MLFQ learns
whether a task is interactive from its behaviour. CFS reaches a similar outcome
through one number, `vruntime`, instead of queue-juggling rules.
