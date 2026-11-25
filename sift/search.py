"""The query pipeline: embed -> lexical -> vector -> fuse, with per-stage timings.

This module is plumbing. All of the math lives in ``sift/ranking.py``:

==============  ==========================================  =================
stage           what happens here                           math it calls
==============  ==========================================  =================
``embed_ms``    embed the query string                      (the model)
``lexical_ms``  load tokens + stats, score every chunk      ``compute_bm25``
``vector_ms``   load vectors, score every chunk             ``cosine_similarity``
``fusion_ms``   merge the two ranked lists                  ``reciprocal_rank_fusion``
``total_ms``    wall clock from entry to hydrated results
==============  ==========================================  =================

Both retrieval stages are scans that read from SQLite on every query, and
their timings include that I/O.

Model loading is not part of ``total_ms``. A CLI invocation pays it once per
process; it is reported separately as ``model_load_ms`` so that the stage
numbers describe the search itself.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass

from sift import config, db, ranking
from sift.embeddings import Embedder
from sift.errors import SiftError
from sift.telemetry import StageTimer
from sift.tokenizer import tokenize

Scored = list[tuple[int, float]]
"""``(chunk_id, score)`` pairs, best first."""


@dataclass(frozen=True)
class SearchHit:
    """One result row, with enough detail to explain why it ranked where it did."""

    rank: int
    chunk_id: int
    file_path: str
    chunk_index: int
    start_line: int
    heading_context: str
    text: str
    score: float
    """Fused RRF score in hybrid mode; the raw BM25 / cosine score otherwise."""
    lexical_rank: int | None
    lexical_score: float | None
    semantic_rank: int | None
    semantic_score: float | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SearchResponse:
    """Hits for one query with the per-stage timings that produced them."""
    query: str
    mode: str
    hits: list[SearchHit]
    timings: dict[str, float]
    model_load_ms: float | None = None
    """Set when this call had to load the embedding model; not part of ``timings``."""

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "mode": self.mode,
            "timings_ms": self.timings,
            "model_load_ms": self.model_load_ms,
            "hits": [hit.as_dict() for hit in self.hits],
        }


def _best_first(scored: Scored, limit: int) -> Scored:
    """Order by score descending, chunk id ascending on ties; keep ``limit``."""
    return sorted(scored, key=lambda pair: (-pair[1], pair[0]))[:limit]


def _lexical_stage(conn: sqlite3.Connection, query_tokens: list[str], pool_size: int) -> Scored:
    """Score every chunk with BM25; return the top ``pool_size`` with score > 0."""
    num_docs, avg_doc_len = db.corpus_size(conn)
    if num_docs == 0 or not query_tokens:
        return []
    stats = ranking.CorpusStats(
        num_docs=num_docs,
        avg_doc_len=avg_doc_len,
        doc_freqs=db.doc_freqs(conn, query_tokens),
    )
    scored: Scored = []
    for chunk_id, doc_tokens in db.iter_chunk_tokens(conn):
        score = ranking.compute_bm25(query_tokens, doc_tokens, stats)
        if score > 0.0:
            scored.append((chunk_id, float(score)))
    return _best_first(scored, pool_size)


def _vector_stage(conn: sqlite3.Connection, query_vector, pool_size: int) -> Scored:
    """Score every chunk by cosine similarity; return the top ``pool_size``."""
    scored: Scored = [
        (chunk_id, float(ranking.cosine_similarity(query_vector, vector)))
        for chunk_id, vector in db.iter_chunk_embeddings(conn)
    ]
    return _best_first(scored, pool_size)


def _check_embedder(conn: sqlite3.Connection, embedder: Embedder) -> None:
    stored_dim = db.get_meta(conn, "embedding_dim")
    if stored_dim is not None and int(stored_dim) != embedder.dim:
        raise SiftError(
            f"the index holds {stored_dim}-dimensional vectors but the embedder "
            f"{embedder.kind}:{embedder.model} produces {embedder.dim}. "
            "Re-index with --rebuild."
        )


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    embedder: Embedder | None,
    mode: str = config.DEFAULT_MODE,
    top_k: int = config.DEFAULT_TOP_K,
    pool_size: int = config.DEFAULT_POOL_SIZE,
    rrf_k: int = ranking.RRF_K,
    log: bool = True,
) -> SearchResponse:
    """Run one query and return the top ``top_k`` chunks.

    Args:
        conn: Open index database.
        query: Free-text query.
        embedder: The model the index was built with. May be ``None`` only in
            ``lexical`` mode, which never embeds.
        mode: ``hybrid`` (BM25 + vectors fused with RRF), ``lexical`` (BM25
            only) or ``semantic`` (vectors only). The single-retriever modes
            exist for ablation: they show what fusion adds.
        top_k: Number of hits to return.
        pool_size: Candidates each retriever contributes to fusion.
        rrf_k: The ``k`` constant passed to ``reciprocal_rank_fusion``.
        log: Append this query's timings to ``query_log`` (feeds ``sift stats``).

    Raises:
        NotImplementedError: A function in ``sift/ranking.py`` is still a stub.
        SiftError: Bad arguments, or an embedder that does not match the index.
    """
    if mode not in config.SEARCH_MODES:
        raise SiftError(f"unknown mode {mode!r}; expected one of {config.SEARCH_MODES}")
    if top_k <= 0 or pool_size <= 0:
        raise SiftError("top_k and pool_size must be positive")
    use_lexical = mode in ("hybrid", "lexical")
    use_semantic = mode in ("hybrid", "semantic")
    if use_semantic and embedder is None:
        raise SiftError(f"mode {mode!r} needs an embedder")

    model_load_ms: float | None = None
    if use_semantic:
        assert embedder is not None
        already_loaded = embedder.load_ms is not None
        embedder.load()
        if not already_loaded:
            model_load_ms = embedder.load_ms
        _check_embedder(conn, embedder)

    timer = StageTimer()
    lexical: Scored = []
    semantic: Scored = []

    with timer.stage("total_ms"):
        if use_semantic:
            assert embedder is not None
            with timer.stage("embed_ms"):
                query_vector = embedder.encode([query])[0]

        if use_lexical:
            with timer.stage("lexical_ms"):
                lexical = _lexical_stage(conn, tokenize(query), pool_size)

        if use_semantic:
            with timer.stage("vector_ms"):
                semantic = _vector_stage(conn, query_vector, pool_size)

        with timer.stage("fusion_ms"):
            if mode == "hybrid":
                fused = [
                    (int(chunk_id), float(score))
                    for chunk_id, score in ranking.reciprocal_rank_fusion(
                        [chunk_id for chunk_id, _ in lexical],
                        [chunk_id for chunk_id, _ in semantic],
                        k=rrf_k,
                    )
                ]
            else:
                fused = lexical if mode == "lexical" else semantic

        top = fused[:top_k]
        lexical_view = {cid: (rank, score) for rank, (cid, score) in enumerate(lexical, 1)}
        semantic_view = {cid: (rank, score) for rank, (cid, score) in enumerate(semantic, 1)}
        rows = db.chunks_by_id(conn, [chunk_id for chunk_id, _ in top])

        hits: list[SearchHit] = []
        for chunk_id, score in top:
            row = rows.get(chunk_id)
            if row is None:  # an id the fusion function invented; skip, do not crash
                continue
            lexical_rank, lexical_score = lexical_view.get(chunk_id, (None, None))
            semantic_rank, semantic_score = semantic_view.get(chunk_id, (None, None))
            hits.append(
                SearchHit(
                    rank=len(hits) + 1,
                    chunk_id=chunk_id,
                    file_path=row["file_path"],
                    chunk_index=row["chunk_index"],
                    start_line=row["start_line"],
                    heading_context=row["heading_context"],
                    text=row["text"],
                    score=score,
                    lexical_rank=lexical_rank,
                    lexical_score=lexical_score,
                    semantic_rank=semantic_rank,
                    semantic_score=semantic_score,
                )
            )

    timings = {name: round(ms, 3) for name, ms in timer.as_dict().items()}
    if log:
        try:
            db.record_query(
                conn, query=query, mode=mode, top_k=top_k, n_results=len(hits), timings=timings
            )
        except sqlite3.Error:
            # Telemetry must never break a search (read-only media, a locked
            # database, a full disk...). The results are still valid.
            conn.rollback()

    return SearchResponse(
        query=query, mode=mode, hits=hits, timings=timings, model_load_ms=model_load_ms
    )
