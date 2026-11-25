"""Retrieval-quality metrics used by ``eval/benchmark.py``.

Every function body that computes a metric is yours to write. Definitions,
input domains and error conditions: IMPLEMENTATION_GUIDE.md section 6.

``ranked``
    Document identifiers (file names), best first.

``relevance``
    Ground truth for one query: ``{doc_id: grade}``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def recall_at_k(ranked: Sequence[str], relevance: Mapping[str, int], k: int) -> float:
    """Recall@K for one query. See specification section 6.2."""
    raise NotImplementedError("eval/ir_metrics.py: recall_at_k (IMPLEMENTATION_GUIDE.md section 6)")


def reciprocal_rank(ranked: Sequence[str], relevance: Mapping[str, int]) -> float:
    """Reciprocal rank for one query; its mean over queries is MRR. See section 6.3."""
    raise NotImplementedError(
        "eval/ir_metrics.py: reciprocal_rank (IMPLEMENTATION_GUIDE.md section 6)"
    )


def ndcg_at_k(ranked: Sequence[str], relevance: Mapping[str, int], k: int) -> float:
    """NDCG@K for one query. See specification section 6.4."""
    raise NotImplementedError("eval/ir_metrics.py: ndcg_at_k (IMPLEMENTATION_GUIDE.md section 6)")


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean. Raises ``ValueError`` for an empty sequence."""
    if not values:
        raise ValueError("mean() of an empty sequence")
    return sum(values) / len(values)
