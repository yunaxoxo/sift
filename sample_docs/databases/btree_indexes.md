# B-Tree Indexes

An index is a second data structure that lets the database find rows without
reading the whole table. Nearly every relational database uses a B+tree for its
default index, and for the primary-key storage of the table itself.

## Why a tree, and why this tree

A sequential scan reads every page: O(n). A balanced binary tree would find a
key in O(log₂ n) node visits, but on disk each visit is a page read, and for a
billion rows that is about 30 of them. A B-tree makes each node as large as a
disk page (4–16 KiB) and stores hundreds of keys in it, so the base of the
logarithm becomes the **fanout**:

| fanout | rows reachable with height 3 | height 4          |
|--------|------------------------------|-------------------|
| 100    | 1,000,000                    | 100,000,000       |
| 500    | 125,000,000                  | 62,500,000,000    |

Three or four page reads reach any row, and the upper levels are small enough
to stay in the buffer cache permanently, so a lookup is typically one physical
read.

## Structure of a B+tree

- **Internal nodes** hold only separator keys and child pointers. They exist to
  route a search.
- **Leaf nodes** hold every key together with its payload (the row itself, or a
  pointer to it).
- **All leaves are at the same depth**, so every lookup costs the same.
- **Leaves are linked** to their neighbours in key order, as a doubly linked
  list.

### Point lookups

Start at the root, binary-search the node for the child whose key range contains
the target, descend, and repeat until a leaf is reached. Cost: the height of the
tree.

### Range scans

This is where the linked leaves pay off. For `WHERE created_at BETWEEN a AND b`
the engine descends once to find `a`, then simply walks sideways along the leaf
chain until it passes `b`. It never climbs back up to an internal node, and the
leaf pages are read in key order, which is close to sequential I/O. The same
property gives `ORDER BY` on the indexed column for free, and makes prefix
searches (`LIKE 'abc%'`) efficient. A hash index, by contrast, can only answer
equality.

## Inserts and page splits

An insert finds the correct leaf and places the key in sorted position. If the
leaf is full it **splits**: half of its entries move to a new page, and a
separator key is pushed up into the parent, which may split in turn. The tree
grows taller only when the root itself splits, which is why it stays balanced.

Consequences worth knowing:

- Monotonically increasing keys (auto-increment ids, timestamps) always insert
  into the rightmost leaf, which keeps pages full and inserts cheap, but
  concentrates contention on one page.
- Random keys such as UUIDv4 insert all over the tree. Pages end up about half
  full after splits, and most inserts touch a page that is not in cache.
  UUIDv7 and ULIDs put a timestamp first to avoid this.
- Deletes leave gaps. Space is reused by later inserts, but an index can become
  bloated after mass deletes and need a rebuild.

## Clustered and secondary indexes

A **clustered index** stores the rows themselves in its leaves, so the table
*is* the tree, ordered by that key. InnoDB always clusters on the primary key;
SQLite does so for rowid and `WITHOUT ROWID` tables. PostgreSQL does not: its
tables are unordered heaps and every index is secondary.

A **secondary index** stores the indexed columns plus a locator for the row:
the primary key in InnoDB, a physical tuple id in PostgreSQL. A lookup through a
secondary index is therefore two steps: search the index, then fetch the row.
That second step is random I/O, once per matching row.

## Covering indexes

If an index contains *every column the query needs*, the engine can answer from
the index alone and skip the second step entirely. This is an **index-only
scan**, and the index is said to *cover* the query.

```sql
-- the query
SELECT status, total FROM orders WHERE customer_id = 42;

-- finds matching rows, but must then visit the table for status and total
CREATE INDEX orders_customer ON orders (customer_id);

-- covers the query: no table access at all
CREATE INDEX orders_customer_cov ON orders (customer_id) INCLUDE (status, total);
```

For a query returning thousands of rows this is often the difference between
milliseconds and seconds. The trade-off is a larger index and more work on every
write.

## Composite indexes and the leftmost prefix

An index on `(a, b, c)` is sorted by `a`, then by `b` within equal `a`, then by
`c`. It can serve predicates on `a`, on `a, b`, or on `a, b, c`, but not on `b`
alone, in the same way that a phone book sorted by surname does not help you
find everyone called "Maria". Put equality columns first and the range column
last: after a range predicate, the remaining columns are no longer in order.

## When indexes hurt

- Every index must be updated on every insert, and on updates that touch its
  columns. Five indexes means roughly six writes per inserted row.
- Low-selectivity predicates (`WHERE active = true` matching 90% of rows) are
  faster as a sequential scan; the planner will ignore the index, correctly.
- Wrapping the column in a function (`WHERE lower(email) = ...`) defeats a plain
  index. Use an expression index or store the normalised value.

Check what the planner actually does rather than guessing:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT status, total FROM orders WHERE customer_id = 42;
```

## B-trees versus LSM trees

| aspect            | B+tree                               | LSM tree                          |
|-------------------|--------------------------------------|-----------------------------------|
| write path        | update pages in place, random I/O    | append to memtable, sequential    |
| read path         | one tree, predictable                | several levels, Bloom filters     |
| space             | pages partly empty after splits      | compact, but needs compaction     |
| best at           | read-heavy and range-heavy workloads | write-heavy ingestion             |

PostgreSQL, MySQL/InnoDB and SQLite are B-tree engines. RocksDB, Cassandra and
ScyllaDB are LSM engines.
