"""The indexing pipeline: chunk -> embed -> store, with per-stage timings.

Stages (each timed separately and persisted to the ``index_runs`` table)::

    chunk_ms       the call into the chunker. For the Rust chunker this
                   includes walking the tree, reading files, chunking, and
                   converting the result into Python objects at the FFI boundary.
    model_load_ms  importing the ML stack and loading weights (only when a
                   model actually had to be loaded during this run)
    embed_ms       embedding-model inference for the chunks that changed
    db_write_ms    SQLite transaction: deletes, inserts, document-frequency rebuild
    total_ms       wall clock for the whole run, all of the above included

Indexing is incremental. Each file's chunks are hashed; a file whose hash is
unchanged is skipped entirely, so re-running ``sift index`` after editing one
note only re-embeds that note. The hash covers the chunker's *output*, so
changing ``--max-chars`` or the chunking algorithm correctly invalidates it.

Chunking runs *before* the model is loaded, so a missing extension or a Rust
stub fails in milliseconds instead of after a multi-second model load, and a
run with nothing to embed never loads the model at all.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sift import config, db
from sift.chunking import ChunkLike, chunk_directory
from sift.embeddings import Embedder
from sift.errors import SiftError
from sift.telemetry import StageTimer
from sift.tokenizer import tokenize


@dataclass(frozen=True)
class IndexReport:
    """Outcome of one ``sift index`` run."""

    root: str
    chunker: str
    embedder: str
    files_seen: int
    files_indexed: int
    files_unchanged: int
    files_removed: int
    chunks_seen: int
    chunks_embedded: int
    timings: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def searchable_text(chunk: ChunkLike) -> str:
    """The text that is embedded and tokenized for a chunk.

    The heading breadcrumb is prepended so that a chunk deep inside a section
    still "knows" its topic: a paragraph under ``TCP > Congestion control >
    Slow start`` that never repeats the word "TCP" remains findable by it.
    The stored ``text`` column (what the user sees) is left untouched.
    """
    if chunk.heading_context:
        return f"{chunk.heading_context}\n{chunk.text}"
    return chunk.text


def _file_hash(chunks: Sequence[ChunkLike]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        header = f"{chunk.chunk_index}\x1f{chunk.start_line}\x1f{chunk.heading_context}\x1f"
        digest.update(header.encode("utf-8"))
        digest.update(chunk.text.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def _check_embedder_matches_index(conn: sqlite3.Connection, embedder: Embedder) -> None:
    """Refuse to mix vectors from two different models in one index."""
    stored_kind = db.get_meta(conn, "embedder_kind")
    stored_model = db.get_meta(conn, "embedding_model")
    if stored_model is None:
        return
    # 'sentence-transformers' and 'onnx' run the same weights, so they may mix.
    families = {"sentence-transformers": "st", "onnx": "st"}
    same_family = families.get(stored_kind or "", stored_kind) == families.get(
        embedder.kind, embedder.kind
    )
    if not same_family or stored_model != embedder.model:
        raise SiftError(
            f"this index was built with {stored_kind}:{stored_model}, "
            f"but the current embedder is {embedder.kind}:{embedder.model}.\n"
            "  Vectors from different models are not comparable. "
            "Re-run with --rebuild to re-embed everything."
        )


def index_directory(
    conn: sqlite3.Connection,
    root: Path,
    embedder: Embedder,
    *,
    chunker: str = config.DEFAULT_CHUNKER,
    extensions: Sequence[str] = config.DEFAULT_EXTENSIONS,
    max_chars: int = config.DEFAULT_MAX_CHARS,
    overlap: int = config.DEFAULT_OVERLAP,
    rebuild: bool = False,
) -> IndexReport:
    """Index every matching file under ``root`` into ``conn``.

    Args:
        conn: Open index database (see :func:`sift.db.connect`).
        root: Directory to index.
        embedder: Embedding model; loaded on demand, only if something changed.
        chunker: ``"rust"``.
        extensions: File extensions to include, without dots.
        max_chars: Maximum chunk length in characters (Rust chunker only).
        overlap: Characters shared by consecutive pieces (Rust chunker only).
        rebuild: Discard the existing index content first.

    Raises:
        SiftError: Bad arguments, or an embedder that does not match the index.
        StubNotImplementedError: The Rust chunker is still ``todo!()``.
    """
    timer = StageTimer()
    started_at = db.utc_now()
    root = root.resolve()

    with timer.stage("total_ms"):
        if rebuild:
            with conn:
                db.clear_index(conn)
        _check_embedder_matches_index(conn, embedder)

        # --- stage 1: walk + chunk -------------------------------------
        with timer.stage("chunk_ms"):
            chunks = chunk_directory(
                root,
                chunker=chunker,
                extensions=extensions,
                max_chars=max_chars,
                overlap=overlap,
            )

        by_file: dict[str, list[ChunkLike]] = {}
        for chunk in chunks:
            by_file.setdefault(chunk.file_path, []).append(chunk)

        # --- diff against what is already indexed ----------------------
        known = db.indexed_files_under(conn, str(root))
        hashes = {path: _file_hash(file_chunks) for path, file_chunks in by_file.items()}
        changed = [path for path in by_file if known.get(path, (0, ""))[1] != hashes[path]]
        unchanged = len(by_file) - len(changed)
        removed_ids = [file_id for path, (file_id, _) in known.items() if path not in by_file]

        # --- stage 2: embed only what changed --------------------------
        pending = [chunk for path in changed for chunk in by_file[path]]
        texts = [searchable_text(chunk) for chunk in pending]
        model_load_ms: float | None = None
        vectors: Sequence = ()
        if pending:
            already_loaded = embedder.load_ms is not None
            embedder.load()
            if not already_loaded:
                model_load_ms = embedder.load_ms
            with timer.stage("embed_ms"):
                vectors = embedder.encode(texts)
        else:
            timer.add("embed_ms", 0.0)
        if len(vectors) != len(pending):
            raise SiftError(f"embedder returned {len(vectors)} vectors for {len(pending)} chunks")

        # --- stage 3: one transaction for all writes -------------------
        with timer.stage("db_write_ms"), conn:
            db.delete_files(conn, removed_ids)
            cursor = 0
            for path in changed:
                file_chunks = by_file[path]
                db.delete_file_by_path(conn, path)
                file_id = db.insert_file(conn, path, hashes[path], len(file_chunks))
                rows = []
                for chunk in file_chunks:
                    tokens = tokenize(texts[cursor])
                    rows.append(
                        (
                            file_id,
                            chunk.chunk_index,
                            chunk.start_line,
                            chunk.heading_context,
                            chunk.text,
                            " ".join(tokens),
                            len(tokens),
                            db.vector_to_blob(vectors[cursor]),
                        )
                    )
                    cursor += 1
                db.insert_chunks(conn, rows)
            if changed or removed_ids:
                db.rebuild_term_stats(conn)
            if pending:
                db.set_meta(conn, "embedder_kind", embedder.kind)
                db.set_meta(conn, "embedding_model", embedder.model)
                db.set_meta(conn, "embedding_dim", str(embedder.dim))

    timings = {name: round(ms, 3) for name, ms in timer.as_dict().items()}
    if model_load_ms is not None:
        timings["model_load_ms"] = round(model_load_ms, 3)

    report = IndexReport(
        root=str(root),
        chunker=chunker,
        embedder=f"{embedder.kind}:{embedder.model}",
        files_seen=len(by_file),
        files_indexed=len(changed),
        files_unchanged=unchanged,
        files_removed=len(removed_ids),
        chunks_seen=len(chunks),
        chunks_embedded=len(pending),
        timings=timings,
    )
    with conn:
        db.record_index_run(
            conn,
            {
                "started_at": started_at,
                "root": report.root,
                "chunker": report.chunker,
                "embedder": report.embedder,
                "files_seen": report.files_seen,
                "files_indexed": report.files_indexed,
                "files_unchanged": report.files_unchanged,
                "files_removed": report.files_removed,
                "chunks_seen": report.chunks_seen,
                "chunks_embedded": report.chunks_embedded,
                "model_load_ms": timings.get("model_load_ms"),
                "chunk_ms": timings.get("chunk_ms", 0.0),
                "embed_ms": timings.get("embed_ms", 0.0),
                "db_write_ms": timings.get("db_write_ms", 0.0),
                "total_ms": timings.get("total_ms", 0.0),
            },
        )
    return report
