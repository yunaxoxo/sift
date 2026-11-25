# TCP Congestion Control

TCP has to answer a question it cannot observe directly: how fast can I send
without overloading a network I know nothing about? Congestion control is the
feedback loop that estimates the answer.

## Two windows

A sender may have at most `min(cwnd, rwnd)` bytes unacknowledged:

- **rwnd** (receive window) is advertised by the receiver and protects the
  *receiver's* buffer. That is flow control.
- **cwnd** (congestion window) is private sender state and protects the
  *network*. That is congestion control, and the rest of this note.

## The classic loss-based algorithm

### Slow start

A new connection knows nothing, so it probes. `cwnd` starts at a small initial
window (10 segments since RFC 6928) and grows by one segment for every ACK
received, which doubles it every round trip. "Slow" is a historical name: the
growth is exponential. Slow start ends when `cwnd` reaches the slow-start
threshold `ssthresh`, or when loss is detected.

### Congestion avoidance (AIMD)

Above `ssthresh` the sender probes gently: `cwnd` grows by about one segment per
round trip (**additive increase**). When a loss is detected, `cwnd` is cut in
half (**multiplicative decrease**).

Why halve, instead of backing off a little? Loss means a queue somewhere
overflowed, so the path is carrying more than it can drain, and every sender
sharing that bottleneck needs to reduce quickly to empty the queue. The
asymmetry also matters for fairness: with additive increase and multiplicative
decrease, a flow with a large window gives up more in absolute terms than a flow
with a small one, so competing flows converge towards equal shares. Additive
decrease, or multiplicative increase, does not converge. The result is the
familiar sawtooth in a plot of `cwnd` over time.

### Fast retransmit and fast recovery

Waiting for a retransmission timeout (RTO) is expensive: the minimum is
typically 200 ms and the sender goes idle. Instead, the receiver sends a
duplicate ACK for every out-of-order segment. **Three duplicate ACKs** are taken
as a sign that one segment was lost while later ones arrived, and the sender
retransmits immediately (fast retransmit). Since data is still flowing, it
halves `cwnd` rather than collapsing to one segment (fast recovery).

A real timeout is treated as much worse news: `ssthresh` is set to half the
current window, `cwnd` drops to one segment, and slow start begins again.

| event               | interpretation            | reaction                         |
|---------------------|---------------------------|----------------------------------|
| new ACK             | path is fine              | grow `cwnd`                      |
| 3 duplicate ACKs    | isolated loss             | retransmit, `cwnd` = `cwnd` / 2  |
| RTO expires         | severe congestion         | `cwnd` = 1 segment, slow start   |

## Modern variants

### CUBIC

The Linux default since 2.6.19. Reno's one-segment-per-RTT increase is painfully
slow on fast long-distance links: after a loss on a 10 Gbit/s, 100 ms path,
recovering the window takes over an hour. CUBIC grows the window as a **cubic
function of the time since the last loss**, independent of RTT: fast at first,
flattening as it nears the window size where loss last occurred, then probing
beyond it. It also reduces by a factor of 0.7 on loss rather than 0.5. It is
still loss-based: it fills the bottleneck buffer until something is dropped.

### BBR

Google's BBR does not treat loss as the primary signal. It builds an explicit
**model of the path** from two measurements: the bottleneck bandwidth (maximum
recent delivery rate) and the minimum round-trip time. It then paces packets at
the estimated bandwidth and keeps roughly one bandwidth-delay product in flight.

BBR compared with CUBIC:

- On links with random, non-congestive loss (Wi-Fi, cellular, long-haul), CUBIC
  backs off unnecessarily; BBR keeps its rate and gets far better throughput.
- With deep buffers, CUBIC fills them and inflates latency (bufferbloat); BBR
  aims to keep queues short.
- BBRv1 can be unfair to loss-based flows sharing a shallow buffer and can cause
  high retransmission rates; BBRv2 and v3 respond to loss and ECN to fix that.

```bash
# which algorithms are available, and which one is active
sysctl net.ipv4.tcp_available_congestion_control
sysctl net.ipv4.tcp_congestion_control

# per-connection cwnd, rtt and retransmits
ss -tin dst 93.184.216.34
```

## Signals other than loss

- **ECN** (Explicit Congestion Notification): routers mark packets instead of
  dropping them; the receiver echoes the mark and the sender reduces `cwnd`
  without any retransmission.
- **Delay**: rising RTT means a queue is building. Vegas and, in part, BBR use
  this to react before loss occurs.

## Bufferbloat

Oversized, unmanaged buffers in home routers let loss-based senders queue
seconds of data before the first drop. Throughput looks fine while latency
becomes terrible. The fixes live in the network as well as the endpoints: active
queue management such as CoDel and fq_codel drops or marks early, on purpose,
to keep queues short.

## Things I keep forgetting

- Congestion control is per connection and sender-side; the two directions of
  one connection are controlled independently.
- QUIC implements the same algorithms in user space on top of UDP.
- `cwnd` is measured in bytes in real stacks; "segments" is textbook shorthand.
