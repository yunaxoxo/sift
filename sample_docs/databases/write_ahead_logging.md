# Write-Ahead Logging

A database must not lose a committed transaction when the machine loses power,
and must not leave half of an uncommitted one behind. Rewriting data pages in
place and hoping is not enough: a crash can interrupt the write. Write-ahead
logging (WAL) is the standard answer.

## The rule

> Before a change to a data page reaches disk, the log record describing that
> change must already be on disk.

Everything else follows from that ordering. The log is an append-only file. Each
record describes one change (which page, what was modified) and carries a
monotonically increasing **log sequence number** (LSN). Each data page remembers
the LSN of the last record applied to it.

A commit is durable as soon as its commit record is on stable storage. The
modified data pages themselves can stay in memory and be written back lazily,
because they can always be reconstructed from the log.

## Why this is fast as well as safe

- **Sequential I/O.** A transaction that touches twenty scattered pages needs
  one sequential append to the log at commit, not twenty random page writes.
- **Deferred page writes.** Dirty pages are flushed in the background, and a
  page changed a hundred times is written once.

## Making it durable: fsync

`write()` only copies bytes into the operating system's page cache. They are
safe against a process crash, but not against power loss. Durability requires
`fsync()` (or `fdatasync`), which blocks until the storage device reports that
the data is persistent. That takes anywhere from tens of microseconds on a good
NVMe drive to ten milliseconds on a spinning disk.

Two details that bite:

- Some consumer drives acknowledge a flush while the data is still in a volatile
  write cache. A database cannot be more durable than its hardware is honest.
- If `fsync` returns an error, the kernel may already have marked the dirty
  pages clean. Retrying can report success without the data being on disk.
  PostgreSQL treats an fsync failure as fatal and restarts recovery for this
  reason.

### Group commit

One `fsync` per transaction would cap throughput at a few thousand commits per
second on fast storage, and far fewer on slow storage. With **group commit**,
transactions that arrive while a flush is in progress queue up, and the next
single `fsync` makes *all* of their commit records durable together. The cost of
one flush is shared by many transactions, so throughput under load rises by an
order of magnitude while each commit waits only slightly longer.

Related knobs trade durability for speed. PostgreSQL's
`synchronous_commit = off` acknowledges the commit before the flush, risking the
last few hundred milliseconds of transactions on a crash but never corruption.
MySQL's `innodb_flush_log_at_trx_commit = 2` is similar.

## Checkpoints

The log cannot grow without bound, and replaying a week of log after a crash
would take hours. A **checkpoint** flushes dirty pages to the data files and
records the LSN up to which the data files are known to be complete. Recovery
then starts from the last checkpoint, and log segments older than it can be
recycled or archived.

Checkpoints are deliberately spread over time. Flushing every dirty page at once
would stall foreground queries.

## Crash recovery

After a crash the data files are in an unknown state: some committed changes
never reached them, and some uncommitted changes did. Recovery in the ARIES
style has three passes over the log.

### 1. Analysis

Scan forward from the last checkpoint to work out which transactions were in
flight at the moment of the crash and which pages may have been dirty.

### 2. Redo

Replay the log forward and re-apply **every** logged change whose LSN is newer
than the LSN stored on the page, including changes made by transactions that
never committed. This "repeats history" and brings the data files to exactly the
state they were in when the crash happened. The page LSN makes redo idempotent:
a change that already reached the page is skipped, so recovery can itself crash
and be restarted safely.

### 3. Undo

Walk backwards and roll back every transaction that had not committed, using the
undo information in the log. Each undo step is itself logged, with a
compensation record, so that a crash during recovery does not undo the same
change twice.

After the redo pass no committed transaction is missing. After the undo pass no
uncommitted change remains. Together they deliver durability and atomicity.

PostgreSQL does not need the undo pass: under MVCC the versions written by an
aborted transaction are simply never visible and are cleaned up later by vacuum.

## Torn pages

A database page is 8 or 16 KiB, but a disk sector is 512 bytes or 4 KiB. A crash
in the middle of a page write can leave a **torn page**, half old and half new,
and a log record that says "set byte 100 to 7" cannot repair it. Two common
defences:

- **Full-page writes** (PostgreSQL): the first modification of a page after each
  checkpoint logs the entire page image, so redo starts from a known-good copy.
- **Doublewrite buffer** (InnoDB): pages are first written to a dedicated
  scratch area and only then to their real location.

## SQLite's WAL mode

SQLite's default rollback journal copies the *original* pages aside before
overwriting them, and readers are blocked while a writer commits. In WAL mode
(`PRAGMA journal_mode = WAL`) the roles are reversed:

- New page versions are appended to a separate `-wal` file; the main database
  file is left untouched.
- A reader notes the end of the WAL when it starts and consults both files, so
  it sees a consistent snapshot. **Readers do not block the writer and the
  writer does not block readers.** There is still only one writer at a time.
- A checkpoint copies WAL pages back into the main file, automatically once the
  WAL reaches about 1000 pages.

```sql
PRAGMA journal_mode = WAL;
-- with WAL, NORMAL fsyncs at checkpoints only: a power cut may lose the most
-- recent commits but cannot corrupt the database
PRAGMA synchronous = NORMAL;
-- run a checkpoint now and truncate the -wal file
PRAGMA wal_checkpoint(TRUNCATE);
```

A reader that never finishes prevents checkpoints from completing, and the
`-wal` file grows without limit. This is the SQLite version of the
long-transaction problem.

## The log as a replication stream

Because the log is a complete, ordered description of every change, shipping it
to another machine and replaying it there produces a replica. PostgreSQL
streaming replication, MySQL's binlog and most change-data-capture tools are
built on this, as is point-in-time recovery: restore a base backup, then replay
archived log up to the moment just before the mistake.
