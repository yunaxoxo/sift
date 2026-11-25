"""SQLite storage: schema, connection setup, and every SQL statement in the project.

Keeping all SQL in this one module means the rest of the code works with plain
Python values and the schema can change without a hunt through the codebase.

Layout (schema version 1)::

    meta         key/value: embedder kind, model name, vector dimension
    files        one row per indexed file, with a hash of its chunks
    chunks       one row per chunk: text, lexical tokens, embedding BLOB
    term_stats   document frequency per term (the "n(t)" in BM25's IDF)
    index_runs   telemetry: one row per `sift index`
    query_log    telemetry: one row per `sift search`

Vectors are stored as raw little-endian float32 bytes (``dim * 4`` bytes per
chunk). At this scale a brute-force scan over BLOBs is faster and simpler than
any ANN index; `sift stats` tells you when that stops being true.
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import numpy.typing as npt

from sift.config import QUERY_LOG_MAX_ROWS
from sift.errors import IndexNotFoundError, SiftError

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY,
    path         TEXT    NOT NULL UNIQUE,
    -- sha256 over every chunk of the file; changes when the content OR the
    -- chunking output changes, which is exactly when re-embedding is needed.
    content_hash TEXT    NOT NULL,
    n_chunks     INTEGER NOT NULL,
    indexed_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY,
    file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    start_line      INTEGER NOT NULL,
    heading_context TEXT    NOT NULL DEFAULT '',
    text            TEXT    NOT NULL,
    -- space-joined output of sift.tokenizer.tokenize(); BM25's view of the chunk
    tokens          TEXT    NOT NULL,
    n_tokens        INTEGER NOT NULL,
    -- little-endian float32, length = meta.embedding_dim * 4 bytes
    embedding       BLOB    NOT NULL,
    UNIQUE (file_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS term_stats (
    term     TEXT PRIMARY KEY,
    doc_freq INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS index_runs (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT    NOT NULL,
    root            TEXT    NOT NULL,
    chunker         TEXT    NOT NULL,
    embedder        TEXT    NOT NULL,
    files_seen      INTEGER NOT NULL,
    files_indexed   INTEGER NOT NULL,
    files_unchanged INTEGER NOT NULL,
    files_removed   INTEGER NOT NULL,
    chunks_seen     INTEGER NOT NULL,
    chunks_embedded INTEGER NOT NULL,
    model_load_ms   REAL,
    chunk_ms        REAL    NOT NULL,
    embed_ms        REAL    NOT NULL,
    db_write_ms     REAL    NOT NULL,
    total_ms        REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS query_log (
    id         INTEGER PRIMARY KEY,
    ts         TEXT    NOT NULL,
    query      TEXT    NOT NULL,
    mode       TEXT    NOT NULL,
    top_k      INTEGER NOT NULL,
    n_results  INTEGER NOT NULL,
    -- NULL means the stage did not run (e.g. embed_ms in lexical mode)
    embed_ms   REAL,
    lexical_ms REAL,
    vector_ms  REAL,
    fusion_ms  REAL,
    total_ms   REAL    NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------


def connect(db_path: Path, *, create: bool = False) -> sqlite3.Connection:
    """Open the index database.

    ``create=True`` (used by ``sift index``) creates the file and schema when
    missing. ``create=False`` (``search`` / ``stats``) raises
    :class:`IndexNotFoundError` instead of silently creating an empty index.
    """
    if not db_path.exists():
        if not create:
            raise IndexNotFoundError(f"no index at {db_path}. Build one first:  sift index <dir>")
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL: readers never block the writer and vice versa, and commits are a
    # sequential append instead of in-place page rewrites.
    conn.execute("PRAGMA journal_mode = WAL")
    # NORMAL is the recommended pairing with WAL: fsync at checkpoints only.
    # A power cut can lose the last commits but cannot corrupt the database,
    # which is the right trade for an index that can always be rebuilt.
    conn.execute("PRAGMA synchronous = NORMAL")
    # Off by default in SQLite; needed for ON DELETE CASCADE on chunks.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the tables on first use; refuse databases from another schema version."""
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found == SCHEMA_VERSION:
        return
    if found != 0:
        raise SiftError(
            f"index schema is version {found} but this build expects {SCHEMA_VERSION}. "
            "Delete the database file and re-run `sift index`."
        )
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def utc_now() -> str:
    """The current UTC time as an ISO-8601 string, to the second."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """The value stored under ``key`` in the ``meta`` table, or ``None``."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Store ``value`` under ``key`` in the ``meta`` table, replacing any earlier value."""
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------------------
# Vector (de)serialisation
# ---------------------------------------------------------------------------


def vector_to_blob(vector: npt.ArrayLike) -> bytes:
    """Serialise a 1-D vector as little-endian float32 bytes."""
    return np.asarray(vector, dtype="<f4").tobytes()


def blob_to_vector(blob: bytes) -> npt.NDArray[np.float32]:
    """Inverse of :func:`vector_to_blob`. Zero-copy: the array is read-only."""
    return np.frombuffer(blob, dtype="<f4")


# ---------------------------------------------------------------------------
# Index writes
# ---------------------------------------------------------------------------


def indexed_files_under(conn: sqlite3.Connection, root: str) -> dict[str, tuple[int, str]]:
    """Map ``path -> (file_id, content_hash)`` for every indexed file below ``root``."""
    prefix = root.rstrip(os.sep) + os.sep
    rows = conn.execute(
        "SELECT id, path, content_hash FROM files WHERE substr(path, 1, ?) = ?",
        (len(prefix), prefix),
    )
    return {row["path"]: (row["id"], row["content_hash"]) for row in rows}


def delete_files(conn: sqlite3.Connection, file_ids: Iterable[int]) -> None:
    """Delete files; their chunks go with them via ON DELETE CASCADE."""
    conn.executemany("DELETE FROM files WHERE id = ?", [(file_id,) for file_id in file_ids])


def delete_file_by_path(conn: sqlite3.Connection, path: str) -> None:
    """Delete a file row; its chunks follow by ``ON DELETE CASCADE``."""
    conn.execute("DELETE FROM files WHERE path = ?", (path,))


def insert_file(conn: sqlite3.Connection, path: str, content_hash: str, n_chunks: int) -> int:
    """Record an indexed file and return its row id."""
    cursor = conn.execute(
        "INSERT INTO files (path, content_hash, n_chunks, indexed_at) VALUES (?, ?, ?, ?)",
        (path, content_hash, n_chunks, utc_now()),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def insert_chunks(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[int, int, int, str, str, str, int, bytes]],
) -> None:
    """Insert chunk rows.

    Row shape: ``(file_id, chunk_index, start_line, heading_context, text,
    tokens, n_tokens, embedding_blob)``.
    """
    conn.executemany(
        "INSERT INTO chunks "
        "(file_id, chunk_index, start_line, heading_context, text, tokens, n_tokens, embedding) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def clear_index(conn: sqlite3.Connection) -> None:
    """Drop all indexed content (not the telemetry). Used by ``--rebuild``."""
    conn.execute("DELETE FROM files")
    conn.execute("DELETE FROM term_stats")
    conn.execute("DELETE FROM meta")


def rebuild_term_stats(conn: sqlite3.Connection) -> int:
    """Recompute document frequencies from scratch; returns the vocabulary size.

    ``doc_freq(t)`` is the number of chunks that contain term ``t`` at least
    once. Recounting everything on each index run is O(total tokens) and
    trivially correct. Maintaining the counts incrementally is a worthwhile
    optimisation only once `sift stats` shows ``db_write_ms`` dominating.
    """
    counts: Counter[str] = Counter()
    for (tokens,) in conn.execute("SELECT tokens FROM chunks"):
        counts.update(set(tokens.split()))
    conn.execute("DELETE FROM term_stats")
    conn.executemany("INSERT INTO term_stats (term, doc_freq) VALUES (?, ?)", counts.items())
    return len(counts)


# ---------------------------------------------------------------------------
# Search reads
# ---------------------------------------------------------------------------


def corpus_size(conn: sqlite3.Connection) -> tuple[int, float]:
    """Return ``(N, avgdl)``: number of chunks and their mean token count."""
    row = conn.execute("SELECT COUNT(*), COALESCE(AVG(n_tokens), 0.0) FROM chunks").fetchone()
    return int(row[0]), float(row[1])


def doc_freqs(conn: sqlite3.Connection, terms: Iterable[str]) -> dict[str, int]:
    """Document frequency for each of ``terms``. Unknown terms are omitted."""
    wanted = sorted(set(terms))
    found: dict[str, int] = {}
    # Stay far below SQLite's bound-parameter limit (999 on old builds).
    for start in range(0, len(wanted), 500):
        batch = wanted[start : start + 500]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT term, doc_freq FROM term_stats WHERE term IN ({placeholders})", batch
        )
        found.update((row["term"], row["doc_freq"]) for row in rows)
    return found


def iter_chunk_tokens(conn: sqlite3.Connection) -> Iterator[tuple[int, list[str]]]:
    """Yield ``(chunk_id, tokens)`` for every chunk, in id order."""
    for chunk_id, tokens in conn.execute("SELECT id, tokens FROM chunks ORDER BY id"):
        yield chunk_id, tokens.split()


def iter_chunk_embeddings(
    conn: sqlite3.Connection,
) -> Iterator[tuple[int, npt.NDArray[np.float32]]]:
    """Yield ``(chunk_id, vector)`` for every chunk, in id order."""
    for chunk_id, blob in conn.execute("SELECT id, embedding FROM chunks ORDER BY id"):
        yield chunk_id, blob_to_vector(blob)


def chunks_by_id(conn: sqlite3.Connection, chunk_ids: Sequence[int]) -> dict[int, sqlite3.Row]:
    """Fetch display fields for the given chunk ids (joined with their file path)."""
    found: dict[int, sqlite3.Row] = {}
    for start in range(0, len(chunk_ids), 500):
        batch = list(chunk_ids[start : start + 500])
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            "SELECT c.id, f.path AS file_path, c.chunk_index, c.start_line, "
            "       c.heading_context, c.text "
            "FROM chunks AS c JOIN files AS f ON f.id = c.file_id "
            f"WHERE c.id IN ({placeholders})",
            batch,
        )
        found.update((row["id"], row) for row in rows)
    return found


def embedding_norms(conn: sqlite3.Connection, chunk_ids: Sequence[int]) -> dict[int, float]:
    """L2 norm of each stored embedding (1.0 for a normalised model)."""
    found: dict[int, float] = {}
    for start in range(0, len(chunk_ids), 500):
        batch = list(chunk_ids[start : start + 500])
        placeholders = ",".join("?" * len(batch))
        for chunk_id, blob in conn.execute(
            f"SELECT id, embedding FROM chunks WHERE id IN ({placeholders})", batch
        ):
            found[chunk_id] = float(np.linalg.norm(blob_to_vector(blob)))
    return found


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

_INDEX_RUN_COLUMNS = (
    "started_at",
    "root",
    "chunker",
    "embedder",
    "files_seen",
    "files_indexed",
    "files_unchanged",
    "files_removed",
    "chunks_seen",
    "chunks_embedded",
    "model_load_ms",
    "chunk_ms",
    "embed_ms",
    "db_write_ms",
    "total_ms",
)


def record_index_run(conn: sqlite3.Connection, run: Mapping[str, object]) -> None:
    """Persist one indexing run. ``run`` must provide every index_runs column."""
    columns = ", ".join(_INDEX_RUN_COLUMNS)
    placeholders = ", ".join("?" * len(_INDEX_RUN_COLUMNS))
    conn.execute(
        f"INSERT INTO index_runs ({columns}) VALUES ({placeholders})",
        [run[column] for column in _INDEX_RUN_COLUMNS],
    )


def last_index_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The most recent indexing run, or ``None`` before the first."""
    return conn.execute("SELECT * FROM index_runs ORDER BY id DESC LIMIT 1").fetchone()


def record_query(
    conn: sqlite3.Connection,
    *,
    query: str,
    mode: str,
    top_k: int,
    n_results: int,
    timings: Mapping[str, float],
) -> None:
    """Append one query to the log and trim the log to its size cap."""
    cursor = conn.execute(
        "INSERT INTO query_log "
        "(ts, query, mode, top_k, n_results, embed_ms, lexical_ms, vector_ms, fusion_ms, total_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            utc_now(),
            query,
            mode,
            top_k,
            n_results,
            timings.get("embed_ms"),
            timings.get("lexical_ms"),
            timings.get("vector_ms"),
            timings.get("fusion_ms"),
            timings["total_ms"],
        ),
    )
    conn.execute(
        "DELETE FROM query_log WHERE id <= ?", ((cursor.lastrowid or 0) - QUERY_LOG_MAX_ROWS,)
    )
    conn.commit()


def recent_query_timings(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Stage timings of the ``limit`` most recent queries, newest first."""
    return conn.execute(
        "SELECT mode, embed_ms, lexical_ms, vector_ms, fusion_ms, total_ms "
        "FROM query_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def overview(conn: sqlite3.Connection) -> dict[str, object]:
    """Corpus-level numbers for ``sift stats``."""
    n_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    n_chunks, avg_tokens = corpus_size(conn)
    vocabulary = conn.execute("SELECT COUNT(*) FROM term_stats").fetchone()[0]
    queries_logged = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    dim = get_meta(conn, "embedding_dim")
    return {
        "files": n_files,
        "chunks": n_chunks,
        "avg_chunk_tokens": round(avg_tokens, 1),
        "vocabulary": vocabulary,
        "embedder": get_meta(conn, "embedder_kind"),
        "embedding_model": get_meta(conn, "embedding_model"),
        "embedding_dim": None if dim is None else int(dim),
        "queries_logged": queries_logged,
    }
