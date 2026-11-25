# Transactions and Isolation Levels

A transaction groups several statements into one unit that either happens
completely or not at all. Isolation is the part of the guarantee that says what
concurrent transactions are allowed to see of each other's work.

## ACID

- **Atomicity**: all of the transaction's changes are applied, or none are.
- **Consistency**: constraints that held before the transaction hold after it.
- **Isolation**: concurrent transactions do not observe each other's
  intermediate states (to a degree set by the isolation level).
- **Durability**: once a commit is acknowledged, the changes survive a crash.
  This is the job of the write-ahead log.

## What goes wrong without isolation

The standard example is a transfer between two accounts:

```sql
BEGIN;
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
UPDATE accounts SET balance = balance + 100 WHERE id = 2;
COMMIT;
```

Run many of these at once, with reports reading the same rows, and the following
anomalies become possible.

### Dirty read

Transaction B reads a value that A has written but not committed. If A rolls
back, B acted on data that never existed.

### Non-repeatable read

A reads a row, B updates that row and commits, A reads the row again and gets a
different value inside the same transaction.

### Phantom read

A runs `SELECT ... WHERE balance > 1000` and gets five rows. B inserts a new row
that matches the predicate and commits. A runs the same query again and gets
six. The individual rows A read earlier did not change; a new row *appeared*.
Row locks on existing rows cannot prevent this, because the phantom did not
exist when the locks were taken.

### Lost update

A and B both read a counter as 10, both compute 11, both write 11. One increment
is gone.

### Write skew

Two doctors are on call and the rule says at least one must remain. Each
transaction checks "is someone else on call?", sees yes, and removes itself.
Both commit, and nobody is on call. Neither transaction wrote a row the other
wrote, so there is no write conflict to detect, yet the invariant is broken.

## The four standard levels

| level            | dirty read | non-repeatable read | phantom read |
|------------------|------------|---------------------|--------------|
| READ UNCOMMITTED | possible   | possible            | possible     |
| READ COMMITTED   | prevented  | possible            | possible     |
| REPEATABLE READ  | prevented  | prevented           | possible     |
| SERIALIZABLE     | prevented  | prevented           | prevented    |

The SQL standard defines the levels by which anomalies they forbid, which
leaves room for interpretation:

- PostgreSQL's REPEATABLE READ is snapshot isolation and in fact shows no
  phantoms, but it does permit write skew.
- MySQL/InnoDB's REPEATABLE READ prevents most phantoms with gap locks.
- Defaults differ: READ COMMITTED in PostgreSQL and Oracle, REPEATABLE READ in
  MySQL. Do not assume; check.

## How it is implemented

### Two-phase locking (2PL)

Readers take shared locks, writers take exclusive locks, and no lock is released
until the transaction ends. This yields serializable behaviour (with predicate
or gap locks to stop phantoms), but readers block writers and writers block
readers.

Locks also bring **deadlocks**: A holds row 1 and wants row 2 while B holds row
2 and wants row 1. Databases do not try to prevent this. The lock manager
maintains a wait-for graph, detects the cycle, and aborts one transaction as the
victim with a "deadlock detected" error. The application is expected to retry.
Updating rows in a consistent order makes such cycles rare.

### Multi-version concurrency control (MVCC)

Instead of overwriting a row, an update writes a **new version** and marks the
old one as superseded. Every version records which transaction created it and
which one deleted it. A reader works from a **snapshot**, the set of
transactions that had committed when the snapshot was taken, and simply ignores
versions that are not visible to it.

The consequence that makes MVCC the dominant design: **readers do not block
writers, and writers do not block readers.** A long analytical query sees a
consistent picture of the database as of its start, while updates proceed
underneath it. Only two writers updating the *same row* have to wait for one
another.

The price is garbage. Old versions must be kept for as long as any snapshot
might need them and removed afterwards: VACUUM in PostgreSQL, purge of the undo
log in InnoDB. A transaction left open for hours pins old versions, and tables
bloat.

### Snapshot isolation and serializable

Under snapshot isolation a transaction reads from its snapshot and, at commit,
aborts if another transaction has already committed a write to a row it also
wrote (first committer wins). That stops lost updates but not write skew.
PostgreSQL's SERIALIZABLE level adds *serializable snapshot isolation* (SSI): it
tracks read/write dependencies between transactions and aborts one when a
dangerous pattern appears. It is optimistic, so there is no extra blocking, but
the application must be ready to retry.

## Practical guidance

- Keep transactions short. Never hold one open across a network call or while
  waiting for user input.
- For read-modify-write, either lock explicitly with `SELECT ... FOR UPDATE`,
  or fold the change into one statement
  (`UPDATE ... SET n = n + 1 WHERE id = ?`), or use an optimistic version
  column.
- Treat serialization failures and deadlock errors as normal: wrap transactions
  in a retry loop with back-off.
- Raise the isolation level for the few transactions that protect invariants
  spanning several rows, not globally.
