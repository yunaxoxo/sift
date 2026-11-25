"""Ranking functions. Every function body in this file is yours to write.

The search pipeline (``sift/search.py``) calls these three functions; nothing
else needs to change when a body is filled in.

==========================  ====================================================
function                    specification
==========================  ====================================================
``compute_bm25``            IMPLEMENTATION_GUIDE.md section 3
``cosine_similarity``       IMPLEMENTATION_GUIDE.md section 4
``reciprocal_rank_fusion``  IMPLEMENTATION_GUIDE.md section 5
==========================  ====================================================
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

ChunkId = int
"""Primary key of a row in the ``chunks`` table."""

Vector = npt.NDArray[np.floating] | Sequence[float]
"""A 1-D numpy array (what the pipeline passes) or any sequence of floats."""

BM25_K1 = 1.2
"""Default ``k1`` (specification section 3.2)."""

BM25_B = 0.75
"""Default ``b`` (specification section 3.2)."""

RRF_K = 60
"""Default ``k`` (specification section 5.2)."""


@dataclass(frozen=True)
class CorpusStats:
    """Corpus-level quantities supplied to ``compute_bm25`` (read-only).

    A BM25 "document" is one chunk, not one file.
    """

    num_docs: int
    """N: number of chunks in the index."""

    avg_doc_len: float
    """avgdl: mean number of tokens per chunk."""

    doc_freqs: Mapping[str, int]
    """n(t) for the terms of the current query. Terms not present in the
    mapping are not guaranteed to be loaded."""


def compute_bm25(
    query_tokens: Sequence[str],
    doc_tokens: Sequence[str],
    corpus_stats: CorpusStats,
    *,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> float:
    """Okapi BM25 score of one chunk for one query. See specification section 3.

    Args:
        query_tokens: ``sift.tokenizer.tokenize(query)``.
        doc_tokens: Tokens of one chunk, from the same tokenizer.
        corpus_stats: N, avgdl and n(t) for the query terms.
        k1: Term-frequency saturation parameter.
        b: Length-normalisation parameter.

    Returns:
        The score as a Python ``float``.
    """
    raise NotImplementedError("sift/ranking.py: compute_bm25 (IMPLEMENTATION_GUIDE.md section 3)")


def cosine_similarity(vec_a: Vector, vec_b: Vector) -> float:
    """Cosine similarity of two vectors. See specification section 4.

    Args:
        vec_a: First vector (the query embedding, in the pipeline).
        vec_b: Second vector (one chunk embedding).

    Returns:
        The similarity as a Python ``float``.

    Raises:
        ValueError: As specified in section 4.
    """
    raise NotImplementedError(
        "sift/ranking.py: cosine_similarity (IMPLEMENTATION_GUIDE.md section 4)"
    )


def reciprocal_rank_fusion(
    lexical_ranks: Sequence[ChunkId],
    semantic_ranks: Sequence[ChunkId],
    k: int = RRF_K,
) -> list[tuple[ChunkId, float]]:
    """Fuse two ranked lists of chunk ids. See specification section 5.

    Args:
        lexical_ranks: Chunk ids from the lexical stage, best first.
        semantic_ranks: Chunk ids from the vector stage, best first.
        k: Smoothing constant.

    Returns:
        ``(chunk_id, fused_score)`` pairs.
    """
    raise NotImplementedError(
        "sift/ranking.py: reciprocal_rank_fusion (IMPLEMENTATION_GUIDE.md section 5)"
    )
